"""把转换检查转成编辑可逐项核对的原稿摘录，不改变自动转换结论。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from word2jats.parse.docx_reader import read_source_docx


GUIDANCE = {
    "TEXT_UNCOVERED": ("原稿内容尚未完整输出", "核对以下原稿内容在成品中的去向；必要时整理原稿后重新转换。"),
    "OBJECT_UNCOVERED": ("图片或公式需要核对", "检查原稿对象是否出现在预览中，并核对下载包中的文件。"),
    "VISIBLE_NODE_UNCLAIMED": ("内容所属章节需要确认", "核对该段应属于文首、正文还是文后，必要时整理原稿的段落结构。"),
    "BIBR_XREF_AMBIGUOUS": ("文献引用需要核对", "引用文字已保留，请确认它是否链接到对应参考文献。"),
    "FLATTENED_TABLE_UNRESOLVED": ("表格列结构需要校订", "表内文字已保留；建议将原稿中用制表符排成的表格改为 Word 原生表格，再重新转换。"),
    "REFERENCE_FIELDS_INCOMPLETE": ("参考文献保留了完整原文", "该条未完全拆成作者、题名、年份等字段，请核对其著录结构。"),
    "SINGLE_CELL_WRAPPER_UNWRAPPED": ("已整理表头内的排版表格", "内层排版表的内容已并入外层单元格，请确认表头含义与原稿一致。"),
    "REFERENCE_BOUNDARY_UNRESOLVED": ("参考文献条目边界需要核对", "请对照原稿，确认是否存在两条合为一条或一条拆为两条的情况。"),
    "REUSE_WITHOUT_BASIS": ("同一段原文可能重复输出", "核对成品是否重复出现以下内容或题注，判断是否需要修订原稿后重新转换。"),
}


def review_items(docx_path, result):
    report_path = Path(result.candidate_dir).parent / "report.json"
    if not report_path.is_file():
        return []
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source = read_source_docx(docx_path)
    nodes = {n.node_id: n for n in source.nodes}
    issues = list(report.get("understanding", {}).get("issues", []))
    for section in ("source_coverage", "output_provenance", "structure"):
        issues.extend(report.get(section, {}).get("issues", []))
    unique = {}
    for issue in issues:
        code = issue.get("code", "")
        # 正常的标题拆分等执行记录不是待办，避免编辑淹没在无须处理的提示中。
        if code in {"DECLARATION_TITLE_ABSENT", "DECLARATION_TITLE_SPAN_STRIPPED"}:
            continue
        location = issue.get("source_id", "")
        node = nodes.get(location)
        if node is None and location in source._occurrences:
            node = nodes.get(source.occurrence(location).node_id)
        if node is None or not node.text.strip():
            node = next((n for n in source.nodes if n.node_id.startswith(location + "/") and n.text.strip()), None)
        if node is None and location.startswith("table:"):
            try:
                spec = report["understanding"]["body"]["tables"][int(location.split(":")[1]) - 1]
                hints = [*(spec.get("caption_nodes") or []), *(spec.get("flattened_row_nodes") or [])]
                node = next((nodes[h] for h in hints if h in nodes), None)
            except (KeyError, ValueError, IndexError):
                pass
        # 覆盖检查可能逐字符报告同一段复用；编辑按原段落处理，不重复展示相同卡片。
        identity = {"code": code, "source_id": location,
                    "detail": issue.get("detail", "") if not location else ""}
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        if key in unique:
            unique[key]["issue_count"] += 1
            unique[key]["blocking"] |= issue.get("severity") in {"high", "review_blocking"}
            continue
        title, action = GUIDANCE.get(code, ("结构需要核对", "请结合原稿检查对应内容及其在成品中的位置。"))
        unique[key] = {
            "id": key, "code": code, "title": title, "action": action,
            "blocking": issue.get("severity") in {"high", "review_blocking"},
            "source_text": node.text.replace("\ufffc", "〔图片或公式〕") if node else "",
            "source_id": location,
            "issue_count": 1,
        }
    return sorted(unique.values(), key=lambda item: not item["blocking"])
