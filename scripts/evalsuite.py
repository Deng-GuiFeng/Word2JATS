"""两套评测器的编排层:一条命令对同一批转换输出跑完 V1 与 V2,出一份并排小结。

**本文件不含任何评分逻辑**,只负责调度和排版。判分全在两个评测器内部,它们彼此独立、
互不引用,只共用 `样例数据/样例登记.json` 这一份样例名单(名单是数据,不是判分规则)。

用法(项目根目录下):
  python -m scripts.evalsuite                       # 评 reports/outputs/latest,成绩口径 10 例
  python -m scripts.evalsuite --tag mytest          # 换标签
  python -m scripts.evalsuite --samples 01,X03      # 指定样例
  python -m scripts.evalsuite --samples all         # 含 X 组(需先跑过 X 组转换)

产物:
  reports/eval_v1/<tag>/{eval.json,eval.txt,eval.html}
  reports/eval_v2/<tag>/{<样例>-eval-v2.json,<样例>-eval-v2.md,summary-eval-v2.json}
  reports/evalsuite/<tag>/小结.md          两器并排

**两器的数字不可相加、也不可直接对照**:
  V1 出「缺陷清单条数」——L0 错误 + L1 词种 + L2 字段差异,单位本就不同,是规模提示不是分数;
  V2 出「通过/不通过 + 八维质量向量 + 问题条数」——按完整语义树逐项展开,一个子树缺失会
  按其中每一条事实计,量级天然比 V1 大一到两个数量级。
两者一致时结论更可信;不一致时,分歧点本身就是最值得查的地方——这正是同时养两把尺子的理由。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.eval_v1 import report as v1_report          # noqa: E402
from scripts.eval_v1 import run as v1_run                # noqa: E402
from scripts.eval_v1 import samples as v1_samples        # noqa: E402
from scripts.eval_v2.engine import evaluate_sample as v2_evaluate   # noqa: E402
from scripts.eval_v2.report import write_reports as v2_write        # noqa: E402
from scripts.output_manifest import resolve_output                  # noqa: E402


def _rel(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def run_v1(keys, out_root, report_dir):
    """V1:只评分,不跑转换器。返回 {样例: 报告字典}。"""
    out = {}
    reports = []
    for key in keys:
        smp = v1_samples.get(key)
        xml_path, media_dir = v1_run.find_output(smp, out_root)
        if not xml_path:
            out[key] = None
            continue
        rep = v1_run.score(smp, xml_path, media_dir)
        out[key] = rep
        reports.append(rep)
    if reports:
        v1_report.dump(reports, report_dir)
    return out


def run_v2(keys, out_root, report_dir):
    """V2:只读候选包。返回 {样例: 结果对象}。"""
    out = {}
    results = []
    for key in keys:
        result = v2_evaluate(key, resolve_output(out_root, key).candidate_dir)
        out[key] = result
        results.append(result)
        write = write_reports_safe(result, report_dir)
        del write
    summary = {
        "evaluator": "eval_v2",
        "samples": list(keys),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [{"sample": r.sample, "passed": r.passed,
                     "issues": r.statistics.get("issues", {})} for r in results],
    }
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "summary-eval-v2.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return out


def write_reports_safe(result, report_dir):
    os.makedirs(report_dir, exist_ok=True)
    return v2_write(result, report_dir)


def _v2_counts(result):
    sev = {"critical": 0, "error": 0, "warning": 0, "info": 0}
    for issue in result.issues:
        if issue.severity in sev:
            sev[issue.severity] += 1
    return sev


# 小结里必须带 V2 的质量向量,**不能只列问题条数**。问题条数是"报告了几条",整棵子树缺失
# 只报少数几条;而质量向量的分母包含子树内所有事实(V2 设计说明第七节)。只看条数会得出
# 与事实相反的排序——2026-08-14 实测:X 组三例整个参考文献列表没输出,问题条数(181/248/346)
# 反而比 10 例(391~1599)还低,按 F1 看才是 0.29~0.53 对 0.87~0.97,与 V1 的排序一致。
_V2_DIMS = (("elements", "元素"), ("texts", "文本"), ("relations", "关系"))


def _v2_f1(result):
    dims = ((result.statistics.get("quality_vector") or {}).get("dimensions") or {})
    out = []
    for name, _label in _V2_DIMS:
        f1 = (dims.get(name) or {}).get("f1")
        out.append("%.2f" % f1 if isinstance(f1, (int, float)) else "—")
    return out


def summarize(keys, v1, v2, out_root, tag):
    lines = []
    lines.append("# 评测小结 · %s" % tag)
    lines.append("")
    lines.append("评测对象:`%s/`(两套评测器读的是同一批转换输出)" % _rel(out_root))
    lines.append("")
    lines.append("两器**平级、彼此独立**,数字不可相加也不可直接对照:V1 出缺陷清单条数,"
                 "V2 出通过判定与按语义事实展开的质量向量。两者一致时结论更可信;不一致处最值得查。")
    lines.append("")
    lines.append("**V2 那一栏要看 F1,不要拿问题条数横向比样例。** 条数是「报告了几条」,"
                 "整棵子树缺失只报少数几条;F1 的分母才包含子树内的每一条事实。")
    lines.append("")
    lines.append("| 样例 | 组 | V1 缺陷 | V1 分层(L0/L1/L2) | V2 判定 | V2 问题(危急/错误) | "
                 "V2 元素F1 | V2 文本F1 | V2 关系F1 |")
    lines.append("|---|---|---:|---|---|---|---:|---:|---:|")
    for key in keys:
        smp = v1_samples.get(key)
        r1, r2 = v1.get(key), v2.get(key)
        if r1 is None:
            c1, layers = "—(无输出)", "—"
        else:
            c1 = str(r1["defect_total"])
            layers = "%d / %d / %d" % (r1["L0_validity"]["n_error"],
                                       r1["L1_fidelity"]["defect_n"],
                                       r1["L2_structure"]["defect_n"])
        if r2 is None:
            verdict, c2, f1s = "—", "—", ["—"] * len(_V2_DIMS)
        else:
            sev = _v2_counts(r2)
            verdict = "通过" if r2.passed else "不通过"
            c2 = "%d / %d" % (sev["critical"], sev["error"])
            f1s = _v2_f1(r2)
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            key, smp.group, c1, layers, verdict, c2, " | ".join(f1s)))
    lines.append("")
    lines.append("完整报告:`reports/eval_v1/%s/eval.txt`、`reports/eval_v2/%s/`" % (tag, tag))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(
        prog="python -m scripts.evalsuite",
        description="对同一批转换输出同时跑 V1 与 V2 两套评测器,出并排小结。不调用转换器。")
    ap.add_argument("--tag", default="latest", help="产物标签;默认 latest")
    ap.add_argument("--outputs", default=None,
                    help="转换输出目录;默认 reports/outputs/<tag>/")
    ap.add_argument("--samples", default=None,
                    help="逗号分隔样例;也可用 all/main/supp/external;默认成绩口径 10 例")
    args = ap.parse_args()

    groups = {"all": [s.key for s in v1_samples.SAMPLES],
              "main": [s.key for s in v1_samples.SAMPLES if s.group == "main"],
              "supp": [s.key for s in v1_samples.SAMPLES if s.group == "supp"],
              "external": [s.key for s in v1_samples.SAMPLES if s.group == "external"]}
    if args.samples in groups:
        keys = groups[args.samples]
    elif args.samples:
        keys = [k.strip() for k in args.samples.split(",") if k.strip()]
        for k in keys:
            v1_samples.get(k)
    else:
        keys = [s.key for s in v1_samples.EVAL_SET]

    out_root = (args.outputs if args.outputs else os.path.join(ROOT, "reports", "outputs", args.tag))
    if not os.path.isabs(out_root):
        out_root = os.path.join(ROOT, out_root)
    if not os.path.isdir(out_root):
        print("找不到转换输出目录:%s\n先跑转换:python -m scripts.eval_v1 --tag %s" % (out_root, args.tag),
              file=sys.stderr)
        return 2

    v1_dir = os.path.join(ROOT, "reports", "eval_v1", args.tag)
    v2_dir = os.path.join(ROOT, "reports", "eval_v2", args.tag)
    suite_dir = os.path.join(ROOT, "reports", "evalsuite", args.tag)

    print("V1 评测中 …", flush=True)
    v1 = run_v1(keys, out_root, v1_dir)
    print("V2 评测中 …", flush=True)
    v2 = run_v2(keys, out_root, v2_dir)

    text = summarize(keys, v1, v2, out_root, args.tag)
    os.makedirs(suite_dir, exist_ok=True)
    path = os.path.join(suite_dir, "小结.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("\n" + text)
    print("小结:%s" % _rel(path))

    missing = [k for k in keys if v1.get(k) is None]
    if missing:
        print("注意:%s 在 %s 下没有输出,未参与评测。" % (",".join(missing), _rel(out_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
