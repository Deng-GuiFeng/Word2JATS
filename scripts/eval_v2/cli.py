"""命令行入口。只评测已有输出，不调用转换器。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .engine import evaluate_sample
from .report import json_text, write_reports
from .samples import ALL_SAMPLES, ROOT, get_sample
from scripts.output_manifest import resolve_output

# 与 V1 对称的产物落点：转换输出属于两器共有、不挂在任何一方名下；各自的报告分开放。
#   reports/outputs/<tag>/<样例>/   转换输出（评测对象）
#   reports/eval_v1/<tag>/          V1 报告
#   reports/eval_v2/<tag>/          V2 报告
DEFAULT_CANDIDATE_ROOT = ROOT / "reports" / "outputs" / "latest"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "eval_v2"


def _sample_keys(value: str) -> list[str]:
    if value == "all":
        return [sample.key for sample in ALL_SAMPLES]
    if value in {"main", "supp", "external"}:
        return [sample.key for sample in ALL_SAMPLES if sample.group == value]
    keys = [part.strip() for part in value.split(",") if part.strip()]
    for key in keys:
        get_sample(key)
    return keys


def _exit_code(results) -> int:
    if all(result.passed for result in results):
        return 0
    infrastructure = any(
        issue.severity == "critical" and issue.domain == "infrastructure"
        for result in results for issue in result.issues
    )
    return 2 if infrastructure else 1


def _evaluate(args: argparse.Namespace) -> int:
    result = evaluate_sample(args.sample, args.candidate)
    if args.report_dir:
        json_path, md_path = write_reports(result, args.report_dir)
    else:
        json_path = md_path = None
    if args.json:
        sys.stdout.write(json_text(result))
    else:
        print(
            f"[{result.sample}] {'通过' if result.passed else '不通过'}；"
            f"critical/error/warning/info="
            f"{sum(i.severity == 'critical' for i in result.issues)}/"
            f"{sum(i.severity == 'error' for i in result.issues)}/"
            f"{sum(i.severity == 'warning' for i in result.issues)}/"
            f"{sum(i.severity == 'info' for i in result.issues)}"
        )
        shown = result.issues[:max(0, args.max_issues)]
        for issue in shown:
            print(f"  {issue.severity} {issue.code}: {issue.message}")
        if len(result.issues) > len(shown):
            print(
                f"  ……另有 {len(result.issues) - len(shown)} 条未在终端展开；"
                "请使用 --json 或 --report-dir 查看完整结果。"
            )
        if json_path:
            print(f"报告：{json_path}；{md_path}")
    return _exit_code([result])


def _batch(args: argparse.Namespace) -> int:
    keys = _sample_keys(args.samples)
    root = Path(args.candidate_root)
    results = []
    for key in keys:
        result = evaluate_sample(key, resolve_output(root, key).candidate_dir)
        results.append(result)
        if args.report_dir:
            write_reports(result, args.report_dir)
        print(f"[{key}] {'通过' if result.passed else '不通过'}，问题 {len(result.issues)} 条")
    summary = {
        "evaluator": "eval_v2",
        "samples": keys,
        "passed": sum(result.passed for result in results),
        "failed": sum(not result.passed for result in results),
        "results": [
            {
                "sample": result.sample,
                "passed": result.passed,
                "issues": result.statistics.get("issues", {}),
            }
            for result in results
        ],
    }
    if args.report_dir:
        summary_path = Path(args.report_dir) / "summary-eval-v2.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"汇总：{summary_path}")
    return _exit_code(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.eval_v2",
        description="独立、只读地核验 docx 转 JATS 的候选输出包。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    one = subparsers.add_parser("evaluate", help="评测一个样例")
    one.add_argument("--sample", required=True, help="样例编号，如 01、S01、X03")
    one.add_argument("--candidate", required=True, help="候选 XML 或候选输出目录")
    one.add_argument("--report-dir", help="可选；写入 JSON 和 Markdown 的独立目录")
    one.add_argument("--json", action="store_true", help="把完整 JSON 输出到标准输出")
    one.add_argument("--max-issues", type=int, default=20, help="终端最多展开的问题数；默认 20")
    one.set_defaults(func=_evaluate)

    batch = subparsers.add_parser("batch", help="批量评测候选根目录下的样例子目录")
    batch.add_argument(
        "--candidate-root", default=str(DEFAULT_CANDIDATE_ROOT),
        help="其下应有 01、02 等子目录；默认 reports/outputs/latest",
    )
    batch.add_argument(
        "--samples", default="all",
        help="all、main、supp、external，或逗号分隔编号；默认 all",
    )
    batch.add_argument(
        "--report-dir", default=str(DEFAULT_REPORT_ROOT / "latest"),
        help="写入逐例报告和汇总 JSON；默认 reports/eval_v2/latest",
    )
    batch.set_defaults(func=_batch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        parser.error(str(exc))
    except Exception as exc:  # 命令行边界：运行故障用退出码 2，与质量不通过区分。
        print(f"评测器运行失败：{exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
