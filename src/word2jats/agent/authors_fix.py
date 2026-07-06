"""Agent 闭环——作者元数据(归属)修正:按字段选用最可靠的方法(混合策略)。

为什么需要它(第一性原理):
  作者与单位/ORCID/邮箱/通讯标记的**对应关系**,在 docx 里分散于作者行上标、单位行、
  "Addresses & ORCID"块、"Address of corresponding authors"块等多处,热启动启发式会错配
  (实测:多单位作者只关联到第 1 个单位;某 ORCID 挂到错误同姓作者名下)。

**明确分工(不同字段用不同方法,各取最可靠):**
- **单位 affs**:交给 LLM 据源前置区做多单位关联(语义判断,LLM 擅长)。
- **ORCID**:**不**用 LLM,改用**确定性解析**(orcid_block_map:按"姓名: 16 位号"全名+去重音+
  按词精确匹配)——因为多个同姓作者时 LLM 会张冠李戴(实测三个 Wang),确定性解析更稳。
- **通讯/共同贡献/邮箱**:**不**在本模块改,沿用热启动对作者行 `*`/`†` 标记的确定性检测
  与据 "Correspondence:" 块构建的邮箱映射(同上,LLM 在同姓下不可靠)。

在**姓名集合不变**(不增删作者)前提下,把 LLM 的单位 + 确定性 ORCID 回填到 sd.authors,
再重建 <front>。重建后即时 DTD 校验,失败回滚。仅据源已有信息,不凭空新增。
"""
from __future__ import annotations

import re

from ..model.blocks import Paragraph
from .structure import _norm


def front_region_text(doc, max_paras: int = 70) -> str:
    """源 docx 前置区文本(到正文第一个 Heading 1 为止):含作者/单位/ORCID/邮箱/通讯。"""
    lines = []
    for b in doc.blocks:
        if not isinstance(b, Paragraph):
            continue
        sname = (b.style_name or "").lower()
        t = b.text.strip()
        # 到正文第一个 Heading 1(如 Introduction)即停
        if "heading 1" in sname or (sname == "heading" and t.lower() in (
                "introduction", "background")):
            break
        if t:
            tag = "{B}" if b.is_bold else ""
            lines.append(tag + t)
        if len(lines) >= max_paras:
            break
    return "\n".join(lines)


def current_authors(article) -> list:
    out = []
    for c in article.findall('.//contrib[@contrib-type="author"]'):
        affs = []
        for x in c.findall('xref[@ref-type="aff"]'):
            affs.extend((x.get("rid") or "").replace("aff", "").split())
        cid = c.find('.//contrib-id[@contrib-id-type="orcid"]')
        out.append({
            "surname": (c.findtext(".//surname") or "").strip(),
            "given": (c.findtext(".//given-names") or "").strip(),
            "affs": affs,
            "orcid": (cid.text or "").strip() if cid is not None else "",
            "corresponding": bool(c.findall('xref[@ref-type="corresp"]')),
        })
    return out


_SYS = "你是严谨的学术元数据核对员,只依据给定源文本判断作者归属,只输出 JSON。"


def _prompt(front_text, cur) -> str:
    cur_lines = []
    for a in cur:
        cur_lines.append("%s %s | aff=%s orcid=%s corr=%s" % (
            a["given"], a["surname"], ",".join(a["affs"]) or "-",
            a["orcid"] or "-", a["corresponding"]))
    return (
        "下面是一篇论文的【源 docx 前置区文本】和【当前程序抽取的作者归属】。请只依据源文本,"
        "给出每位作者**正确的**归属。\n\n"
        "【源前置区文本】\n" + front_text[:6000] + "\n\n"
        "【当前抽取(可能有归属错误)】\n" + "\n".join(cur_lines) + "\n\n"
        "任务:逐位作者核对并输出修正后的归属。判定规则(全部据源文本):\n"
        "1. 单位编号 affs:作者姓名后的上标数字(可能多个,如 1,2,3);源里有几个就给几个。\n"
        "2. orcid:从源的 ORCID 块里**按姓名**匹配到该作者的 16 位 ORCID(形如 0000-0000-0000-0000);"
        "源里没给该作者 ORCID 就留空。务必按姓名对应,**不要张冠李戴**。\n"
        "3. email:该作者的邮箱(源里按姓名给出);没有就留空。\n"
        "4. corresponding:该作者是否为通讯作者(源里带 * 通讯标记,或在 'corresponding author' 块中列出)。\n"
        "5. equal:该作者是否标注共同贡献/共同第一作者(源里带 #/†/‡ 标记或 'contributed equally' 声明)。\n"
        "6. 作者的姓名与人数**必须与当前抽取完全一致**(只修归属,不增删作者、不改姓名拼写)。\n"
        '严格输出 JSON:{"authors":[{"surname":"姓","given":"名","affs":["1","2"],'
        '"orcid":"0000-...","email":"x@y","corresponding":true/false,"equal":true/false}]}'
    )


def propose_authors(llm, front_text, cur):
    if llm is None or not getattr(llm, "enabled", False):
        return None
    data = llm.extract_json(_SYS, _prompt(front_text, cur), max_tokens=2048)
    items = data.get("authors") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None
    return items


def _orcid16(s):
    s = s or ""
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dxX])", s)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?<!\d)(\d{15}[\dxX])(?!\d)", s)   # 无连字符的 16 位形式
    if m:
        d = m.group(1)
        return ("%s-%s-%s-%s" % (d[0:4], d[4:8], d[8:12], d[12:16])).upper()
    return ""


def _deaccent(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def orcid_block_map(front_text, authors) -> dict:
    """从源前置区的 'Name: ORCID' 行**确定性**提取每位作者的 ORCID(全名 + 按词精确匹配)。

    ORCID 块是结构化的"姓名 + 16 位号"数据,确定性解析比让 LLM 猜更可靠(实测:三个 Wang
    同姓时 LLM 张冠李戴)。两道防误配:① 去重音(Pérez↔Perez);② 姓按**完整词**匹配
    (复合姓 'perez-rubio' 作整词,使短姓 'rubio' 不会误中 'perez-rubio' 行)。
    返回 {作者下标: orcid16};未在块中出现的作者不入表(其 ORCID 应被清除)。
    """
    assigned, used = {}, set()
    for line in front_text.splitlines():
        oc = _orcid16(line)
        if not oc:
            continue
        # 连字符感知的词块:既把复合姓 "perez-rubio" 作整词(区分于 "rubio"),
        # 又能从带括号/标点的 "(Xiaoze Li)" 里干净取出 "xiaoze"/"li"。
        tokens = set(re.findall(r"[a-z]+(?:-[a-z]+)*", _deaccent(line).lower()))
        cand_full, cand_sur = None, None
        for i, a in enumerate(authors):
            if i in used:
                continue
            sur = _deaccent(a.surname or "").lower()
            giv = _deaccent((a.given_names or "").strip()).lower().split()
            if not sur or sur not in tokens:        # 姓须作为完整词出现
                continue
            if giv and giv[0] in tokens:            # 名首词也命中 → 最优(区分同姓)
                cand_full = i
                break
            if cand_sur is None:
                cand_sur = i
        pick = cand_full if cand_full is not None else cand_sur
        if pick is not None:
            assigned[pick] = oc
            used.add(pick)
    return assigned


def fix_authors(llm, article, sd, doc, rebuild_front, validator=None) -> dict:
    """作者归属修正主入口。姓名集合不变前提下回填 sd.authors + 通讯邮箱,重建 <front>。"""
    rec = {"applied": False, "reason": ""}
    cur = current_authors(article)
    if not cur:
        rec["reason"] = "无作者"
        return rec
    front_text = front_region_text(doc)
    if not front_text:
        rec["reason"] = "源前置区为空"
        return rec
    proposed = propose_authors(llm, front_text, cur)
    if not proposed:
        rec["reason"] = "LLM 未给出有效提议"
        return rec

    # 安全:提议作者姓名集合必须与当前完全一致(不增删、不改姓)
    cur_sur = sorted(_norm(a["surname"]) for a in cur)
    prop_sur = sorted(_norm(str(p.get("surname", ""))) for p in proposed)
    if cur_sur != prop_sur:
        rec["reason"] = "提议作者集合与当前不一致,放弃(防误改): cur=%s prop=%s" % (cur_sur, prop_sur)
        return rec

    prop_by = {_norm(str(p.get("surname", ""))): p for p in proposed}
    changes = []
    for a in sd.authors:
        p = prop_by.get(_norm(a.surname))
        if not p:
            continue
        # 仅修单位 affs(LLM 擅长:多单位关联,实测样例4 正确补回 1,2/1,3/1,4)。
        # **不**用 LLM 覆盖 corresponding/equal/email:作者行的 */† 标记与邮箱是确定性可解析的,
        # 热启动 parse_author_line 已正确检测(实测样例2 准确区分 Bin=*通讯、Shuang/Aili=†共同贡献);
        # LLM 在多个同姓作者下会误判(曾把三个 Wang 全标通讯+同一邮箱),故这几项信任热启动。
        new_affs = [str(x) for x in (p.get("affs") or []) if str(x).strip()]
        if new_affs and new_affs != a.aff_labels:
            changes.append("%s aff %s→%s" % (a.surname, a.aff_labels, new_affs))
            a.aff_labels = new_affs

    # ORCID 确定性覆盖:源前置区有 ORCID 块时,按全名解析为准(纠正张冠李戴)。
    # 谨慎规则(避免误清正确值):
    #   · 块里给某作者明确 ORCID 且与当前不同 → 改为源值;
    #   · 作者不在块里、但其当前 ORCID 恰好属于**另一位**作者(块里有主)→ 系张冠李戴,清除;
    #   · 作者不在块里、当前 ORCID 也不归属他人 → 保留(可能是匹配遗漏,值未必错,不动)。
    if re.search(r"orcid", front_text, re.I):
        omap = orcid_block_map(front_text, sd.authors)        # {idx: orcid}
        owner = {oc: i for i, oc in omap.items()}             # 源里每个 ORCID 的正主
        for i, a in enumerate(sd.authors):
            cur = a.orcid or ""
            if i in omap:
                if omap[i] != cur:
                    changes.append("%s orcid(确定) %s→%s" % (a.surname, cur or "-", omap[i]))
                    a.orcid = omap[i]
            elif cur and owner.get(cur) is not None and owner[cur] != i:
                changes.append("%s orcid(张冠李戴清除) %s" % (a.surname, cur))
                a.orcid = None
                a.orcid_authenticated = False

    if not changes:
        rec["reason"] = "归属无需调整"
        return rec

    # 注:通讯邮箱块(sd.corresp_email_map)由热启动据源 "Correspondence:" 块构建,信任之、
    # 不在此重建(邮箱存于该映射而非 Author.email);corresponding/equal 用热启动的 */† 检测。
    old_front = article.find("front")
    new_front = rebuild_front()
    if old_front is None or new_front is None:
        rec["reason"] = "无法重建 front"
        return rec
    article.replace(old_front, new_front)

    if validator is not None:
        from ..build.jats import serialize
        res = validator.validate_bytes(serialize(article))
        if not (res and res.ok):
            article.replace(new_front, old_front)
            rec["reason"] = "重建 front 后 DTD 不通过,回滚"
            return rec

    rec["applied"] = True
    rec["changes"] = changes
    return rec
