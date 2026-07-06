"""主分类器：IR Document → 语义 StructuredDoc。

用"区段状态机"把文档切成 前置 / 摘要 / 关键词 / 正文 / 参考文献 / 后置 几大块，
再在前置块内用位置 + 内容启发式识别各元数据。忠实抽取，不臆造编辑改动。
"""

from __future__ import annotations

import re
from typing import Optional

from ..model.blocks import Paragraph, Table
from ..model.structured import (AbstractSection, Author, DateInfo,
                                StructuredDoc)
from . import frontmatter as FM
from . import patterns as P
from .sections import build_sections


def _decl_label_len(text: str) -> int:
    """行内声明标签("Author Contributions:")在**原始段文本**中的长度(含标签后空白)。

    供渲染时从首块剥离前缀,使正文不重复标签。必须基于原始 text(而非 strip 后的串)
    计算,否则与 drop_leading_chars 作用的原始 runs 偏移不一致;尾随空白一并吃掉,
    避免内容出现前导空格。"""
    m = P.DECLARATION_INLINE.match(text)
    if not m:
        return 0
    end = m.end()
    while end < len(text) and text[end] in " \t ":
        end += 1
    return end


def _is_decl_boundary(text: str) -> bool:
    """声明区的"终止边界"行:出现即表示声明小节到此为止,该行本身不成声明小节,
    也不应被前一声明小节当作内容吞并。

    覆盖两类紧跟在声明区之后、却既非声明也非正文的块:
      - "Author information:" / "Authors:" 等作者元数据标签(AUTHOR_LABEL,整行=标签);
      - "Capsule: …" 一句话研究摘要(CAPSULE_LABEL)。
    根因:补充样例3 的声明区在摘要之前,Trial registration 之后紧跟 Capsule 与重复的
    Author information;旧逻辑里 Capsule 被当成声明小节、又把其后的 Author information
    一并吞入,致 <back> 过抽(6 节,且边界错乱)。以此边界收住,声明区止于上一真声明。"""
    return bool(P.CAPSULE_LABEL.match(text) or P.AUTHOR_LABEL.match(text))


def _nonempty(b) -> bool:
    if isinstance(b, Table):
        return True
    # 保留有文本、或只含图片/公式的段(图片表/图/块级公式需要这些段不被丢弃)
    return bool(b.text.strip()) or bool(b.images) or bool(b.maths)


class Classifier:
    def __init__(self, journal_id: Optional[str] = None, doi: Optional[str] = None):
        self.journal_id = journal_id
        self.doi = doi

    def classify(self, doc) -> StructuredDoc:
        sd = StructuredDoc()
        blocks = [b for b in doc.blocks if _nonempty(b)]

        # ---- 定位区段边界 ---------------------------------------------- #
        idx_abstract = idx_keywords = idx_refs = None
        for i, b in enumerate(blocks):
            if not isinstance(b, Paragraph):
                continue
            t = b.text.strip()
            sname = (b.style_name or "")
            if idx_abstract is None and (
                    P.ABSTRACT_LABEL.match(t) or
                    P.ABSTRACT_INLINE.match(t) or          # "Abstract: 正文…"(行内标签)
                    (P.ABSTRACT_STYLE.search(sname) and len(t) < 30
                     and t.lower().startswith("abstract"))):
                idx_abstract = i
            if idx_keywords is None and (P.KEYWORDS_LABEL.match(t) or
                                         P.KEYWORD_STYLE.search(sname)):
                idx_keywords = i
            if idx_refs is None and P.REFERENCES_HEAD.match(t):
                idx_refs = i
        # 摘要无显式标签时：取关键词前首个"结构化子标题"段作为摘要起点
        if idx_abstract is None:
            limit = idx_keywords if idx_keywords is not None else len(blocks)
            for i in range(min(limit, len(blocks))):
                b = blocks[i]
                if isinstance(b, Paragraph) and \
                        P.ABSTRACT_SUBHEAD.match(b.text.strip()):
                    idx_abstract = i
                    break

        # ---- 关键词（内容可能在标签下一段）---------------------------- #
        kw_last = None
        if idx_keywords is not None:
            kw_last = self._parse_keywords(blocks, idx_keywords, sd)

        # ---- 正文起点 -------------------------------------------------- #
        body_start = None
        if idx_keywords is not None:
            body_start = (kw_last if kw_last is not None else idx_keywords) + 1
        elif idx_abstract is not None:
            body_start = idx_abstract + 1

        # ---- 前置区解析 ------------------------------------------------ #
        front_end = idx_abstract if idx_abstract is not None else (
            idx_keywords if idx_keywords is not None else (
                body_start if body_start is not None else len(blocks)))
        self._parse_front(blocks[:front_end], sd)

        # ---- 摘要 ------------------------------------------------------ #
        if idx_abstract is not None:
            ab_end = idx_keywords if idx_keywords is not None else (
                body_start if body_start is not None else len(blocks))
            self._parse_abstract(blocks[idx_abstract:ab_end], sd)

        # ---- 正文 + 后置 ----------------------------------------------- #
        if body_start is None:
            body_start = front_end
        body_end = idx_refs if idx_refs is not None else len(blocks)
        body_blocks, back_blocks = self._split_back(blocks[body_start:body_end])
        sd.body = build_sections(body_blocks)
        self._parse_back(back_blocks, sd)

        # ---- 参考文献 -------------------------------------------------- #
        if idx_refs is not None:
            from .references import parse_references
            sd.references = parse_references(blocks[idx_refs + 1:])

        return sd

    # ------------------------------------------------------------------ #
    def _parse_front(self, blocks, sd: StructuredDoc):
        paras = [b for b in blocks if isinstance(b, Paragraph)]
        if not paras:
            return
        i = 0
        n = len(paras)

        # 1) 稿件类型
        for j in range(min(3, n)):
            key = paras[j].text.strip().lower().rstrip(":")
            if key in P.ARTICLE_TYPES:
                cat, atype = P.ARTICLE_TYPES[key]
                sd.article_category, sd.article_type = cat, atype
                i = j + 1
                break

        # 2) 标题：类型之后第一段非空
        while i < n and not paras[i].text.strip():
            i += 1
        if i < n:
            sd.title = re.sub(r"\s+", " ", paras[i].text.strip())
            i += 1

        # 3) 作者行：标题之后第一段;跳过 "Author information:" / "Authors:" 这类标签行
        while i < n and (not paras[i].text.strip() or
                         P.AUTHOR_LABEL.match(paras[i].text.strip())):
            i += 1
        if i < n:
            authors = FM.parse_author_line(paras[i])
            if authors:
                sd.authors = authors
                i += 1

        # 4) 其余前置块：单位 / ORCID / 通讯 / 编辑 / 日期 / 共同贡献
        orcids = []  # (orcid, nearby_text)
        aff_seen = False
        corresp_started = False  # 联系/通讯区开始后，后续地址行不再当作 affiliation
        in_corresp = False       # 是否处于"通讯"区(仅 correspondence 标签触发):其后邮箱按通讯邮箱归属
        last_person = None       # 最近被点名的作者(如 "Aimin Dang:" 行):供后续无名邮箱行归属
        # 作者上标引用到的单位编号集合:据此识别"以该编号开头"的单位行(即便无机构关键词)
        needed_aff = {lab for a in sd.authors for lab in a.aff_labels}
        for k in range(i, n):
            p = paras[k]
            t = p.text.strip()
            if not t:
                continue
            low = t.lower()
            sname = p.style_name or ""

            if P.AFFIL_LABEL.match(t):           # "Affiliations:" 标签行
                aff_seen = True
                continue
            if P.EDITOR_LABEL.search(t):
                self._parse_editor(t, sd)
                continue
            # ORCID
            if "orcid" in low or P.ORCID_URL.search(t) or \
               (P.ORCID.search(t.replace(" ", "")) and len(t) < 90):
                oc = P.normalize_orcid(t)
                if oc:
                    orcids.append((oc, t))
                    continue
            # 通讯标签行(Address for correspondence / *Corresponding author / Correspondence:)
            #  → 开启通讯区(其后邮箱按通讯邮箱归属);标签行本身可能同时带姓名/邮箱
            if P.CORRESP_LABEL.search(t):
                corresp_started = True   # 抑制后续地址行被当 aff
                in_corresp = True
                who = self._match_author_by_name(sd, t)
                if who is not None:
                    last_person = who
                if not P.EMAIL.search(t):
                    continue
            # 邮箱行:关联到"本行点名 / 最近点名"的作者;在通讯区(或该作者带 * 标记)→ 通讯邮箱,
            #  否则 → 该作者的普通 <email>(须在 aff 之前判定,否则会被误当机构)
            if P.EMAIL.search(t):
                corresp_started = True   # 邮箱之后的地址行同样不再当 aff(保持原行为)
                last_person = self._collect_email(t, sd, in_corresp, last_person)
                continue
            # 名字行(短、含某作者姓名、非机构):记为"最近点名作者",供后续无名邮箱行归属
            person = self._match_author_by_name(sd, t)
            if person is not None and len(t) < 60 and not P.INSTITUTION.search(t):
                last_person = person
            # 共同贡献说明(忠实搬运 docx 原文;措辞多样,不止"contributed equally")
            if re.search(r"(?i)contribut(?:ed|e) equally|equal contribut|"
                         r"joint first author|co-?first author|first two authors|"
                         r"regarded as joint|share[ds]? (?:the )?first authorship|"
                         r"equally to (?:this|the) (?:work|study|manuscript)", t) and len(t) < 200:
                sd.equal_contrib_note = t
                continue
            # 收发日期:含 Submitted/Revised/Accepted 标签的段(可能一段多行多日期)
            if re.search(r"(?i)submit|receiv|revis|accept", t) and re.search(r"\d", t) and len(t) < 200:
                labeled = FM.parse_labeled_dates(t)
                if labeled:
                    for slot, dd in labeled.items():
                        setattr(sd.dates, slot, dd)
                    continue
            # 裸日期(短行)
            d = FM._parse_date(t)
            if d and len(t) < 90 and not P.INSTITUTION.search(t):
                self._assign_date(t, d, sd)
                continue
            # affiliation：样式名暗示 / 含机构关键词 / 或"以作者引用的单位编号开头"
            # (后者用于机构名无常见关键词的情形,如 "2Academician Workstation…")。
            # 通讯区开始后不再识别 affiliation（避免把通讯地址当机构）
            lead_aff = re.match(r"^\s*(\d{1,2})\s*[A-Za-z]", t)
            starts_needed = bool(lead_aff and lead_aff.group(1) in needed_aff)
            is_aff = (not corresp_started) and len(t) < 300 and not P.EMAIL.search(t) and (
                bool(P.AFF_STYLE.search(sname)) or
                (aff_seen and P.INSTITUTION.search(t)) or
                P.INSTITUTION.search(t) or
                starts_needed)
            if is_aff:
                aff = FM.parse_affiliation(p)
                if aff and aff.text and len(aff.text) > 4:
                    sd.affiliations.append(aff)
                    continue

        self._assign_affiliation_ids(sd)
        self._assign_orcids(orcids, sd)
        # 前置区里出现的声明类小节(Funding/Conflict/Author Contributions 等)
        # 收进 back(独立扫描,不干扰上面的作者/ORCID/单位抽取)
        self._collect_front_declarations(paras, sd)

    def _collect_front_declarations(self, paras, sd):
        """扫描前置区,把声明类小节(带冒号标签 + 内联/后续内容)收进 sd.back_sections。

        bounded:label 行开启一节,内容=该行冒号后文本 + 后续段,直到下一个声明 label
        或前置区结束;标准化标题去重(已存在的不重复加)。"""
        from ..model.structured import Section
        existing = {(s.title or "").lower() for s in sd.back_sections}
        i, n = 0, len(paras)
        while i < n:
            t = paras[i].text.strip()
            m = P.DECLARATION_INLINE.match(t)
            if not m:
                i += 1
                continue
            title = m.group(1).strip()           # 保留 docx 原标题文字
            sec = Section(title=title)
            sec.label_len = _decl_label_len(paras[i].text)  # 首块剥离 "标签:" 前缀
            blocks = [paras[i]]                   # 含标签段
            j = i + 1
            # 收后续段,直到下一个声明 label,或声明区终止边界(Capsule/作者信息标签)。
            # 边界行不收入本节,outer 循环因其不匹配 DECLARATION_INLINE 会自然跳过它,
            # 故 Capsule / Author information 既不成节也不被吞并。
            while j < n:
                tj = paras[j].text.strip()
                if P.DECLARATION_INLINE.match(tj) or _is_decl_boundary(tj):
                    break
                blocks.append(paras[j])
                j += 1
            if title.lower() not in existing:
                sec.blocks = blocks
                sd.back_sections.append(sec)
                existing.add(title.lower())
            i = j

    def _parse_editor(self, text, sd):
        # 剥离 "Academic Editor(s):"/"学编:" 前缀(含复数 s,避免残留 "s:")
        names = re.sub(r"^.*?(学编|academic\s+editors?|handling\s+editors?|editors?)\s*[:：]\s*",
                       "", text, flags=re.I).strip(" :：,")
        if not names:
            return
        from ..model.structured import Editor
        # 多位编辑:按 " and "/"&"/";"/"," 拆分(编辑名为"名 姓",逗号即分隔人)
        for part in re.split(r"\s+and\s+|\s*[;&]\s*|\s*,\s*", names, flags=re.I):
            part = part.strip()
            if len(part.split()) >= 2:  # 至少"名 姓"
                surname, given = FM._flip_name(part)
                if surname:
                    sd.editors.append(Editor(surname=surname, given_names=given))

    def _assign_date(self, text, d, sd):
        low = text.lower()
        if "receiv" in low or "submit" in low:
            sd.dates.received = d
        elif "revis" in low:
            sd.dates.revised = d
        elif "accept" in low:
            sd.dates.accepted = d
        else:
            # 无标签的裸日期，按 received→revised→accepted 顺序填充
            if sd.dates.received is None:
                sd.dates.received = d
            elif sd.dates.revised is None:
                sd.dates.revised = d
            elif sd.dates.accepted is None:
                sd.dates.accepted = d

    @staticmethod
    def _match_author_by_name(sd, text):
        """文本里点到了哪位作者:姓(复合姓拆成各部件)须全部按整词命中(去重音);名首词也命中→
        强候选(区分同姓,如 Carmen Rubio vs Moisés Rubio-Osornio);弱候选偏好姓部件更多的。"""
        from ..agent.authors_fix import _deaccent
        toks = set(re.findall(r"[a-z]+", _deaccent(text).lower()))
        weak = None
        for a in sd.authors:
            parts = [p for p in re.split(r"[^a-z]+", _deaccent(a.surname or "").lower()) if p]
            if not parts or not all(p in toks for p in parts):
                continue
            giv = _deaccent(a.given_names or "").lower().split()
            if giv and giv[0] in toks:
                return a                                  # 姓各部件 + 名首词都命中 → 确定
            if weak is None or len(parts) > weak[1]:
                weak = (a, len(parts))
        return weak[0] if weak else None

    def _collect_email(self, text, sd, in_corresp, last_person):
        """把行内邮箱关联到作者并分类。返回本行归属的作者(更新 last_person)。

        - 归属:本行若点名某作者用之,否则用最近点名的作者(通讯地址常"姓名行 + 换行 + 邮箱行")。
        - 分类:处于通讯区、或该作者作者行带 * 标记 → 通讯邮箱(进 corresp_email_map + 标记通讯);
          否则 → 该作者的普通 <email>(如 01 的 Yinze Ji,非通讯、邮箱在"Addresses & ORCID"块)。
        """
        who = self._match_author_by_name(sd, text) or last_person
        for em in P.EMAIL.findall(text):
            if in_corresp or (who is not None and who.is_corresponding):
                nm = ("%s %s" % (who.given_names or "", who.surname or "")).strip() if who else None
                sd.corresp_email_map.append((em, nm or None))
                if who is not None:
                    who.is_corresponding = True
            elif who is not None and not who.email:
                who.email = em
        return who

    def _assign_affiliation_ids(self, sd):
        # 1) 按文本去重（docx 常重复出现机构行）
        seen, uniq = set(), []
        for a in sd.affiliations:
            key = a.text[:50].lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(a)
        # 2) 分配唯一 id：有显式数字标签则用之，否则按顺序补号
        used, nxt = set(), 1
        for a in uniq:
            if a.label and a.label.isdigit() and ("aff" + a.label) not in used:
                n = a.label
            else:
                while str(nxt) in (x.label for x in uniq if x.label.isdigit()) or \
                        ("aff" + str(nxt)) in used:
                    nxt += 1
                n = str(nxt)
                a.label = n
            a.aff_id = "aff" + n
            used.add(a.aff_id)
        sd.affiliations = uniq

    def _assign_orcids(self, orcids, sd):
        """把 ORCID 归属到作者。优先用"姓名: 号"块的确定性匹配(去重音+整词+used 护栏,
        复用 agent.authors_fix.orcid_block_map,能区分同姓与复合姓,如 Rubio vs Rubio-Osornio);
        块里没点名的裸 ORCID(如 01 的 "ORCID: xxxx")按文中出现顺序做位置兜底。"""
        if not orcids or not sd.authors:
            return
        from ..agent.authors_fix import orcid_block_map
        front_text = "\n".join(line for _, line in orcids)
        block = orcid_block_map(front_text, sd.authors)   # {作者下标: orcid16}
        for idx, oc in block.items():
            sd.authors[idx].orcid = oc
            sd.authors[idx].orcid_authenticated = True
        # 未被"姓名: 号"块匹配到的 ORCID → 按作者顺序位置兜底(仅填尚无 ORCID 的作者)
        assigned = set(block.values())
        leftover = [oc for oc, _ in orcids if oc not in assigned]
        oi = 0
        for a in sd.authors:
            if a.orcid or oi >= len(leftover):
                continue
            a.orcid = leftover[oi]
            a.orcid_authenticated = False
            oi += 1


    def _parse_abstract(self, blocks, sd):
        paras = [b for b in blocks if isinstance(b, Paragraph)]
        content = [p for p in paras if not P.ABSTRACT_LABEL.match(p.text.strip())]
        full = " ".join(p.text.strip() for p in content if p.text.strip()).strip()
        full = P.ABSTRACT_PREFIX.sub("", full, count=1)  # 去掉行内 "Abstract:" 前缀
        if not full:
            return
        # 结构化摘要：按"行内子标题"（Background:/Methods:/...）切分；
        # 兼容"全在一段"与"逐段一标题"两种排版。
        matches = list(P.ABSTRACT_SUBHEAD_INLINE.finditer(full))
        sections = []
        if matches:
            if matches[0].start() > 0:
                lead = full[:matches[0].start()].strip()
                if lead:
                    s = AbstractSection(title=None, paragraphs=[])
                    s.lead = lead
                    sections.append(s)
            for mi, m in enumerate(matches):
                start = m.end()
                end = matches[mi + 1].start() if mi + 1 < len(matches) else len(full)
                body = full[start:end].strip(" :;")
                s = AbstractSection(title=m.group(0).strip(), paragraphs=[])
                s.lead = body
                sections.append(s)
        else:
            s = AbstractSection(title=None, paragraphs=[])
            s.lead = full
            sections.append(s)
        sd.abstract = sections

    def _parse_keywords(self, blocks, idx, sd) -> int:
        """解析关键词，返回最后消费的块索引（内容可能在标签下一段）。"""
        t = blocks[idx].text.strip()
        content = P.KEYWORDS_LABEL.sub("", t).strip(" :;.")
        consumed = idx
        if not content and idx + 1 < len(blocks) and \
                isinstance(blocks[idx + 1], Paragraph):
            nxt = blocks[idx + 1].text.strip()
            if nxt and not P.REFERENCES_HEAD.match(nxt):
                content = nxt
                consumed = idx + 1
        # 优先按分号切分；仅当无分号时才用逗号，避免拆散含逗号的关键词
        sep = ";" if ";" in content else ","
        parts = content.split(sep)
        sd.keywords = [w.strip(" .") for w in parts if w.strip(" .")]
        return consumed

    def _split_back(self, blocks):
        """从正文块中分出后置声明类小节（Funding/Conflicts/...）。

        后置声明区在 docx 里有两种排版:
          1) 独立标题行(无冒号):"Author Contributions" 单独成段,内容在下一段;
          2) 行内标签(带冒号):"Author Contributions: …内容…" 标签与内容同段(MDPI 风格)。
        旧实现只认第 1 种(且 rstrip 冒号后按短行判定),第 2 种因标签行很长而漏判,
        导致整个声明区(含 Funding/Conflicts/Ethics 等)被当成正文最后一节的内容,
        没进 <back>(实测样例4、样例5)。这里两种都识别。"""
        back_start = None
        for i, b in enumerate(blocks):
            if not isinstance(b, Paragraph):
                continue
            raw = b.text.strip()
            t = raw.rstrip(":").strip()
            is_head = len(t) <= 90 and (
                P.DECLARATION_HEADING.match(t)
                or P.ABBREV_HEADING.match(raw)
                or any(t.lower() == x.lower() for x in P.BACK_SECTION_TITLES))
            # 行内标签:用"强标签子集"判定起点(排除 abbreviations 等表脚注易混词)
            is_inline = bool(P.DECLARATION_START_INLINE.match(raw))
            if is_head or is_inline:
                back_start = i
                break
        if back_start is None:
            return blocks, []
        return blocks[:back_start], blocks[back_start:]

    def _parse_back(self, blocks, sd):
        """把后置块按声明小节标题归组(同时支持独立标题行与行内标签两种排版)。"""
        from ..model.structured import Section
        existing = {(s.title or "").lower() for s in sd.back_sections}

        def get_or_new(title):
            """按标题取已有小节(去重,避免前置区已收过的重复),否则新建。"""
            key = title.lower()
            for s in sd.back_sections:
                if (s.title or "").lower() == key:
                    return s, False
            sec = Section(title=title)
            sd.back_sections.append(sec)
            existing.add(key)
            if "acknowledg" in key:
                sd.acknowledgment = ""
            return sec, True

        cur = None
        for b in blocks:
            if not isinstance(b, Paragraph):
                if cur is not None:
                    cur.blocks.append(b)
                continue
            raw = b.text.strip()
            t = raw.rstrip(":").strip()
            # 0) 声明区终止边界(Capsule / 作者信息标签)→ 不成节,并终止前一节的内容吞并。
            # 与前置区 _collect_front_declarations 同一边界规则,保证后置声明区在 body 排版
            # 下也不会把其后的 Capsule / Author information 误并入末节。
            if _is_decl_boundary(raw):
                cur = None
                continue
            # 1) 行内标签 "Author Contributions: 内容" → 新小节,标题=标签,首块剥前缀
            m_inline = P.DECLARATION_INLINE.match(raw)
            if m_inline:
                title = m_inline.group(1).strip()
                cur, fresh = get_or_new(title)
                if fresh:
                    cur.label_len = _decl_label_len(b.text)
                cur.blocks.append(b)
                continue
            # 2) 独立标题行(无冒号 / 固定词表 / 缩写表标题)→ 新小节,内容在后续段
            is_head = len(t) <= 90 and (
                any(t.lower() == x.lower() for x in P.BACK_SECTION_TITLES)
                or P.DECLARATION_HEADING.match(t)
                or P.ABBREV_HEADING.match(raw))
            if is_head:
                cur, _ = get_or_new(t)
                continue
            # 3) 普通内容段 → 并入当前小节(首个标题之前的散块丢弃,与旧行为一致)
            if cur is not None:
                cur.blocks.append(b)
