"""一键评测(设计 §8.3 / §9):对每个样例 转换器出输出 → L0/L1/L2 → 汇总。

用法(在 scripts/ 下):
  python -m eval.run                       # 全 10 例,默认 dashscope(复现 80 缺陷基线)
  python -m eval.run --samples 01,03       # 指定样例
  python -m eval.run --llm deepseek        # 换后端
产物:reports/eval/<tag>/{eval.json,eval.txt} 及各样例转换输出。对照物永远是冻结的 结构参考.xml。
理解层由 LLM 承担、无规则降级档,故 --llm 必须是真实后端(见 word2jats/pipeline.py)。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))

from word2jats.pipeline import ConvertOptions, convert  # noqa: E402

from eval import samples as S       # noqa: E402
from eval import validity, fidelity, structure, report  # noqa: E402

CACHE_ROOT = os.path.join(ROOT, "reports", "eval", "_llm_cache")


def convert_sample(smp, out_root, llm="dashscope"):
    out_dir = os.path.join(out_root, smp.key)
    cache = os.path.join(CACHE_ROOT, llm, smp.key)
    os.makedirs(cache, exist_ok=True)
    res = convert(ConvertOptions(
        docx_path=smp.docx, out_dir=out_dir, journal_id=smp.journal, doi=smp.doi,
        llm=llm, llm_cache_dir=cache))
    return res.xml_path, out_dir


def eval_one(smp, out_root, **kw):
    xml_path, out_dir = convert_sample(smp, out_root, **kw)
    l0 = validity.check(xml_path)
    l1 = fidelity.run(smp, xml_path, out_dir)
    l2 = structure.run(smp, xml_path)
    return report.sample_report(smp, l0, l1, l2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None, help="逗号分隔 key(如 01,03,S01);默认全 10 例")
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--llm", default="dashscope", choices=["dashscope", "deepseek", "local"],
                    help="理解层模型后端(方法必需,无降级档);默认 dashscope=qwen3.7-plus")
    args = ap.parse_args()

    keys = [k.strip() for k in args.samples.split(",")] if args.samples else [s.key for s in S.SAMPLES]
    out_root = os.path.join(ROOT, "reports", "eval", args.tag)

    # 全量并发：10 个样例彼此独立（各自独立 LLMClient / 输出目录 / 打分），一律并发执行；
    # 每个样例内部 front/body/refs 及参考分块再并发（见 understand/passes）。DashScope 云端
    # 支持高并发，串行是纯浪费。打分函数（validity/fidelity/structure）逐字不改，结果确定复现。
    t_all = time.time()

    def _run(k):
        smp = S.get(k)
        t0 = time.time()
        r = eval_one(smp, out_root, llm=args.llm)
        print("[%s] %ss 缺陷合计=%d (L0e=%d L1=%d L2=%d)" % (
            k, round(time.time() - t0, 1), r["defect_total"],
            r["L0_validity"]["n_error"], r["L1_fidelity"]["defect_n"], r["L2_structure"]["defect_n"]),
            flush=True)
        return r

    by_key = {}
    with ThreadPoolExecutor(max_workers=len(keys)) as ex:
        futs = {ex.submit(_run, k): k for k in keys}
        for fut in as_completed(futs):
            by_key[futs[fut]] = fut.result()
    reports = [by_key[k] for k in keys]   # 汇总顺序按输入 keys，报告稳定
    print("== 全量并发完成，总耗时 %ss ==" % round(time.time() - t_all, 1), flush=True)

    txt = report.dump(reports, out_root)
    print("\n" + txt)
    print("\n产物: %s/{eval.json,eval.txt}" % out_root)


if __name__ == "__main__":
    main()
