"""结构一致性与提取质量检查(DTD 之外的"检查"层)。

DTD 只管格式合不合法,管不了"提取得对不对、全不全"。本层找的是**会泛化的**质量问题:
正文为空、有参考文献区却没解析出条目、引用指向不存在的目标、作者没名字……
这是被动的质量诊断(供出口报告与前端展示),不驱动任何重跑或修复循环。

只报"能泛化、能辨识的一类问题",不针对单个样例。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree


@dataclass
class Issue:
    code: str        # 机器可读的问题类别
    severity: str    # high / medium / low
    detail: str

    def __repr__(self):
        return "[%s/%s] %s" % (self.severity, self.code, self.detail)


def _root(xml_bytes: bytes):
    text = re.sub(r"<!DOCTYPE.*?>", "", xml_bytes.decode("utf-8"), flags=re.S)
    return etree.fromstring(text.encode("utf-8"))


def run_checks(xml_bytes: bytes) -> list:
    """返回 Issue 列表(空=未发现问题)。纯结构推理,不联网、不调用模型。"""
    root = _root(xml_bytes)
    issues = []

    def find_all(tag):
        return root.findall(".//" + tag)

    # 1) 悬空引用:xref/@rid 指向不存在的 id
    ids = set()
    for el in root.iter():
        if el.get("id"):
            ids.add(el.get("id"))
    for x in find_all("xref"):
        rid = x.get("rid")
        if rid:
            for one in rid.split():
                if one not in ids:
                    issues.append(Issue("dangling_xref", "high",
                                        "xref 指向不存在的 id: %s" % one))

    # 1b) 重复 id(会导致 DTD/IDREF 失效)
    id_seen = {}
    for el in root.iter():
        i = el.get("id")
        if i:
            id_seen[i] = id_seen.get(i, 0) + 1
    for i, c in id_seen.items():
        if c > 1:
            issues.append(Issue("duplicate_id", "high", "id 重复 %d 次: %s" % (c, i)))

    # 2) 正文为空
    body = root.find(".//body")
    if body is None or not body.findall(".//p"):
        issues.append(Issue("empty_body", "high", "正文没有任何段落"))

    # 3) 标题/作者缺失（标题可能含 <bold>/<italic> 等内联子元素，须取全文而非直接文本）
    at = root.find(".//article-title")
    if at is None or not "".join(at.itertext()).strip():
        issues.append(Issue("no_title", "high", "缺文章标题"))
    if not find_all("contrib"):
        issues.append(Issue("no_authors", "medium", "没有解析出作者"))

    # 4) 有"参考文献"区却没条目;或反之
    ref_list = root.find(".//ref-list")
    if ref_list is not None and not ref_list.findall(".//ref"):
        issues.append(Issue("empty_ref_list", "high", "有 ref-list 但没有任何 ref"))
    # 4b) 反之:整篇没有任何 <ref>,但正文仍残留未解析的方括号数字引用(如 [1]、[12]),
    #     说明参考文献整类丢失且交叉引用退化为纯文本——这是最严重的静默丢失,必须报出。
    if not find_all("ref"):
        body_text = " ".join(p for p in (
            (el.text or "") for el in (body.iter() if body is not None else [])))
        if re.search(r"\[\d{1,3}(?:[-,–]\s*\d{1,3})*\]", body_text):
            issues.append(Issue("references_missing", "high",
                                "正文含未解析的数字引用([n])但全篇无任何 <ref>,疑似参考文献整类丢失"))

    # 5) 空表格行 / 表无内容(DTD 多能抓,这里给可读信息)
    for tr in find_all("tr"):
        if not (tr.findall("td") or tr.findall("th")):
            issues.append(Issue("empty_tr", "high", "存在没有单元格的表格行"))
            break

    # 6) 图/表缺关键子元素
    for fig in find_all("fig"):
        if fig.find("graphic") is None:
            issues.append(Issue("fig_no_graphic", "medium",
                                "fig %s 没有 graphic" % (fig.get("id") or "?")))
    # table-wrap 的内容可以是 <table> 网格,也可以是 <graphic>/<media> 图片表(源稿里
    # 直接以图片形式给出的表格,JATS 合法且常见——本赛题样例 02 的 8 张表金标准即全是图片表)。
    # 三者皆无才是真"空表",只认 <table> 会把图片表误报成没内容。
    for tw in find_all("table-wrap"):
        if (tw.find(".//table") is None and tw.find(".//graphic") is None
                and tw.find(".//media") is None):
            issues.append(Issue("table_no_content", "medium",
                                "table-wrap %s 既无 table 网格也无 graphic 图片" % (tw.get("id") or "?")))

    # 7) 作者无姓名
    for c in find_all("contrib"):
        if c.find(".//surname") is None and c.find(".//string-name") is None:
            issues.append(Issue("contrib_no_name", "medium", "存在没有姓名的 contrib"))
            break

    return issues


def summarize(issues: list) -> dict:
    out = {}
    for it in issues:
        out[it.severity] = out.get(it.severity, 0) + 1
    return out
