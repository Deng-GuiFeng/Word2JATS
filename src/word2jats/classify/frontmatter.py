"""前置元数据（front matter）识别。

依据调研：作者行常为单段、用 run 级上标标记单位/通讯/共同贡献；样式名不可靠，
故以**内容 + run 格式**为主进行启发式判定。本模块不追求复现金标准的人工编辑，
只忠实抽取 docx 中真实存在的信息。
"""

from __future__ import annotations

import re
from typing import Optional

from ..model.blocks import Paragraph, TextRun
from ..model.structured import (Affiliation, Author, AbstractSection, DateInfo,
                                Editor)
from . import patterns as P

# 学位后缀（作者行里需剔除）
_DEGREES = {
    "md", "phd", "msc", "ms", "bsc", "bs", "ma", "mph", "dphil", "mbbs", "mb",
    "bch", "facc", "facp", "frcp", "dsc", "do", "rn", "pharmd", "dvm", "md.",
    "ph.d", "m.d", "m.s", "b.s", "m.a", "b.a", "drph", "scd", "edd",
}
_MARKER_CHARS = "0123456789*†#§¶‡∗¹²³⁰⁴⁵⁶⁷⁸⁹"
_SUP_UNICODE = {"¹": "1", "²": "2", "³": "3", "⁰": "0",
                "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
                "⁸": "8", "⁹": "9"}


def _is_degree(tok: str) -> bool:
    """判断一个逗号分隔片段是否为学位后缀（如 M.D./Ph.D./MSc）。

    注意：只匹配显式学位集合，或**含句点的点分缩写**（如 "M.D."）。
    绝不能仅凭"短纯字母串"判定——否则 Lee/Wang/Kim/Liu/Yang 等常见短姓会被
    误当学位而从作者名中丢弃（已由对抗审查证实为高危 bug）。
    """
    t = tok.strip().lower()
    t2 = t.replace(".", "")
    if t2 in {d.replace(".", "") for d in _DEGREES}:
        return True
    # 点分缩写：必须含句点，如 "m.d." / "ph.d." / "b.sc."
    return bool(re.fullmatch(r"(?:[a-z]+\.){1,4}", t)) and len(t2) <= 6


def _merge_segments(para: Paragraph):
    """把段落 runs 合并为 (text, is_sup) 段序列。"""
    segs = []
    for r in para.runs:
        if isinstance(r, TextRun):
            is_sup = r.superscript
            if segs and segs[-1][1] == is_sup:
                segs[-1][0] += r.text
            else:
                segs.append([r.text, is_sup])
    return [(t, s) for t, s in segs]


def _parse_markers(text: str) -> list:
    """把上标串解析为 marker 列表。

    关键:数字与符号要拆开——"1†"→['1','†']、"1*"→['1','*']、"1,2"→['1','2'],
    否则数字+符号连写时会同时丢失单位编号和通讯/共同贡献标记(实测样例2 的 bug)。
    """
    text = "".join(_SUP_UNICODE.get(c, c) for c in text)
    # 连续数字算一个 marker;每个非数字、非分隔符号各算一个
    return [m.group() for m in re.finditer(r"\d+|[^\d\s,;]", text) if m.group().strip()]


def _clean_name(buf: str) -> str:
    """从缓冲串中取出作者姓名（剔除前导逗号 / 学位）。"""
    parts = [p.strip() for p in buf.split(",")]
    keep = [p for p in parts if p and not _is_degree(p)]
    # 姓名通常是最后一个非学位片段
    return keep[-1].strip() if keep else ""


def _flip_name(name: str):
    """'Given Middle Surname' → (surname, given_names)。末词作姓（贴合 IMR 约定）。"""
    name = name.strip().strip(",")
    # 剥离首尾的标点/分隔符(如作者间 ". " 分隔残留的前导点、和、& 等)
    name = re.sub(r"^[\s.,;&]+|[\s.,;&]+$", "", name)
    name = re.sub(r"^(and|&)\s+", "", name, flags=re.I)
    # 剥离尾部 marker 字符（unicode 上标 / 紧贴的数字符号）
    name = re.sub(r"[%s]+$" % re.escape(_MARKER_CHARS), "", name).strip()
    toks = name.split()
    if not toks:
        return "", ""
    if len(toks) == 1:
        return toks[0], ""
    return toks[-1], " ".join(toks[:-1])


_TRAILING_MARK = re.compile(r"^\s*([*†#‡§∗¶]+)")


def _apply_trailing_markers(author, marks: str):
    """把紧跟在某作者(上标编号)之后、出现在普通文本里的 */†/# 标记归属给该作者。"""
    if any(c in marks for c in "*∗"):
        author.is_corresponding = True
    if any(c in marks for c in "†#‡§¶"):
        author.equal_contrib = True


def parse_author_line(para: Paragraph) -> list:
    """解析作者段，返回 Author 列表。"""
    segs = _merge_segments(para)
    authors = []
    buf = ""
    for text, is_sup in segs:
        if is_sup:
            markers = _parse_markers(text)
            name = _clean_name(buf)
            if name:
                authors.append(_make_author(name, markers))
            buf = ""
        else:
            # 刚结束一个作者时,本段开头的 */† 等(非上标)标记属于**上一个**作者
            # (常见排版:"Kang^{1,2}*  Next Author" —— * 不在上标里,挂到了下一段开头)
            if not buf and authors:
                mlead = _TRAILING_MARK.match(text)
                if mlead:
                    _apply_trailing_markers(authors[-1], mlead.group(1))
                    text = text[mlead.end():]
            buf += text
    # 处理无上标的尾随作者（或整行无上标的情况）
    if buf.strip():
        for chunk in _split_plain_authors(buf):
            a = _make_author_plain(chunk)
            if a:
                authors.append(a)
    return [a for a in authors if a and (a.surname or a.given_names)]


def _split_plain_authors(buf: str) -> list:
    """无上标段：按逗号切分，剔除学位，返回姓名块。

    作者行单位号是**纯文本**时(如样例4 "Ricardo Pérez-Rubio1,2"),逗号既分作者又分单位号,
    会把 "…1,2" 切成 "…1"+"2"。这里把"只含数字/标记的延续碎片"(如 "2"/"3*")并回上一位作者,
    避免多单位丢失、也避免把 "2" 误当成一位空作者。
    """
    raw = [p.strip() for p in re.split(r"[,，]", buf)]
    parts: list = []
    for p in raw:
        if not p or _is_degree(p):
            continue
        if parts and re.fullmatch(r"[%s\s]+" % re.escape(_MARKER_CHARS), p):
            parts[-1] = parts[-1] + "," + p
        else:
            parts.append(p)
    return parts


def _make_author(name: str, markers: list) -> Author:
    surname, given = _flip_name(name)
    aff_labels = [m for m in markers if m.isdigit()]
    corr = any(m in ("*", "∗") for m in markers)
    equal = any(m in ("†", "#", "‡", "§") for m in markers)
    return Author(surname=surname, given_names=given, aff_labels=aff_labels,
                  is_corresponding=corr, equal_contrib=equal, raw=name)


def _make_author_plain(chunk: str) -> Optional[Author]:
    # 去掉尾部句点等标点，再剥离尾随 unicode 上标 / 数字 marker(含逗号分隔的多单位号 "1,2")
    chunk = chunk.strip().rstrip(".")
    mc = re.escape(_MARKER_CHARS)
    m = re.search(r"([%s]+(?:[,\s]+[%s]+)*)$" % (mc, mc), chunk)
    markers = _parse_markers(m.group(1)) if m else []
    name = chunk[: m.start()] if m else chunk
    surname, given = _flip_name(name)
    if not (surname or given):
        return None
    aff_labels = [x for x in markers if x.isdigit()]
    corr = any(x in ("*", "∗") for x in markers)
    equal = any(x in ("†", "#", "‡", "§") for x in markers)
    return Author(surname=surname, given_names=given, aff_labels=aff_labels,
                  is_corresponding=corr, equal_contrib=equal, raw=name)


def parse_affiliation(para: Paragraph) -> Optional[Affiliation]:
    """机构段：前导数字为 label，其余为机构文本。"""
    text = para.text.strip()
    if not text:
        return None
    # 前导上标数字（来自 run）或普通前导数字
    m = re.match(r"^\s*(\d{1,2})\s*[\.\)]?\s*(.+)$", text)
    if m:
        label, body = m.group(1), m.group(2).strip()
    else:
        label, body = "", text
    return Affiliation(aff_id="aff" + (label or "1"), label=label, text=body)


def _parse_date(text: str):
    """返回 (year, month, day);兼容 YYYY/M/D 与 D/M/YYYY、空格与不间断空格分隔。"""
    text = text.replace("\xa0", " ")
    # 数字斜杠/连字符日期(允许分隔符两侧空格):判断哪个是 4 位年
    m = re.search(r"(\d{1,4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,4})", text)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if len(a) == 4:            # YYYY/M/D
            return (a, str(int(b)), str(int(c)))
        if len(c) == 4:            # D/M/YYYY(IMR/国际常用)
            return (c, str(int(b)), str(int(a)))
    m = P.DATE_DMY.search(text)
    if m:
        return (m.group(3), str(P.MONTHS[m.group(2).lower()]), m.group(1))
    m = P.DATE_MDY.search(text)
    if m:
        return (m.group(3), str(P.MONTHS[m.group(1).lower()]), m.group(2))
    return None


_DATE_SLOTS = [("submit", "received"), ("receiv", "received"),
               ("revis", "revised"), ("accept", "accepted")]


def parse_labeled_dates(text: str) -> dict:
    """从含 Submitted/Revised/Accepted 标签的文本(可能一段多行)逐行抽日期。"""
    out = {}
    for line in re.split(r"[\n;]+", text.replace("\xa0", " ")):
        low = line.lower()
        for key, slot in _DATE_SLOTS:
            if key in low:
                d = _parse_date(line)
                if d:
                    out[slot] = d
                break
    return out
