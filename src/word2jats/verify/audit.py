"""候选 XML 的独立结构、来源与源覆盖审计。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from html import unescape
from typing import Iterable

from lxml import etree

from ..model.source import OBJECT_REPLACEMENT, SourceDocument
from ..semantic.normalize import canonical_orcid
from .ledger import LedgerIssue, LedgerReport, SourceCoverageLedger
from .provenance import ProvenanceEntry


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    code: str
    detail: str


@dataclass(frozen=True)
class AuditReport:
    issues: tuple[AuditIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(item.severity in {"high", "review_blocking"}
                       for item in self.issues)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [asdict(item) for item in self.issues]}


def _parse(xml_bytes: bytes):
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    return etree.fromstring(xml_bytes, parser)


def audit_structure(xml_bytes: bytes) -> AuditReport:
    """独立检查全文 ID 唯一和所有内部引用闭合。"""
    issues = []
    try:
        root = _parse(xml_bytes)
    except etree.XMLSyntaxError as error:
        return AuditReport((AuditIssue("high", "XML_UNREADABLE", str(error)),))

    seen = {}
    for element in root.iter():
        value = element.get("id")
        if not value:
            continue
        if value in seen:
            issues.append(AuditIssue(
                "high", "DUPLICATE_ID",
                f"{value!r} 同时出现于 {seen[value]} 与 {root.getroottree().getpath(element)}",
            ))
        else:
            seen[value] = root.getroottree().getpath(element)
    for element in root.iter():
        for target in (element.get("rid") or "").split():
            if target not in seen:
                issues.append(AuditIssue(
                    "high", "DANGLING_RID",
                    f"{root.getroottree().getpath(element)} 指向不存在的 {target!r}",
                ))
    return AuditReport(tuple(issues))


_TRANSFORMS = {"mathml-tree", "omml-to-mathml", "orcid-uri", "numbering-restore", "xml-entity-decode", "word-layout-to-png"}


def audit_provenance(xml_bytes: bytes, entries: Iterable[ProvenanceEntry],
                     source: SourceDocument) -> AuditReport:
    """反向核对来源记录，并证明最终 XML 的每个可见字符都有来处。"""
    issues = []
    try:
        root = _parse(xml_bytes)
    except etree.XMLSyntaxError as error:
        return AuditReport((AuditIssue("high", "XML_UNREADABLE", str(error)),))
    tree = root.getroottree()
    elements = {tree.getpath(element): element for element in root.iter()}
    entries = tuple(entries)
    coverage: dict[tuple[str, str], list[bool]] = {}
    for path, element in elements.items():
        coverage[(path, "text")] = [False] * len(element.text or "")
        coverage[(path, "tail")] = [False] * len(element.tail or "")

    for index, entry in enumerate(entries):
        prefix = f"来源记录 {index + 1} ({entry.output_path})"
        element = elements.get(entry.output_path)
        if element is None:
            issues.append(AuditIssue("high", "PROVENANCE_PATH_MISSING", prefix))
            continue
        if entry.origin_kind not in {
            "source", "object", "config", "transform", "model",
        }:
            issues.append(AuditIssue(
                "high", "PROVENANCE_ORIGIN_UNKNOWN",
                f"{prefix}: {entry.origin_kind!r}",
            ))
        for source_range in entry.source_ranges:
            try:
                source.slice_text(source_range)
            except (KeyError, ValueError) as error:
                issues.append(AuditIssue(
                    "high", "PROVENANCE_SOURCE_RANGE_INVALID", f"{prefix}: {error}",
                ))
        if entry.source_object:
            try:
                source.occurrence(entry.source_object)
            except KeyError:
                issues.append(AuditIssue(
                    "high", "PROVENANCE_SOURCE_OBJECT_INVALID",
                    f"{prefix}: {entry.source_object}",
                ))
        if entry.origin_kind == "config" and (
            not entry.config_key or not entry.config_version
        ):
            issues.append(AuditIssue(
                "high", "CONFIG_PROVENANCE_INCOMPLETE",
                f"{prefix}: 配置键或配置版本缺失",
            ))
        if entry.origin_kind == "transform" and entry.transform not in _TRANSFORMS:
            issues.append(AuditIssue(
                "high", "TRANSFORM_NOT_ALLOWED",
                f"{prefix}: {entry.transform!r}",
            ))
        if entry.origin_kind == "transform" and entry.transform == "orcid-uri":
            source_value = "".join(
                source.slice_text(item) for item in entry.source_ranges
            )
            if (not entry.source_ranges
                    or canonical_orcid(source_value) != entry.value):
                issues.append(AuditIssue(
                    "high", "TRANSFORM_VALUE_INVALID",
                    f"{prefix}: ORCID 规范值与源值不符",
                ))
        if entry.origin_kind == "transform" and entry.transform == "word-layout-to-png":
            from ..semantic.source_layout import candidate_groups
            record = entry.derivation or {}
            group = tuple(record.get('nodes') or ())
            valid = (
                group in candidate_groups(source)
                and record.get('source_sha256') == source.metadata.get('source_sha256')
                and record.get('recipe') == 'word-formula-layout-v1'
                and re.fullmatch(r'[a-f0-9]{64}',str(record.get('png_sha256','')))
                and entry.value.endswith('/formula-'+str(record.get('png_sha256'))+'.png')
                and element.tag == 'graphic' and entry.target_kind == 'media'
            )
            if entry.source_object:
                valid = valid and any(entry.source_object == a.occ_id for node_id in group
                                      for a in source.node(node_id).objects)
            if not valid:
                issues.append(AuditIssue('high','TRANSFORM_VALUE_INVALID',
                    f'{prefix}: 公式排版图的源范围、文件摘要或变换记录不符'))
        if entry.origin_kind == "transform" and entry.transform == "xml-entity-decode":
            literal = "".join(source.slice_text(r) for r in entry.source_ranges)
            if not entry.source_ranges or unescape(literal) != entry.value:
                issues.append(AuditIssue("high", "TRANSFORM_VALUE_INVALID",
                    f"{prefix}: XML 实体解码结果与源值不符"))
        if entry.origin_kind == "transform" and entry.transform == "numbering-restore":
            node_ids = {item[0] for item in entry.source_ranges}
            rendered = None
            if len(node_ids) == 1:
                try:
                    rendered = (source.node(next(iter(node_ids))).properties
                                or {}).get("numbering_rendered")
                except KeyError:
                    rendered = None
            if rendered != entry.value:
                issues.append(AuditIssue(
                    "high", "TRANSFORM_VALUE_INVALID",
                    f"{prefix}: 还原编号与节点编号事实不符",
                ))
        if entry.target_kind in {"text", "tail"}:
            actual = element.text if entry.target_kind == "text" else element.tail
            actual = actual or ""
            if not isinstance(entry.start, int) or not isinstance(entry.end, int) \
                    or not 0 <= entry.start <= entry.end <= len(actual):
                issues.append(AuditIssue(
                    "high", "PROVENANCE_OUTPUT_RANGE_INVALID", prefix,
                ))
                continue
            if actual[entry.start:entry.end] != entry.value:
                issues.append(AuditIssue(
                    "high", "PROVENANCE_VALUE_MISMATCH",
                    f"{prefix}: 记录值与最终 XML 不同",
                ))
                continue
            if entry.origin_kind == "source" and entry.source_ranges:
                source_value = "".join(source.slice_text(item)
                                       for item in entry.source_ranges)
                if source_value != entry.value:
                    issues.append(AuditIssue(
                        "high", "SOURCE_TEXT_CHANGED",
                        f"{prefix}: 输出不是源区间逐字副本",
                    ))
            mask = coverage[(entry.output_path, entry.target_kind)]
            for position in range(entry.start, entry.end):
                mask[position] = True
        elif entry.target_kind in {"attribute", "media"}:
            actual = element.get(entry.target_name) if entry.target_name else None
            if actual != entry.value:
                issues.append(AuditIssue(
                    "high", "PROVENANCE_ATTRIBUTE_MISMATCH",
                    f"{prefix}: 属性记录与最终 XML 不同",
                ))
        elif entry.target_kind != "transform":
            issues.append(AuditIssue(
                "high", "PROVENANCE_TARGET_UNKNOWN",
                f"{prefix}: {entry.target_kind!r}",
            ))

    for (path, slot), mask in coverage.items():
        missing = [index for index, covered in enumerate(mask) if not covered]
        for start, end in _runs(missing):
            element = elements[path]
            value = element.text if slot == "text" else element.tail
            if not value[start:end].strip():
                # 纯空白不承载内容：元素间缩进属表现层，无需来源。
                continue
            issues.append(AuditIssue(
                "high", "OUTPUT_TEXT_WITHOUT_PROVENANCE",
                f"{path} {slot}[{start}:{end}]={value[start:end]!r}",
            ))
    return AuditReport(tuple(issues))


def _usage_role(entry: ProvenanceEntry) -> str:
    path = entry.output_path
    if entry.target_kind == "attribute":
        return "source-attribute"
    if "/address" in path or "/addr-line" in path or "/postal-code" in path:
        return "address"
    if "/aff" in path:
        return "affiliation"
    if "/corresp" in path:
        return "correspondence"
    if "/email" in path:
        return "email"
    return "content"


def _reuse_allowed(old_roles: set[str], new_role: str) -> bool:
    if new_role == "source-attribute" or "source-attribute" in old_roles:
        return True
    address_family = {"address", "affiliation", "correspondence", "email"}
    return new_role in address_family and old_roles <= address_family


def audit_source_coverage(source: SourceDocument,
                          entries: Iterable[ProvenanceEntry],
                          assignments: Iterable[dict] = (),
                          explicit_uses: Iterable[dict] = ()) -> LedgerReport:
    """从最终渲染来源记录反建源覆盖账，不采信“已分类”等于“已输出”。"""
    ledger = SourceCoverageLedger(source)
    occupied: dict[str, list[set[str]]] = {
        node.node_id: [set() for _ in node.text] for node in source.nodes
    }
    input_issues = []
    consumed_objects = set()
    for index, entry in enumerate(entries):
        role = _usage_role(entry)
        usage = f"{entry.output_path}:{entry.target_kind}:{entry.target_name}:{index}"
        for source_range in entry.source_ranges:
            node_id, start, end = source_range
            old_roles = set().union(*occupied[node_id][start:end]) if end > start else set()
            reuse = (
                "同一源地址用于多个经白名单许可的输出槽位"
                if old_roles and _reuse_allowed(old_roles, role) else None
            )
            ledger.consume_text(
                source_range, usage_id=usage, role=role, reuse_reason=reuse,
            )
            for position in range(start, end):
                occupied[node_id][position].add(role)
        if entry.source_object and entry.source_object not in consumed_objects:
            ledger.consume_object(
                entry.source_object, usage_id=usage,
                role=entry.transform or entry.target_kind,
            )
            consumed_objects.add(entry.source_object)

    allowed_roles = {
        "semantic-label", "list-notation", "layout-notation", "model-head",
        "citation-connector", "reference-xml-markup", "unfilled-publication-template",
    }
    for index, raw in enumerate(explicit_uses):
        if not isinstance(raw, dict):
            input_issues.append(LedgerIssue(
                "review_blocking", "SEMANTIC_USE_INVALID", str(index), None, None,
                "非输出源用途不是对象",
            ))
            continue
        node_id = raw.get("source_id")
        start, end = raw.get("start"), raw.get("end")
        usage_id, role = raw.get("usage_id"), raw.get("role")
        if (role not in allowed_roles or not isinstance(usage_id, str)
                or not usage_id or not isinstance(node_id, str)
                or not isinstance(start, int) or not isinstance(end, int)):
            input_issues.append(LedgerIssue(
                "review_blocking", "SEMANTIC_USE_INVALID", str(node_id),
                start if isinstance(start, int) else None,
                end if isinstance(end, int) else None,
                f"未许可的非输出源用途: role={role!r}, usage={usage_id!r}",
            ))
            continue
        try:
            source.slice_text((node_id, start, end))
        except (KeyError, ValueError) as error:
            input_issues.append(LedgerIssue(
                "review_blocking", "SEMANTIC_USE_INVALID", node_id, start, end,
                str(error),
            ))
            continue
        if role == "citation-connector":
            from ..semantic.normalize import is_citation_connector
            if not is_citation_connector(source.slice_text((node_id, start, end))):
                input_issues.append(LedgerIssue(
                    "high", "CITATION_CONNECTOR_HAS_CONTENT", node_id, start, end,
                    "著录连接记法中含有未输出的内容",
                ))
                continue
        if role == 'unfilled-publication-template':
            from ..semantic.templates import is_unfilled_publication_history
            node=source.node(node_id)
            if (start!=0 or end!=len(node.text) or node.objects
                    or node.part!='document' or node.parent is not None
                    or not is_unfilled_publication_history(node.text)):
                input_issues.append(LedgerIssue('high','PUBLICATION_TEMPLATE_HAS_CONTENT',
                    node_id,start,end,'出版日期模板中包含实际内容，不能作为空白模板略去'))
                continue
        if role == "reference-xml-markup" and not re.fullmatch(
                r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^<>]*)?/?>",
                source.slice_text((node_id,start,end))):
            input_issues.append(LedgerIssue("high", "REFERENCE_MARKUP_HAS_CONTENT",
                node_id,start,end,"XML 结构记号中含有未输出的正文"))
            continue
        old_roles = set().union(*occupied[node_id][start:end]) if end > start else set()
        ledger.consume_text(
            (node_id, start, end), usage_id=f"semantic:{usage_id}", role=role,
            reuse_reason=(
                "同一源记号同时承载可见文字与语义结构"
                if old_roles else None
            ),
        )
        for position in range(start, end):
            occupied[node_id][position].add(role)

    assignment_by_id = {
        item.get("source_id"): item
        for item in assignments if isinstance(item, dict)
    }
    roles = {
        source_id: item.get("role") for source_id, item in assignment_by_id.items()
    }
    discard_approved = {
        source_id for source_id, item in assignment_by_id.items()
        if "discard-review:approved" in (item.get("evidence") or [])
    }
    tag_shell = re.compile(r"<[^<>]+>")
    for node in source.nodes:
        allowed = [False] * len(node.text)
        reason = None
        if node.part.startswith(("header", "footer")):
            allowed = [True] * len(node.text)
            reason = "header_footer_template"
        elif not node.text.strip(OBJECT_REPLACEMENT).strip():
            allowed = [True] * len(node.text)
            reason = "blank"
        elif roles.get(node.node_id) == "reference-entry":
            for position, char in enumerate(node.text):
                if not char.isalnum() and char != OBJECT_REPLACEMENT:
                    allowed[position] = True
            for match in tag_shell.finditer(node.text):
                allowed[match.start():match.end()] = [True] * (match.end() - match.start())
            reason = "ref_notation"
        elif roles.get(node.node_id) == "table":
            # 压平表格中的制表符/软换行只承载二维版式。格子文字由
            # 源区间逐字输出；布局分隔符不应作为单元格正文写进 JATS。
            for position, char in enumerate(node.text):
                if char in {"\t", "\n"}:
                    allowed[position] = True
            for start, end in _runs([
                position for position, permit in enumerate(allowed)
                if permit and not occupied[node.node_id][position]
            ]):
                ledger.consume_text(
                    (node.node_id, start, end),
                    usage_id=f"layout:{node.node_id}:{start}:{end}",
                    role="layout-notation",
                )
                for position in range(start, end):
                    occupied[node.node_id][position].add("layout-notation")
            continue
        elif (roles.get(node.node_id) in {"blank", "decorative"}
              and node.node_id in discard_approved):
            allowed = [char != OBJECT_REPLACEMENT for char in node.text]
            reason = roles[node.node_id]
        discard = [
            position for position, permit in enumerate(allowed)
            if permit and not occupied[node.node_id][position]
            and node.text[position] != OBJECT_REPLACEMENT
        ]
        for start, end in _runs(discard):
            ledger.discard_text(
                (node.node_id, start, end),
                usage_id=f"discard:{node.node_id}:{start}:{end}", reason=reason,
            )

    for occurrence in source.occurrences:
        if occurrence.occ_id in consumed_objects:
            continue
        node = source.node(occurrence.node_id)
        if node.part.startswith(("header", "footer")):
            ledger.discard_object(
                occurrence.occ_id, usage_id=f"discard:{occurrence.occ_id}",
                reason="header_footer_template",
            )
        elif occurrence.representation_role == "fallback":
            ledger.discard_object(
                occurrence.occ_id, usage_id=f"discard:{occurrence.occ_id}",
                reason="fallback_superseded",
            )
        elif (roles.get(occurrence.occ_id) == "decorative"
              and occurrence.occ_id in discard_approved):
            ledger.discard_object(
                occurrence.occ_id, usage_id=f"discard:{occurrence.occ_id}",
                reason="decorative",
            )
    report = ledger.audit()
    if not input_issues:
        return report
    return LedgerReport(report.records, tuple(input_issues) + report.issues)


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
