"""逐维度比对两份画像(我们的输出 vs 权威标签),产出描述性差异 + 分值。

设计原则(针对"用被定义的正确冒充真正的正确"这一错误):
- 不把整篇压成一个 pass/fail。每个维度都给出**精确差异**(缺什么、多什么、错在哪)
  和一个 0~1 分值,既能量化也能逐条追问。
- 章节层级是重点维度:既比标题集合,也比每个标题的嵌套深度和章节编号是否保留。
- 标签未提供的维度(如伪标签无 body 章节树)显式标 not_available,不臆造、不算分。
"""
from __future__ import annotations

from .profile import norm_title, norm_ws, surname_key


def _f1(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else (1.0 if not fn else 0.0)
    r = tp / (tp + fn) if (tp + fn) else (1.0 if not fp else 0.0)
    f = (2 * p * r / (p + r)) if (p + r) else (1.0 if (not fp and not fn) else 0.0)
    return round(p, 4), round(r, 4), round(f, 4)


def compare_sets(out_items, ref_items):
    """集合比对(字符串集),返回 {score(F1), missing, extra, common}。"""
    o, r = set(out_items), set(ref_items)
    tp = len(o & r)
    fp = len(o - r)
    fn = len(r - o)
    p, rec, f = _f1(tp, fp, fn)
    return {"score": f, "precision": p, "recall": rec,
            "missing": sorted(r - o), "extra": sorted(o - r),
            "n_out": len(o), "n_ref": len(r)}


# --------------------------- 章节树 --------------------------- #
def compare_sections(out_secs, ref_secs, ref_available=True):
    if not ref_available:
        return {"available": False, "note": "标签未提供 body 章节树,本维度不评分"}
    out = [{"key": norm_title(s["title_with_label"] or s["title"]),
            "depth": s["depth"], "raw": s["title_with_label"] or s["title"],
            "number": s.get("number")} for s in out_secs]
    ref = [{"key": norm_title(s["title_with_label"] or s["title"]),
            "depth": s["depth"], "raw": s["title_with_label"] or s["title"],
            "number": s.get("number")} for s in ref_secs]
    out_by = {s["key"]: s for s in out if s["key"]}
    ref_by = {s["key"]: s for s in ref if s["key"]}

    matched, depth_mismatch = [], []
    for k, rs in ref_by.items():
        os_ = out_by.get(k)
        if os_ is not None:
            ok = (os_["depth"] == rs["depth"])
            matched.append({"title": rs["raw"], "ref_depth": rs["depth"],
                            "out_depth": os_["depth"], "depth_ok": ok})
            if not ok:
                depth_mismatch.append({"title": rs["raw"],
                                       "ref_depth": rs["depth"], "out_depth": os_["depth"]})
    missing = [ref_by[k]["raw"] for k in ref_by if k not in out_by]
    extra = [out_by[k]["raw"] for k in out_by if k not in ref_by]

    n_match = len(matched)
    p, rec, f_head = _f1(n_match, len(extra), len(missing))
    depth_ok_n = sum(1 for m in matched if m["depth_ok"])
    depth_acc = round(depth_ok_n / n_match, 4) if n_match else (1.0 if not ref_by else 0.0)
    # 章节编号保留率(标签里有编号的标题,我们是否也有编号)
    ref_numbered = [s for s in ref if s["number"]]
    num_kept = sum(1 for rs in ref_numbered
                   if out_by.get(rs["key"]) and out_by[rs["key"]]["number"])
    numbering = round(num_kept / len(ref_numbered), 4) if ref_numbered else None

    out_top = sum(1 for s in out if s["depth"] == 0)
    ref_top = sum(1 for s in ref if s["depth"] == 0)
    # 综合分:标题F1 与 层级准确率 的乘积(都得对才算结构对)
    score = round(f_head * depth_acc, 4)
    return {
        "available": True, "score": score,
        "heading_f1": f_head, "heading_precision": p, "heading_recall": rec,
        "depth_accuracy": depth_acc, "numbering_retention": numbering,
        "top_level_out": out_top, "top_level_ref": ref_top,
        "n_headings_out": len(out), "n_headings_ref": len(ref),
        "missing_headings": missing, "extra_headings": extra,
        "depth_mismatches": depth_mismatch,
    }


# --------------------------- 作者 --------------------------- #
def compare_authors(out_authors, ref_authors, out_emails=None):
    out_emails = set(out_emails or [])
    out_keys = [surname_key(a["surname"]) for a in out_authors]
    ref_keys = [surname_key(a["surname"]) for a in ref_authors]
    out_by = {surname_key(a["surname"]): a for a in out_authors}
    ref_by = {surname_key(a["surname"]): a for a in ref_authors}

    setres = compare_sets(out_keys, ref_keys)
    order_ok = (out_keys == ref_keys)

    field_stats = {f: {"checked": 0, "match": 0, "diffs": []}
                   for f in ("given", "corresponding", "equal", "orcid", "email", "affs")}
    for k in ref_by:
        if k not in out_by:
            continue
        o, r = out_by[k], ref_by[k]
        for f in field_stats:
            rv, ov = r.get(f), o.get(f)
            # 标签缺该字段(空)时不计入(避免拿标签的空白扣分)
            if f in ("orcid", "email", "given") and not rv:
                continue
            if f == "email":
                # 文档级邮箱集合:标签里该作者的邮箱,只要出现在我们输出的任意位置
                # (contrib 内 或 author-notes/corresp 内)即算命中,避免位置差异误判丢失
                field_stats[f]["checked"] += 1
                if str(rv).lower() in out_emails:
                    field_stats[f]["match"] += 1
                else:
                    field_stats[f]["diffs"].append({"surname": k, "ref": rv, "out": "(全文未见此邮箱)"})
                continue
            if f == "affs":
                rv, ov = set(rv or []), set(ov or [])
            field_stats[f]["checked"] += 1
            if (ov == rv) or (f == "given" and norm_ws(str(ov)).lower() == norm_ws(str(rv)).lower()):
                field_stats[f]["match"] += 1
            else:
                field_stats[f]["diffs"].append({"surname": k, "ref": _show(rv), "out": _show(ov)})

    field_scores = {}
    for f, st in field_stats.items():
        field_scores[f] = round(st["match"] / st["checked"], 4) if st["checked"] else None
    # 综合:姓名集合F1 × 顺序 × 各字段均分
    fvals = [v for v in field_scores.values() if v is not None]
    favg = sum(fvals) / len(fvals) if fvals else 1.0
    score = round(setres["score"] * (1.0 if order_ok else 0.9) * favg, 4)
    return {
        "score": score, "name_f1": setres["score"], "order_ok": order_ok,
        "n_out": len(out_authors), "n_ref": len(ref_authors),
        "missing": setres["missing"], "extra": setres["extra"],
        "field_scores": field_scores,
        "field_diffs": {f: st["diffs"] for f, st in field_stats.items() if st["diffs"]},
    }


def _show(v):
    if isinstance(v, set):
        return sorted(v)
    return v


# --------------------------- 标量/计数 --------------------------- #
def compare_scalar(out_v, ref_v, normfn=lambda x: norm_ws(x).lower()):
    if not ref_v:
        return {"available": False, "out": out_v, "ref": ref_v}
    match = (normfn(out_v) == normfn(ref_v))
    return {"score": 1.0 if match else 0.0, "match": match, "out": out_v, "ref": ref_v}


def compare_count(out_n, ref_n, label=""):
    if ref_n is None:
        return {"available": False, "out": out_n, "ref": ref_n}
    match = (out_n == ref_n)
    score = 1.0 if match else round(min(out_n, ref_n) / max(out_n, ref_n), 4) if max(out_n or 0, ref_n or 0) else 1.0
    return {"score": score, "match": match, "out": out_n, "ref": ref_n, "delta": (out_n or 0) - (ref_n or 0)}


# --------------------------- 顶层 --------------------------- #
def compare(out_p: dict, ref_p: dict) -> dict:
    sec_avail = ref_p.get("source") == "xml" or ref_p.get("sections_available", False)
    res = {
        "ref_source": ref_p.get("source"),
        "title": compare_scalar(out_p["title"], ref_p["title"]),
        "sections": compare_sections(out_p["sections"], ref_p["sections"], sec_avail),
        "authors": compare_authors(out_p["authors"], ref_p["authors"], out_p.get("emails")),
        "affiliations_count": compare_count(len(out_p["affiliations"]), len(ref_p["affiliations"])),
        "affiliations_text": compare_sets(
            [norm_ws(a["text"])[:60].lower() for a in out_p["affiliations"] if a["text"]],
            [norm_ws(a["text"])[:60].lower() for a in ref_p["affiliations"] if a["text"]]),
        "abstract_structured": compare_scalar(
            str(out_p["abstract"]["structured"]), str(ref_p["abstract"]["structured"]),
            normfn=lambda x: x.lower()),
        "abstract_subheads": compare_sets(
            [s.lower() for s in out_p["abstract"]["subheads"]],
            [s.lower() for s in ref_p["abstract"]["subheads"]]),
        "keywords": compare_sets([k.lower() for k in out_p["keywords"]],
                                 [k.lower() for k in ref_p["keywords"]]),
        "figures_count": compare_count(len(out_p["figures"]), len(ref_p["figures"])),
        "tables_count": compare_count(len(out_p["tables"]), len(ref_p["tables"])),
        "references_count": compare_count(out_p["references"]["count"], ref_p["references"]["count"]),
        "back_sections": compare_sets([norm_title(s) for s in out_p["back_sections"]],
                                      [norm_title(s) for s in ref_p["back_sections"]]),
        "editors_count": compare_count(len(out_p.get("editors", [])), len(ref_p.get("editors", []))),
    }
    if ref_p.get("disp_formula") is not None:
        res["disp_formula"] = compare_count(out_p["disp_formula"], ref_p["disp_formula"])

    # 参考文献条目抽样核对(标签给了 references_head 的前几条)
    ref_items = ref_p["references"]["items"]
    if ref_items:
        out_by_label = {r["label"]: r for r in out_p["references"]["items"] if r["label"]}
        hits = 0
        diffs = []
        for ri in ref_items:
            oi = out_by_label.get(ri["label"])
            ok = bool(oi and oi["first_surname"] == ri["first_surname"])
            if ok:
                hits += 1
            else:
                diffs.append({"label": ri["label"], "ref": ri["first_surname"],
                              "out": (oi or {}).get("first_surname", "(缺)")})
        res["references_sample"] = {
            "score": round(hits / len(ref_items), 4), "checked": len(ref_items),
            "hits": hits, "diffs": diffs}

    # 汇总分(对可用维度取均值;章节、作者权重更高)
    weights = {"title": 1, "sections": 3, "authors": 2, "keywords": 1,
               "figures_count": 1, "tables_count": 1, "references_count": 1,
               "back_sections": 1, "abstract_subheads": 1}
    num, den = 0.0, 0.0
    for k, w in weights.items():
        d = res.get(k, {})
        if isinstance(d, dict) and d.get("available", True) and "score" in d:
            num += w * d["score"]
            den += w
    res["overall_score"] = round(num / den, 4) if den else None
    return res
