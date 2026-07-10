"""报告聚合(设计 §7):机器可读 JSON + 人类可读文本。

顶层**不出加权总分**;只出"剩余真缺陷条数"(按样例、按组)+ 逐类命中/漏标/多标。
两组(main=01-05 / supp=S01-05)分列(§3.7):main 的对照物由本队据 docx + 上线版本构建,
supp 无上线版本;两组测的都是"对 结构参考 的 A+B 保真",可同口径看,但来源不同,分列呈现。
"""
import json


def sample_report(sample, l0, l1, l2):
    return {
        "key": sample.key,
        "group": sample.group,
        "L0_validity": {
            "ok": l0["ok"], "dtd_ok": l0["dtd_ok"], "doctype_ok": l0["doctype_ok"],
            "n_error": sum(1 for v in l0["violations"] if v["severity"] == "error"),
            "n_warning": sum(1 for v in l0["violations"] if v["severity"] == "warning"),
            "n_info": sum(1 for v in l0["violations"] if v["severity"] == "info"),
            "violations": l0["violations"],
        },
        "L1_fidelity": {
            "defect_n": l1["defect_n"],
            "n_lost": len(l1["lost"]), "n_fabricated": len(l1["fabricated"]),
            "n_img_bad": l1["n_img_bad"],
            "lost": l1["lost"], "fabricated": l1["fabricated"],
            "altered_pairs": l1["altered_pairs"], "images": l1["images"],
            "image_count": l1["image_count"],
            "b_additions": l1["b_additions"],
        },
        "L2_structure": {
            "defect_n": l2["defect_n"],
            "by_category": [
                {"cat": c["cat"], "hit": c["hit"], "missing": c["missing"],
                 "extra": c["extra"], "n_defects": len(c["defects"]), "defects": c["defects"]}
                for c in l2["by_category"]
            ],
        },
        # B 档补全覆盖率(§6.3 步5):可联网补全项单列,不计入结构缺陷分
        "B_network_coverage": l2.get("b_coverage", {}),
        "defect_total": (0 if l0["ok"] else l0_error_n(l0)) + l1["defect_n"] + l2["defect_n"],
    }


def l0_error_n(l0):
    return sum(1 for v in l0["violations"] if v["severity"] == "error")


def _fmt_defects(defs, limit=12):
    lines = []
    for d in defs[:limit]:
        lines.append("        · [%s] %s: %s" % (d.get("kind", ""), d.get("key", ""), d.get("detail", "")))
    if len(defs) > limit:
        lines.append("        … 另 %d 条" % (len(defs) - limit))
    return lines


def text_report(reports):
    """人类可读汇总(两组分列)。reports: [sample_report...]"""
    out = []
    out.append("=" * 78)
    out.append("word2jats 评测报告 —— 缺陷清单(无加权总分;0 缺陷=满分)")
    out.append("对照物 = 冻结的 结构参考.xml(A+B、无 C)。设计:docs/06-评测与成绩.md")
    for grp, name in (("main", "第一组 01-05(委员会主样例,有上线版本)"),
                      ("supp", "补充组 S01-05(仅 docx,held-out)")):
        rs = [r for r in reports if r["group"] == grp]
        if not rs:
            continue
        out.append("\n" + "#" * 78)
        out.append("# %s" % name)
        out.append("#" * 78)
        for r in rs:
            l0, l1, l2 = r["L0_validity"], r["L1_fidelity"], r["L2_structure"]
            out.append("\n【%s】 剩余真缺陷合计=%d   (L0违规 err=%d/warn=%d/info=%d  L1忠实=%d  L2对位=%d)" % (
                r["key"], r["defect_total"], l0["n_error"], l0["n_warning"], l0.get("n_info", 0),
                l1["defect_n"], l2["defect_n"]))
            out.append("  L0 合法: DTD=%s DOCTYPE=%s%s" % (
                l0["dtd_ok"], l0["doctype_ok"],
                "" if not l0["violations"] else "  违规:" + "; ".join(
                    "%s(%s)" % (v["rule"], v["severity"]) for v in l0["violations"][:8])))
            if l1["defect_n"]:
                ic = l1.get("image_count", {})
                cnt_note = "" if ic.get("match", True) else "(图数 输出%s≠参考%s)" % (ic.get("out"), ic.get("ref"))
                out.append("  L1 忠实: 丢失%d类 编造%d类 图问题%d%s" % (
                    l1["n_lost"], l1["n_fabricated"], l1["n_img_bad"], cnt_note))
                if l1["lost"]:
                    out.append("    丢失Top: " + ", ".join("%s×%d" % (x["token"], x["n"]) for x in l1["lost"][:10]))
                if l1["fabricated"]:
                    out.append("    编造Top: " + ", ".join("%s×%d" % (x["token"], x["n"]) for x in l1["fabricated"][:10]))
            else:
                out.append("  L1 忠实: ✓ 无丢失/编造/图问题")
            out.append("  L2 对位(命中/漏标/多标):")
            for c in l2["by_category"]:
                flag = "" if (c["missing"] == 0 and c["extra"] == 0 and c["n_defects"] == 0) else "  ⚠"
                out.append("    %-8s 命中%3d 漏标%3d 多标%3d 缺陷%3d%s" % (
                    c["cat"], c["hit"], c["missing"], c["extra"], c["n_defects"], flag))
                if c["n_defects"]:
                    out.extend(_fmt_defects(c["defects"]))
            bc = r.get("B_network_coverage") or {}
            if bc:
                out.append("  B档补全覆盖率(可联网补全项,单列不计结构分):  " + "  ".join(
                    "%s 参考%d/输出%d" % (k, v["ref"], v["out"]) for k, v in bc.items()))
    # 系统性根因:跨样例按 (类别,kind) 聚合缺陷频次
    out.append("\n" + "=" * 78)
    out.append("系统性根因(跨样例缺陷聚合,按出现样例数排序)")
    agg = {}
    for r in reports:
        seen = set()
        for c in r["L2_structure"]["by_category"]:
            for d in c["defects"]:
                k = (d["cat"], d["kind"])
                seen.add(k)
        for k in seen:
            agg[k] = agg.get(k, 0) + 1
    for (cat, kind), n in sorted(agg.items(), key=lambda kv: -kv[1]):
        out.append("  %2d 样例  %s / %s" % (n, cat, kind))
    return "\n".join(out)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def html_report(reports):
    """人类可读 HTML(设计 §7/§8.2):逐样例逐类缺陷,颜色标注;顶层无总分。"""
    h = ["<!doctype html><meta charset='utf-8'><title>word2jats 评测报告</title>",
         "<style>body{font:14px/1.5 system-ui,sans-serif;margin:24px;color:#222}"
         "h1{font-size:20px}h2{border-bottom:2px solid #ccc;padding-bottom:4px}"
         "table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #ddd;padding:2px 8px;text-align:right}"
         ".ok{color:#127a1f}.bad{color:#b00}.warn{color:#b8860b}.muted{color:#888}"
         ".def{font-family:ui-monospace,monospace;font-size:12px;color:#555;margin-left:16px}</style>"]
    h.append("<h1>word2jats 评测报告 —— 缺陷清单(无加权总分;0 缺陷=满分)</h1>")
    h.append("<p class=muted>对照物 = 冻结的 结构参考.xml(A+B、无 C)。设计:docs/06-评测与成绩.md</p>")
    for grp, name in (("main", "第一组 01-05(有上线版本)"), ("supp", "补充组 S01-05(仅 docx)")):
        rs = [r for r in reports if r["group"] == grp]
        if not rs:
            continue
        h.append("<h2>%s</h2>" % _esc(name))
        for r in rs:
            l0, l1, l2 = r["L0_validity"], r["L1_fidelity"], r["L2_structure"]
            cls = "ok" if r["defect_total"] == 0 else "bad"
            h.append("<h3>%s <span class='%s'>剩余真缺陷=%d</span> "
                     "<span class=muted>(L0 err=%d/warn=%d/info=%d · L1=%d · L2=%d)</span></h3>" % (
                         _esc(r["key"]), cls, r["defect_total"], l0["n_error"], l0["n_warning"],
                         l0.get("n_info", 0), l1["defect_n"], l2["defect_n"]))
            h.append("<table><tr><th>类别</th><th>命中</th><th>漏标</th><th>多标</th><th>缺陷</th></tr>")
            for c in l2["by_category"]:
                bad = c["missing"] or c["extra"] or c["n_defects"]
                h.append("<tr class='%s'><td style='text-align:left'>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td></tr>" % (
                    "bad" if bad else "", _esc(c["cat"]), c["hit"], c["missing"], c["extra"], c["n_defects"]))
            h.append("</table>")
            for c in l2["by_category"]:
                for d in c["defects"][:20]:
                    h.append("<div class=def>[%s/%s] %s: %s</div>" % (
                        _esc(c["cat"]), _esc(d.get("kind", "")), _esc(d.get("key", "")), _esc(d.get("detail", ""))))
            bc = r.get("B_network_coverage") or {}
            if bc:
                h.append("<div class=muted>B档补全覆盖率:" + " · ".join(
                    "%s 参考%d/输出%d" % (_esc(k), v["ref"], v["out"]) for k, v in bc.items()) + "</div>")
    return "\n".join(h)


def dump(reports, out_dir):
    import os
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "eval.json"), "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    txt = text_report(reports)
    with open(os.path.join(out_dir, "eval.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    with open(os.path.join(out_dir, "eval.html"), "w", encoding="utf-8") as f:
        f.write(html_report(reports))
    return txt
