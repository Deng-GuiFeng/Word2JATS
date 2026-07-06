"""构建 JATS ``<ref-list>``。

依据调研：mixed-citation（整段包裹）安全、容错，是金标准的主力形式；解析置信度高
的条目可升级为 element-citation。当前默认 mixed-citation，结构化字段齐全时用 element。
"""

from __future__ import annotations

import re

from ..model.structured import Reference
from .jats import E, sub


def build_ref_list(refs: list):
    """构建 ref-list。返回 (元素, label号→id映射)。

    **关键**:ref 的 id 取自其显示编号(label),而非简单顺序号——否则当文献编号
    有跳号(如缺 [16]/[31])时,正文按显示号拼出的 xref rid 会指向错误条目。
    """
    rl = E("ref-list")
    sub(rl, "title", "References")
    num_to_id = {}
    used = set()
    for i, ref in enumerate(refs, 1):
        # 优先用 label 里的数字作 id;无则顺序号;冲突则退化为顺序号保证唯一
        m = re.search(r"\d+", ref.label or "")
        num = int(m.group()) if m else i
        rid = "b%d" % num
        if rid in used:
            rid = "b%d" % i
        used.add(rid)
        if m:
            num_to_id[num] = rid
        r = sub(rl, "ref", id=rid)
        if ref.label:
            sub(r, "label", ref.label)
        if ref.structured and ref.article_title and ref.source:
            _element_citation(r, ref)
        else:
            _mixed_citation(r, ref)
    return rl, num_to_id


def _mixed_citation(r, ref: Reference):
    mc = sub(r, "mixed-citation")
    mc.text = ref.raw_text


def _element_citation(r, ref: Reference):
    ec = sub(r, "element-citation", **{"publication-type": ref.pub_type})
    if ref.authors or ref.collab:
        pg = sub(ec, "person-group", **{"person-group-type": "author"})
        for c in ref.collab:                       # 机构/团体作者在个人名之前(与金标准同序)
            sub(pg, "collab", c)
        for surname, given in ref.authors:
            nm = sub(pg, "name")
            sub(nm, "surname", surname)
            if given:
                sub(nm, "given-names", given)
        if ref.etal:
            sub(pg, "etal")
    if ref.article_title:
        sub(ec, "article-title", ref.article_title)
    if ref.source:
        sub(ec, "source", ref.source)
    if ref.year:
        sub(ec, "year", ref.year, **{"iso-8601-date": ref.year})
    if ref.volume:
        sub(ec, "volume", ref.volume)
    if ref.issue:
        sub(ec, "issue", ref.issue)
    if ref.fpage:
        sub(ec, "fpage", ref.fpage)
    if ref.lpage:
        sub(ec, "lpage", ref.lpage)
    if ref.doi:
        # 与 IMR 金标准一致:DOI 用 ext-link 标成可点链接
        doi = ref.doi.strip()
        url = doi if doi.startswith("http") else "https://doi.org/" + doi
        sub(ec, "ext-link", " " + url, **{"ext-link-type": "uri", "xlink_href": url})
