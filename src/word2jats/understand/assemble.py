"""把已落锚的语义指针组装为 SemanticDoc v2。

本模块不接受模型自由文字。任何可见字段均须先通过
``ground.py`` 变成 SourceText；落锚失败时只能结构降级或报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, Optional

from ..model.source import OBJECT_REPLACEMENT, SourceDocument, SourceText, TextRange
from ..semantic import model as sm
from .ground import (
    GroundRequest, find_candidates, find_context_candidates, ground,
    ground_context, ground_joint, record_source_range,
    ground_ordered,
)
from .math import occurrence_math
from .merge import DocumentAssignment, MergeIssue, ReferenceSpan
from .serialize import SerializedDocument
from .xrefs import link_bibliographic_citations, link_display_object_callouts


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

    # 标题剥离后紧跟的标签分隔符按口径吸收（标签词由元素承载则分隔符不产出）。
    _LABEL_SEPARATORS = " \t :：.．;；"

    def _strip_title_spans(self, node_id: str, title_source: SourceText):
        """同段「标题: 正文」剥离标题区间后的剩余可见文字区间。"""
        text = self.source.node(node_id).text
        taken = sorted(
            (start, end) for owner, start, end in title_source.ranges
            if owner == node_id
        )
        segments = []
        cursor = 0
        for start, end in [*taken, (len(text), len(text))]:
            if cursor < start:
                left, right = cursor, start
                while left < right and text[left] in self._LABEL_SEPARATORS:
                    left += 1
                while right > left and text[right - 1] in " \t ":
                    right -= 1
                if left < right:
                    segments.append(SourceText(((node_id, left, right),)))
            cursor = max(cursor, end)
        return segments

    def _trim_trailing_separators(self, value: SourceText) -> SourceText:
        """吸收标题尾部的标签分隔符（口径：标签词由元素承载则分隔符不产出）。"""
        ranges = list(value.ranges)
        while ranges:
            node_id, start, end = ranges[-1]
            text = self.source.node(node_id).text
            while end > start and text[end - 1] in self._LABEL_SEPARATORS:
                end -= 1
            if end > start:
                ranges[-1] = (node_id, start, end)
                break
            ranges.pop()
        return SourceText(tuple(ranges))

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

    def _front_record(self, raw, purpose: str):
        value = record_source_range(self.view, raw) if isinstance(raw, str) else None
        return self._front_source(SourceText((value,)) if value else None, purpose)

    def _front_quote_scopes(self, raw, scopes, purpose: str):
        return self._front_source(
            _source_quote_scopes(self.source, raw, scopes), purpose
        )

    def _abstracts(self):
        values = []
        consumed_graphics = set()
        for abstract_index, abstract in enumerate(self.front.get("abstracts") or [], 1):
            if not isinstance(abstract, dict):
                continue
            # 容器文字只证明摘要边界，不自动成为 JATS 标题。
            # container_title_quote 仅用于读取旧的已保存理解结果。
            container_title = self._front_quote(
                abstract.get("container_quote")
                if "container_quote" in abstract
                else abstract.get("container_title_quote"),
                f"abstract:{abstract_index}:container-title",
            )
            if container_title:
                self._record_semantic_use(
                    container_title,
                    f"abstract:{abstract_index}:container-title",
                    "semantic-label",
                )
            label_source = self._front_quote(
                abstract.get("label_quote"),
                f"abstract:{abstract_index}:label",
            )
            title_source = self._front_quote(
                abstract.get("title_quote"),
                f"abstract:{abstract_index}:title",
            )
            sections = []
            content_scopes = []
            content_sources = []
            for section_index, raw in enumerate(abstract.get("sections") or [], 1):
                if not isinstance(raw, dict):
                    continue
                paragraphs = []
                pointers = raw.get("paragraphs")
                if not isinstance(pointers, list):
                    pointers = raw.get("paragraph_quotes") or []
                for paragraph_index, pointer in enumerate(pointers, 1):
                    purpose = (
                        f"abstract:{abstract_index}:section:{section_index}:"
                        f"paragraph:{paragraph_index}"
                    )
                    value = (
                        self._front_record(pointer, purpose)
                        if isinstance(pointer, str)
                        else self._front_quote(pointer, purpose)
                    )
                    if value:
                        paragraphs.append(sm.Paragraph(None, self.rich_source(value)))
                        content_sources.append(value)
                    if isinstance(pointer, str):
                        scope = record_source_range(self.view, pointer)
                    else:
                        scope = record_source_range(
                            self.view, pointer.get("node_hint")
                        ) if isinstance(pointer, dict) else None
                    if scope and scope not in content_scopes:
                        content_scopes.append(scope)
                section_title_source = self._front_quote(
                    raw.get("title_quote"),
                    f"abstract:{abstract_index}:section:{section_index}:title",
                )
                if section_title_source:
                    content_sources.append(section_title_source)
                    title_raw = raw.get("title_quote")
                    scope = record_source_range(
                        self.view, title_raw.get("node_hint")
                    ) if isinstance(title_raw, dict) else None
                    if scope and scope not in content_scopes:
                        content_scopes.append(scope)
                requested_wrapped = bool(raw.get("wrapped", True))
                if requested_wrapped and section_title_source is None:
                    self.issue(
                        "review_blocking", "ABSTRACT_SECTION_TITLE_UNRESOLVED",
                        f"abstract:{abstract_index}:section:{section_index}",
                        "模型要求生成摘要 sec，但没有可落锚的小节标题；已降级为摘要直属段落",
                    )
                sections.append(sm.AbstractSection(
                    self.rich_source(section_title_source)
                    if section_title_source else None,
                    tuple(paragraphs),
                    requested_wrapped and section_title_source is not None,
                ))
            self._record_gaps(
                content_scopes, content_sources,
                usage_id=f"abstract:{abstract_index}:notation",
                role="list-notation", punctuation_only=True,
            )
            blocks = []
            # graphics 只用于兼容旧的已保存理解结果。新流程由全文
            # 对象任务唯一判定 graphical-abstract。
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
            kind = abstract.get("abstract_type")
            if kind is None and "kind" in abstract:
                kind = abstract.get("kind")
            values.append(sm.Abstract(
                kind, tuple(sections), tuple(blocks),
                abstract.get("element") or "abstract",
                self.rich_source(label_source) if label_source else None,
                self.rich_source(title_source) if title_source else None,
                abstract.get("language"),
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
            graphical_title = None
            for item in self.body_json.get("objects") or []:
                if (not isinstance(item, dict)
                        or item.get("occurrence_id") not in inferred
                        or item.get("role") != "graphical-abstract"
                        or item.get("title_quote") is None):
                    continue
                candidate = self._front_quote(
                    item.get("title_quote"), "graphical-abstract:title"
                )
                if candidate:
                    graphical_title = candidate
                    break
            values.append(sm.Abstract(
                "graphical", (), tuple(
                    sm.Paragraph(
                        None, sm.RichText((sm.InlineGraphic(item, display=True),))
                    ) for item in inferred
                ), title=self.rich_source(graphical_title) if graphical_title else None,
            ))
        # JATS article-meta 中先放 abstract，再放 trans-abstract；同为
        # abstract 时，主摘要先于 precis/graphical 等功能性摘要。
        # sorted 是稳定的，因此同类摘要仍保留 Word 中的相对顺序。
        return tuple(sorted(values, key=lambda item: (
            item.element != "abstract", item.kind not in {None, "main"},
        )))

    def _keywords(self):
        raw_groups = self.front.get("keyword_groups")
        if not isinstance(raw_groups, list):
            legacy = self.front.get("keywords")
            raw_groups = [legacy] if isinstance(legacy, dict) else []
        result = []
        for group_index, raw in enumerate(raw_groups, 1):
            if not isinstance(raw, dict):
                continue
            quotes = raw.get("keyword_quotes") or []
            scopes = []
            for hint in raw.get("source_nodes") or []:
                node_id = _hint(self.source, hint)
                if node_id:
                    scopes.append((node_id, 0, len(self.source.node(node_id).text)))
            container_raw = raw.get("container_quote")
            container_source = self._front_quote(
                container_raw, f"keyword-group:{group_index}:container"
            )
            if container_source:
                self._record_semantic_use(
                    container_source, f"keyword-group:{group_index}:container",
                    "semantic-label",
                )
            if isinstance(container_raw, dict):
                scope = record_source_range(
                    self.view, container_raw.get("node_hint")
                )
                if scope and scope not in scopes:
                    scopes.append(scope)
            requests = []
            for index, item in enumerate(quotes):
                quote, _ = _quote_parts(item)
                if quote:
                    requests.append(GroundRequest(f"keyword:{index}", quote))
            allocated = (
                ground_ordered(requests, self.source, scopes=scopes)
                if scopes else None
            )
            keyword_sources = []
            if allocated and len(requests) == len(quotes):
                for index in range(len(requests)):
                    value = self._front_source(
                        SourceText((allocated[f"keyword:{index}"],)),
                        f"keyword-group:{group_index}:keyword:{index + 1}",
                    )
                    if value:
                        keyword_sources.append(value)
            else:
                for index, item in enumerate(quotes):
                    value = self._front_quote(
                        item, f"keyword-group:{group_index}:keyword:{index + 1}"
                    )
                    if value:
                        keyword_sources.append(value)
            label_source = (
                self._front_quote_scopes(
                    raw.get("label_quote"), scopes,
                    f"keyword-group:{group_index}:label",
                ) if scopes else self._front_quote(
                    raw.get("label_quote"),
                    f"keyword-group:{group_index}:label",
                )
            )
            title_source = (
                self._front_quote_scopes(
                    raw.get("title_quote"), scopes,
                    f"keyword-group:{group_index}:title",
                ) if scopes else self._front_quote(
                    raw.get("title_quote"),
                    f"keyword-group:{group_index}:title",
                )
            )
            self._record_gaps(
                scopes,
                [container_source, label_source, title_source, *keyword_sources],
                usage_id=f"keyword-group:{group_index}:notation",
                role="list-notation", punctuation_only=True,
            )
            result.append(sm.KeywordGroup(
                raw.get("group_type"),
                self.rich_source(title_source) if title_source else None,
                tuple(self.rich_source(item) for item in keyword_sources),
                self.rich_source(label_source) if label_source else None,
                raw.get("language"),
            ))
        return tuple(result)

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
                if title_source is not None:
                    # 标题尾部的标签分隔符按口径吸收（如「Funding:」→「Funding」）。
                    title_source = self._trim_trailing_separators(title_source)
                title = self.rich_source(title_source) if title_source else None
                if title is not None and not title.plain_text(self.source).strip():
                    # 落锚成空白的标题等于没有标题，不产出空 <title>。
                    title, title_source = None, None
                content_ids = []
                for raw in group.get("content_nodes") or []:
                    node_id = _hint(self.source, raw)
                    if node_id and node_id not in content_ids:
                        content_ids.append(node_id)
                if not content_ids:
                    # 另一种同样合规的输出形状：声明只列在 nodes 里、
                    # content_nodes 为空。声明源节点本身就是内容载体，
                    # 标题区间剥离后其剩余文字即正文。
                    content_ids = list(group_nodes)
                paragraphs = [self.paragraph(node_id) for node_id in content_ids]
                if title is None and group.get("title_quote") is not None:
                    self.issue(
                        "review_blocking", "DECLARATION_TITLE_UNRESOLVED", first,
                        f"{kind} 声明的标题摘抄未能落锚",
                    )
                elif title is None:
                    # 源稿本就没有印刷标题：不是落锚失败，模板标题由
                    # 出版配置在 enrich 阶段按 kind 补齐（有配置键记账）。
                    self.issue(
                        "warning", "DECLARATION_TITLE_ABSENT", first,
                        f"{kind} 声明无印刷标题",
                    )
                if title is not None:
                    title_nodes = {node_id for node_id, _, _ in title_source.ranges}
                    duplicate = title_nodes.intersection(content_ids)
                    if duplicate:
                        # 「标题: 正文」同段是源稿常态：只剥离标题字符区间，
                        # 剩余可见文字仍是该声明的正文，不得整节点丢弃。
                        self.issue(
                            "warning", "DECLARATION_TITLE_SPAN_STRIPPED",
                            first, f"标题与正文同段,已剥离标题区间: {sorted(duplicate)}",
                        )
                        paragraphs = []
                        for node_id in content_ids:
                            if node_id not in duplicate:
                                paragraphs.append(self.paragraph(node_id))
                                continue
                            for segment in self._strip_title_spans(
                                node_id, title_source
                            ):
                                paragraphs.append(
                                    sm.Paragraph(None, self.rich_source(segment))
                                )
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
        # 元信息 JATS 由头部任务直接交付，装配器不再从源指针重建标题、
        # 作者、机构与日期；摘要和关键词仍由独立任务返回源指针，
        # 再按与正文相同的原则从 Word 原文组装。
        abstracts = self._abstracts()
        keyword_groups = self._keywords()
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
        # 图表提及与实体编号的机械匹配在 bibr 之后进行：已包装的引用
        # 不再是纯文本区间，两类 xref 不会互相重叠。认不出的提及保持原文。
        body = link_display_object_callouts(body, self.source)
        document = sm.SemanticDoc(
            source=self.source,
            abstracts=abstracts,
            keyword_groups=keyword_groups, body=body, back_sections=back,
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
