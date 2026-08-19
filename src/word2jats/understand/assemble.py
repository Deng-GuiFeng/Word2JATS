"""把已落锚的语义指针组装为 SemanticDoc v2。

本模块不接受模型自由文字。任何可见字段均须先通过
``ground.py`` 变成 SourceText；落锚失败时只能结构降级或报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Iterable, Optional

from ..model.source import OBJECT_REPLACEMENT, SourceDocument, SourceText, TextRange
from ..semantic import model as sm
from ..semantic.normalize import canonical_orcid
from .ground import (
    GroundRequest, find_candidates, find_context_candidates, ground,
    ground_context, ground_joint,
    ground_ordered,
)
from .math import occurrence_math
from .merge import DocumentAssignment, MergeIssue, ReferenceSpan
from .serialize import SerializedDocument
from .xrefs import link_bibliographic_citations


@dataclass(frozen=True)
class AssemblyResult:
    document: sm.SemanticDoc
    issues: tuple[MergeIssue, ...]
    source_uses: tuple["SemanticSourceUse", ...] = ()


@dataclass(frozen=True)
class SemanticSourceUse:
    """语义结构消耗了源文字，但该文字不作为可见文字重复输出。"""

    source_id: str
    start: int
    end: int
    usage_id: str
    role: str


def _hint(source: SourceDocument, raw) -> Optional[str]:
    if isinstance(raw, str) and raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if isinstance(raw, str) and raw.endswith("|表") and raw[:-2] in source._nodes:
        return raw[:-2]
    if isinstance(raw, str) and raw in source._nodes:
        return raw
    if isinstance(raw, str):
        head, dot, tail = raw.rpartition(".")
        if dot and tail.isdigit() and head in source._nodes:
            return head
    return None


def _quote_parts(raw):
    if isinstance(raw, str):
        return raw, None
    if not isinstance(raw, dict):
        return None, None
    # 部分模型会把同一指针对象的文本键写成 text。这只是
    # 协议别名；仍须依 node_hint 回源唯一落锚，不直接采信文字。
    value = raw.get("quote") if "quote" in raw else raw.get("text")
    return (value if isinstance(value, str) else None), raw.get("node_hint")


def _quote_context(raw):
    if not isinstance(raw, dict):
        return "", ""
    left = raw.get("left_context")
    right = raw.get("right_context")
    return (
        left if isinstance(left, str) else "",
        right if isinstance(right, str) else "",
    )


def _range_inside(inner: TextRange, outer: Optional[TextRange]) -> bool:
    """只约束摘抄本身；用于消歧的相邻上下文可以位于容器外。"""
    return outer is None or (
        inner[0] == outer[0] and outer[1] <= inner[1] <= inner[2] <= outer[2]
    )


def _source_quote(source: SourceDocument, raw) -> Optional[SourceText]:
    quote, raw_hint = _quote_parts(raw)
    if not quote:
        return None
    resolved_hint = _hint(source, raw_hint)
    if raw_hint is not None and resolved_hint is None:
        return None
    explicit_object = bool(re.search(r"⟦(?:图|公式|对象)#o\d+⟧", quote))
    quote = re.sub(r"⟦(?:图|公式|对象)#o\d+⟧", OBJECT_REPLACEMENT, quote)
    left, right = _quote_context(raw)
    match = ground_context(
        quote, source, left_context=left, right_context=right,
        block_hint=resolved_hint, allow_object=explicit_object,
    )
    return SourceText((match,)) if match else None


def _source_quote_in(source: SourceDocument, raw,
                     scope: Optional[TextRange]) -> Optional[SourceText]:
    quote, raw_hint = _quote_parts(raw)
    if not quote:
        return None
    resolved_hint = _hint(source, raw_hint)
    if raw_hint is not None and resolved_hint is None:
        return None
    explicit_object = bool(re.search(r"⟦(?:图|公式|对象)#o\d+⟧", quote))
    quote = re.sub(r"⟦(?:图|公式|对象)#o\d+⟧", OBJECT_REPLACEMENT, quote)
    left, right = _quote_context(raw)
    match = ground_context(
        quote, source, left_context=left, right_context=right,
        scope=scope, block_hint=resolved_hint, allow_object=explicit_object,
    )
    return SourceText((match,)) if match else None


def _source_quote_scopes(source: SourceDocument, raw,
                         scopes: Iterable[TextRange]) -> Optional[SourceText]:
    """在一组已指明的语义容器中要求摘抄唯一。"""
    quote, raw_hint = _quote_parts(raw)
    if not quote:
        return None
    explicit_object = bool(re.search(r"⟦(?:图|公式|对象)#o\d+⟧", quote))
    quote = re.sub(r"⟦(?:图|公式|对象)#o\d+⟧", OBJECT_REPLACEMENT, quote)
    hinted = _hint(source, raw_hint)
    if raw_hint is not None and hinted is None:
        return None
    left, right = _quote_context(raw)
    scopes = tuple(scopes)
    matches = find_context_candidates(
        quote, source, left_context=left, right_context=right,
        block_hint=hinted, allow_object=explicit_object,
    )
    matches = sorted({
        match for match in matches
        if any(_range_inside(match, scope) for scope in scopes)
    })
    return SourceText((matches[0],)) if len(matches) == 1 else None


def _rich_quote(source: SourceDocument, raw) -> Optional[sm.RichText]:
    value = _source_quote(source, raw)
    return sm.RichText.from_source(value) if value else None


def _index_runs(indices):
    indices = list(indices)
    if not indices:
        return
    start = previous = indices[0]
    for index in indices[1:]:
        if index != previous + 1:
            yield start, previous + 1
            start = index
        previous = index
    yield start, previous + 1


class _Assembler:
    def __init__(self, source: SourceDocument, view: SerializedDocument,
                 front: dict, body: dict, references: tuple[ReferenceSpan, ...],
                 fields: list[dict], assignment: DocumentAssignment):
        self.source = source
        self.view = view
        self.front = front
        self.body_json = body
        self.reference_spans = references
        self.reference_fields = fields
        self.assignment = assignment
        self.issues = list(assignment.issues)
        self.inline_formulas: dict[str, sm.Formula] = {}
        self._formula_number = 0
        self._paragraph_number = 0
        self.object_roles = {
            item.source_id: item.role for item in assignment.assignments
            if item.source_kind == "object"
        }
        self.node_roles = {
            item.source_id: item.role for item in assignment.assignments
            if item.source_kind == "node"
        }
        self._reported_front_rejections = set()
        self.source_uses = []

    def issue(self, severity, code, source_id, detail):
        self.issues.append(MergeIssue(severity, code, source_id, detail))

    def _record_semantic_use(self, value: SourceText, usage_id: str,
                             role: str) -> None:
        for node_id, start, end in value.ranges:
            if start < end:
                self.source_uses.append(SemanticSourceUse(
                    node_id, start, end, usage_id, role
                ))

    def _record_gaps(self, containers: Iterable[TextRange],
                     covered: Iterable[Optional[SourceText]], *,
                     usage_id: str, role: str, punctuation_only: bool = False) -> None:
        """记录已指明语义容器中未作为可见字段输出的结构记号。"""
        by_node = {}
        for value in covered:
            if value:
                for node_id, start, end in value.ranges:
                    by_node.setdefault(node_id, []).append((start, end))
        for number, (node_id, start, end) in enumerate(containers, 1):
            occupied = [False] * (end - start)
            for child_start, child_end in by_node.get(node_id, ()):
                left = max(start, child_start)
                right = min(end, child_end)
                for position in range(left, right):
                    occupied[position - start] = True
            candidates = []
            text = self.source.node(node_id).text
            for position in range(start, end):
                if occupied[position - start] or text[position] == OBJECT_REPLACEMENT:
                    continue
                if punctuation_only and text[position].isalnum():
                    continue
                candidates.append(position)
            for gap_start, gap_end in _index_runs(candidates):
                self._record_semantic_use(
                    SourceText(((node_id, gap_start, gap_end),)),
                    f"{usage_id}:{number}:{gap_start}:{gap_end}", role,
                )

    def _formula(self, occurrence_id: str, *, display: bool,
                 label: Optional[sm.RichText] = None) -> sm.Formula:
        old = self.inline_formulas.get(occurrence_id)
        if old is not None:
            return old
        self._formula_number += 1
        occurrence = self.source.occurrence(occurrence_id)
        entity_id = f"formula:{self._formula_number}"
        if occurrence.kind == "omml":
            try:
                value = sm.Formula(
                    entity_id, "mathml",
                    math=occurrence_math(self.source, occurrence_id, display=display),
                    omml_occurrence=occurrence_id, label=label, display=display,
                )
            except (ValueError, TypeError) as error:
                self.issue("high", "OMML_CONVERSION_FAILED", occurrence_id, str(error))
                raise
        else:
            value = sm.Formula(
                entity_id, "image", image_occurrence=occurrence_id,
                label=label, display=display,
            )
        self.inline_formulas[occurrence_id] = value
        return value

    def rich(self, ranges: Iterable[TextRange]) -> sm.RichText:
        parts = []
        for node_id, start, end in ranges:
            node = self.source.node(node_id)
            anchors = [item for item in node.objects if start <= item.pos < end]
            cursor = start
            for anchor in sorted(anchors, key=lambda item: item.pos):
                if anchor.pos > cursor:
                    parts.append(sm.Text(SourceText(((node_id, cursor, anchor.pos),))))
                role = self.object_roles.get(anchor.occ_id)
                occurrence = self.source.occurrence(anchor.occ_id)
                if role in {"inline-formula", "ole-formula"} or (
                    occurrence.kind == "omml" and role not in {"display-formula"}
                ):
                    formula = self._formula(anchor.occ_id, display=False)
                    parts.append(sm.InlineFormula(formula.entity_id))
                elif role == "inline-graphic":
                    parts.append(sm.InlineGraphic(anchor.occ_id))
                elif role not in {"figure", "table-image", "display-formula",
                                  "preview-superseded", "fallback-superseded",
                                  "decorative"}:
                    self.issue(
                        "review_blocking", "INLINE_OBJECT_ROLE_UNRESOLVED", anchor.occ_id,
                        f"行内容器中的对象角色为 {role!r}",
                    )
                cursor = anchor.pos + 1
            if cursor < end:
                parts.append(sm.Text(SourceText(((node_id, cursor, end),))))
        return sm.RichText(tuple(parts))

    def rich_node(self, node_id: str) -> sm.RichText:
        node = self.source.node(node_id)
        return self.rich(((node_id, 0, len(node.text)),))

    def rich_source(self, value: SourceText) -> sm.RichText:
        return self.rich(value.ranges)

    def paragraph(self, node_id: str) -> sm.Paragraph:
        self._paragraph_number += 1
        return sm.Paragraph(None, self.rich_node(node_id))

    def _front_source(self, value: Optional[SourceText], purpose: str):
        """前置区指针只有在全局归并把其节点判为 front 时才生效。"""
        if value is None:
            return None
        rejected = sorted({
            node_id for node_id, _, _ in value.ranges
            if self.node_roles.get(node_id) != "front"
        })
        if not rejected:
            return value
        key = (purpose, tuple(rejected))
        if key not in self._reported_front_rejections:
            self._reported_front_rejections.add(key)
            self.issue(
                "warning", "FRONT_POINTER_NOT_SELECTED", ",".join(rejected),
                f"{purpose} 指针未获全局 front 主角色，组装时忽略",
            )
        return None

    def _front_quote(self, raw, purpose: str):
        return self._front_source(_source_quote(self.source, raw), purpose)

    def _front_quote_in(self, raw, scope, purpose: str):
        return self._front_source(
            _source_quote_in(self.source, raw, scope), purpose
        )

    def _front_quote_scopes(self, raw, scopes, purpose: str):
        return self._front_source(
            _source_quote_scopes(self.source, raw, scopes), purpose
        )

    def _front_rich(self, raw, purpose: str):
        value = self._front_quote(raw, purpose)
        return self.rich_source(value) if value else None

    # ------------------------------------------------------------------
    # front
    # ------------------------------------------------------------------
    def _title(self):
        ranges = []
        for raw in self.front.get("title_quotes") or []:
            value = self._front_quote(raw, "article-title")
            if value:
                ranges.extend(value.ranges)
        if ranges:
            return self.rich(ranges)
        # 关键字段失败时只允许退到模型指明的整源节点。
        first = (self.front.get("title_quotes") or [{}])[0]
        node_id = _hint(self.source, first.get("node_hint") if isinstance(first, dict) else None)
        if node_id and self.node_roles.get(node_id) == "front":
            self.issue("warning", "TITLE_QUOTE_FALLBACK", node_id, "标题摘抄未唯一落锚，退整节点")
            return self.rich_node(node_id)
        self.issue("review_blocking", "TITLE_UNRESOLVED", "front", "标题无可用源节点")
        return None

    def _relations_from(self, source_id: str, kind: Optional[str] = None):
        """读取模型已经判定的关系；不根据显示文字或位置补关系。"""
        for relation in self.front.get("relations") or []:
            if not isinstance(relation, dict) or relation.get("source_id") != source_id:
                continue
            if kind is None or relation.get("kind") == kind:
                yield relation

    def _addresses(self):
        values = []
        raw_to_semantic = {}
        for index, raw in enumerate(self.front.get("addresses") or []):
            if not isinstance(raw, dict):
                continue
            raw_id = raw.get("entity_id")
            semantic_id = f"address:{index + 1}"
            source_nodes = []
            for hint in raw.get("source_nodes") or []:
                node_id = _hint(self.source, hint)
                if node_id and node_id not in source_nodes:
                    source_nodes.append(node_id)
            for item in [
                *(raw.get("line_quotes") or []), raw.get("postal_label_quote"),
                raw.get("postal_quote"), raw.get("phone_label_quote"),
                raw.get("phone_quote"),
            ]:
                _, hint = _quote_parts(item)
                node_id = _hint(self.source, hint)
                if node_id and node_id not in source_nodes:
                    source_nodes.append(node_id)
            scopes = tuple(
                (node_id, 0, len(self.source.node(node_id).text))
                for node_id in source_nodes
            )

            line_sources = []
            lines = []
            for item in raw.get("line_quotes") or []:
                value = (
                    self._front_quote_scopes(item, scopes, f"{semantic_id}:line")
                    if scopes else self._front_quote(item, f"{semantic_id}:line")
                )
                if value:
                    line_sources.append(value)
                    lines.append(self.rich_source(value))

            def scoped(field):
                return (
                    self._front_quote_scopes(raw.get(field), scopes, f"{semantic_id}:{field}")
                    if scopes else self._front_quote(raw.get(field), f"{semantic_id}:{field}")
                )

            postal = scoped("postal_quote")
            phone = scoped("phone_quote")
            postal_label = scoped("postal_label_quote")
            phone_label = scoped("phone_label_quote")
            for name, label in (("postal", postal_label), ("phone", phone_label)):
                if label:
                    self._record_semantic_use(
                        label, f"{semantic_id}:{name}-label", "semantic-label"
                    )
            self._record_gaps(
                scopes, [*line_sources, postal_label, postal, phone_label, phone],
                usage_id=f"{semantic_id}:notation", role="list-notation",
                punctuation_only=True,
            )
            if not (lines or postal or phone):
                self.issue(
                    "review_blocking", "ADDRESS_UNRESOLVED", semantic_id,
                    "地址实体没有任何可唯一落锚的源文",
                )
                continue
            values.append(sm.Address(semantic_id, tuple(lines), postal, phone))
            if isinstance(raw_id, str) and raw_id:
                raw_to_semantic[raw_id] = semantic_id
        return tuple(values), raw_to_semantic

    def _affiliations(self, address_ids):
        values = []
        raw_to_semantic = {}
        for index, raw in enumerate(self.front.get("affiliations") or []):
            if not isinstance(raw, dict):
                continue
            raw_id = raw.get("entity_id")
            semantic_id = f"affiliation:{index + 1}"
            label_source = self._front_quote(
                raw.get("label_quote"), f"{semantic_id}:label"
            )
            label = self.rich_source(label_source) if label_source else None
            contents = []
            for item in raw.get("content_quotes") or []:
                value = self._front_quote(item, f"{semantic_id}:content")
                if value:
                    contents.extend(value.ranges)
            if not contents:
                self.issue(
                    "review_blocking", "AFFILIATION_UNRESOLVED", semantic_id,
                    "单位没有可唯一落锚的源文",
                )
                continue

            linked_addresses = []
            if isinstance(raw_id, str):
                for relation in self._relations_from(raw_id, "affiliation-address"):
                    target = address_ids.get(relation.get("target_id"))
                    if target and target not in linked_addresses:
                        linked_addresses.append(target)
                    elif not target:
                        self.issue(
                            "review_blocking", "AFFILIATION_ADDRESS_UNRESOLVED",
                            semantic_id, "单位关系指向了未落锚的地址实体",
                        )
            values.append(sm.Affiliation(
                semantic_id, label, self.rich(contents), tuple(linked_addresses)
            ))
            if isinstance(raw_id, str) and raw_id:
                raw_to_semantic[raw_id] = semantic_id
        return tuple(values), raw_to_semantic

    def _correspondence(self):
        values = []
        raw_to_semantic = {}
        for index, raw in enumerate(self.front.get("correspondences") or []):
            if not isinstance(raw, dict):
                continue
            raw_id = raw.get("entity_id")
            semantic_id = f"correspondence:{index + 1}"
            parts = []
            for item in raw.get("content_quotes") or []:
                value = self._front_quote(item, f"{semantic_id}:content")
                if not value:
                    continue
                # Publishing 1.3 的 corresp 是行内混合内容，不允许用 p
                # 表示 Word 物理段边界，本地 DTD 也不接受 break。多段
                # 逻辑通讯仍为同一实体；各源片段直接顺序投影，
                # 不凭空加入分隔符。
                parts.extend(self.rich_source(value).parts)
            if not parts:
                self.issue(
                    "review_blocking", "CORRESPONDENCE_UNRESOLVED", semantic_id,
                    "通讯实体没有任何可唯一落锚的源文",
                )
                continue
            values.append(sm.Correspondence(semantic_id, sm.RichText(tuple(parts))))
            if isinstance(raw_id, str) and raw_id:
                raw_to_semantic[raw_id] = semantic_id
        return tuple(values), raw_to_semantic

    def _contributor_notes(self):
        values = []
        raw_to_semantic = {}
        for index, raw in enumerate(self.front.get("contributor_notes") or []):
            if not isinstance(raw, dict):
                continue
            raw_id = raw.get("entity_id")
            semantic_id = f"contributor-note:{index + 1}"
            marker = self._front_quote(
                raw.get("marker_quote"), f"{semantic_id}:note-marker"
            )
            paragraphs = tuple(filter(None, (
                self._front_rich(item, f"{semantic_id}:paragraph")
                for item in raw.get("paragraph_quotes") or []
            )))
            if not marker or not paragraphs:
                self.issue(
                    "review_blocking", "CONTRIBUTOR_NOTE_UNRESOLVED", semantic_id,
                    "作者附注的正文或注释端标记无法唯一落锚",
                )
                continue
            self._record_semantic_use(
                marker, f"{semantic_id}:note-marker", "semantic-label"
            )
            values.append(sm.Note(
                semantic_id,
                "equal" if raw.get("kind") == "equal" else None,
                None, paragraphs, "contrib-group",
            ))
            if isinstance(raw_id, str) and raw_id:
                raw_to_semantic[raw_id] = semantic_id
        return tuple(values), raw_to_semantic

    def _marker_reference(self, marker_raw, target: str,
                          scope: Optional[TextRange], *, ref_type: str):
        if marker_raw is None:
            return None
        source = self._front_quote_in(
            marker_raw, scope, f"contributor-marker:{ref_type}"
        )
        if not source or len(source.ranges) != 1:
            return None
        return sm.CrossReference(
            ref_type, (target,), self.rich_source(source), source.ranges[0]
        )

    def _contributors(self, affiliation_ids, address_ids,
                      correspondence_ids, note_ids):
        values = []
        raw_to_semantic = {}
        author_wholes = []
        authors = tuple(self.front.get("authors") or [])

        # 作者名单是确有源顺序的序列。这里只联合落锚模型给出的作者整体摘抄，
        # 不根据姓名形态、标点或角标猜作者边界。
        anchor_requests = []
        anchor_scopes = []
        for index, raw in enumerate(authors):
            quote_text, raw_hint = _quote_parts(raw.get("author_quote"))
            node_id = _hint(self.source, raw_hint)
            if quote_text and node_id:
                anchor_requests.append(GroundRequest(f"author:{index}", quote_text))
                scope = (node_id, 0, len(self.source.node(node_id).text))
                if scope not in anchor_scopes:
                    anchor_scopes.append(scope)
        anchored = ground_ordered(
            anchor_requests, self.source, scopes=anchor_scopes
        ) if len(anchor_requests) == len(authors) and authors else None

        def author_scope(index):
            if anchored:
                return anchored[f"author:{index}"]
            whole = self._front_quote(
                authors[index].get("author_quote"), f"author:{index + 1}:whole"
            )
            return whole.ranges[0] if whole and len(whole.ranges) == 1 else None

        target_maps = {
            "author-affiliation": ("aff", affiliation_ids, True),
            "author-correspondence": ("corresp", correspondence_ids, True),
            "author-address": ("address", address_ids, False),
            "author-note": ("fn", note_ids, True),
        }

        for index, raw in enumerate(authors):
            if not isinstance(raw, dict):
                continue
            raw_id = raw.get("entity_id")
            semantic_id = f"contributor:{index + 1}"
            author_source = self._front_quote(
                raw.get("author_quote"), f"{semantic_id}:whole"
            )
            if author_source:
                author_wholes.append(author_source)
            scope = author_scope(index)
            surname = self._front_quote_in(
                raw.get("surname_quote"), scope, f"{semantic_id}:surname"
            )
            given = self._front_quote_in(
                raw.get("given_quote"), scope, f"{semantic_id}:given"
            )
            if not surname or not given:
                self.issue(
                    "review_blocking", "AUTHOR_NAME_UNRESOLVED", semantic_id,
                    "姓或名无法在该作者摘抄中唯一落锚",
                )
                continue
            suffix = self._front_quote_in(
                raw.get("suffix_quote"), scope, f"{semantic_id}:suffix"
            )
            degrees = tuple(filter(None, (
                self._front_quote(item, f"{semantic_id}:degree")
                for item in raw.get("degree_quotes") or []
            )))
            emails = tuple(filter(None, (
                self._front_quote(item, f"{semantic_id}:email")
                for item in raw.get("email_quotes") or []
            )))
            orcid = self._front_quote(raw.get("orcid_quote"), f"{semantic_id}:orcid")
            identifiers = ()
            if orcid:
                source_value = orcid.text(self.source)
                normalized = canonical_orcid(source_value)
                if normalized is None:
                    self.issue(
                        "review_blocking", "ORCID_INVALID", semantic_id,
                        "ORCID 不是完整且校验码正确的标准标识符",
                    )
                    identifier_value = self.rich_source(orcid)
                elif normalized == source_value:
                    identifier_value = self.rich_source(orcid)
                else:
                    identifier_value = sm.RichText((sm.TransformedText(
                        orcid, normalized, "orcid-uri",
                    ),))
                identifiers = (sm.ContributorIdentifier(
                    "orcid", identifier_value,
                ),)

            affiliations = []
            addresses = []
            references = []
            corresponding = False
            if isinstance(raw_id, str):
                for relation in self._relations_from(raw_id):
                    kind = relation.get("kind")
                    spec = target_maps.get(kind)
                    if spec is None:
                        continue
                    ref_type, target_map, emits_reference = spec
                    target = target_map.get(relation.get("target_id"))
                    if not target:
                        self.issue(
                            "review_blocking", "CONTRIBUTOR_RELATION_UNRESOLVED",
                            semantic_id, f"{kind} 指向了未落锚的目标实体",
                        )
                        continue
                    if kind == "author-affiliation" and target not in affiliations:
                        affiliations.append(target)
                    elif kind == "author-address" and target not in addresses:
                        addresses.append(target)
                    elif kind == "author-correspondence":
                        corresponding = True
                    if emits_reference:
                        marker = self._marker_reference(
                            relation.get("marker_quote"), target, scope,
                            ref_type=ref_type,
                        )
                        references.append(marker or sm.CrossReference(
                            ref_type, (target,), sm.RichText(), None,
                        ))

            comments = []
            comment_sources = []
            for item in raw.get("author_comment_quotes") or []:
                value = self._front_quote_in(item, scope, f"{semantic_id}:comment")
                if value:
                    comment_sources.append(value)
                    comments.append(sm.Paragraph(None, self.rich_source(value)))

            child_order = [f"identifier:{i}" for i in range(len(identifiers))]
            child_order.append("name")
            child_order += [f"degrees:{i}" for i in range(len(degrees))]
            child_order += [f"reference:{i}" for i in range(len(references))]
            child_order += [f"email:{i}" for i in range(len(emails))]
            child_order += [f"address:{i}" for i in range(len(addresses))]
            child_order += [f"author-comment:{i}" for i in range(len(comments))]
            values.append(sm.Contributor(
                semantic_id, "author", sm.PersonName(surname, given, suffix),
                degrees=degrees, identifiers=identifiers,
                affiliation_ids=tuple(affiliations), address_ids=tuple(addresses),
                references=tuple(references), emails=emails,
                author_comments=tuple(comments), corresponding=corresponding,
                child_order=tuple(child_order),
            ))
            if isinstance(raw_id, str) and raw_id:
                raw_to_semantic[raw_id] = semantic_id

            if author_source:
                marker_sources = [
                    SourceText((item.source_occurrence,))
                    for item in references if item.source_occurrence
                ]
                self._record_gaps(
                    author_source.ranges,
                    [surname, given, suffix, *marker_sources, *comment_sources],
                    usage_id=f"{semantic_id}:notation", role="list-notation",
                    punctuation_only=True,
                )

        # 只有作者整体摘抄已覆盖一个源节点中的全部字母数字时，才能把其余字符
        # 认作名单分隔符；这项守恒判断与姓名或编号样式无关。
        by_node = {}
        for value in author_wholes:
            for node_id, start, end in value.ranges:
                by_node.setdefault(node_id, []).append((start, end))
        for node_id, ranges in by_node.items():
            node = self.source.node(node_id)
            covered = [False] * len(node.text)
            for start, end in ranges:
                covered[start:end] = [True] * (end - start)
            if any(char.isalnum() and not covered[position]
                   for position, char in enumerate(node.text)):
                continue
            self._record_gaps(
                ((node_id, 0, len(node.text)),), author_wholes,
                usage_id=f"author-list:{node_id}", role="list-notation",
                punctuation_only=True,
            )
        groups = (sm.ContributorGroup(None, tuple(values)),) if values else ()
        return groups, raw_to_semantic

    def _attach_note_targets(self, notes, note_ids, author_ids):
        targets = {}
        for raw_author, semantic_author in author_ids.items():
            for relation in self._relations_from(raw_author, "author-note"):
                semantic_note = note_ids.get(relation.get("target_id"))
                if semantic_note:
                    targets.setdefault(semantic_note, []).append(semantic_author)
        return tuple(replace(
            note, target_ids=tuple(dict.fromkeys(targets.get(note.entity_id, ())))
        ) for note in notes)

    def _editors(self):
        values = []
        for index, raw in enumerate(self.front.get("editors") or []):
            if not isinstance(raw, dict):
                continue
            surname = self._front_quote(
                raw.get("surname_quote"), f"editor:{index + 1}:surname"
            )
            given = self._front_quote(
                raw.get("given_quote"), f"editor:{index + 1}:given"
            )
            if not surname or not given:
                self.issue(
                    "review_blocking", "EDITOR_NAME_UNRESOLVED", f"editor:{index + 1}",
                    "编辑姓名无法唯一落锚",
                )
                continue
            role = self._front_rich(
                raw.get("role_quote"), f"editor:{index + 1}:role"
            )
            roles = (role,) if role else ()
            child_order = ("name", "role:0") if roles else ("name",)
            values.append(sm.Contributor(
                f"editor:{index + 1}", "editor", sm.PersonName(surname, given),
                roles=roles, child_order=child_order,
            ))
        return (sm.ContributorGroup(None, tuple(values)),) if values else ()
    def _dates(self):
        result = []
        date_format = (self.front.get("dates") or {}).get("format") or "unknown"
        for raw in (self.front.get("dates") or {}).get("items") or []:
            whole = self._front_quote(
                raw.get("whole_quote"), f"date:{raw.get('kind')}:whole"
            )
            scope = whole.ranges[0] if whole and len(whole.ranges) == 1 else None
            components = {}
            requests = []
            for name in ("year", "month", "day"):
                quote, _ = _quote_parts(raw.get(f"{name}_quote"))
                if quote:
                    requests.append(GroundRequest(name, quote))
            order_by_format = {
                "ymd": ("year", "month", "day"),
                "mdy": ("month", "day", "year"),
                "dmy": ("day", "month", "year"),
            }
            if scope and requests:
                by_name = {item.name: item for item in requests}
                ordered_requests = [by_name[name] for name in order_by_format.get(date_format, ())
                                    if name in by_name]
                if len(ordered_requests) == len(requests):
                    components = ground_ordered(
                        ordered_requests, self.source, scopes=(scope,)
                    ) or {}

            def component(name):
                match = components.get(name)
                if match:
                    return self._front_source(
                        SourceText((match,)), f"date:{raw.get('kind')}:{name}"
                    )
                return self._front_quote_in(
                    raw.get(f"{name}_quote"), scope,
                    f"date:{raw.get('kind')}:{name}",
                )

            year = component("year")
            if not year:
                self.issue("review_blocking", "DATE_YEAR_UNRESOLVED", str(raw.get("kind")),
                           "日期年份未落锚")
                continue
            month = component("month")
            day = component("day")
            result.append(sm.DateValue(
                raw.get("kind") or "received", year,
                month, day,
            ))
            if whole:
                self._record_gaps(
                    whole.ranges, (year, month, day),
                    usage_id=f"date:{raw.get('kind')}:notation",
                    role="semantic-label",
                )
        return tuple(result)

    def _abstracts(self):
        values = []
        consumed_graphics = set()
        for abstract_index, abstract in enumerate(self.front.get("abstracts") or [], 1):
            container_title = self._front_quote(
                abstract.get("container_title_quote"),
                f"abstract:{abstract_index}:container-title",
            )
            if container_title:
                self._record_semantic_use(
                    container_title,
                    f"abstract:{abstract_index}:container-title",
                    "semantic-label",
                )
            sections = []
            for raw in abstract.get("sections") or []:
                paragraphs = []
                section_scopes = []
                for quote in raw.get("paragraph_quotes") or []:
                    value = self._front_quote(
                        quote, f"abstract:{len(values) + 1}:paragraph"
                    )
                    if value:
                        paragraphs.append(sm.Paragraph(None, self.rich_source(value)))
                        for node_id, _, _ in value.ranges:
                            scope = (node_id, 0, len(self.source.node(node_id).text))
                            if scope not in section_scopes:
                                section_scopes.append(scope)
                title_source = (
                    self._front_quote_scopes(
                        raw.get("title_quote"), tuple(section_scopes),
                        f"abstract:{len(values) + 1}:section-title",
                    ) if section_scopes else
                    self._front_quote(
                        raw.get("title_quote"),
                        f"abstract:{len(values) + 1}:section-title",
                    )
                )
                requested_wrapped = bool(raw.get("wrapped", True))
                if requested_wrapped and title_source is None:
                    self.issue(
                        "review_blocking", "ABSTRACT_SECTION_TITLE_UNRESOLVED",
                        f"abstract:{len(values) + 1}:section:{len(sections) + 1}",
                        "模型要求生成摘要 sec，但没有可落锚的小节标题；已降级为摘要直属段落",
                    )
                sections.append(sm.AbstractSection(
                    self.rich_source(title_source) if title_source else None,
                    tuple(paragraphs), requested_wrapped and title_source is not None,
                ))
            blocks = []
            for occurrence_id in abstract.get("graphics") or []:
                if occurrence_id not in self.source._occurrences:
                    self.issue(
                        "review_blocking", "ABSTRACT_GRAPHIC_UNRESOLVED",
                        str(occurrence_id), "图文摘要指向了不存在的对象出现",
                    )
                    continue
                if self.object_roles.get(occurrence_id) not in {
                    None, "graphical-abstract",
                }:
                    self.issue(
                        "review_blocking", "ABSTRACT_GRAPHIC_ROLE_CONFLICT",
                        str(occurrence_id),
                        f"图文摘要对象最终角色为 {self.object_roles.get(occurrence_id)}",
                    )
                    continue
                consumed_graphics.add(occurrence_id)
                blocks.append(sm.Paragraph(
                    None, sm.RichText((sm.InlineGraphic(occurrence_id, display=True),))
                ))
            values.append(sm.Abstract(
                abstract.get("kind") or "main", tuple(sections), tuple(blocks)
            ))
        inferred = [
            occurrence.occ_id for occurrence in sorted(
                self.source.occurrences,
                key=lambda item: (self.source.node(item.node_id).order, item.char_pos),
            )
            if self.object_roles.get(occurrence.occ_id) == "graphical-abstract"
            and occurrence.occ_id not in consumed_graphics
        ]
        if inferred:
            values.append(sm.Abstract(
                "graphical", (), tuple(
                    sm.Paragraph(
                        None, sm.RichText((sm.InlineGraphic(item, display=True),))
                    ) for item in inferred
                ),
            ))
        return tuple(values)

    def _keywords(self):
        raw = self.front.get("keywords")
        if not isinstance(raw, dict):
            return ()
        quotes = raw.get("keyword_quotes") or []
        scopes = []
        for hint in raw.get("source_nodes") or []:
            node_id = _hint(self.source, hint)
            if node_id:
                scopes.append((node_id, 0, len(self.source.node(node_id).text)))
        requests = []
        for index, item in enumerate(quotes):
            quote, _ = _quote_parts(item)
            if quote:
                requests.append(GroundRequest(f"keyword:{index}", quote))
        allocated = ground_ordered(requests, self.source, scopes=scopes) if scopes else None
        keyword_sources = []
        if allocated and len(requests) == len(quotes):
            for index in range(len(requests)):
                value = self._front_source(
                    SourceText((allocated[f"keyword:{index}"],)),
                    f"keyword:{index + 1}",
                )
                if value:
                    keyword_sources.append(value)
        else:
            for index, item in enumerate(quotes):
                value = self._front_quote(item, f"keyword:{index + 1}")
                if value:
                    keyword_sources.append(value)
        keywords = tuple(self.rich_source(item) for item in keyword_sources)
        title_source = (
            self._front_quote_scopes(raw.get("title_quote"), scopes, "keywords:title")
            if scopes else self._front_quote(raw.get("title_quote"), "keywords:title")
        )
        title = self.rich_source(title_source) if title_source else None
        self._record_gaps(
            scopes, [title_source, *keyword_sources],
            usage_id="keywords:notation", role="list-notation",
            punctuation_only=True,
        )
        return (sm.KeywordGroup(None, title, keywords),)

    # ------------------------------------------------------------------
    # tables, figures, body
    # ------------------------------------------------------------------
    def _caption_scopes(self, spec):
        node_ids = []
        for raw in spec.get("caption_nodes") or []:
            node_id = _hint(self.source, raw)
            if node_id and node_id not in node_ids:
                node_ids.append(node_id)
        for key in ("label_quote", "caption_title_quote"):
            _, raw_hint = _quote_parts(spec.get(key))
            node_id = _hint(self.source, raw_hint)
            if node_id and node_id not in node_ids:
                node_ids.append(node_id)
        for raw in spec.get("caption_paragraph_quotes") or []:
            _, raw_hint = _quote_parts(raw)
            node_id = _hint(self.source, raw_hint)
            if node_id and node_id not in node_ids:
                node_ids.append(node_id)
        return tuple(
            (node_id, 0, len(self.source.node(node_id).text)) for node_id in node_ids
        )

    def _caption_quote(self, spec, key):
        raw = spec.get(key)
        quote, _ = _quote_parts(raw)
        if not quote:
            return None
        matches = []
        for scope in self._caption_scopes(spec):
            matches.extend(find_candidates(quote, self.source, scope=scope))
        matches = sorted(set(matches))
        if len(matches) == 1:
            return SourceText((matches[0],))
        return None

    def _caption(self, spec):
        title_source = self._caption_quote(spec, "caption_title_quote")
        title = self.rich_source(title_source) if title_source else None
        paragraphs = []
        for raw in spec.get("caption_paragraph_quotes") or []:
            quote, _ = _quote_parts(raw)
            found = []
            for scope in self._caption_scopes(spec):
                found.extend(find_candidates(quote, self.source, scope=scope))
            found = sorted(set(found))
            value = SourceText((found[0],)) if len(found) == 1 else None
            if value:
                paragraphs.append(sm.Paragraph(None, self.rich_source(value)))
        return sm.Caption(title, tuple(paragraphs)) if title or paragraphs else None

    def _display_label(self, spec):
        value = self._caption_quote(spec, "label_quote")
        return self.rich_source(value) if value else None

    def _figure_value(self, spec, entity_id):
        if not isinstance(spec, dict):
            return None
        graphics = tuple(
            value for value in spec.get("graphics") or []
            if isinstance(value, str) and value in self.source._occurrences
        )
        if not graphics:
            self.issue("review_blocking", "FIGURE_WITHOUT_GRAPHIC", entity_id,
                       "图没有可用对象出现")
            return None
        return sm.Figure(
            entity_id, self._display_label(spec), self._caption(spec), graphics,
        )

    def _figures(self):
        result = []
        for index, spec in enumerate(self.body_json.get("figures") or []):
            value = self._figure_value(spec, f"figure:{index + 1}")
            if value is None:
                continue
            anchors = [self.source.node(self.source.occurrence(item).node_id).order
                       for item in value.graphics]
            result.append((min(anchors), value, set(
                self.source.occurrence(item).node_id for item in value.graphics
            )))
        for group_index, spec in enumerate(self.body_json.get("figure_groups") or []):
            if not isinstance(spec, dict):
                continue
            members = []
            for member_index, member in enumerate(spec.get("members") or []):
                value = self._figure_value(
                    member,
                    f"figure-group:{group_index + 1}:member:{member_index + 1}",
                )
                if value is not None:
                    members.append(value)
            if not members:
                self.issue(
                    "review_blocking", "FIGURE_GROUP_WITHOUT_MEMBERS",
                    f"figure-group:{group_index + 1}", "图组没有可用成员",
                )
                continue
            group = sm.FigureGroup(
                f"figure-group:{group_index + 1}", self._display_label(spec),
                self._caption(spec), tuple(members),
            )
            graphics = tuple(item for member in members for item in member.graphics)
            nodes = {self.source.occurrence(item).node_id for item in graphics}
            anchor = min(self.source.node(node_id).order for node_id in nodes)
            result.append((anchor, group, nodes))
        return result

    def _cell_rich(self, cell_id):
        paras = sorted(
            [node for node in self.source.nodes
             if node.parent == cell_id and node.kind == "para"],
            key=lambda node: node.order,
        )
        parts = []
        for index, para in enumerate(paras):
            if index:
                parts.append(sm.Break())
            parts.extend(self.rich_node(para.node_id).parts)
        return sm.RichText(tuple(parts))

    def _table_note_specs(self, spec):
        """将表注协议转成“注→段→源片段”三层。"""
        result = []
        for note in spec.get("footnotes") or []:
            if not isinstance(note, dict):
                continue
            paragraphs = []
            for paragraph in note.get("paragraphs") or []:
                if isinstance(paragraph, dict):
                    quotes = tuple(paragraph.get("content_quotes") or [])
                else:
                    quotes = ()
                if quotes:
                    paragraphs.append(quotes)
            result.append((note.get("kind"), tuple(paragraphs)))
        return tuple(result)

    def _native_table(self, table_id: str, spec, entity_id: str,
                      grounded_notes=None):
        table = self.source.node(table_id)
        rows = sorted([node for node in self.source.nodes
                       if node.parent == table_id and node.kind == "row"],
                      key=lambda node: node.order)
        explicit_header_count = 0
        for row in rows:
            if not row.properties.get("header"):
                break
            explicit_header_count += 1
        proposed_header_count = spec.get("header_rows")
        if (isinstance(proposed_header_count, bool)
                or not isinstance(proposed_header_count, int)):
            proposed_header_count = 0
        header_count = explicit_header_count or max(
            0, min(proposed_header_count, len(rows))
        )
        row_header_cells = {
            (item.get("row"), item.get("column"))
            for item in spec.get("row_header_cells") or []
            if isinstance(item, dict)
            and isinstance(item.get("row"), int)
            and not isinstance(item.get("row"), bool)
            and isinstance(item.get("column"), int)
            and not isinstance(item.get("column"), bool)
        }
        rendered_rows = []
        for row_index, row in enumerate(rows):
            cells = []
            physical = sorted([node for node in self.source.nodes
                               if node.parent == row.node_id and node.kind == "cell"],
                              key=lambda node: node.order)
            for cell_index, cell in enumerate(physical):
                if cell.properties.get("v_merge") == "continue":
                    continue
                rowspan = 1
                if cell.properties.get("v_merge") == "restart":
                    column = cell.properties.get("column_position")
                    for later in rows[row_index + 1:]:
                        matches = [candidate for candidate in self.source.nodes
                                   if candidate.parent == later.node_id
                                   and candidate.kind == "cell"
                                   and candidate.properties.get("column_position") == column]
                        if matches and matches[0].properties.get("v_merge") == "continue":
                            rowspan += 1
                        else:
                            break
                is_header = row_index < header_count
                is_row_header = (row_index + 1, cell_index + 1) in row_header_cells
                cells.append(sm.TableCell(
                    self._cell_rich(cell.node_id),
                    cell_type="th" if is_header or is_row_header else "td",
                    colspan=int(cell.properties.get("grid_span") or 1), rowspan=rowspan,
                    header_kind="col" if is_header else "row" if is_row_header else None,
                    style=sm.TableCellStyle(
                        align=None, valign={
                            "center": "middle", "both": "middle",
                            "top": "top", "bottom": "bottom",
                        }.get(cell.properties.get("vertical_alignment"))
                    ),
                ))
            rendered_rows.append(sm.TableRow(tuple(cells)))
        widths = table.properties.get("grid_cols_twips") or []
        total = sum(widths) or 1
        column_widths = tuple(f"{value * 100 / total:.1f}%" for value in widths)
        notes = self._table_notes(spec, entity_id, grounded_notes)
        return sm.TableBlock(
            entity_id, self._display_label(spec),
            self._caption(spec), column_widths,
            tuple(rendered_rows[:header_count]), tuple(rendered_rows[header_count:]),
            notes=tuple(notes),
        )

    def _table_notes(self, spec, entity_id, grounded_notes=None):
        notes = []
        for note_index, (kind, paragraphs) in enumerate(self._table_note_specs(spec)):
            rich_paragraphs = []
            for paragraph_index, quotes in enumerate(paragraphs):
                ranges = []
                for quote_index, raw in enumerate(quotes):
                    value = (grounded_notes or {}).get(
                        (note_index, paragraph_index, quote_index)
                    )
                    if value:
                        ranges.extend(value.ranges)
                if ranges:
                    rich_paragraphs.append(self.rich(ranges))
            if rich_paragraphs:
                notes.append(sm.Note(
                    f"{entity_id}:note:{note_index + 1}", kind, None,
                    tuple(rich_paragraphs), "table", (entity_id,),
                ))
        return notes

    def _flattened_table(self, spec, entity_id, grounded_notes=None):
        """消费通过机械校验的段号→列号归属；这里再次核对源范围。"""
        layout = spec.get("flattened_layout")
        if not isinstance(layout, dict) or layout.get("valid") is not True:
            return None
        n_cols = layout.get("n_cols")
        header_rows = layout.get("header_rows")
        rows = layout.get("rows")
        if (isinstance(n_cols, bool) or not isinstance(n_cols, int) or n_cols < 1
                or isinstance(header_rows, bool) or not isinstance(header_rows, int)
                or not isinstance(rows, list) or not 0 <= header_rows <= len(rows)):
            return None
        rendered = []
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("cells"), list):
                return None
            cells = []
            previous_position = None
            for raw_cell in row["cells"]:
                if not isinstance(raw_cell, dict):
                    return None
                column = raw_cell.get("column")
                colspan = raw_cell.get("colspan")
                rowspan = raw_cell.get("rowspan")
                row_header = raw_cell.get("row_header")
                raw_ranges = raw_cell.get("ranges")
                if (any(isinstance(value, bool) or not isinstance(value, int)
                        for value in (column, colspan, rowspan))
                        or not 1 <= column <= n_cols or colspan < 1 or rowspan < 1
                        or column + colspan - 1 > n_cols
                        or not isinstance(row_header, bool)
                        or not isinstance(raw_ranges, list)):
                    return None
                if not isinstance(raw_ranges, list):
                    return None
                ranges = []
                for raw_range in raw_ranges:
                    if (not isinstance(raw_range, (list, tuple)) or len(raw_range) != 3
                            or raw_range[0] not in self.source._nodes
                            or isinstance(raw_range[1], bool) or isinstance(raw_range[2], bool)
                            or not isinstance(raw_range[1], int)
                            or not isinstance(raw_range[2], int)
                            or not 0 <= raw_range[1] <= raw_range[2] <= len(
                                self.source.node(raw_range[0]).text
                            )):
                        return None
                    position = (self.source.node(raw_range[0]).order, raw_range[1])
                    if previous_position is not None and position < previous_position:
                        return None
                    ranges.append(tuple(raw_range))
                    previous_position = (
                        self.source.node(raw_range[0]).order, raw_range[2]
                    )
                is_header = row_index < header_rows
                cells.append(sm.TableCell(
                    self.rich(ranges),
                    cell_type="th" if is_header or row_header else "td",
                    colspan=colspan, rowspan=rowspan,
                    header_kind=(
                        "col" if is_header else "row" if row_header else None
                    ),
                ))
            rendered.append(sm.TableRow(tuple(cells)))
        return sm.TableBlock(
            entity_id, self._display_label(spec), self._caption(spec), (),
            tuple(rendered[:header_rows]), tuple(rendered[header_rows:]),
            notes=tuple(self._table_notes(spec, entity_id, grounded_notes)),
        )

    def _coarse_flattened_table(self, spec, entity_id, grounded_notes=None):
        """归属未决时仍逐行照抄原文，绝不让表格内容消失。"""
        layout = spec.get("flattened_layout")
        raw_rows = layout.get("source_rows") if isinstance(layout, dict) else None
        ranges = []
        if isinstance(raw_rows, list):
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                node_id, start, end = row.get("node_id"), row.get("start"), row.get("end")
                if (node_id in self.source._nodes and isinstance(start, int)
                        and not isinstance(start, bool) and isinstance(end, int)
                        and not isinstance(end, bool)
                        and 0 <= start <= end <= len(self.source.node(node_id).text)):
                    ranges.append((node_id, start, end))
        if not ranges:
            seen = set()
            for raw in spec.get("flattened_row_nodes") or []:
                node_id = _hint(self.source, raw)
                if node_id and node_id not in seen:
                    seen.add(node_id)
                    ranges.append((node_id, 0, len(self.source.node(node_id).text)))
        if not ranges:
            return None
        rows = tuple(sm.TableRow((sm.TableCell(self.rich((source_range,))),))
                     for source_range in ranges)
        try:
            header_count = int(spec.get("header_rows") or 0)
        except (TypeError, ValueError):
            header_count = 0
        header_count = max(0, min(header_count, len(rows)))
        return sm.TableBlock(
            entity_id, self._display_label(spec), self._caption(spec), (),
            rows[:header_count], rows[header_count:],
            notes=tuple(self._table_notes(spec, entity_id, grounded_notes)),
        )

    def _ground_table_notes(self):
        result = {}
        for table_index, spec in enumerate(self.body_json.get("tables") or []):
            if not isinstance(spec, dict):
                continue
            raws = []
            for note_index, (_, paragraphs) in enumerate(self._table_note_specs(spec)):
                for paragraph_index, quotes in enumerate(paragraphs):
                    for quote_index, raw in enumerate(quotes):
                        quote, _ = _quote_parts(raw)
                        if not quote:
                            continue
                        position = (note_index, paragraph_index, quote_index)
                        raws.append((position, raw))
            if not raws:
                continue
            scope_nodes = []
            for raw in spec.get("footnote_nodes") or []:
                node_id = _hint(self.source, raw)
                if node_id and node_id not in scope_nodes:
                    scope_nodes.append(node_id)
            for _, raw in raws:
                _, hint = _quote_parts(raw)
                node_id = _hint(self.source, hint)
                if node_id and node_id not in scope_nodes:
                    scope_nodes.append(node_id)
            scopes = tuple(
                (node_id, 0, len(self.source.node(node_id).text))
                for node_id in sorted(
                    scope_nodes, key=lambda value: self.source.node(value).order
                )
            )
            individually = {}
            for position, raw in raws:
                value = _source_quote_scopes(self.source, raw, scopes) if scopes else None
                if value:
                    individually[position] = value
            if len(individually) != len(raws):
                self.issue(
                    "review_blocking", "TABLE_NOTES_UNRESOLVED",
                    f"table:{table_index + 1}",
                    "表注无法在该表已指明的表注节点中全部唯一落锚",
                )
            result[table_index] = individually
        return result

    def _tables(self):
        result = []
        note_map = self._ground_table_notes()
        for index, spec in enumerate(self.body_json.get("tables") or []):
            entity_id = f"table:{index + 1}"
            table_id = _hint(self.source, spec.get("table_node"))
            graphic = spec.get("graphic")
            if table_id and self.source.node(table_id).kind == "table":
                value = self._native_table(
                    table_id, spec, entity_id, note_map.get(index)
                )
                anchor = self.source.node(table_id).order
                consumed = {table_id}
            elif isinstance(graphic, str) and graphic in self.source._occurrences:
                value = sm.TableBlock(
                    entity_id, self._display_label(spec),
                    self._caption(spec), (), (), (), graphic_occurrence=graphic,
                )
                anchor = self.source.node(self.source.occurrence(graphic).node_id).order
                consumed = {self.source.occurrence(graphic).node_id}
            else:
                value = self._flattened_table(spec, entity_id, note_map.get(index))
                if value is None:
                    value = self._coarse_flattened_table(
                        spec, entity_id, note_map.get(index)
                    )
                    self.issue(
                        "review_blocking", "FLATTENED_TABLE_UNRESOLVED", entity_id,
                        "格子归属未通过机械校验；候选中按一行一格保全源文",
                    )
                if value is None:
                    continue
                layout = spec.get("flattened_layout") or {}
                consumed = {
                    row.get("node_id") for row in layout.get("source_rows") or []
                    if isinstance(row, dict) and row.get("node_id") in self.source._nodes
                }
                if not consumed:
                    consumed = {
                        node_id for raw in spec.get("flattened_row_nodes") or []
                        for node_id in [_hint(self.source, raw)] if node_id
                    }
                anchor = min(self.source.node(node_id).order for node_id in consumed)
            result.append((anchor, value, consumed))
        return result

    def _display_formulas(self):
        values = []
        for spec in self.body_json.get("formulas") or []:
            occurrence_id = spec.get("occurrence_id")
            if occurrence_id not in self.source._occurrences or not spec.get("display"):
                continue
            label = _rich_quote(self.source, spec.get("label_quote"))
            value = self._formula(occurrence_id, display=True, label=label)
            node_id = self.source.occurrence(occurrence_id).node_id
            values.append((self.source.node(node_id).order, value, {node_id}))
        return values

    def _block_role_meta(self):
        result = {}
        for group in self.body_json.get("blocks") or []:
            if not isinstance(group, dict):
                continue
            for raw in group.get("nodes") or []:
                record = self.view.by_key(raw) if isinstance(raw, str) else None
                node_ids = record.source_nodes if record else ((_hint(self.source, raw),) if _hint(self.source, raw) else ())
                for node_id in node_ids:
                    result[node_id] = group
        return result

    def _special_node_ids(self, spec):
        node_ids = []

        def add(raw):
            record = self.view.by_key(raw) if isinstance(raw, str) else None
            candidates = record.source_nodes if record else (
                (_hint(self.source, raw),) if _hint(self.source, raw) else ()
            )
            for node_id in candidates:
                if node_id and node_id not in node_ids:
                    node_ids.append(node_id)

        for raw in spec.get("nodes") or []:
            add(raw)
        for raw in [spec.get("title_quote"), *(spec.get("paragraph_quotes") or [])]:
            _, hint = _quote_parts(raw)
            add(hint)
        for item in spec.get("items") or []:
            if not isinstance(item, dict):
                continue
            for raw in [item.get("term_quote"), *(item.get("definition_quotes") or [])]:
                _, hint = _quote_parts(raw)
                add(hint)
        return tuple(sorted(node_ids, key=lambda value: self.source.node(value).order))

    def _special_value(self, spec, number):
        role = spec.get("role")
        title = _rich_quote(self.source, spec.get("title_quote"))
        ordered_blocks = []
        for raw in spec.get("paragraph_quotes") or []:
            value = _source_quote(self.source, raw)
            if value:
                order = min(self.source.node(item[0]).order for item in value.ranges)
                ordered_blocks.append((
                    order, sm.Paragraph(None, self.rich_source(value))
                ))

        definitions = []
        definition_orders = []
        for item_index, item in enumerate(spec.get("items") or []):
            if not isinstance(item, dict):
                continue
            term = _rich_quote(self.source, item.get("term_quote"))
            raw_definitions = tuple(filter(None, (
                _rich_quote(self.source, raw)
                for raw in item.get("definition_quotes") or []
            )))
            term_source = _source_quote(self.source, item.get("term_quote"))
            if term is None or not raw_definitions or term_source is None:
                self.issue(
                    "review_blocking", "DEFINITION_ITEM_UNRESOLVED",
                    f"special:{number}:item:{item_index + 1}",
                    "术语或定义没有全部落锚",
                )
                continue
            definitions.append(sm.DefinitionItem(term, raw_definitions))
            definition_orders.append(min(
                self.source.node(value[0]).order for value in term_source.ranges
            ))
        if definitions:
            ordered_blocks.append((
                min(definition_orders), sm.DefinitionList(tuple(definitions))
            ))
        blocks = tuple(value for _, value in sorted(
            ordered_blocks, key=lambda item: item[0]
        ))
        if not blocks:
            self.issue(
                "review_blocking", "SPECIAL_BLOCK_CONTENT_UNRESOLVED",
                f"special:{number}", f"{role} 没有可用正文",
            )
        if role == "glossary":
            return sm.Glossary(f"glossary:{number}", title, blocks)
        if role == "definition-list":
            if len(blocks) == 1 and isinstance(blocks[0], sm.DefinitionList) and title is None:
                return blocks[0]
            return sm.Section(f"definition-section:{number}", title, blocks)
        return None

    def _body(self):
        displays = self._figures() + self._tables() + self._display_formulas()
        at_order = {}
        consumed = set()
        for order, value, nodes in displays:
            at_order.setdefault(order, []).append(value)
            consumed.update(nodes)
        meta = self._block_role_meta()
        top = [node for node in sorted(self.source.nodes, key=lambda node: node.order)
               if node.part == "document" and node.parent is None
               and node.kind in {"para", "table"}]

        @dataclass
        class MutableSection:
            entity_id: str
            title: sm.RichText
            level: int
            blocks: list = field(default_factory=list)

        roots = []
        stack: list[MutableSection] = []
        back_sections = []
        section_number = 0
        declaration_number = 0
        declaration_specs = {}
        special_specs = {}
        for group in self.body_json.get("blocks") or []:
            if not isinstance(group, dict) or group.get("role") != "declaration":
                continue
            node_ids = []
            for raw in group.get("nodes") or []:
                record = self.view.by_key(raw) if isinstance(raw, str) else None
                candidates = record.source_nodes if record else (
                    (_hint(self.source, raw),) if _hint(self.source, raw) else ()
                )
                for node_id in candidates:
                    if node_id not in node_ids:
                        node_ids.append(node_id)
            for raw in group.get("content_nodes") or []:
                node_id = _hint(self.source, raw)
                if node_id and node_id not in node_ids:
                    node_ids.append(node_id)
            if node_ids:
                for node_id in node_ids:
                    declaration_specs[node_id] = (node_ids[0], group, tuple(node_ids))

        for index, spec in enumerate(self.body_json.get("special_blocks") or []):
            if not isinstance(spec, dict):
                continue
            node_ids = self._special_node_ids(spec)
            if not node_ids:
                self.issue(
                    "review_blocking", "SPECIAL_BLOCK_NODES_UNRESOLVED",
                    f"special:{index + 1}", "特殊结构没有可用源节点",
                )
                continue
            for node_id in node_ids:
                special_specs[node_id] = (node_ids[0], spec, index + 1)

        for node in top:
            for display in at_order.get(node.order, ()):
                (stack[-1].blocks if stack else roots).append(display)
            if node.node_id in consumed:
                continue
            role = self.assignment.role(node.node_id)
            info = meta.get(node.node_id, {})
            if node.node_id in special_specs:
                first, spec, number = special_specs[node.node_id]
                if node.node_id != first:
                    continue
                value = self._special_value(spec, number)
                if value is None:
                    continue
                if spec.get("container") == "back":
                    if isinstance(value, sm.Glossary):
                        back_sections.append(sm.BackSection(
                            "glossary", value.entity_id, value.title, value.blocks
                        ))
                    elif isinstance(value, sm.Section):
                        back_sections.append(sm.BackSection(
                            "declaration", value.entity_id, value.title, value.blocks
                        ))
                    else:
                        back_sections.append(sm.BackSection(
                            "declaration", f"definition-section:{number}", None,
                            (value,),
                        ))
                elif isinstance(value, sm.Glossary) and not stack:
                    # JATS body 不允许 glossary 与 sec 并列；全文顶层术语表
                    # 的合法容器是 back。位于一个已打开正文节内时仍可作为
                    # 该节的末尾结构，不能一概搬运。
                    back_sections.append(sm.BackSection(
                        "glossary", value.entity_id, value.title, value.blocks
                    ))
                    self.issue(
                        "warning", "TOP_LEVEL_GLOSSARY_MOVED_TO_BACK", first,
                        "顶层 glossary 不能直接置于 body，已放入 back",
                    )
                else:
                    (stack[-1].blocks if stack else roots).append(value)
            elif role == "section-title":
                section_number += 1
                level = max(1, int(info.get("level") or 1))
                section = MutableSection(
                    f"section:{section_number}", self.rich_node(node.node_id), level
                )
                while stack and stack[-1].level >= level:
                    stack.pop()
                (stack[-1].blocks if stack else roots).append(section)
                stack.append(section)
            elif role == "body-paragraph":
                (stack[-1].blocks if stack else roots).append(self.paragraph(node.node_id))
            elif role == "declaration":
                first, group, group_nodes = declaration_specs.get(
                    node.node_id, (node.node_id, info, (node.node_id,))
                )
                if node.node_id != first:
                    continue
                kind = info.get("kind") or "declaration"
                title_source = _source_quote(self.source, group.get("title_quote"))
                title = self.rich_source(title_source) if title_source else None
                content_ids = []
                for raw in group.get("content_nodes") or []:
                    node_id = _hint(self.source, raw)
                    if node_id and node_id not in content_ids:
                        content_ids.append(node_id)
                paragraphs = [self.paragraph(node_id) for node_id in content_ids]
                if title is None and group_nodes:
                    self.issue(
                        "review_blocking", "DECLARATION_TITLE_UNRESOLVED", first,
                        f"{kind} 声明没有可落锚的标题摘抄",
                    )
                if title is not None:
                    title_nodes = {node_id for node_id, _, _ in title_source.ranges}
                    duplicate = title_nodes.intersection(content_ids)
                    if duplicate:
                        self.issue(
                            "review_blocking", "DECLARATION_TITLE_REUSED_AS_CONTENT",
                            first, f"标题节点重复列入正文: {sorted(duplicate)}",
                        )
                        paragraphs = [
                            self.paragraph(node_id) for node_id in content_ids
                            if node_id not in duplicate
                        ]
                if not paragraphs:
                    self.issue(
                        "review_blocking", "DECLARATION_CONTENT_UNRESOLVED", first,
                        f"{kind} 声明没有可用正文节点",
                    )
                declaration_number += 1
                back_sections.append(sm.BackSection(
                    "ack" if kind == "acknowledgments" else kind,
                    f"back-section:{declaration_number}", title, tuple(paragraphs),
                ))
            elif role in {"blank", "decorative", "front", "reference-title",
                          "reference-entry", "figure-caption", "table-caption",
                          "table", "table-footnote", "display-formula", "glossary",
                          "definition-list"}:
                continue
            elif role:
                # 脚注等有专用去向；无容器的非空角色不静默塞进正文。
                if node.text.strip():
                    self.issue("review_blocking", "BODY_ROLE_NOT_ASSEMBLED", node.node_id, role)

        def freeze(value):
            if isinstance(value, MutableSection):
                return sm.Section(
                    value.entity_id, value.title,
                    tuple(freeze(item) for item in value.blocks),
                )
            return value
        return tuple(freeze(item) for item in roots), tuple(back_sections)

    # ------------------------------------------------------------------
    # references
    # ------------------------------------------------------------------
    def _q(self, raw, allocated):
        quote, hint = _quote_parts(raw)
        if not quote:
            return None
        key = f"field:{len(allocated)}"
        allocated.append(GroundRequest(
            key, quote, block_hint=_hint(self.source, hint),
        ))
        return key

    def _structured_reference(self, span: ReferenceSpan, raw: dict):
        requests = []
        pointers = {}
        pointers["label"] = self._q(raw.get("label_quote"), requests)
        person_pointer_keys = set()
        member_scope_keys = []
        for group_index, group in enumerate(raw.get("person_groups") or []):
            for member_index, member in enumerate(group.get("members") or []):
                prefix = f"g{group_index}:m{member_index}"
                member_key = f"{prefix}:member"
                pointers[member_key] = self._q(member.get("member_quote"), requests)
                if pointers[member_key]:
                    person_pointer_keys.add(member_key)
                    member_scope_keys.append(member_key)
                if "collab_quote" in member:
                    key = f"{prefix}:collab"
                    pointers[key] = self._q(member.get("collab_quote"), requests)
                    if pointers[key]:
                        person_pointer_keys.add(key)
                else:
                    for part in ("surname", "given", "suffix"):
                        key = f"{prefix}:{part}"
                        pointers[key] = self._q(member.get(f"{part}_quote"), requests)
                        if pointers[key]:
                            person_pointer_keys.add(key)
            key = f"g{group_index}:etal"
            pointers[key] = self._q(group.get("etal_quote"), requests)
            if pointers[key]:
                person_pointer_keys.add(key)
        fields = raw.get("fields") or {}
        identity_pointer_keys = set()
        for name, value in fields.items():
            if name == "comments":
                for index, item in enumerate(value or []):
                    pointers[f"comment:{index}"] = self._q(item, requests)
            else:
                pointers[name] = self._q(value, requests)
                if name == "year_suffix" and pointers[name]:
                    identity_pointer_keys.add(pointers[name])
        requests = [item for item in requests if item is not None]
        person_names = {
            pointers[key] for key in person_pointer_keys if pointers.get(key)
        }
        person_requests = [item for item in requests if item.name in person_names]
        ordinary_requests = [
            item for item in requests
            if item.name not in person_names and item.name not in identity_pointer_keys
        ]
        # 完全无候选的单个字段按契约置空；其余字段必须
        # 存在唯一的整体不重叠分配，不用最大子集隐藏歧义。
        grounded_requests = []
        for request in ordinary_requests:
            candidates = []
            for text_scope in span.source.ranges:
                candidates.extend(find_candidates(
                    request.quote, self.source, scope=text_scope,
                    block_hint=request.block_hint,
                    allow_object=request.allow_object,
                ))
            if candidates:
                grounded_requests.append(request)
        ordered_pointers = []
        identifier_pointers = [
            pointers.get(name) for name in ("doi", "pmid")
            if pointers.get(name)
        ]
        for raw_token in raw.get("field_order") or []:
            token = str(raw_token).replace("person-group", "person_group")
            if token.startswith("person_group:"):
                continue
            if token.startswith("identifier:"):
                try:
                    pointer = identifier_pointers[int(token.partition(":")[2])]
                except (ValueError, IndexError):
                    continue
            else:
                pointer = pointers.get(token)
            if pointer and pointer in {item.name for item in grounded_requests}:
                ordered_pointers.append(pointer)
        allocation = ground_joint(
            grounded_requests, self.source, scope=span.source.ranges,
            source_order=ordered_pointers,
        ) if grounded_requests else {}
        if allocation is None:
            return None

        suffix_pointer = pointers.get("year_suffix")
        year_pointer = pointers.get("year")
        if suffix_pointer and year_pointer and year_pointer in allocation:
            suffix_request = next(
                item for item in requests if item.name == suffix_pointer
            )
            suffix_range = ground(
                suffix_request.quote, self.source, scope=allocation[year_pointer],
                block_hint=suffix_request.block_hint,
            )
            if suffix_range is not None:
                allocation[suffix_pointer] = suffix_range

        member_requests = [
            next(item for item in person_requests if item.name == pointers[key])
            for key in member_scope_keys
        ]
        member_allocation = ground_ordered(
            member_requests, self.source, scopes=span.source.ranges
        ) if member_requests else {}
        if member_allocation is None or len(member_requests) != len(member_scope_keys):
            return None
        allocation.update(member_allocation)
        for group_index, group in enumerate(raw.get("person_groups") or []):
            for member_index, member in enumerate(group.get("members") or []):
                prefix = f"g{group_index}:m{member_index}"
                scope_pointer = pointers.get(f"{prefix}:member")
                if not scope_pointer or scope_pointer not in allocation:
                    return None
                part_names = (
                    ("collab",) if "collab_quote" in member
                    else ("surname", "given", "suffix")
                )
                part_requests = [
                    next(item for item in person_requests if item.name == pointer)
                    for part in part_names
                    for pointer in [pointers.get(f"{prefix}:{part}")]
                    if pointer
                ]
                member_parts = ground_joint(
                    part_requests, self.source, scope=allocation[scope_pointer]
                ) if part_requests else {}
                if member_parts is None:
                    return None
                allocation.update(member_parts)
        for key in person_pointer_keys:
            if not key.endswith(":etal") or not pointers.get(key):
                continue
            request = next(item for item in person_requests if item.name == pointers[key])
            found = []
            for scope in span.source.ranges:
                found.extend(find_candidates(request.quote, self.source, scope=scope))
            if len(found) != 1:
                return None
            allocation[pointers[key]] = found[0]

        person_output_ranges = [
            allocation[pointers[key]]
            for key in person_pointer_keys
            if not key.endswith(":member") and pointers.get(key) in allocation
        ]
        for request in ordinary_requests:
            candidate = allocation.get(request.name)
            if candidate and any(
                candidate[0] == other[0]
                and candidate[1] < other[2] and other[1] < candidate[2]
                for other in person_output_ranges
            ):
                return None

        def source_for(pointer):
            key = pointers.get(pointer)
            return SourceText((allocation[key],)) if key and key in allocation else None

        def rich_for(pointer):
            value = source_for(pointer)
            return self.rich_source(value) if value else None

        groups = []
        token_positions = {}
        for group_index, raw_group in enumerate(raw.get("person_groups") or []):
            persons = []
            collaborations = []
            generated_order = []
            person_no = collab_no = 0
            for member_index, member in enumerate(raw_group.get("members") or []):
                collab = rich_for(f"g{group_index}:m{member_index}:collab")
                if collab:
                    collaborations.append(collab); generated_order.append(f"collaboration:{collab_no}"); collab_no += 1
                    continue
                surname = source_for(f"g{group_index}:m{member_index}:surname")
                given = source_for(f"g{group_index}:m{member_index}:given")
                if surname and given:
                    persons.append(sm.PersonName(
                        surname, given, source_for(f"g{group_index}:m{member_index}:suffix")
                    ))
                    generated_order.append(f"person:{person_no}"); person_no += 1
            etal = rich_for(f"g{group_index}:etal")
            if etal:
                generated_order.append("et_al")
            order = tuple(raw_group.get("child_order") or generated_order)
            # 模型次序中的 token 必须与落锚后实体闭合，否则用机械原序。
            valid = set(generated_order)
            if set(order) != valid:
                order = tuple(generated_order)
            groups.append(sm.ReferencePersonGroup(
                raw_group.get("kind") or "author", tuple(persons),
                tuple(collaborations), etal, order,
            ))
            group_ranges = [
                allocation[pointers[key]]
                for key in member_scope_keys
                if key.startswith(f"g{group_index}:")
                and pointers.get(key) in allocation
            ]
            etal_pointer = pointers.get(f"g{group_index}:etal")
            if etal_pointer in allocation:
                group_ranges.append(allocation[etal_pointer])
            if group_ranges:
                token_positions[f"person_group:{group_index}"] = min(
                    (self.source.node(item[0]).order, item[1]) for item in group_ranges
                )
        identifiers = []
        for kind in ("doi", "pmid"):
            value = rich_for(kind)
            if not value:
                continue
            ranges = tuple(item for part in value.parts if isinstance(part, sm.Text)
                           for item in part.source.ranges)
            visible = value.plain_text(self.source)
            links = [link.target for node_id, start, end in ranges
                     for link in self.source.node(node_id).links
                     if link.end > start and link.start < end]
            carrier = "hyperlink" if links else (
                "url" if re.search(r"(?:https?://|www\.|doi\.org/)", visible, re.I)
                else "bare"
            )
            identifiers.append(sm.ReferenceIdentifier(
                kind, value, carrier, links[0] if links else None
            ))
            source_value = source_for(kind)
            if source_value:
                token_positions[f"identifier:{len(identifiers) - 1}"] = min(
                    (self.source.node(item[0]).order, item[1])
                    for item in source_value.ranges
                )
        comments = tuple(filter(None, (
            rich_for(f"comment:{index}")
            for index, _ in enumerate(fields.get("comments") or [])
        )))
        for index in range(len(comments)):
            source_value = source_for(f"comment:{index}")
            if source_value:
                token_positions[f"comment:{index}"] = min(
                    (self.source.node(item[0]).order, item[1])
                    for item in source_value.ranges
                )
        scalar_names = (
            "article_title", "chapter_title", "source", "year", "month", "day",
            "volume", "issue", "fpage", "lpage", "elocation_id", "edition",
            "publisher_name", "publisher_location",
        )
        scalars = {name: rich_for(name) for name in scalar_names}
        for name, value in scalars.items():
            source_value = source_for(name)
            if value and source_value:
                token_positions[name] = min(
                    (self.source.node(item[0]).order, item[1])
                    for item in source_value.ranges
                )
        publication_type = raw.get("publication_type")
        if not isinstance(publication_type, str) or not publication_type:
            return None
        available = set(token_positions)
        # 输出顺序由已落锚的源坐标机械确定；模型给出的
        # field_order 只参与落锚消歧，不能改排源文。
        order = sorted(available, key=lambda name: (token_positions[name], name))
        citation = sm.StructuredCitation(
            publication_type=publication_type,
            person_groups=tuple(groups), identifiers=tuple(identifiers), comments=comments,
            field_order=tuple(order), **scalars,
        )
        if not (groups or identifiers or comments or any(scalars.values())):
            return None
        label = rich_for("label")
        identity_surnames = tuple(
            person.surname.text(self.source)
            for raw_group, group in zip(raw.get("person_groups") or [], groups)
            if isinstance(raw_group, dict) and raw_group.get("kind") == "author"
            for person in group.persons
        )
        year_source = source_for("year")
        suffix_source = source_for("year_suffix")
        title_source = source_for("article_title") or source_for("chapter_title")
        identity = sm.ReferenceIdentity(
            surnames=identity_surnames,
            year=year_source.text(self.source) if year_source else None,
            year_suffix=suffix_source.text(self.source) if suffix_source else None,
            title_key=title_source.text(self.source) if title_source else None,
        )
        return label, citation, identity

    def _references(self):
        values = []
        for index, span in enumerate(self.reference_spans):
            raw = self.reference_fields[index] if index < len(self.reference_fields) else {}
            structured = None
            if raw.get("structured"):
                structured = self._structured_reference(span, raw)
            if structured:
                label, citation, identity = structured
            else:
                label_source = _source_quote(self.source, raw.get("label_quote"))
                label = self.rich_source(label_source) if label_source else None
                mixed_ranges = list(span.source.ranges)
                if label_source and len(label_source.ranges) == 1:
                    label_range = label_source.ranges[0]
                    trimmed = []
                    for node_id, start, end in mixed_ranges:
                        if node_id != label_range[0] or label_range[2] <= start or end <= label_range[1]:
                            trimmed.append((node_id, start, end)); continue
                        if start < label_range[1]:
                            trimmed.append((node_id, start, label_range[1]))
                        if label_range[2] < end:
                            trimmed.append((node_id, label_range[2], end))
                    mixed_ranges = trimmed
                citation = sm.MixedCitation(
                    raw.get("publication_type"), self.rich(tuple(mixed_ranges))
                )
                identity = sm.ReferenceIdentity()
            values.append(sm.Reference(f"reference:{index + 1}", label, citation, identity))
        if not values:
            return None
        title_node = next((
            item.source_id for item in self.assignment.assignments
            if item.source_kind == "node" and item.role == "reference-title"
        ), None)
        title = self.rich_node(title_node) if title_node else None
        return sm.ReferenceList(title, tuple(values))

    def build(self):
        addresses, address_ids = self._addresses()
        affiliations, affiliation_ids = self._affiliations(address_ids)
        correspondence, correspondence_ids = self._correspondence()
        contributor_notes, note_ids = self._contributor_notes()
        author_groups, author_ids = self._contributors(
            affiliation_ids, address_ids, correspondence_ids, note_ids
        )
        contributor_notes = self._attach_note_targets(
            contributor_notes, note_ids, author_ids
        )
        body, back = self._body()
        reference_list = self._references()
        body, xref_issues = link_bibliographic_citations(
            body, reference_list, self.reference_spans, self.source,
            self.body_json.get("bibliographic_citations") or (),
            view=self.view,
        )
        for node_id, start, end, detail in xref_issues:
            self.issue("review_blocking", "BIBR_XREF_AMBIGUOUS", node_id,
                       f"{start}:{end} {detail}")
        for detail in self.body_json.get("bibliographic_citation_issues") or ():
            self.issue(
                "review_blocking", "BIBR_XREF_AMBIGUOUS", "citation",
                str(detail),
            )
        author_notes = tuple(filter(None, (
            self._front_rich(item, "author-note")
            for item in self.front.get("author_note_quotes") or []
        )))
        category = self._front_rich(
            self.front.get("category_quote"), "article-category"
        )
        article_type = self.front.get("article_type")
        if not isinstance(article_type, str) or not article_type:
            article_type = None
            self.issue(
                "review_blocking", "ARTICLE_TYPE_UNRESOLVED", "front",
                "文章类型未经理解层判定",
            )
        document = sm.SemanticDoc(
            source=self.source,
            article_type=article_type,
            categories=((sm.ArticleCategory("heading", category),) if category else ()),
            title=self._title(),
            contributor_groups=author_groups + self._editors(),
            affiliations=affiliations, addresses=addresses,
            correspondence=correspondence,
            author_note_paragraphs=author_notes,
            notes=contributor_notes,
            dates=self._dates(), abstracts=self._abstracts(),
            keyword_groups=self._keywords(), body=body, back_sections=back,
            reference_list=reference_list,
            inline_formulas=tuple(value for value in self.inline_formulas.values()
                                  if not value.display),
        )
        try:
            document.validate()
        except ValueError as error:
            self.issue("high", "SEMANTIC_CONTRACT_INVALID", "document", str(error))
        return AssemblyResult(document, tuple(self.issues), tuple(self.source_uses))


def assemble(source: SourceDocument, view: SerializedDocument, front: dict, body: dict,
             references: tuple[ReferenceSpan, ...], fields: list[dict],
             assignment: DocumentAssignment) -> AssemblyResult:
    return _Assembler(
        source, view, front, body, references, fields, assignment
    ).build()
