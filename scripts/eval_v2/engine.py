"""V2 评测流程的唯一编排入口。"""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
from typing import Any

from lxml import etree

from .artifacts import (
    CandidatePackage,
    GoldMediaStore,
    check_extra_files,
    inspect_candidate_media,
    local_path,
    media_elements,
    parse_xml,
    sha256_file,
    MAX_XML_BYTES,
)
from .canonical import canonicalize
from .compare import compare_nodes
from .models import EvaluationResult, IssueCollector, ProvenanceReport
from .policy import EVALUATOR_VERSION, XLINK_NS
from .provenance import audit_provenance
from .samples import Sample, get_sample
from .validity import validate_xml


XLINK_HREF = f"{{{XLINK_NS}}}href"
MODULE_DIR = Path(__file__).resolve().parent


def _evaluator_hashes() -> dict[str, Any]:
    files: dict[str, str] = {}
    # 只哈希实际参与评测的顶层 Python 模块。测试、说明文档和调用者可能
    # 放入本目录的报告不会改变“评测逻辑版本”指纹。
    for path in sorted(MODULE_DIR.glob("*.py")):
        files[str(path.relative_to(MODULE_DIR))] = sha256_file(path)
    combined = hashlib.sha256(
        "\n".join(f"{name}\0{digest}" for name, digest in files.items()).encode("utf-8")
    ).hexdigest()
    return {"manifest_sha256": combined, "files": files}


def _input_hash(path: Path, collector: IssueCollector, label: str) -> str | None:
    if not path.is_file():
        collector.add(
            "REFERENCE_INPUT_MISSING", "infrastructure", f"{label} 不存在",
            severity="critical", actual=str(path),
        )
        return None
    try:
        return sha256_file(path)
    except OSError as exc:
        collector.add(
            "REFERENCE_INPUT_UNREADABLE", "infrastructure", f"{label} 无法读取",
            severity="critical", actual=str(path), evidence={"error": str(exc)},
        )
        return None


def _empty_result(
    sample: Sample,
    candidate: Path,
    collector: IssueCollector,
    hashes: dict[str, Any],
    statistics: dict[str, Any],
) -> EvaluationResult:
    return EvaluationResult(
        evaluator_version=EVALUATOR_VERSION,
        sample=sample.key,
        candidate=str(candidate.resolve()),
        gold_xml=str(sample.gold_xml),
        docx=str(sample.docx),
        passed=False,
        issues=collector.issues,
        coverage={},
        provenance=ProvenanceReport(),
        hashes=hashes,
        statistics=statistics,
    )


def _gold_media_integrity(
    sample: Sample,
    root: etree._Element,
    store: GoldMediaStore,
    collector: IssueCollector,
) -> set[str]:
    referenced_members: set[str] = set()
    for element in media_elements(root):
        href = element.get(XLINK_HREF) or ""
        blob = store.resolve(href)
        if blob is None:
            collector.add(
                "GOLD_MEDIA_UNRESOLVED", "infrastructure",
                "金标准 XML 的媒体引用无法在 figures.zip 中唯一解析",
                severity="critical", gold_path=local_path(element), actual=href,
            )
        else:
            referenced_members.add(blob.logical_name)
    extras = sorted(set(store.by_member) - referenced_members)
    for member in extras:
        collector.add(
            "GOLD_MEDIA_UNREFERENCED", "infrastructure",
            "figures.zip 含金标准 XML 未引用的文件",
            severity="critical", actual=member,
        )
    return referenced_members


def _issue_statistics(collector: IssueCollector) -> dict[str, Any]:
    severity = Counter(issue.severity for issue in collector.issues)
    domains = Counter(issue.domain for issue in collector.issues)
    codes = Counter(issue.code for issue in collector.issues)
    return {
        "total": len(collector.issues),
        "by_severity": dict(sorted(severity.items())),
        "by_domain": dict(sorted(domains.items())),
        "by_code": dict(sorted(codes.items())),
    }


def _semantic_inventory(node) -> Counter[str]:
    counts: Counter[str] = Counter()

    def visit(current) -> None:
        counts["elements"] += 1
        counts["attributes"] += len(current.attrs)
        if current.text:
            counts["texts"] += 1
        if current.tail:
            counts["tails"] += 1
        attrs = dict(current.attrs)
        if "id" in attrs:
            counts["relations"] += 1
        if "rid" in attrs:
            counts["relations"] += 1
        if "xlink:href" in attrs and current.local_tag in {"graphic", "inline-graphic", "media"}:
            counts["media_links"] += 1
        counts["hierarchy_edges"] += len(current.children)
        counts["order_edges"] += max(0, len(current.children) - 1)
        for child in current.children:
            visit(child)

    visit(node)
    return counts


def _quality_vector(gold_doc, candidate_doc, matched: Counter[str]) -> dict[str, Any]:
    gold = _semantic_inventory(gold_doc.root)
    candidate = _semantic_inventory(candidate_doc.root)
    dimensions = {}
    for dimension in (
        "elements", "attributes", "texts", "tails", "relations", "media_links",
        "hierarchy_edges", "order_edges",
    ):
        matched_count = matched[f"matched_{dimension}"]
        recall = matched_count / gold[dimension] if gold[dimension] else 1.0
        precision = matched_count / candidate[dimension] if candidate[dimension] else (
            1.0 if not gold[dimension] else 0.0
        )
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        dimensions[dimension] = {
            "gold": gold[dimension],
            "candidate": candidate[dimension],
            "matched": matched_count,
            "precision": round(precision, 8),
            "recall": round(recall, 8),
            "f1": round(f1, 8),
            "exact": matched_count == gold[dimension] == candidate[dimension],
        }
    return {
        "dimensions": dimensions,
        "combined_score": None,
        "explanation": "各维度不可相互抵消；V2 不以主观权重合成单一分数。",
    }


def evaluate_sample(sample: str | Sample, candidate: str | Path) -> EvaluationResult:
    """只读评测一例候选输出。

    ``candidate`` 可以是主 XML 文件，也可以是包含唯一顶层 XML 和媒体的目录。
    函数不调用转换器，也不改动候选、金标准或 docx。
    """

    sample_obj = get_sample(sample) if isinstance(sample, str) else sample
    candidate_path = Path(candidate)
    collector = IssueCollector()
    package = CandidatePackage(candidate_path, collector)
    hashes: dict[str, Any] = {
        "evaluator": _evaluator_hashes(),
        "gold_xml_sha256": _input_hash(sample_obj.gold_xml, collector, "结构参考.xml"),
        "figures_zip_sha256": _input_hash(sample_obj.figures_zip, collector, "figures.zip"),
        "docx_sha256": _input_hash(sample_obj.docx, collector, "初始文件.docx"),
    }
    statistics: dict[str, Any] = {}

    if package.xml_path is None:
        statistics["issues"] = _issue_statistics(collector)
        return _empty_result(sample_obj, candidate_path, collector, hashes, statistics)

    try:
        candidate_xml_size = package.xml_path.stat().st_size
    except OSError as exc:
        collector.add(
            "CANDIDATE_XML_UNREADABLE", "package", "候选主 XML 无法读取",
            severity="critical", actual=str(package.xml_path), evidence={"error": str(exc)},
        )
        statistics["issues"] = _issue_statistics(collector)
        return _empty_result(sample_obj, candidate_path, collector, hashes, statistics)
    if candidate_xml_size > MAX_XML_BYTES:
        collector.add(
            "CANDIDATE_XML_TOO_LARGE", "package", "候选主 XML 超过 256 MiB 安全上限",
            severity="critical", actual=candidate_xml_size,
        )
        statistics["issues"] = _issue_statistics(collector)
        return _empty_result(sample_obj, candidate_path, collector, hashes, statistics)

    try:
        hashes["candidate_xml_sha256"] = sha256_file(package.xml_path)
    except OSError as exc:
        collector.add(
            "CANDIDATE_XML_UNREADABLE", "package", "候选主 XML 无法读取",
            severity="critical", actual=str(package.xml_path), evidence={"error": str(exc)},
        )
        statistics["issues"] = _issue_statistics(collector)
        return _empty_result(sample_obj, candidate_path, collector, hashes, statistics)
    try:
        candidate_tree = parse_xml(package.xml_path)
    except (OSError, etree.XMLSyntaxError) as exc:
        collector.add(
            "XML_NOT_WELL_FORMED", "validity", "候选 XML 不能安全解析",
            severity="critical", actual=str(package.xml_path), evidence={"error": str(exc)},
        )
        statistics["issues"] = _issue_statistics(collector)
        return _empty_result(sample_obj, candidate_path, collector, hashes, statistics)

    try:
        gold_tree = parse_xml(sample_obj.gold_xml)
    except (OSError, etree.XMLSyntaxError) as exc:
        collector.add(
            "GOLD_XML_UNREADABLE", "infrastructure", "金标准 XML 不能安全解析",
            severity="critical", actual=str(sample_obj.gold_xml), evidence={"error": str(exc)},
        )
        statistics["issues"] = _issue_statistics(collector)
        return _empty_result(sample_obj, candidate_path, collector, hashes, statistics)

    validity = validate_xml(package, candidate_tree, collector)
    candidate_blobs = inspect_candidate_media(package, candidate_tree.getroot(), collector)
    extra_files = check_extra_files(package, collector)
    hashes["candidate_media"] = {
        href: {"sha256": blob.sha256, "size": blob.size}
        for href, blob in sorted(candidate_blobs.items())
    }

    gold_media = GoldMediaStore(sample_obj.figures_zip)
    if gold_media.error:
        collector.add(
            "GOLD_MEDIA_ARCHIVE_UNREADABLE", "infrastructure", "figures.zip 无法读取",
            severity="critical", actual=str(sample_obj.figures_zip),
            evidence={"error": gold_media.error},
        )
    referenced_gold = _gold_media_integrity(
        sample_obj, gold_tree.getroot(), gold_media, collector
    )

    gold_doc = canonicalize(
        gold_tree.getroot(),
        lambda href: (blob.sha256 if (blob := gold_media.resolve(href)) else None),
        side="gold",
    )
    candidate_doc = canonicalize(
        candidate_tree.getroot(),
        lambda href: (blob.sha256 if (blob := candidate_blobs.get(href)) else None),
        side="candidate",
    )
    matched = compare_nodes(gold_doc.root, candidate_doc.root, collector)

    for side, coverage in (("gold", gold_doc.coverage), ("candidate", candidate_doc.coverage)):
        if not coverage.complete:
            collector.add(
                "COVERAGE_INCOMPLETE", "coverage", f"{side} 语义投影有未处理对象",
                severity="critical", evidence={"unhandled": coverage.unhandled},
            )

    provenance = audit_provenance(
        sample_obj.docx, candidate_tree.getroot(), candidate_blobs, collector
    )

    fatal = any(issue.severity in {"critical", "error"} for issue in collector.issues)
    statistics.update({
        "validity": validity,
        "issues": _issue_statistics(collector),
        "semantic_digest": {
            "gold": gold_doc.root.digest(),
            "candidate": candidate_doc.root.digest(),
        },
        "quality_vector": _quality_vector(gold_doc, candidate_doc, matched),
        "tag_counts": {
            "gold": dict(sorted(gold_doc.tag_counts.items())),
            "candidate": dict(sorted(candidate_doc.tag_counts.items())),
        },
        "attribute_counts": {
            "gold": dict(sorted(gold_doc.attr_counts.items())),
            "candidate": dict(sorted(candidate_doc.attr_counts.items())),
        },
        "strict_fallback_tags": {
            "gold": dict(sorted(gold_doc.fallback_tags.items())),
            "candidate": dict(sorted(candidate_doc.fallback_tags.items())),
        },
        "gold_media": {
            "archive_files": len(gold_media.by_member),
            "referenced_files": len(referenced_gold),
        },
        "candidate_unreferenced_files": extra_files,
    })
    return EvaluationResult(
        evaluator_version=EVALUATOR_VERSION,
        sample=sample_obj.key,
        candidate=str(candidate_path.resolve()),
        gold_xml=str(sample_obj.gold_xml),
        docx=str(sample_obj.docx),
        passed=not fatal and gold_doc.coverage.complete and candidate_doc.coverage.complete,
        issues=collector.issues,
        coverage={"gold": gold_doc.coverage, "candidate": candidate_doc.coverage},
        provenance=provenance,
        hashes=hashes,
        statistics=statistics,
    )
