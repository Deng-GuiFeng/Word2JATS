#!/usr/bin/env python
"""消融分析:逐个关闭组件,用 Layer-1 指标量化各组件对正确率的贡献。

为什么(对齐"全面消融证明每个部件作用"):只说"我们有 N 个修复阶段"不够,必须用数据
证明每个阶段确实把输出推得更接近权威标签。本脚本对每个样例跑多套配置,逐维度对照金标准/
伪标签算分,给出"关掉某组件后掉了多少分"。

配置:
  full            —— Agent 全开(structure+authors+declarations+content)
  warm_start_only —— 关掉整个 Agent 闭环(仅热启动草稿)
  -structure / -authors / -declarations / -content —— 各关掉一个 Agent 阶段
  -llm_tables     —— 关掉看图重建表(热启动表回退)
  -crossref       —— 关掉 CrossRef 文献增强

同一样例的视觉清点/表格 VLM 调用按缓存复用(配置间共享),故并非 N 倍成本。
用法: python -m eval.ablation --samples 1,4,5 --tag abl
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from word2jats.pipeline import ConvertOptions, convert  # noqa: E402
from eval.compare import compare  # noqa: E402
from eval.profile import load_profile_from_xml_path, profile_from_pseudo  # noqa: E402
from eval.regen import ORIG, SUPP, BASE  # noqa: E402
from eval.run import GOLD, load_pseudo  # noqa: E402

# 复用 regen 的 LLM 缓存(视觉清点已热),消融各配置间共享 → 不重算昂贵的 VLM 清点
CACHE_ROOT = os.path.join(
    "/tmp/claude-1001/-home-denggf--------------",
    "08636aa7-31b4-4609-8fce-ab357852803d", "scratchpad", "regen_cache")

ALL = ("structure", "authors", "declarations", "content")
CONFIGS = {
    "full": dict(agent=True, agent_phases=frozenset(ALL), crossref=False),
    "warm_start_only": dict(agent=False, agent_phases=None, crossref=False),
    "-structure": dict(agent=True, agent_phases=frozenset(set(ALL) - {"structure"}), crossref=False),
    "-authors": dict(agent=True, agent_phases=frozenset(set(ALL) - {"authors"}), crossref=False),
    "-declarations": dict(agent=True, agent_phases=frozenset(set(ALL) - {"declarations"}), crossref=False),
    "-content": dict(agent=True, agent_phases=frozenset(set(ALL) - {"content"}), crossref=False),
    "+crossref": dict(agent=True, agent_phases=frozenset(ALL), crossref=True),
}


def ref_profile(key):
    if key.startswith("S"):
        return profile_from_pseudo(load_pseudo(key))
    return load_profile_from_xml_path(os.path.join(BASE, GOLD[key]))


def run_cfg(key, spec, cfg_name, cfg, tag):
    docx, gold, fig, journal, doi = spec
    out_dir = os.path.join(ROOT, "reports", "ablation", tag, key, cfg_name.lstrip("+-") or cfg_name)
    cache = os.path.join(CACHE_ROOT, key)   # 同一样例配置间共享缓存
    os.makedirs(cache, exist_ok=True)
    res = convert(ConvertOptions(
        docx_path=os.path.join(BASE, docx), out_dir=out_dir, journal_id=journal, doi=doi,
        figures_path=os.path.join(BASE, fig) if fig else None,
        llm="local", llm_cache_dir=cache, agent_dpi=120, **cfg))
    out_p = load_profile_from_xml_path(res.xml_path)
    r = compare(out_p, ref_profile(key))
    return r.get("overall_score"), r["sections"].get("score") if r["sections"].get("available") else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default="1,2,3,4,5")
    ap.add_argument("--tag", default="abl")
    ap.add_argument("--configs", default=None, help="逗号分隔子集,默认全部")
    args = ap.parse_args()
    allc = {**ORIG, **SUPP}
    keys = [k.strip() for k in args.samples.split(",") if k.strip() in allc]
    cfgs = list(CONFIGS) if not args.configs else [c for c in args.configs.split(",") if c in CONFIGS]

    table = {}  # cfg -> {key: (overall, sec)}
    for cfg_name in cfgs:
        table[cfg_name] = {}
        for key in keys:
            t0 = time.time()
            ov, sec = run_cfg(key, allc[key], cfg_name, CONFIGS[cfg_name], args.tag)
            table[cfg_name][key] = (ov, sec)
            print("  [%s/%s] overall=%.3f sec=%s (%.0fs)" % (
                cfg_name, key, ov or 0, ("%.2f" % sec) if sec is not None else "—",
                time.time() - t0), flush=True)

    # 汇总:各配置宏平均 + 相对 full 的掉分
    import json
    print("\n==== 消融汇总(宏平均 overall;Δ=相对 full)====")
    base = None
    summary = {}
    for cfg_name in cfgs:
        ovs = [v[0] for v in table[cfg_name].values() if v[0] is not None]
        macro = sum(ovs) / len(ovs) if ovs else 0
        summary[cfg_name] = macro
        if cfg_name == "full":
            base = macro
    for cfg_name in cfgs:
        d = (summary[cfg_name] - base) if base is not None else 0
        print("  %-18s 宏平均=%.3f  Δ=%+.3f" % (cfg_name, summary[cfg_name], d))
    out = os.path.join(ROOT, "reports", "ablation", args.tag, "ablation_summary.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"table": {c: {k: list(v) for k, v in d.items()} for c, d in table.items()},
               "macro": summary}, open(out, "w"), ensure_ascii=False, indent=2)
    print("已存档", out)


if __name__ == "__main__":
    main()
