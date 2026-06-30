#!/usr/bin/env python
"""用当前(LLM 主导闭环)方法重新生成各样例的 JATS 输出,供 Layer-1/Layer-2 评测对照标签。

dpi 固定 120(实测 OCR 可读);每样例独立 LLM 缓存目录(便于复跑命中)。
用法:
  python -m eval.regen --samples 1            # 单样例(验证接线)
  python -m eval.regen --all --tag v2         # 全 10 样例,输出到 reports/eval_runs/v2
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))

from word2jats.pipeline import ConvertOptions, convert  # noqa: E402

BASE = os.path.join(ROOT, "样例")

# key -> (docx, gold, figzip, journal, doi)
ORIG = {
    "1": ("样例1/第一组/初始word.docx", "样例1/第一组/最终上线.xml", "样例1/第一组/figure.zip", "RCM", "10.31083/RCM46777"),
    "2": ("样例2/第一组/初始文件.docx", "样例2/第一组/最终上线.xml", "样例2/第一组/figures.zip", "RCM", "10.31083/RCM46175"),
    "3": ("样例3/第一组/初始文件.docx", "样例3/第一组/最终文件.xml", "样例3/第一组/figures.zip", "JIN", "10.31083/JIN49347"),
    "4": ("样例4/第一组/初始文件.docx", "样例4/第一组/最终文件.xml", "样例4/第一组/figures.zip", "JIN", "10.31083/JIN52316"),
    "5": ("样例5/第一组/初始文件.docx", "样例5/第一组/最终文件.xml", "样例5/第一组/figures.zip", "HSF", "10.31083/HSF49106"),
}
SUPP = {
    "S1": ("补充案例-仅输入/选题一/样例1.docx", None, None, None, None),
    "S2": ("补充案例-仅输入/选题一/样例2.docx", None, None, None, None),
    "S3": ("补充案例-仅输入/选题一/样例3.docx", None, None, None, None),
    "S4": ("补充案例-仅输入/选题一/样例4.docx", None, None, None, None),
    "S5": ("补充案例-仅输入/选题一/样例5.docx", None, None, None, None),
}
CACHE_ROOT = os.path.join(
    "/tmp/claude-1001/-home-denggf--------------",
    "08636aa7-31b4-4609-8fce-ab357852803d", "scratchpad", "regen_cache")


def regen(key, spec, out_root, dpi=120):
    docx, gold, fig, journal, doi = spec
    docx_p = os.path.join(BASE, docx)
    fig_p = os.path.join(BASE, fig) if fig else None
    out_dir = os.path.join(out_root, "out_%s" % key)
    cache = os.path.join(CACHE_ROOT, key)
    os.makedirs(cache, exist_ok=True)
    t0 = time.time()
    res = convert(ConvertOptions(
        docx_path=docx_p, out_dir=out_dir, journal_id=journal, doi=doi,
        figures_path=fig_p, llm="local", agent=True, crossref=False,
        llm_cache_dir=cache, agent_dpi=dpi))
    el = round(time.time() - t0, 1)
    ag = res.stats.get("agent", {})
    st = res.stats.get("agent_struct") if False else None
    print("[%s] %ss DTD=%s 章节=%s 作者=%s 声明=%s 轮=%s 内容修复=%s -> %s" % (
        key, el, bool(res.validation and res.validation.ok),
        _phase(out_dir, res.article_id, "structure"),
        _phase(out_dir, res.article_id, "authors"),
        _phase(out_dir, res.article_id, "declarations"),
        ag.get("rounds"), ag.get("repairs_applied"), res.xml_path), flush=True)
    return res


def _phase(out_dir, article_id, key):
    import json
    p = os.path.join(out_dir, "%s.agent-trace.json" % article_id)
    if not os.path.exists(p):
        return "?"
    try:
        with open(p, encoding="utf-8") as f:
            tr = json.load(f)
        ph = tr.get(key, {})
        return "✓" if ph.get("applied") else ("✗:" + (ph.get("reason", "")[:20]))
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None)
    ap.add_argument("--orig", action="store_true")
    ap.add_argument("--supp", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--dpi", type=int, default=120)
    args = ap.parse_args()

    cases = {}
    if args.all or (not args.orig and not args.supp and not args.samples):
        cases.update(ORIG); cases.update(SUPP)
    else:
        if args.orig:
            cases.update(ORIG)
        if args.supp:
            cases.update(SUPP)
        if args.samples:
            allc = {**ORIG, **SUPP}
            for k in args.samples.split(","):
                k = k.strip()
                if k in allc:
                    cases[k] = allc[k]
    if args.samples and not (args.orig or args.supp or args.all):
        allc = {**ORIG, **SUPP}
        cases = {k.strip(): allc[k.strip()] for k in args.samples.split(",") if k.strip() in allc}

    orig_root = os.path.join(ROOT, "reports", "eval_runs", args.tag)
    supp_root = os.path.join(ROOT, "reports", "eval_runs", args.tag + "-supp")
    for key, spec in cases.items():
        root = supp_root if key.startswith("S") else orig_root
        os.makedirs(root, exist_ok=True)
        regen(key, spec, root, dpi=args.dpi)


if __name__ == "__main__":
    main()
