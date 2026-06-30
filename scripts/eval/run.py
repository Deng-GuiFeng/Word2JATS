#!/usr/bin/env python
"""Layer-1 评测运行器:对每个样例的输出,逐维度对照权威标签,产出 JSON + HTML。

参考目标(永远是外部权威标签,绝不用本系统旧输出):
  · 原始样例 1-5:委员会金标准 XML(样例N/第一组/最终*.xml)。
  · 补充样例 S1-S5:独立三方核验的伪标签 JSON。

用法:
  python -m eval.run                         # 用现有输出(reports/eval_runs/v2 · v2-supp)对照标签
  python -m eval.run --orig-root DIR --supp-root DIR   # 指定新输出目录(方法迭代后重测)
  python -m eval.run --samples 1,3,S2
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from eval.compare import compare  # noqa: E402
from eval.profile import (load_profile_from_xml_path, profile_from_pseudo)  # noqa: E402

BASE = os.path.join(ROOT, "样例")
PL_DIR = os.path.join(BASE, "补充案例-仅输入", "伪标签")

# 原始样例:金标准 XML 路径
GOLD = {
    "1": "样例1/第一组/最终上线.xml",
    "2": "样例2/第一组/最终上线.xml",
    "3": "样例3/第一组/最终文件.xml",
    "4": "样例4/第一组/最终文件.xml",
    "5": "样例5/第一组/最终文件.xml",
}
ORIG_KEYS = ["1", "2", "3", "4", "5"]
SUPP_KEYS = ["S1", "S2", "S3", "S4", "S5"]


def _latest_xml(d: str):
    xs = sorted(glob.glob(os.path.join(d, "*.xml")))
    return xs[0] if xs else None


def default_orig_out(root, key):
    return _latest_xml(os.path.join(root, "out_%s" % key))


def default_supp_out(root, key):
    return _latest_xml(os.path.join(root, "out_%s" % key))


def load_pseudo(key):
    n = key.lstrip("S")
    p = os.path.join(PL_DIR, "样例%s.json" % n)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def eval_one(key, out_xml, ref_profile, ref_kind):
    out_p = load_profile_from_xml_path(out_xml)
    res = compare(out_p, ref_profile)
    return {"key": key, "out_xml": os.path.relpath(out_xml, ROOT),
            "ref_kind": ref_kind, "result": res}


def run(orig_root, supp_root, keys):
    results = []
    for key in keys:
        if key in ORIG_KEYS:
            out_xml = default_orig_out(orig_root, key)
            if not out_xml:
                print("[warn] 样例%s 无输出 XML(%s)" % (key, orig_root))
                continue
            gold = os.path.join(BASE, GOLD[key])
            ref_p = load_profile_from_xml_path(gold)
            results.append(eval_one(key, out_xml, ref_p, "委员会金标准 XML"))
        else:
            out_xml = default_supp_out(supp_root, key)
            if not out_xml:
                print("[warn] %s 无输出 XML(%s)" % (key, supp_root))
                continue
            ref_p = profile_from_pseudo(load_pseudo(key))
            results.append(eval_one(key, out_xml, ref_p, "独立核验伪标签 JSON"))
    return results


# --------------------------- HTML --------------------------- #
def _esc(s):
    return html.escape(str(s))


DIM_LABELS = [
    ("title", "标题"), ("sections", "章节树/层级"), ("authors", "作者"),
    ("affiliations_count", "单位数"), ("affiliations_text", "单位文本"),
    ("abstract_structured", "摘要结构化"), ("abstract_subheads", "摘要子标题"),
    ("keywords", "关键词"), ("figures_count", "图数"), ("tables_count", "表数"),
    ("references_count", "文献数"), ("references_sample", "文献抽样"),
    ("back_sections", "back小节"), ("editors_count", "编辑数"), ("disp_formula", "块公式"),
]


def _score_cell(d):
    if not isinstance(d, dict):
        return "<td>—</td>"
    if d.get("available") is False:
        return "<td class=na>N/A</td>"
    s = d.get("score")
    if s is None:
        return "<td>—</td>"
    cls = "g" if s >= 0.999 else ("y" if s >= 0.8 else "r")
    return "<td class=%s>%.2f</td>" % (cls, s)


def build_html(results, out_html):
    css = """
    body{font-family:-apple-system,Segoe UI,'Noto Sans CJK SC',sans-serif;margin:24px;color:#1c1c1c;line-height:1.5;max-width:1200px}
    h1{border-bottom:3px solid #2c6;padding-bottom:6px}
    h2{margin-top:34px;border-left:5px solid #2c6;padding-left:10px}
    h3{margin-top:20px;color:#333}
    table{border-collapse:collapse;margin:8px 0;font-size:13px}
    td,th{border:1px solid #ccc;padding:4px 8px;text-align:center}
    th{background:#f4f4f4}
    td.g{background:#d8f5dd;font-weight:600}td.y{background:#fff3cd}td.r{background:#ffd6d6;font-weight:700}
    td.na{color:#999}
    .miss{color:#c22}.extra{color:#b8860b}
    .muted{color:#777;font-size:12px}
    code{background:#f0f0f0;padding:1px 5px;border-radius:4px;font-size:12px}
    .box{background:#fafafa;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0}
    """
    P = ["<!doctype html><meta charset=utf-8><title>Layer-1 可定义指标评测</title><style>%s</style>" % css]
    P.append("<h1>word2jats · Layer-1 可定义指标评测(对照权威标签)</h1>")
    P.append("<p class=muted>参考目标:原始样例=委员会金标准 XML;补充样例=独立核验伪标签。"
             "逐维度对照,描述性差异 + 0~1 分值,不简化为单一 pass/fail。</p>")

    # 总览
    P.append("<h2>总览(逐样例 × 逐维度分值)</h2>")
    P.append("<table><tr><th>样例</th><th>综合</th>")
    for _, lab in DIM_LABELS:
        P.append("<th>%s</th>" % lab)
    P.append("</tr>")
    for r in results:
        res = r["result"]
        ov = res.get("overall_score")
        cls = "g" if (ov or 0) >= 0.999 else ("y" if (ov or 0) >= 0.8 else "r")
        P.append("<tr><td><b>%s</b></td><td class=%s>%.3f</td>" % (r["key"], cls, ov or 0))
        for k, _ in DIM_LABELS:
            P.append(_score_cell(res.get(k, {})))
        P.append("</tr>")
    P.append("</table>")
    macro = [r["result"].get("overall_score") for r in results if r["result"].get("overall_score") is not None]
    if macro:
        P.append("<p class=muted>宏平均综合分:<b>%.3f</b>(%d 样例)</p>" % (sum(macro) / len(macro), len(macro)))

    # 逐样例详情
    for r in results:
        res = r["result"]
        P.append("<h2>样例 %s</h2>" % r["key"])
        P.append("<p class=muted>参考:%s · 输出:<code>%s</code> · 综合 %.3f</p>"
                 % (_esc(r["ref_kind"]), _esc(r["out_xml"]), res.get("overall_score") or 0))
        # 章节树
        sec = res.get("sections", {})
        if sec.get("available"):
            P.append("<h3>章节树/层级(分 %.3f)</h3><div class=box>" % sec["score"])
            P.append("标题F1=%.2f 层级准确=%.2f 编号保留=%s 顶级节数 出%d/参%d<br>"
                     % (sec["heading_f1"], sec["depth_accuracy"],
                        ("%.2f" % sec["numbering_retention"]) if sec["numbering_retention"] is not None else "—",
                        sec["top_level_out"], sec["top_level_ref"]))
            if sec["missing_headings"]:
                P.append("<span class=miss>缺标题:%s</span><br>" % _esc("; ".join(sec["missing_headings"])))
            if sec["extra_headings"]:
                P.append("<span class=extra>多标题:%s</span><br>" % _esc("; ".join(sec["extra_headings"])))
            for dm in sec["depth_mismatches"]:
                P.append("层级错:<code>%s</code> 参考深度%d→输出深度%d<br>"
                         % (_esc(dm["title"]), dm["ref_depth"], dm["out_depth"]))
            P.append("</div>")
        # 作者
        au = res.get("authors", {})
        P.append("<h3>作者(分 %.3f)</h3><div class=box>" % au["score"])
        P.append("姓名F1=%.2f 顺序=%s 数 出%d/参%d 字段分:%s<br>"
                 % (au["name_f1"], "✓" if au["order_ok"] else "✗", au["n_out"], au["n_ref"],
                    _esc(au["field_scores"])))
        if au.get("field_diffs"):
            P.append("<span class=miss>字段差异:%s</span>" % _esc(json.dumps(au["field_diffs"], ensure_ascii=False)))
        P.append("</div>")
        # 其它有差异的集合维度
        for k, lab in [("keywords", "关键词"), ("back_sections", "back小节"),
                       ("abstract_subheads", "摘要子标题"), ("affiliations_text", "单位文本")]:
            d = res.get(k, {})
            if isinstance(d, dict) and d.get("score", 1) < 0.999 and (d.get("missing") or d.get("extra")):
                P.append("<h3>%s(分 %.3f)</h3><div class=box>" % (lab, d["score"]))
                if d.get("missing"):
                    P.append("<span class=miss>缺:%s</span><br>" % _esc("; ".join(map(str, d["missing"]))))
                if d.get("extra"):
                    P.append("<span class=extra>多:%s</span>" % _esc("; ".join(map(str, d["extra"]))))
                P.append("</div>")

    with open(out_html, "w", encoding="utf-8") as f:
        f.write("\n".join(P))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig-root", default=os.path.join(ROOT, "reports", "eval_runs", "v2"))
    ap.add_argument("--supp-root", default=os.path.join(ROOT, "reports", "eval_runs", "v2-supp"))
    ap.add_argument("--samples", default=None, help="如 1,3,S2;默认全部 10")
    ap.add_argument("--report", default=os.path.join(ROOT, "reports", "eval_l1_v2"))
    args = ap.parse_args()

    keys = ORIG_KEYS + SUPP_KEYS
    if args.samples:
        want = set(args.samples.split(","))
        keys = [k for k in keys if k in want]

    results = run(args.orig_root, args.supp_root, keys)
    os.makedirs(args.report, exist_ok=True)
    with open(os.path.join(args.report, "eval_l1.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    build_html(results, os.path.join(args.report, "eval_l1.html"))

    # 控制台摘要
    print("\n==== Layer-1 评测摘要 ====")
    for r in results:
        res = r["result"]
        print("样例 %-3s 综合=%.3f | 章节=%s 作者=%.2f 关键词=%.2f 表=%.2f back=%.2f 文献=%.2f"
              % (r["key"], res.get("overall_score") or 0,
                 ("%.2f" % res["sections"]["score"]) if res["sections"].get("available") else "N/A",
                 res["authors"]["score"], res["keywords"]["score"],
                 res["tables_count"]["score"], res["back_sections"]["score"],
                 res["references_count"]["score"]))
    macro = [r["result"].get("overall_score") for r in results if r["result"].get("overall_score") is not None]
    if macro:
        print("宏平均综合分:%.3f(%d 样例)" % (sum(macro) / len(macro), len(macro)))
    print("报告:%s" % os.path.join(args.report, "eval_l1.html"))


if __name__ == "__main__":
    main()
