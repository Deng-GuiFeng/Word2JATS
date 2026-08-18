"""V1 一键评测(设计 §8.3 / §9):对每个样例 转换器出输出 → L0/L1/L2 → 汇总。

用法(项目根目录下):
  python -m scripts.eval_v1                       # 全 10 例,默认 dashscope(成绩口径)
  python -m scripts.eval_v1 --samples 01,03       # 指定样例(X01-X04 需显式指定)
  python -m scripts.eval_v1 --llm deepseek        # 换后端
  python -m scripts.eval_v1 --score-only 输出目录  # **只评分,不跑转换器**:对已有输出重新打分

产物分家:
  转换输出   reports/outputs/<tag>/<样例>/   —— 两套评测器共同的评测对象,不属于任何一方
  V1 报告    reports/eval_v1/<tag>/{eval.json,eval.txt,eval.html}
对照物永远是冻结的金标准(结构参考.xml + figures.zip)。
理解层由 LLM 承担、无规则降级档,故 --llm 必须是真实后端(见 word2jats/pipeline.py)。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))

from . import samples as S       # noqa: E402
from . import validity, fidelity, structure, report  # noqa: E402
from scripts.output_manifest import (  # noqa: E402
    clear_manifest, record_from_result, resolve_output, write_manifest,
)

# LLM 磁盘缓存不属于任何一套评测器,是转换器的:同输入靠它逐字节复现,也让集成测试零成本。
# 故与两器的报告目录平级,不再挂在某一方名下。
CACHE_ROOT = os.path.join(ROOT, "reports", "_llm_cache")
OUTPUT_ROOT = os.path.join(ROOT, "reports", "outputs")
REPORT_ROOT = os.path.join(ROOT, "reports", "eval_v1")


def convert_sample(smp, out_root, llm="dashscope", cache_tag=None):
    """cache_tag:缓存分区名。给一个此前没用过的名字,本次运行就不会命中任何历史响应
    (全部真实调用当前转换器),同时本次响应仍被记下,将来可据此逐字节复现这一版基准。

    注意 `llm_cache_dir=None` **不等于停用缓存**:DiskCache 收到 None 会退回默认目录、
    照样命中旧响应。要"不吃旧缓存",只能换一个干净的分区。"""
    from word2jats.pipeline import ConvertOptions, convert
    out_dir = os.path.join(out_root, smp.key)
    # 先清空:输出目录不清理,上一次转换留下的图片文件会让这一次"缺图"被存在性检查蒙混过关
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    cache = os.path.join(CACHE_ROOT, cache_tag or llm, smp.key)
    os.makedirs(cache, exist_ok=True)
    res = convert(ConvertOptions(
        docx_path=smp.docx, out_dir=out_dir, journal_id=smp.journal, doi=smp.doi,
        llm=llm, llm_cache_dir=cache))
    return res.candidate_xml, res.candidate_dir, record_from_result(res, out_root)


def score(smp, xml_path, out_dir):
    """只打分,不跑转换。L0 判非良构则短路——XML 都解析不了,L1/L2 再去解析同一文件只会
    抛异常打断整轮评测,报告里也应当直接呈现"这份输出根本不是 XML"。"""
    l0 = validity.check(xml_path)
    if not l0["wellformed"]:
        empty1 = {"lost": [], "fabricated": [], "lost_numeric": [], "fabricated_numeric": [],
                  "altered_pairs": [], "images": [], "b_additions": [],
                  "gold_free": {"n_lost": 0, "n_fabricated": 0, "lost": [], "fabricated": [],
                                "lost_numeric": [], "fabricated_numeric": []},
                  "formula_tokens": {"lost": [], "fabricated": [], "n_lost": 0, "n_fabricated": 0},
                  "image_count": {"out": 0, "ref": 0, "match": False},
                  "n_lost": 0, "n_fabricated": 0, "n_img_bad": 0, "defect_n": 0}
        empty2 = {"by_category": [], "defect_n": 0, "b_coverage": {}, "skipped": "XML 非良构"}
        return report.sample_report(smp, l0, empty1, empty2)
    l1 = fidelity.run(smp, xml_path, out_dir)
    l2 = structure.run(smp, xml_path, out_dir)
    return report.sample_report(smp, l0, l1, l2)


def find_output(smp, out_root):
    """在已有报告目录里定位某样例的输出 XML 与媒体目录。"""
    location = resolve_output(out_root, smp.key)
    if location.candidate_xml is None or not location.candidate_xml.is_file():
        return None, None
    return str(location.candidate_xml), str(location.candidate_dir)


def eval_one(smp, out_root, **kw):
    xml_path, out_dir, manifest_record = convert_sample(smp, out_root, **kw)
    return score(smp, xml_path, out_dir), manifest_record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None,
                    help="逗号分隔 key(如 01,03,S01,X01);默认成绩口径 10 例")
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--llm", default="dashscope", choices=["dashscope", "deepseek", "local"],
                    help="理解层模型后端(方法必需,无降级档);默认 dashscope=qwen3.7-plus")
    ap.add_argument("--score-only", metavar="DIR", default=None,
                    help="只对该目录里已有的转换输出重新打分,不调用转换器(零成本重评)")
    ap.add_argument("--report-dir", metavar="DIR", default=None,
                    help="报告落点;默认 reports/eval_v1/<tag>/")
    ap.add_argument("--cache-tag", metavar="NAME", default=None,
                    help="LLM 缓存分区名;默认按后端名(dashscope)。给一个没用过的名字即"
                         "不吃任何历史缓存、全部真实调用,同时本次响应仍被记下可复现")
    ap.add_argument("--fresh-cache", action="store_true",
                    help="等价于 --cache-tag <tag>:用与本次 --tag 同名的干净缓存分区")
    args = ap.parse_args()

    keys = ([k.strip() for k in args.samples.split(",")] if args.samples
            else [s.key for s in S.EVAL_SET])

    def _abs(p):
        return p if os.path.isabs(p) else os.path.join(ROOT, p)

    rep_dir = _abs(args.report_dir) if args.report_dir \
        else os.path.join(REPORT_ROOT, args.tag)

    if args.score_only:
        src = _abs(args.score_only)
        reports = []
        for k in keys:
            smp = S.get(k)
            x, d = find_output(smp, src)
            if not x:
                print("[%s] 跳过:%s 下无输出 XML" % (k, src), flush=True)
                continue
            r = score(smp, x, d)
            reports.append(r)
            print("[%s] 缺陷合计=%d (L0e=%d L1=%d L2=%d)" % (
                k, r["defect_total"], r["L0_validity"]["n_error"],
                r["L1_fidelity"]["defect_n"], r["L2_structure"]["defect_n"]), flush=True)
        if reports:
            print("\n" + report.dump(reports, rep_dir))
            print("\n产物: %s/{eval.json,eval.txt,eval.html}" % rep_dir)
        return

    out_root = os.path.join(OUTPUT_ROOT, args.tag)
    clear_manifest(out_root)
    cache_tag = args.cache_tag or (args.tag if args.fresh_cache else None)
    if cache_tag:
        print("LLM 缓存分区:%s(不吃历史缓存,全部真实调用当前转换器)" % cache_tag, flush=True)

    # 全量并发：样例彼此独立（各自独立 LLMClient / 输出目录 / 打分），一律并发执行；
    # 每个样例内部 front/body/refs 及参考分块再并发（见 understand/passes）。DashScope 云端
    # 支持高并发，串行是纯浪费。打分函数（validity/fidelity/structure）逐字不改，结果确定复现。
    t_all = time.time()

    def _run(k):
        smp = S.get(k)
        t0 = time.time()
        r, manifest_record = eval_one(smp, out_root, llm=args.llm, cache_tag=cache_tag)
        print("[%s] %ss 缺陷合计=%d (L0e=%d L1=%d L2=%d)" % (
            k, round(time.time() - t0, 1), r["defect_total"],
            r["L0_validity"]["n_error"], r["L1_fidelity"]["defect_n"], r["L2_structure"]["defect_n"]),
            flush=True)
        return r, manifest_record

    by_key = {}
    manifest_records = {}
    with ThreadPoolExecutor(max_workers=len(keys)) as ex:
        futs = {ex.submit(_run, k): k for k in keys}
        for fut in as_completed(futs):
            report_result, manifest_record = fut.result()
            by_key[futs[fut]] = report_result
            manifest_records[futs[fut]] = manifest_record
    manifest_path = write_manifest(out_root, manifest_records)
    reports = [by_key[k] for k in keys]   # 汇总顺序按输入 keys，报告稳定
    print("== 全量并发完成，总耗时 %ss ==" % round(time.time() - t_all, 1), flush=True)

    txt = report.dump(reports, rep_dir)
    print("\n" + txt)
    print("\n转换输出: %s/  (两套评测器共同的评测对象)" % out_root)
    print("运行清单: %s" % manifest_path)
    print("V1 报告:  %s/{eval.json,eval.txt,eval.html}" % rep_dir)


if __name__ == "__main__":
    main()
