"""确定性 JSON 与中文 Markdown 报告。"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Iterable

from .models import EvaluationResult, Issue


def json_text(result: EvaluationResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).replace("\r", " ").replace("\n", "↵").replace("|", "\\|")
    return text


def _issue_table(issues: Iterable[Issue]) -> list[str]:
    lines = [
        "| 级别 | 编码 | 范畴 | 参考位置 | 候选位置 | 说明 | 期望 | 实际 | 证据 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for issue in issues:
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                _cell(issue.severity), _cell(issue.code), _cell(issue.domain),
                _cell(issue.gold_path), _cell(issue.candidate_path), _cell(issue.message),
                _cell(issue.expected), _cell(issue.actual), _cell(issue.evidence),
            )
        )
    return lines


def _token_table(rows: list[dict]) -> list[str]:
    lines = [
        "| 词项 | 归一形式 | docx 次数 | 候选次数 |",
        "|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append("| %s | %s | %s | %s |" % (
            _cell(row["token"]), _cell(row["normalized"]),
            row["source_count"], row["candidate_count"],
        ))
    return lines


def markdown_text(result: EvaluationResult) -> str:
    issue_severity = Counter(issue.severity for issue in result.issues)
    issue_domains = Counter(issue.domain for issue in result.issues)
    status = "通过" if result.passed else "不通过"
    lines = [
        f"# 样例 {result.sample}：V2 独立评测报告",
        "",
        f"结论：**{status}**。V2 版本 `{result.evaluator_version}`。",
        "",
        "本报告的通过条件是：候选包合法、语义树与金标准一致、所有媒体均可读取且来自输入 docx、覆盖清单没有未处理对象。文本来源清单是独立旁证，不会用 docx 的缺字去豁免候选与金标准的差异。",
        "",
        "## 评测对象与复现依据",
        "",
        f"- 候选：`{result.candidate}`",
        f"- 金标准：`{result.gold_xml}`",
        f"- 输入 docx：`{result.docx}`",
        f"- 候选 XML SHA-256：`{result.hashes.get('candidate_xml_sha256', '—')}`",
        f"- 金标准 XML SHA-256：`{result.hashes.get('gold_xml_sha256', '—')}`",
        f"- figures.zip SHA-256：`{result.hashes.get('figures_zip_sha256', '—')}`",
        f"- docx SHA-256：`{result.hashes.get('docx_sha256', '—')}`",
        f"- 评测器清单 SHA-256：`{result.hashes.get('evaluator', {}).get('manifest_sha256', '—')}`",
        "",
        "## 问题总览",
        "",
        f"共 {len(result.issues)} 条：critical {issue_severity['critical']}，error {issue_severity['error']}，warning {issue_severity['warning']}，info {issue_severity['info']}。",
        "",
    ]
    if issue_domains:
        lines.append("按范畴：" + "；".join(f"{key} {value}" for key, value in sorted(issue_domains.items())) + "。")
        lines.append("")
    if result.issues:
        lines.extend(_issue_table(result.issues))
    else:
        lines.append("未发现问题。")

    lines.extend(["", "## 完整性覆盖", ""])
    if result.coverage:
        lines.extend([
            "| 一侧 | 元素 | 属性 | 文本节点 | 尾文本 | ID/引用关系 | 媒体引用 | 忽略注释 | 忽略排版空白 | 未处理 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for side in ("gold", "candidate"):
            coverage = result.coverage.get(side)
            if coverage is None:
                continue
            lines.append(
                f"| {side} | {coverage.elements} | {coverage.attributes} | {coverage.text_nodes} | "
                f"{coverage.tails} | {coverage.relations} | {coverage.media_links} | "
                f"{coverage.comments_ignored} | {coverage.formatting_whitespace_ignored} | "
                f"{len(coverage.unhandled)} |"
            )
    else:
        lines.append("候选未能解析，无法建立语义覆盖清单。")

    vector = result.statistics.get("quality_vector", {}).get("dimensions", {})
    lines.extend(["", "## 质量向量", ""])
    if vector:
        lines.extend([
            "V2 不把不同性质的问题用主观权重抵消成一个总分。下列各维度同时给出参考事实数、候选事实数和正确配对数。",
            "",
            "| 维度 | 参考 | 候选 | 配对正确 | 精确率 | 召回率 | F1 | 完全一致 |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ])
        for name, row in vector.items():
            lines.append(
                f"| {name} | {row['gold']} | {row['candidate']} | {row['matched']} | "
                f"{row['precision']:.6f} | {row['recall']:.6f} | {row['f1']:.6f} | "
                f"{'是' if row['exact'] else '否'} |"
            )
    else:
        lines.append("候选未能解析，无法计算。")

    provenance = result.provenance
    lines.extend([
        "",
        "## docx 来源旁证",
        "",
        f"读取 docx 文本部件 {provenance.source_parts} 个、段落 {provenance.source_paragraphs} 个；docx 有 {provenance.source_token_kinds} 种词项，候选有 {provenance.candidate_token_kinds} 种。候选引用媒体 {provenance.referenced_media} 个，其中 {provenance.media_from_docx} 个与 docx 内嵌媒体逐字节相同。",
        "",
        "“候选新增词项”和“docx 未进入候选的词项”只用于追查来源，不单独判错；结构化会拆分、合并、规范化部分文字，期刊模板也会确定性生成少量内容。媒体不是 docx 原字节则属于错误。",
        "",
        "### 候选中 docx 没有的词项",
        "",
    ])
    lines.extend(_token_table(provenance.novel_tokens) if provenance.novel_tokens else ["无。"])
    lines.extend(["", "### 候选次数超过 docx 的词项", ""])
    lines.extend(_token_table(provenance.excess_tokens) if provenance.excess_tokens else ["无。"])
    lines.extend(["", "### docx 中未进入候选的词项", ""])
    lines.extend(_token_table(provenance.missing_source_tokens) if provenance.missing_source_tokens else ["无。"])
    lines.extend(["", "### 允许确定性生成的区域", ""])
    if provenance.allowed_generated_regions:
        lines.extend([
            "| 元素 | 数量 |",
            "|---|---:|",
            *[f"| {_cell(name)} | {count} |" for name, count in provenance.allowed_generated_regions.items()],
        ])
    else:
        lines.append("无。")
    lines.append("")
    return "\n".join(lines)


def write_reports(result: EvaluationResult, report_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{result.sample}-eval-v2.json"
    markdown_path = directory / f"{result.sample}-eval-v2.md"
    json_path.write_text(json_text(result), encoding="utf-8")
    markdown_path.write_text(markdown_text(result), encoding="utf-8")
    return json_path, markdown_path
