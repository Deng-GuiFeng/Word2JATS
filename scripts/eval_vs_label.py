#!/usr/bin/env python
"""把 word2jats 的输出 XML 与"伪标签"逐字段比对,产出差距报告(优化目标)。

用法: python scripts/eval_vs_label.py <label.json> <output.xml>
"""
import argparse
import json
import re
import sys
from lxml import etree


def root_of(xml_path):
    t = re.sub(r"<!DOCTYPE.*?>", "", open(xml_path, encoding="utf-8").read(), flags=re.S)
    return etree.fromstring(t.encode())


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def get_authors(root):
    out = []
    for c in root.findall('.//contrib[@contrib-type="author"]'):
        out.append({
            "surname": c.findtext(".//surname") or "",
            "given": c.findtext(".//given-names") or "",
            "aff": sorted(x.get("rid", "").replace("aff", "") for x in c.findall('xref[@ref-type="aff"]')),
            "corresponding": c.find('xref[@ref-type="corresp"]') is not None,
            "orcid": (c.findtext(".//contrib-id") or "").split("/")[-1],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("xml")
    args = ap.parse_args()
    lab = json.load(open(args.label, encoding="utf-8"))
    root = root_of(args.xml)
    diffs = []

    def rec(field, exp, got, ok=None):
        ok = (norm(str(exp)) == norm(str(got))) if ok is None else ok
        if not ok:
            diffs.append({"field": field, "expected": exp, "got": got})

    # 标题
    rec("title", lab["title"], root.findtext(".//article-title"))
    # 作者数 + 逐位
    la, ga = lab["authors"], get_authors(root)
    rec("authors.count", len(la), len(ga))
    for i, a in enumerate(la):
        g = ga[i] if i < len(ga) else {}
        rec("authors[%d].name" % i, "%s %s" % (a["given"], a["surname"]),
            "%s %s" % (g.get("given", ""), g.get("surname", "")))
        rec("authors[%d].aff" % i, a["aff_labels"], g.get("aff", []),
            ok=sorted(a["aff_labels"]) == sorted(g.get("aff", [])))
        rec("authors[%d].corresponding" % i, a["corresponding"], g.get("corresponding"),
            ok=bool(a["corresponding"]) == bool(g.get("corresponding")))
        rec("authors[%d].orcid" % i, a["orcid"], g.get("orcid", ""),
            ok=(not a["orcid"]) or norm(a["orcid"]) in norm(g.get("orcid", "")))
    # 单位数
    rec("affiliations.count", len(lab["affiliations"]), len(root.findall(".//aff")))
    # 编辑数
    rec("editors.count", len(lab["editors"]), len(root.findall('.//contrib[@contrib-type="editor"]')))
    # 日期
    hist = {d.get("date-type"): "%s-%s-%s" % (d.findtext("year"), d.findtext("month"), d.findtext("day"))
            for d in root.findall(".//history/date")}
    # 关键词数
    rec("keywords.count", len(lab["keywords"]), len(root.findall(".//kwd")))
    # 摘要结构
    rec("abstract.structured", lab["abstract_structured"],
        len(root.findall(".//abstract//sec")) > 0,
        ok=bool(lab["abstract_structured"]) == (len(root.findall(".//abstract//sec")) > 0))
    # 图数 / 表数
    rec("figures.count", len(lab["figures"]), len(root.findall(".//fig")))
    rec("tables.count", len(lab["tables"]), len(root.findall(".//table-wrap")))
    # 表头行数 + 脚注(逐表)
    tws = root.findall(".//table-wrap")
    for t in lab["tables"]:
        idx = t["number"] - 1
        if 0 <= idx < len(tws):
            tw = tws[idx]
            rec("tables[%d].header_rows" % t["number"], t["n_header_rows"],
                len(tw.findall(".//thead/tr")))
            rec("tables[%d].has_footnote" % t["number"], t["has_footnote"],
                tw.find(".//table-wrap-foot") is not None,
                ok=bool(t["has_footnote"]) == (tw.find(".//table-wrap-foot") is not None))
    # 参考文献数
    rec("reference_count", lab["reference_count"], len(root.findall(".//ref")))
    # back 小节
    got_back = set(norm(s.findtext("title")) for s in root.findall(".//back//sec")) | \
        set(norm(a.findtext("title")) for a in root.findall(".//back//ack"))
    for bt in lab["back_section_titles"]:
        rec("back.has(%s)" % bt[:20], bt, sorted(got_back),
            ok=any(norm(bt) in g or g in norm(bt) for g in got_back if g))

    print(json.dumps({"label": args.label, "n_diffs": len(diffs), "diffs": diffs},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
