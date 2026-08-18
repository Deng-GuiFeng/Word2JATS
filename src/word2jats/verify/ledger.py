"""源覆盖账：按字符区间和对象出现记录每个源事实的去向。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from ..model.source import OBJECT_REPLACEMENT, SourceDocument, SourceText, TextRange


ALLOWED_DISCARD_REASONS = {
    "blank", "decorative", "ole_preview_superseded", "fallback_superseded",
    "ref_notation", "header_footer_template",
}


@dataclass(frozen=True)
class CoverageRecord:
    source_kind: str  # text | object
    source_id: str
    start: Optional[int]
    end: Optional[int]
    action: str  # consume | discard
    usage_id: str
    role: str
    reason: Optional[str] = None
    reuse_reason: Optional[str] = None


@dataclass(frozen=True)
class LedgerIssue:
    severity: str  # high | review_blocking | warning
    code: str
    source_id: str
    start: Optional[int]
    end: Optional[int]
    detail: str


@dataclass(frozen=True)
class LedgerReport:
    records: tuple[CoverageRecord, ...]
    issues: tuple[LedgerIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(
            item.severity in {"high", "review_blocking"} for item in self.issues
        )

    def to_dict(self) -> dict:
        return {
            "schema": "word2jats.source-coverage-ledger",
            "version": 1,
            "ok": self.ok,
            "records": [asdict(item) for item in self.records],
            "issues": [asdict(item) for item in self.issues],
        }


class SourceCoverageLedger:
    """
    以 SourceDocument 为唯一底账。

    同一地址可被多个语义实例合法复用，但每条额外用途都必须写明
    ``reuse_reason``；同一 ``usage_id`` 重复消费仍视为错误。
    """

    def __init__(self, source: SourceDocument):
        source.validate()
        self.source = source
        self.records: list[CoverageRecord] = []

    @staticmethod
    def _ranges(value: SourceText | TextRange | Iterable[TextRange]):
        if isinstance(value, SourceText):
            return value.ranges
        if isinstance(value, tuple) and len(value) == 3 and isinstance(value[0], str):
            return (value,)
        return tuple(value)

    def consume_text(self, value: SourceText | TextRange | Iterable[TextRange], *,
                     usage_id: str, role: str,
                     reuse_reason: Optional[str] = None) -> None:
        for node_id, start, end in self._ranges(value):
            self.source.slice_text((node_id, start, end))
            self.records.append(CoverageRecord(
                "text", node_id, start, end, "consume", usage_id, role,
                reuse_reason=reuse_reason,
            ))

    def discard_text(self, value: SourceText | TextRange | Iterable[TextRange], *,
                     usage_id: str, reason: str) -> None:
        for node_id, start, end in self._ranges(value):
            self.source.slice_text((node_id, start, end))
            self.records.append(CoverageRecord(
                "text", node_id, start, end, "discard", usage_id, "discard",
                reason=reason,
            ))

    def consume_object(self, occurrence_id: str, *, usage_id: str, role: str,
                       reuse_reason: Optional[str] = None) -> None:
        self.source.occurrence(occurrence_id)
        self.records.append(CoverageRecord(
            "object", occurrence_id, None, None, "consume", usage_id, role,
            reuse_reason=reuse_reason,
        ))

    def discard_object(self, occurrence_id: str, *, usage_id: str,
                       reason: str) -> None:
        self.source.occurrence(occurrence_id)
        self.records.append(CoverageRecord(
            "object", occurrence_id, None, None, "discard", usage_id,
            "discard", reason=reason,
        ))

    def audit(self) -> LedgerReport:
        issues: list[LedgerIssue] = []
        text_records = [item for item in self.records if item.source_kind == "text"]
        object_records = [item for item in self.records if item.source_kind == "object"]

        for record in self.records:
            if record.action == "discard" and record.reason not in ALLOWED_DISCARD_REASONS:
                issues.append(LedgerIssue(
                    "review_blocking", "DISCARD_REASON_NOT_ALLOWED",
                    record.source_id, record.start, record.end,
                    f"弃置理由不在白名单: {record.reason}",
                ))

        by_node: dict[str, list[CoverageRecord]] = {}
        for record in text_records:
            by_node.setdefault(record.source_id, []).append(record)
        for node in self.source.nodes:
            if not node.text:
                continue
            coverage = [[] for _ in node.text]
            for record in by_node.get(node.node_id, ()):
                assert record.start is not None and record.end is not None
                for index in range(record.start, record.end):
                    coverage[index].append(record)
            missing = [
                index for index, records in enumerate(coverage)
                if not records and node.text[index] != OBJECT_REPLACEMENT
            ]
            for start, end in _runs(missing):
                issues.append(LedgerIssue(
                    "high", "TEXT_UNCOVERED", node.node_id, start, end,
                    repr(node.text[start:end]),
                ))
            for index, records in enumerate(coverage):
                if len(records) < 2:
                    continue
                usage_ids = [item.usage_id for item in records]
                if len(set(usage_ids)) != len(usage_ids):
                    issues.append(LedgerIssue(
                        "high", "SAME_USAGE_DUPLICATED", node.node_id,
                        index, index + 1, f"重复用途: {usage_ids}",
                    ))
                elif any(item.action == "discard" for item in records):
                    issues.append(LedgerIssue(
                        "high", "CONSUME_DISCARD_CONFLICT", node.node_id,
                        index, index + 1, f"用途: {usage_ids}",
                    ))
                elif any(not item.reuse_reason for item in records[1:]):
                    issues.append(LedgerIssue(
                        "high", "REUSE_WITHOUT_BASIS", node.node_id,
                        index, index + 1, f"用途: {usage_ids}",
                    ))

        by_object: dict[str, list[CoverageRecord]] = {}
        for record in object_records:
            by_object.setdefault(record.source_id, []).append(record)
        for occurrence in self.source.occurrences:
            records = by_object.get(occurrence.occ_id, [])
            if not records:
                issues.append(LedgerIssue(
                    "high", "OBJECT_UNCOVERED", occurrence.occ_id,
                    None, None, occurrence.kind,
                ))
            elif len(records) > 1:
                usages = [item.usage_id for item in records]
                if len(set(usages)) != len(usages) or any(
                    not item.reuse_reason for item in records[1:]
                ):
                    issues.append(LedgerIssue(
                        "high", "OBJECT_DUPLICATED", occurrence.occ_id,
                        None, None, f"用途: {usages}",
                    ))
        return LedgerReport(tuple(self.records), tuple(issues))


def _runs(indices: list[int]):
    if not indices:
        return
    start = previous = indices[0]
    for index in indices[1:]:
        if index != previous + 1:
            yield start, previous + 1
            start = index
        previous = index
    yield start, previous + 1
