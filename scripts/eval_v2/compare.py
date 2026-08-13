"""逐层比较两棵语义树；重复对象不会被字典覆盖。"""

from __future__ import annotations

from collections import defaultdict
from collections import Counter
import hashlib

from .canonical import CanonicalNode
from .models import IssueCollector


DOMAIN_PREFIX = {
    "document": "DOCUMENT",
    "metadata": "METADATA",
    "contributors": "CONTRIBUTOR",
    "abstracts": "ABSTRACT",
    "body": "BODY",
    "figures": "FIGURE",
    "tables": "TABLE",
    "formulas": "FORMULA",
    "references": "REFERENCE",
    "cross_references": "XREF",
    "back": "BACK",
    "inline_format": "INLINE",
    "other": "STRICT",
}


def _shown(value: str, limit: int = 300) -> tuple[str, dict[str, str]]:
    if len(value) <= limit:
        return value, {}
    return value[:limit] + "…", {
        "full_length": str(len(value)),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _code(node: CanonicalNode, kind: str) -> str:
    return f"{DOMAIN_PREFIX.get(node.domain, 'STRICT')}_{kind}"


def _issue(
    collector: IssueCollector,
    node: CanonicalNode,
    candidate: CanonicalNode | None,
    kind: str,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
    evidence: dict[str, str] | None = None,
) -> None:
    collector.add(
        _code(node, kind),
        node.domain,
        message,
        severity="error",
        gold_path=node.path,
        candidate_path=candidate.path if candidate else None,
        expected=expected,
        actual=actual,
        evidence=evidence or {},
    )


def _pair_children(
    gold: tuple[CanonicalNode, ...],
    candidate: tuple[CanonicalNode, ...],
) -> tuple[list[tuple[int, int]], list[int], list[int], bool]:
    """先按完整语义身份配对，再把内容有变化的同名元素按次序配对。"""

    candidate_by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, node in enumerate(candidate):
        candidate_by_key[(node.tag, node.match_key)].append(index)

    used_candidate: set[int] = set()
    paired_gold: set[int] = set()
    pairs: list[tuple[int, int]] = []
    exact_sequence: list[int] = []
    key_offsets: dict[tuple[str, str], int] = defaultdict(int)
    for gi, node in enumerate(gold):
        key = (node.tag, node.match_key)
        choices = candidate_by_key.get(key, [])
        offset = key_offsets[key]
        while offset < len(choices) and choices[offset] in used_candidate:
            offset += 1
        key_offsets[key] = offset + 1
        if offset < len(choices):
            ci = choices[offset]
            pairs.append((gi, ci))
            paired_gold.add(gi)
            used_candidate.add(ci)
            exact_sequence.append(ci)

    remaining_by_tag: dict[str, list[int]] = defaultdict(list)
    for ci, node in enumerate(candidate):
        if ci not in used_candidate:
            remaining_by_tag[node.tag].append(ci)
    tag_offsets: dict[str, int] = defaultdict(int)
    for gi, node in enumerate(gold):
        if gi in paired_gold:
            continue
        choices = remaining_by_tag.get(node.tag, [])
        offset = tag_offsets[node.tag]
        while offset < len(choices) and choices[offset] in used_candidate:
            offset += 1
        tag_offsets[node.tag] = offset + 1
        if offset < len(choices):
            ci = choices[offset]
            pairs.append((gi, ci))
            paired_gold.add(gi)
            used_candidate.add(ci)

    missing = [index for index in range(len(gold)) if index not in paired_gold]
    extra = [index for index in range(len(candidate)) if index not in used_candidate]
    reordered = any(a > b for a, b in zip(exact_sequence, exact_sequence[1:]))
    return sorted(pairs), missing, extra, reordered


def compare_nodes(
    gold: CanonicalNode,
    candidate: CanonicalNode,
    collector: IssueCollector,
    stats: Counter[str] | None = None,
) -> Counter[str]:
    if stats is None:
        stats = Counter()
    if gold.tag != candidate.tag:
        _issue(
            collector, gold, candidate, "ELEMENT_CHANGED", "元素名称不同。",
            expected=gold.tag, actual=candidate.tag,
        )
        return stats
    stats["matched_elements"] += 1

    gold_attrs = dict(gold.attrs)
    candidate_attrs = dict(candidate.attrs)
    for name in sorted(set(gold_attrs) | set(candidate_attrs)):
        expected = gold_attrs.get(name)
        actual = candidate_attrs.get(name)
        if expected == actual:
            if expected is not None:
                stats["matched_attributes"] += 1
                if name in {"id", "rid"}:
                    stats["matched_relations"] += 1
                if name == "xlink:href" and gold.local_tag in {"graphic", "inline-graphic", "media"}:
                    stats["matched_media_links"] += 1
            continue
        if name == "rid":
            kind, message = "TARGET_CHANGED", "交叉引用所指向的语义对象不同。"
        elif name == "id":
            kind, message = "ID_PRESENCE_CHANGED", "语义对象是否具有内部 ID 与金标准不同。"
        elif name == "xlink:href" and gold.local_tag in {"graphic", "inline-graphic", "media"}:
            kind, message = "MEDIA_BYTES_CHANGED", "媒体引用所对应的文件字节不同。"
        else:
            kind, message = "ATTRIBUTE_CHANGED", f"属性 {name} 的值不同。"
        _issue(
            collector, gold, candidate, kind, message,
            expected=expected, actual=actual, evidence={"attribute": name},
        )

    if gold.text != candidate.text:
        expected, expected_evidence = _shown(gold.text)
        actual, actual_evidence = _shown(candidate.text)
        _issue(
            collector, gold, candidate, "TEXT_CHANGED", "元素中的文本不同。",
            expected=expected,
            actual=actual,
            evidence={
                **{f"expected_{key}": value for key, value in expected_evidence.items()},
                **{f"actual_{key}": value for key, value in actual_evidence.items()},
            },
        )
    elif gold.text:
        stats["matched_texts"] += 1
    if gold.tail != candidate.tail:
        expected, expected_evidence = _shown(gold.tail)
        actual, actual_evidence = _shown(candidate.tail)
        _issue(
            collector, gold, candidate, "TAIL_TEXT_CHANGED", "内联元素之后的连续文本不同。",
            expected=expected,
            actual=actual,
            evidence={
                **{f"expected_{key}": value for key, value in expected_evidence.items()},
                **{f"actual_{key}": value for key, value in actual_evidence.items()},
            },
        )
    elif gold.tail:
        stats["matched_tails"] += 1

    pairs, missing, extra, reordered = _pair_children(gold.children, candidate.children)
    stats["matched_hierarchy_edges"] += len(pairs)
    candidate_index = {gi: ci for gi, ci in pairs}
    for gi in range(max(0, len(gold.children) - 1)):
        if gi in candidate_index and gi + 1 in candidate_index:
            if candidate_index[gi + 1] == candidate_index[gi] + 1:
                stats["matched_order_edges"] += 1
    if reordered:
        _issue(
            collector, gold, candidate, "CHILD_ORDER_CHANGED", "子元素的语义顺序不同。",
            expected=" | ".join(child.match_key for child in gold.children),
            actual=" | ".join(child.match_key for child in candidate.children),
        )
    for gi, ci in pairs:
        compare_nodes(gold.children[gi], candidate.children[ci], collector, stats)
    for gi in missing:
        child = gold.children[gi]
        _issue(
            collector, child, None, "ELEMENT_MISSING", "参考结构中的元素在候选结果中缺失。",
            expected=child.match_key,
        )
    for ci in extra:
        child = candidate.children[ci]
        collector.add(
            _code(child, "ELEMENT_EXTRA"),
            child.domain,
            "候选结果中出现参考结构没有的元素。",
            severity="error",
            gold_path=gold.path,
            candidate_path=child.path,
            actual=child.match_key,
        )
    return stats
