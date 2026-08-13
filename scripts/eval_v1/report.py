"""报告聚合(设计 §7):机器可读 JSON + 人类可读文本。

顶层**不出加权总分**;只出"缺陷清单条目数"(按样例、按组)+ 逐类命中/漏标/多标。
注意这个合计是**清单规模**,不是加权分:L0 的一条 DTD 错误、L1 的一个丢失词种、L2 的一处
字段不符,单位并不相同,相加只用来看"还剩多少条要处理",不代表严重度等价。

三组分列(§3.7):main=01-05(有上线版本) / supp=S01-05(仅 docx,held-out) /
ext=X01-04(外部投稿件,本队自取,不入成绩,只作评测器盲区探针)。
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
            "n_lost": l1["n_lost"], "n_fabricated": l1["n_fabricated"],
            "n_img_bad": l1["n_img_bad"],
            "lost": l1["lost"], "fabricated": l1["fabricated"],
            # 纯数字单列:邮编/电话/表格数值的增删过去被静默丢弃,现在照常计缺陷、分开呈现
            "lost_numeric": l1["lost_numeric"], "fabricated_numeric": l1["fabricated_numeric"],
            # gold_free:不看金标准的口径。宣称"不依赖金标准的安全不变量"必须引这一栏,
            # 上面的 n_lost/n_fabricated 是以参考为仲裁的 gold_ref 口径,不是 gold-free
            "gold_free": l1["gold_free"],
            # 公式符号单列:对错由 L2 比运算树,这里只作信息量提示
            "formula_tokens": l1["formula_tokens"],
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
            # 覆盖守门里有意"不在 L2 单列比对、由 L1 逐字守恒兜底"的元素:点名列出,
            # 免得"登记了"被读成"比较过"
            "l1_only_elements": next(
                (c.get("l1_only", []) for c in l2["by_category"] if c["cat"] == "覆盖守门"), []),
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
    out.append("对照物 = 冻结的金标准(结构参考.xml + figures.zip;A+B、无 C)。设计:docs/06-评测与成绩.md")
    out.append("合计 = 清单条目数(L0错误 + L1词种 + L2字段差异),单位不同,只看规模不作加权分")
    for grp, name in (("main", "第一组 01-05(委员会主样例,有上线版本)"),
                      ("supp", "补充组 S01-05(仅 docx,held-out)"),
                      ("external", "外部组 X01-04(本队自取投稿件,不入成绩,评测器盲区探针)")):
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
            gf = l1.get("gold_free") or {}
            if l1["defect_n"]:
                out.append("  L1 忠实(gold_ref 口径): 丢失%d类 编造%d类 图问题%d" % (
                    l1["n_lost"], l1["n_fabricated"], l1["n_img_bad"]))
                if l1["lost"]:
                    out.append("    丢失Top: " + ", ".join("%s×%d" % (x["token"], x["n"]) for x in l1["lost"][:10]))
                if l1["fabricated"]:
                    out.append("    编造Top: " + ", ".join("%s×%d" % (x["token"], x["n"]) for x in l1["fabricated"][:10]))
                if l1.get("lost_numeric"):
                    out.append("    丢失数字: " + ", ".join("%s×%d" % (x["token"], x["n"]) for x in l1["lost_numeric"][:12]))
                if l1.get("fabricated_numeric"):
                    out.append("    编造数字: " + ", ".join("%s×%d" % (x["token"], x["n"]) for x in l1["fabricated_numeric"][:12]))
            else:
                out.append("  L1 忠实(gold_ref 口径): ✓ 无丢失/编造/图问题")
            if gf:
                out.append("    gold_free(不看金标准): 丢失%d类 编造%d类 —— 含金标准同样不承载的 C 档剔除项" % (
                    gf.get("n_lost", 0), gf.get("n_fabricated", 0)))
            ft = l1.get("formula_tokens") or {}
            if ft.get("n_lost") or ft.get("n_fabricated"):
                out.append("    公式符号(单列,不计缺陷;对错由 L2 比运算树): 丢%d 造%d" % (
                    ft.get("n_lost", 0), ft.get("n_fabricated", 0)))
            out.append("  L2 对位(命中/漏标/多标):")
            for c in l2["by_category"]:
                flag = "" if (c["missing"] == 0 and c["extra"] == 0 and c["n_defects"] == 0) else "  ⚠"
                out.append("    %-8s 命中%3d 漏标%3d 多标%3d 缺陷%3d%s" % (
                    c["cat"], c["hit"], c["missing"], c["extra"], c["n_defects"], flag))
                if c["n_defects"]:
                    out.extend(_fmt_defects(c["defects"]))
            l1o = l2.get("l1_only_elements") or []
            if l1o:
                out.append("    (以下元素有意不在 L2 单列比对、由 L1 逐字守恒兜底:%s)" % " ".join(l1o))
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
    for grp, name in (("main", "第一组 01-05(有上线版本)"), ("supp", "补充组 S01-05(仅 docx)"),
                      ("external", "外部组 X01-04(不入成绩)")):
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
