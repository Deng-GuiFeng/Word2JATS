"""消融(设计 §10):逐个关闭转换器组件,用评测"剩余真缺陷数"量化每个组件的价值。

指标覆盖 L0/L1/L2 三层(不再是旧的"机器侧差异数",能区分格式层与语义层组件)。
配置:
  off        确定性档(无 LLM 无 Agent)—— 基线
  llm        仅 LLM 结构化参考文献(无 Agent 视觉闭环)
  agent      LLM + Agent 全阶段(structure/authors/declarations/content)
  -<phase>   agent 关掉某一阶段(量化该阶段贡献)
用法(scripts/ 下):
  python -m eval.ablation --llm dashscope --samples 03,05
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
from word2jats.agent.loop import ALL_PHASES  # noqa: E402

from eval import samples as S               # noqa: E402
from eval import validity, fidelity, structure  # noqa: E402

CACHE_ROOT = os.path.join(ROOT, "reports", "eval", "_llm_cache")


def _defects(smp, xml_path, out_dir):
    l0 = validity.check(xml_path)
    l1 = fidelity.run(smp, xml_path, out_dir)
    l2 = structure.run(smp, xml_path)
    n_l0e = sum(1 for v in l0["violations"] if v["severity"] == "error")
    return {"L0e": n_l0e, "L1": l1["defect_n"], "L2": l2["defect_n"],
            "total": n_l0e + l1["defect_n"] + l2["defect_n"]}


def _run_cfg(smp, out_root, name, llm, agent, phases):
    out_dir = os.path.join(out_root, "%s__%s" % (smp.key, name))
    cache = os.path.join(CACHE_ROOT, llm, smp.key)  # 复用 run.py 缓存,避免重复模型调用
    if agent or llm != "off":
        os.makedirs(cache, exist_ok=True)
    res = convert(ConvertOptions(
        docx_path=smp.docx, out_dir=out_dir, journal_id=smp.journal, doi=smp.doi,
        figures_path=smp.figures_zip, llm=llm, agent=agent,
        agent_phases=frozenset(phases) if phases is not None else None,
        llm_cache_dir=cache if (agent or llm != "off") else None))
    return _defects(smp, res.xml_path, out_dir)


def configs(llm):
    cfgs = [("off", "off", False, None), ("llm", llm, False, None),
            ("agent", llm, True, None)]
    for ph in ALL_PHASES:
        rest = [p for p in ALL_PHASES if p != ph]
        cfgs.append(("agent-%s" % ph, llm, True, rest))
    return cfgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None)
    ap.add_argument("--llm", default="dashscope", choices=["off", "local", "dashscope", "deepseek"])
    ap.add_argument("--tag", default="ablation")
    args = ap.parse_args()

    keys = [k.strip() for k in args.samples.split(",")] if args.samples else [s.key for s in S.SAMPLES]
    out_root = os.path.join(ROOT, "reports", "eval", args.tag)
    cfgs = configs(args.llm)

    table = {}  # key -> {cfg: defects}
    for k in keys:
        smp = S.get(k)
        table[k] = {}
        for name, llm, agent, phases in cfgs:
            t0 = time.time()
            d = _run_cfg(smp, out_root, name, llm, agent, phases)
            table[k][name] = d
            print("[%s/%s] %ss total=%d (L0e=%d L1=%d L2=%d)" % (
                k, name, round(time.time() - t0, 1), d["total"], d["L0e"], d["L1"], d["L2"]), flush=True)

    # 汇总表(缺陷总数;越小越好;相对 off 的下降=组件价值)
    names = [c[0] for c in cfgs]
    print("\n%-6s " % "样例" + " ".join("%-12s" % n for n in names))
    for k in keys:
        print("%-6s " % k + " ".join("%-12d" % table[k][n]["total"] for n in names))

    import json
    os.makedirs(out_root, exist_ok=True)
    with open(os.path.join(out_root, "ablation.json"), "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=2)
    print("\n产物: %s/ablation.json" % out_root)


if __name__ == "__main__":
    main()
