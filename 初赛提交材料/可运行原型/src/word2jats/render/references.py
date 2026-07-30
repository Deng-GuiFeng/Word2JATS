"""渲染 ``<ref-list>``：消费 SemanticDoc.references（语义字段已由 LLM 切分）。

纯机械：字段齐全→element-citation，否则→mixed-citation（整段原文兜底）。
ref 的 id 取自显示编号（label 里的数字），以正确处理跳号（正文 xref 按显示号拼 rid）。
"""

from __future__ import annotations

import re

from ..build.jats import E, sub


def build_ref_list(refs: list):
    """构建 ref-list。返回 (元素, 显示号→id 映射)。"""
    rl = E("ref-list")
    sub(rl, "title", "References")
    num_to_id = {}
    used = set()
    for i, ref in enumerate(refs, 1):
        m = re.search(r"\d+", ref.label or "")
        num = int(m.group()) if m else i
        rid = "b%d" % num
        while rid in used:                 # 保证 id 全局唯一（无标签条目与标签号可能相撞）
            rid = "b%d_%d" % (num, i)
            i2 = i
            while rid in used:
                i2 += 1
                rid = "b%d_%d" % (num, i2)
        used.add(rid)
        if m:
            num_to_id[num] = rid
        r = sub(rl, "ref", id=rid)
        if ref.label:
            sub(r, "label", ref.label)
        if ref.structured and ref.source:
            _element_citation(r, ref)
        else:
            _mixed_citation(r, ref)
    return rl, num_to_id


def _mixed_citation(r, ref):
    mc = sub(r, "mixed-citation")
    mc.text = ref.raw_text


def _element_citation(r, ref):
    ec = sub(r, "element-citation", **{"publication-type": ref.pub_type})
    if ref.authors or ref.collab:
        pg = sub(ec, "person-group", **{"person-group-type": "author"})
        for c in ref.collab:                       # 机构/团体作者在个人名之前
            sub(pg, "collab", c)
        for surname, given in ref.authors:
            nm = sub(pg, "name")
            sub(nm, "surname", surname)
            if given:
                sub(nm, "given-names", given)
        if ref.etal:
            sub(pg, "etal", "et al.")   # 带文字：保留 docx 的 "et al"（空 <etal/> 会漏词 L1）
    if ref.editors:                                # 书籍编者：独立 person-group（金标准同款）
        eg = sub(ec, "person-group", **{"person-group-type": "editor"})
        for surname, given in ref.editors:
            nm = sub(eg, "name")
            sub(nm, "surname", surname)
            if given:
                sub(nm, "given-names", given)
    if ref.article_title:
        sub(ec, "article-title", ref.article_title)
    if ref.source:
        sub(ec, "source", ref.source)
    if ref.edition:
        sub(ec, "edition", ref.edition)
    if ref.publisher_name:
        sub(ec, "publisher-name", ref.publisher_name)
    if ref.publisher_loc:
        sub(ec, "publisher-loc", ref.publisher_loc)
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
    if ref.comment:
        sub(ec, "comment", ref.comment)
    if ref.doi:
        doi = ref.doi.strip()
        url = doi if doi.startswith("http") else "https://doi.org/" + doi
        sub(ec, "ext-link", " " + url, **{"ext-link-type": "uri", "xlink_href": url})
