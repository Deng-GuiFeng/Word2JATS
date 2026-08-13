"""V2 的稳定数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}


@dataclass(frozen=True)
class Issue:
    """一条可审计问题。

    ``gold_path`` 和 ``candidate_path`` 使用不依赖命名空间前缀的本地名路径。
    ``expected`` / ``actual`` 只放适合报告展示的短值；完整证据可放 ``evidence``。
    """

    code: str
    domain: str
    severity: str
    message: str
    gold_path: Optional[str] = None
    candidate_path: Optional[str] = None
    expected: Any = None
    actual: Any = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def key(self) -> Tuple[Any, ...]:
        return (
            self.code,
            self.domain,
            self.severity,
            self.message,
            self.gold_path,
            self.candidate_path,
            repr(self.expected),
            repr(self.actual),
            repr(self.evidence),
        )


class IssueCollector:
    """稳定去重的问题收集器。"""

    def __init__(self) -> None:
        self._issues: List[Issue] = []
        self._keys = set()

    def add(
        self,
        code: str,
        domain: str,
        message: str,
        *,
        severity: str = "error",
        gold_path: Optional[str] = None,
        candidate_path: Optional[str] = None,
        expected: Any = None,
        actual: Any = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Issue:
        if severity not in SEVERITY_ORDER:
            raise ValueError("未知严重级别：%s" % severity)
        issue = Issue(
            code=code,
            domain=domain,
            severity=severity,
            message=message,
            gold_path=gold_path,
            candidate_path=candidate_path,
            expected=expected,
            actual=actual,
            evidence=evidence or {},
        )
        if issue.key() not in self._keys:
            self._keys.add(issue.key())
            self._issues.append(issue)
        return issue

    def extend(self, issues: Iterable[Issue]) -> None:
        for issue in issues:
            if issue.key() not in self._keys:
                self._keys.add(issue.key())
                self._issues.append(issue)

    @property
    def issues(self) -> List[Issue]:
        return sorted(
            self._issues,
            key=lambda x: (
                SEVERITY_ORDER[x.severity],
                x.domain,
                x.gold_path or "",
                x.candidate_path or "",
                x.code,
            ),
        )


@dataclass
class CoverageReport:
    side: str
    elements: int = 0
    attributes: int = 0
    text_nodes: int = 0
    tails: int = 0
    relations: int = 0
    media_links: int = 0
    comments_ignored: int = 0
    formatting_whitespace_ignored: int = 0
    unhandled: List[Dict[str, str]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unhandled


@dataclass
class ProvenanceReport:
    source_parts: int = 0
    source_paragraphs: int = 0
    source_token_kinds: int = 0
    candidate_token_kinds: int = 0
    novel_tokens: List[Dict[str, Any]] = field(default_factory=list)
    excess_tokens: List[Dict[str, Any]] = field(default_factory=list)
    missing_source_tokens: List[Dict[str, Any]] = field(default_factory=list)
    allowed_generated_regions: Dict[str, int] = field(default_factory=dict)
    referenced_media: int = 0
    media_from_docx: int = 0
    media_not_from_docx: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EvaluationResult:
    evaluator_version: str
    sample: str
    candidate: str
    gold_xml: str
    docx: str
    passed: bool
    issues: List[Issue]
    coverage: Dict[str, CoverageReport]
    provenance: ProvenanceReport
    hashes: Dict[str, Any]
    statistics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
