"""参考文献切分与结构化。

方法（内容守恒）：把 docx 里已有的每条引用**只做切分、不改写**——各字段都是原文的字面
切片（surnames / article-title / source / year / volume / issue / page / doi）。据引文语法里
**风格无关的强锚点**定位边界：作者列表形态（"姓 缩写"重复串）、`年;卷(期):页` 尾部、DOI。
切分高置信 → element-citation；任一必备字段缺失或边界不可靠 → 保留原文走 mixed-citation
（与旧行为一致，零风险兜底）。风格分派只分两类通用版式——句点式（Vancouver/NLM）与逗号式
（Elsevier），不硬编码任何按刊先验。
"""

from __future__ import annotations

import re
from typing import Optional

from ..model.blocks import Paragraph
from ..model.structured import Reference
from . import patterns as P


# "姓 缩写,"作者列表起始样式(姓可含连字符/重音;后接 1–4 个大写缩写 + 逗号)。
# 用于在编号列表中辨认"缺了编号前缀、却确为新条目"的参考文献(见 is_continuation)。
_REF_START = re.compile(r"^[A-Z][A-Za-z'’À-ſ\-]+\s+[A-Z]{1,4},")


def split_reference_blocks(blocks: list) -> list:
    """把参考文献区的段落切成一条条引用（label, raw_text）。

    两种排版都要支持:
    (1) 有编号前缀(`[n]` 或 `n.`):按编号切条,无编号的段视为上一条的续行(悬挂缩进);
    (2) 无编号:每个段落即一条参考文献(IMR 这批新样例如此)。
    先扫一遍判断是否带编号,再据此切分——避免把无编号列表误并成一条。
    """
    paras = [b.text.strip() for b in blocks
             if isinstance(b, Paragraph) and b.text.strip()]
    if not paras:
        return []

    refs = []          # list[[label, text]]
    seen_label = False  # 是否已遇到第一个带编号的条目

    def is_continuation(t: str) -> bool:
        """t 是否为上一条参考文献的续行(折行),而非新条目。

        规则(兼顾两种排版):
        - 带编号([n]/n.) → 一定是新条目。
        - 以小写字母开头 → 续行(承接上句)。
        - **已进入带编号列表后**(seen_label)出现的无编号段 → 视为上一条的尾部续行
          (如折行的"期刊名. 年;卷:页. doi");此前(首个编号之前)的无编号段则各自成条
          (应对编号从中途开始、前几条丢了编号的情形)。
        """
        if not refs:
            return False
        if P.REF_LABEL_BRACKET.match(t) or P.REF_LABEL_DOT.match(t):
            return False
        if t.lstrip("([")[:1].islower():
            return True
        # 即便已处于编号列表中,若本段以"姓 缩写,"的作者列表样式开头,它是**新条目**——
        # docx 自动编号有时未落入 run 文本,导致某条参考文献缺失 "[n]" 前缀;此时不能把它
        # 误并为上一条的续行(实测样例2:无编号的第 69 条 Freitas-Ferraz 被并入第 68 条并污染页码)。
        # 续行通常是"期刊名. 年;卷:页. doi"片段,不会匹配此作者列表样式。
        if _REF_START.match(t):
            return False
        return seen_label

    for t in paras:
        mb = P.REF_LABEL_BRACKET.match(t)
        md = P.REF_LABEL_DOT.match(t)
        if is_continuation(t):
            refs[-1][1] = (refs[-1][1] + " " + t).strip()
            continue
        if mb:
            label, body = mb.group(1), t[mb.end():].strip()
            seen_label = True
        elif md:
            label, body = md.group(1), t[md.end() - 1:].strip()
            seen_label = True
        else:
            label, body = str(len(refs) + 1), t
        refs.append([label, body])
    return [(lbl, txt) for lbl, txt in refs if txt]


# 作者姓名单元:结尾是 1–4 个大写首字母缩写(可带点/连字符,如 H / ETE / A.B.),
# 其前为姓(可含小写前缀 van/de、连字符、重音)。
_INITIALS = re.compile(r"^[A-Z](?:[.\- ]?[A-Z]){0,3}\.?$")
# 句点式著录尾部:年 [月 日] [;,] 卷 [(期)] [: 起页[-止页]]。用 search(可出现在串中任意位置,
# 兼容 source 与年份之间是 ". "/", "/" " 各种分隔),卷后页码整段可选(容 online-first/无页码文献)。
_TAIL_SEARCH = re.compile(
    r"(?P<year>(?:19|20)\d{2})"
    r"(?:\s+[A-Za-z]{3,9}\.?(?:\s+\d{1,2})?)?"          # 可选 月[ 日]
    r"\s*[;,]?\s*"
    r"(?P<vol>\d+)"
    r"\s*(?:\((?P<issue>[^)]+)\))?"                      # 可选 (期)
    r"(?:\s*[:,]\s*(?P<fp>[A-Za-z]?\d+(?:\.[a-z]\d+)?)"  # 可选 : 起页(容 172.e1 式)
    r"(?:\s*[–-]\s*(?P<lp>[A-Za-z]?\d+(?:\.[a-z]\d+)?))?)?"
    r"(?=[\s.,;]|$)")
# 逗号式(Elsevier)尾部:… , 卷 (年) 起页-止页 .
_TAIL_ELS = re.compile(
    r",\s*(?P<vol>\d+)\s*\((?P<year>(?:19|20)\d{2})\)\s*"
    r"(?P<fp>[A-Za-z]?\d+)\s*[–-]\s*(?P<lp>[A-Za-z]?\d+)\.?\s*$")
_ETAL = re.compile(r"\bet\s+al\b", re.I)


def _split_name(unit: str):
    """把 "姓 缩写" 单元拆成 (surname, initials);不匹配则返回 None(视作机构作者)。

    从尾部连续吃掉"单个大写字母/缩写块"作为首字母缩写——兼顾 "Niba ETE"、"Von Bardeleben RS"、
    以及把缩写用空格分开写的 "Van Keuren A M"、"Hao S J"(否则会把前面的 A/S 误并进姓)。
    """
    toks = unit.split()
    j = len(toks)
    while j > 1 and _INITIALS.match(toks[j - 1]):
        j -= 1
    if 1 <= j < len(toks):
        surname = " ".join(toks[:j])
        given = "".join(t.replace(".", "") for t in toks[j:])
        if surname:
            return surname, given
    return None


def _parse_author_chunk(chunk: str):
    """解析作者段:逗号/分号分隔,"姓 缩写"→作者,其余→机构作者(collab),识别 et al。

    分号切分是为了剥离挂在末位作者后的团体名(如 "Maisano F; EXPAND Investigators"),
    否则整段会被判成机构作者、丢掉真作者。
    """
    etal = bool(_ETAL.search(chunk))
    chunk = re.sub(r",?\s*et\s+al\.?\s*$", "", chunk, flags=re.I)
    authors, collab = [], []
    for u in re.split(r"[,;]", chunk):
        u = u.strip()
        if not u:
            continue
        nm = _split_name(u)
        (authors if nm else collab).append(nm or u)
    return authors, collab, etal


def _segment_vanc(s: str, doi_start: int = None):
    """句点式(Vancouver/NLM):Authors. Title. Source<sep>Year[;]Vol[(Issue)][:pages]。

    搜索式:在(DOI 之前的)串里找**最靠右**的著录尾部,尾部之前按句读切成
    作者 / 标题… / 刊名(刊名=尾部前最后一句)。source 与年份间无论是 ". "/", "/" " 都能分开。
    """
    body = s[:doi_start] if doi_start is not None else s
    tail_m = None
    for m in _TAIL_SEARCH.finditer(body):
        tail_m = m                                   # 取最后一处
    if tail_m is None:
        return None
    head = body[:tail_m.start()].rstrip(" ,.;:")
    # 按句末标点切分(保留 ? ! 在前块),首块=作者,末块=刊名,中间=标题
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", head) if p.strip()]
    if len(parts) < 3:
        return None
    authors, collab, etal = _parse_author_chunk(parts[0])
    source = parts[-1].strip(" .,;:")
    title = " ".join(parts[1:-1]).strip(" .,;:")     # 各中间块保留其末标点(? 不丢)
    if not (authors or collab) or not title or not source:
        return None
    g = tail_m.groupdict()
    return dict(authors=authors, collab=collab, etal=etal, title=title, source=source,
                year=g["year"], vol=g["vol"], issue=g.get("issue"),
                fp=g.get("fp"), lp=g.get("lp"))


def _segment_els(s: str):
    """逗号式(Elsevier):Authors, Title, Source, Vol (Year) pages."""
    m = _TAIL_ELS.search(s)
    if not m:
        return None
    units = [u.strip() for u in s[:m.start()].rstrip(" ,.").split(",") if u.strip()]
    authors, collab, etal = [], [], False
    i = 0
    while i < len(units):
        if re.fullmatch(r"et\s+al\.?", units[i], re.I):
            etal = True
            i += 1
            continue
        nm = _split_name(units[i])
        if not nm:
            break
        authors.append(nm)
        i += 1
    rest = units[i:]
    if not authors or len(rest) < 2:
        return None
    source = rest[-1].strip(" .,;:")
    title = ", ".join(rest[:-1]).strip(" .,;:")
    if not title or not source:
        return None
    return dict(authors=authors, collab=collab, etal=etal, title=title, source=source,
                year=m.group("year"), vol=m.group("vol"), issue=None,
                fp=m.group("fp"), lp=m.group("lp"))


def _parse_one(label: str, text: str) -> Reference:
    ref = Reference(label="[%s]" % label if label else "", raw_text=text)
    s = " ".join(text.split())            # 仅用于解析的空白归一;raw_text 保留原文
    s = re.sub(r"(\.)([A-Z])", r"\1 \2", s)  # 句点后缺空格(如 "study.JACC")补空格,便于切分
    md = P.DOI_IN_TEXT.search(s)
    if md:
        ref.doi = md.group(0).rstrip(" .")
    seg = _segment_vanc(s, md.start() if md else None) or _segment_els(s)
    if seg:
        ref.authors = seg["authors"]
        ref.collab = seg["collab"]
        ref.etal = seg["etal"]
        ref.article_title = seg["title"]
        ref.source = seg["source"]
        ref.year = seg["year"]
        ref.volume = seg["vol"]
        ref.issue = seg["issue"]
        ref.fpage = seg["fp"]
        ref.lpage = seg["lp"]
        # 必备字段齐全才升级 element-citation;否则保留原文 mixed(零风险)
        if (ref.authors or ref.collab) and ref.article_title and ref.source and ref.year:
            ref.structured = True
    return ref


def parse_references(blocks: list) -> list:
    raw = split_reference_blocks(blocks)
    return [_parse_one(lbl, txt) for lbl, txt in raw if txt]
