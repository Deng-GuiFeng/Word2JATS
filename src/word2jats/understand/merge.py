"""专项判断的全局归并总闸与参考文献边界实体化。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..model.source import OBJECT_REPLACEMENT, SourceDocument, SourceText, TextRange
from .ground import Position, ground_sequence
from .passes import (
    UnderstandConfig, adjudicate_boundaries, discard_review,
    role_conflict_judge,
)
from .serialize import SerializedDocument


@dataclass(frozen=True)
class ReferenceSpan:
    index: int
    head: TextRange
    source: SourceText

    def text(self, document: SourceDocument) -> str:
        return self.source.text(document)


@dataclass(frozen=True)
class Assignment:
    source_kind: str  # node | object
    source_id: str
    role: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class MergeIssue:
    severity: str  # high | review_blocking | warning
    code: str
    source_id: str
    detail: str


@dataclass(frozen=True)
class DocumentAssignment:
    assignments: tuple[Assignment, ...]
    references: tuple[ReferenceSpan, ...]
    issues: tuple[MergeIssue, ...]
    audit: tuple[dict, ...] = ()

    @property
    def blocking(self) -> bool:
        return any(item.severity in {"high", "review_blocking"} for item in self.issues)

    def role(self, source_id: str) -> Optional[str]:
        item = next((part for part in self.assignments if part.source_id == source_id), None)
        return item.role if item else None


def project_body_to_assignment(view: SerializedDocument, body: dict,
                               assignment: DocumentAssignment) -> dict:
    """把专项判断中的候选规格投影到全局归并后的唯一主角色。

    一个源节点可能被初答同时放进表格数据和表注规格；归并裁决既然已经
    选定主角色，装配就不得继续消费被否定的候选。本函数只核对模式定义的
    实体字段与主角色，不读取文字内容。
    """
    roles = {item.source_id: item.role for item in assignment.assignments}

    def node_ids(raw):
        return _display_nodes(view, raw)

    def keep_nodes(values, role):
        return [raw for raw in values or []
                if any(roles.get(node_id) == role for node_id in node_ids(raw))]

    result = dict(body)
    tables = []
    for raw in body.get("tables") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["caption_nodes"] = keep_nodes(item.get("caption_nodes"), "table-caption")
        item["footnote_nodes"] = keep_nodes(item.get("footnote_nodes"), "table-footnote")
        item["flattened_row_nodes"] = keep_nodes(
            item.get("flattened_row_nodes"), "table"
        )
        table_nodes = node_ids(item.get("table_node"))
        if not (len(table_nodes) == 1 and roles.get(table_nodes[0]) == "table"
                and view.source.node(table_nodes[0]).kind == "table"):
            item["table_node"] = None
        graphic = item.get("graphic")
        if not isinstance(graphic, str) or roles.get(graphic) != "table-image":
            item["graphic"] = None
        tables.append(item)
    result["tables"] = tables

    figures = []
    for raw in body.get("figures") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["caption_nodes"] = keep_nodes(item.get("caption_nodes"), "figure-caption")
        item["graphics"] = [value for value in item.get("graphics") or []
                            if roles.get(value) == "figure"]
        figures.append(item)
    result["figures"] = figures

    groups = []
    for raw in body.get("figure_groups") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["caption_nodes"] = keep_nodes(item.get("caption_nodes"), "figure-caption")
        members = []
        for member_raw in item.get("members") or []:
            if not isinstance(member_raw, dict):
                continue
            member = dict(member_raw)
            member["caption_nodes"] = keep_nodes(
                member.get("caption_nodes"), "figure-caption"
            )
            member["graphics"] = [value for value in member.get("graphics") or []
                                   if roles.get(value) == "figure"]
            members.append(member)
        item["members"] = members
        groups.append(item)
    result["figure_groups"] = groups

    formulas = []
    for raw in body.get("formulas") or []:
        if not isinstance(raw, dict):
            continue
        expected = "display-formula" if raw.get("display") else "inline-formula"
        if roles.get(raw.get("occurrence_id")) == expected:
            formulas.append(raw)
    result["formulas"] = formulas

    specials = []
    for raw in body.get("special_blocks") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        role = item.get("role")
        item["nodes"] = keep_nodes(item.get("nodes"), role)
        specials.append(item)
    result["special_blocks"] = specials
    return result


def _actual_hint(source: SourceDocument, hint) -> Optional[str]:
    if not isinstance(hint, str):
        return None
    # 模型偶尔把清单的显示括号一并抄回。括号是地址
    # 语法而非源节点的一部分，因此在统一地址入口去除。
    if hint.startswith("[") and hint.endswith("]"):
        hint = hint[1:-1]
    # 序列化清单把原生表显示为 ``[doc/tblN|表]``。
    # 这是公开的地址方言，在所有归并入口统一还原。
    if hint.endswith("|表"):
        hint = hint[:-2]
    if hint in source._nodes:
        return hint
    # 清单的软换行后缀 ``.2`` 不是新源节点。
    head, dot, tail = hint.rpartition(".")
    if dot and tail.isdigit() and head in source._nodes:
        return head
    return None


def _heads(payload: dict, source: SourceDocument):
    items = []
    for raw in payload.get("entries") or []:
        if not isinstance(raw, dict):
            continue
        quote = raw.get("head_quote")
        hint = _actual_hint(source, raw.get("node_hint"))
        if isinstance(quote, str) and quote:
            items.append((quote, hint))
    return items


def grounded_heads(payload: dict, source: SourceDocument) -> list[Optional[TextRange]]:
    return ground_sequence(_heads(payload, source), source)


def reconcile_boundaries(view: SerializedDocument, llm, left: dict, right: dict,
                         config=UnderstandConfig()):
    """先比较两路落锚证据；只在真分歧时发起第三路裁决。"""
    left_ranges = grounded_heads(left, view.source)
    right_ranges = grounded_heads(right, view.source)
    issues = []
    audit = []
    # 边界的实体是条目起点，而不是模型为证明这个起点所摘抄的整段文字。
    # 两路摘抄可以一长一短；某一路的摘抄若不能落锚，也不是一条反证。
    # 只有两条均可落锚且起点不同的证据才构成真正分歧。
    if left_ranges and len(left_ranges) == len(right_ranges):
        chosen_entries = []
        conflict = False
        for left_raw, right_raw, left_range, right_range in zip(
            left.get("entries") or [], right.get("entries") or [],
            left_ranges, right_ranges,
        ):
            if left_range and right_range:
                if left_range[:2] != right_range[:2]:
                    conflict = True
                    break
                chosen_entries.append(left_raw)
            elif left_range:
                chosen_entries.append(left_raw)
            elif right_range:
                chosen_entries.append(right_raw)
            else:
                conflict = True
                break

        left_end_raw = left.get("first_non_reference_after")
        right_end_raw = right.get("first_non_reference_after")
        left_end = _actual_hint(view.source, left_end_raw)
        right_end = _actual_hint(view.source, right_end_raw)
        end_agrees = (
            left_end == right_end
            and ((left_end is not None) or (left_end_raw is None and right_end_raw is None))
        )
        if not conflict and len(chosen_entries) == len(left_ranges) and end_agrees:
            result = dict(left)
            result["entries"] = chosen_entries
            left_title = _actual_hint(view.source, left.get("reference_title_node"))
            right_title = _actual_hint(view.source, right.get("reference_title_node"))
            if left_title is None and right_title is not None:
                result["reference_title_node"] = right.get("reference_title_node")
            elif left_title and right_title and left_title != right_title:
                conflict = True
            if not conflict:
                return result, tuple(issues), tuple(audit)
    judged, meta = adjudicate_boundaries(view, llm, left, right, config)
    audit.append(meta)
    judged_ranges = grounded_heads(judged, view.source)
    if not judged_ranges or not all(judged_ranges):
        issues.append(MergeIssue(
            "review_blocking", "REFERENCE_BOUNDARY_UNRESOLVED", "reference-list",
            "A/B 切条分歧经裁决后仍无法全部唯一落锚",
        ))
        # 裁决失败不能反过来销毁一条完整、可落锚的独立结论。
        # 候选仍保留可恢复的最好证据，但阻断项保证它不会正式交付。
        for candidate, ranges in ((left, left_ranges), (right, right_ranges)):
            if ranges and all(ranges):
                return candidate, tuple(issues), tuple(audit)
    return judged, tuple(issues), tuple(audit)


def _reference_nodes(view: SerializedDocument) -> list[str]:
    result = []
    for record in view.records:
        if record.part != "document":
            continue
        for node_id in record.source_nodes:
            node = view.source.node(node_id)
            if node.kind == "para" and node_id not in result:
                result.append(node_id)
    return result


def build_reference_spans(view: SerializedDocument, payload: dict) -> tuple[ReferenceSpan, ...]:
    source = view.source
    heads = grounded_heads(payload, source)
    if not heads or not all(heads):
        return ()
    starts = [item for item in heads if item is not None]
    if starts != sorted(starts, key=lambda item: (source.node(item[0]).order, item[1])):
        return ()
    nodes = _reference_nodes(view)
    node_index = {node_id: index for index, node_id in enumerate(nodes)}
    end_hint = _actual_hint(source, payload.get("first_non_reference_after"))

    spans = []
    for index, start in enumerate(starts):
        if index + 1 < len(starts):
            end = (starts[index + 1][0], starts[index + 1][1])
        elif end_hint and end_hint in node_index:
            end = (end_hint, 0)
        else:
            end = (nodes[-1], len(source.node(nodes[-1]).text))
        start_index = node_index.get(start[0])
        end_index = node_index.get(end[0])
        if start_index is None or end_index is None or end_index < start_index:
            return ()
        ranges = []
        for position in range(start_index, end_index + 1):
            node_id = nodes[position]
            lo = start[1] if position == start_index else 0
            hi = end[1] if position == end_index else len(source.node(node_id).text)
            if hi > lo:
                ranges.append((node_id, lo, hi))
        if not ranges:
            return ()
        spans.append(ReferenceSpan(index + 1, start, SourceText(tuple(ranges))))
    return tuple(spans)


def _claim(claims, source_id, role, evidence):
    if source_id:
        claims.setdefault(source_id, []).append((role, evidence))


def _display_nodes(view: SerializedDocument, raw) -> tuple[str, ...]:
    if not isinstance(raw, str):
        return ()
    actual = _actual_hint(view.source, raw)
    if actual:
        return (actual,)
    record = view.by_key(raw)
    return record.source_nodes if record else ()


def _body_claims(body: dict, view: SerializedDocument):
    claims = {}

    def claim_native_table(node_id: str, evidence: str) -> None:
        _claim(claims, node_id, "table", evidence)
        # 原生表的语义身份属于整棵 OOXML 表树。一次证明表根后，
        # 单元格内容无需再让模型逐项复述。
        for descendant in view.source.nodes:
            parent = descendant.parent
            while parent:
                if parent == node_id:
                    if descendant.text:
                        _claim(claims, descendant.node_id, "table", evidence)
                    break
                parent = view.source.node(parent).parent

    for group in body.get("blocks") or []:
        if not isinstance(group, dict):
            continue
        role = group.get("role")
        if not isinstance(role, str):
            continue
        for raw in group.get("nodes") or []:
            for node_id in _display_nodes(view, raw):
                _claim(
                    claims, node_id, role,
                    f"body:block role={role} level={group.get('level')} kind={group.get('kind')}",
                )
                if view.source.node(node_id).kind == "table" and role == "table":
                    claim_native_table(node_id, "native-table-parent")
        if role == "declaration":
            for raw in group.get("content_nodes") or []:
                for node_id in _display_nodes(view, raw):
                    _claim(
                        claims, node_id, role,
                        f"body:declaration-content kind={group.get('kind')}",
                    )
    for item in body.get("objects") or []:
        if isinstance(item, dict) and isinstance(item.get("occurrence_id"), str):
            _claim(claims, item["occurrence_id"], item.get("role") or "object", "body-object")

    # 图、表、公式规格不只是装配参数，它们同时对源节点/对象
    # 声明了角色。必须与 blocks/objects 一起进入全局归并，否则
    # “同一段既是图注又是正文”会被静默重复输出。
    for figure in body.get("figures") or []:
        if not isinstance(figure, dict):
            continue
        for raw in figure.get("caption_nodes") or []:
            for node_id in _display_nodes(view, raw):
                _claim(claims, node_id, "figure-caption", "body:figure-caption-node")
        for occurrence_id in figure.get("graphics") or []:
            if isinstance(occurrence_id, str):
                _claim(claims, occurrence_id, "figure", "body:figure-graphic")
    for group in body.get("figure_groups") or []:
        if not isinstance(group, dict):
            continue
        for raw in group.get("caption_nodes") or []:
            for node_id in _display_nodes(view, raw):
                _claim(claims, node_id, "figure-caption", "body:figure-group-caption-node")
        for member in group.get("members") or []:
            if not isinstance(member, dict):
                continue
            for raw in member.get("caption_nodes") or []:
                for node_id in _display_nodes(view, raw):
                    _claim(claims, node_id, "figure-caption", "body:figure-member-caption-node")
            for occurrence_id in member.get("graphics") or []:
                if isinstance(occurrence_id, str):
                    _claim(claims, occurrence_id, "figure", "body:figure-group-member")
    for table in body.get("tables") or []:
        if not isinstance(table, dict):
            continue
        for raw in table.get("caption_nodes") or []:
            for node_id in _display_nodes(view, raw):
                _claim(claims, node_id, "table-caption", "body:table-caption-node")
        for raw in [table.get("table_node"), *(table.get("flattened_row_nodes") or [])]:
            for node_id in _display_nodes(view, raw):
                if view.source.node(node_id).kind == "table":
                    claim_native_table(node_id, "body:table-source")
                else:
                    _claim(claims, node_id, "table", "body:table-source")
        for raw in table.get("footnote_nodes") or []:
            for node_id in _display_nodes(view, raw):
                _claim(claims, node_id, "table-footnote", "body:table-footnote-node")
        graphic = table.get("graphic")
        if isinstance(graphic, str):
            _claim(claims, graphic, "table-image", "body:table-graphic")
    for formula in body.get("formulas") or []:
        if not isinstance(formula, dict):
            continue
        occurrence_id = formula.get("occurrence_id")
        if isinstance(occurrence_id, str):
            role = "display-formula" if formula.get("display") else "inline-formula"
            _claim(claims, occurrence_id, role, "body:formula-spec")
    for special in body.get("special_blocks") or []:
        if not isinstance(special, dict):
            continue
        role = special.get("role")
        if role not in {"glossary", "definition-list"}:
            continue
        for raw in special.get("nodes") or []:
            for node_id in _display_nodes(view, raw):
                _claim(claims, node_id, role, f"body:special-block role={role}")
    return claims


def merge_assignments(view: SerializedDocument, front: dict, body: dict,
                      boundary: dict, references: tuple[ReferenceSpan, ...], llm,
                      config=UnderstandConfig(), prior_issues: Iterable[MergeIssue] = (),
                      prior_audit: Iterable[dict] = ()) -> DocumentAssignment:
    source = view.source
    claims = _body_claims(body, view)
    front_nodes = {
        node_id for raw in front.get("front_nodes") or []
        for node_id in [_actual_hint(source, raw)] if node_id
    }
    for raw in front.get("front_nodes") or []:
        node_id = _actual_hint(source, raw)
        _claim(
            claims, node_id, "front",
            f"front:listed-front; body_start={front.get('body_start_node')}",
        )

    # 给裁决器的不只是“在 front 清单里”这一条泛证据，还要指出
    # 哪个具体 front 语义指针使用了该节点。仍不设固定优先级；两路
    # 冲突照常交独立裁决，只消除角色词义不清造成的信息损失。
    def semantic_front_hints(value, path="front"):
        if isinstance(value, dict):
            # head-metadata-v1.0 的每个可见字段直接携带显示记录地址；
            # 该地址本身就是 front 主角色证据，不再依赖 front_nodes 总清单。
            if set(value) == {"node", "quote"}:
                for node_id in _display_nodes(view, value.get("node")):
                    yield node_id, path
                return
            hint = value.get("node_hint")
            node_id = _actual_hint(source, hint)
            if node_id in front_nodes:
                yield node_id, path
            for key, item in value.items():
                if key in {"front_nodes", "body_start_node", "issues"}:
                    continue
                if key == "source_nodes" and isinstance(item, list):
                    for raw in item:
                        node_id = _actual_hint(source, raw)
                        if node_id in front_nodes:
                            yield node_id, f"{path}.source_nodes"
                else:
                    yield from semantic_front_hints(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from semantic_front_hints(item, f"{path}[{index}]")

    for node_id, path in semantic_front_hints(front):
        _claim(claims, node_id, "front", f"front:semantic-pointer {path}")
    for abstract in front.get("abstracts") or []:
        if (not isinstance(abstract, dict)
                or (abstract.get("abstract_type") or abstract.get("kind")) != "graphical"):
            continue
        for occurrence_id in abstract.get("graphics") or []:
            if isinstance(occurrence_id, str):
                _claim(
                    claims, occurrence_id, "graphical-abstract",
                    "front:graphical-abstract",
                )
    title_node = _actual_hint(source, boundary.get("reference_title_node"))
    _claim(claims, title_node, "reference-title", "refs-boundary")
    for span in references:
        for node_id, _, _ in span.source.ranges:
            _claim(claims, node_id, "reference-entry", "refs-boundary")

    issues = list(prior_issues)
    audit = list(prior_audit)
    resolved = {}
    conflicts = []
    for source_id, values in claims.items():
        roles = sorted(set(role for role, _ in values))
        # front 是排他性主归属，不是可被其他角色静默覆盖的“泛标签”。
        # 例如标题被 body 误判成章节时，必须交独立裁决。
        material = roles
        if len(material) == 1:
            resolved[source_id] = material[0]
        else:
            conflicts.append({"source_id": source_id, "roles": material,
                              "evidence": values})
    if conflicts:
        judged, meta = role_conflict_judge(view.render(), conflicts, llm, config)
        audit.append({**meta, "task": "merge-judge"})
        decisions = {item.get("source_id"): item.get("role")
                     for item in judged.get("decisions") or [] if isinstance(item, dict)}
        unresolved = set(judged.get("unresolved") or [])
        for conflict in conflicts:
            source_id = conflict["source_id"]
            role = decisions.get(source_id)
            if role in conflict["roles"] and source_id not in unresolved:
                resolved[source_id] = role
            else:
                issues.append(MergeIssue(
                    "review_blocking", "ROLE_CONFLICT_UNRESOLVED", source_id,
                    f"候选角色: {conflict['roles']}",
                ))

    discard_candidates = []
    for source_id, role in resolved.items():
        if role not in {"blank", "decorative"}:
            continue
        if source_id in source._nodes:
            node = source.node(source_id)
            visible = "".join(
                char for char in node.text
                if char != OBJECT_REPLACEMENT and not char.isspace()
            )
            if visible:
                discard_candidates.append({
                    "source_id": source_id, "proposed_role": role,
                    "kind": "text-node", "visible_text": node.text,
                })
        elif source_id in source._occurrences:
            occurrence = source.occurrence(source_id)
            discard_candidates.append({
                "source_id": source_id, "proposed_role": role,
                "kind": "object-occurrence", "owner_node": occurrence.node_id,
                "object_kind": occurrence.kind,
            })

    discard_approved = set()
    if discard_candidates:
        reviewed, meta = discard_review(
            view.render(), discard_candidates, llm, config
        )
        audit.append({**meta, "task": "discard-review"})
        supplied = {item["source_id"] for item in discard_candidates}
        approved = {
            item.get("source_id") for item in reviewed.get("approved") or []
            if isinstance(item, dict) and item.get("source_id") in supplied
        }
        unresolved = {
            item for item in reviewed.get("unresolved") or [] if item in supplied
        }
        if approved.isdisjoint(unresolved) and approved | unresolved == supplied:
            discard_approved = approved
        for source_id in sorted(supplied - discard_approved):
            issues.append(MergeIssue(
                "review_blocking", "NONEMPTY_DISCARD_UNRESOLVED", source_id,
                "非空文字或对象的弃置未经独立复核同意",
            ))

    # 模板部件不进语义正文，其余可见文字/对象必须有主角。
    visible_nodes = []
    for node in source.nodes:
        if node.part.startswith(("header", "footer")):
            continue
        if node.text and any(char != OBJECT_REPLACEMENT and not char.isspace()
                             for char in node.text):
            visible_nodes.append(node.node_id)
    for node_id in visible_nodes:
        if node_id not in resolved:
            issues.append(MergeIssue(
                "high", "VISIBLE_NODE_UNCLAIMED", node_id,
                repr(source.node(node_id).text[:120]),
            ))
    for occurrence in source.occurrences:
        if source.node(occurrence.node_id).part.startswith(("header", "footer")):
            continue
        if occurrence.occ_id not in resolved:
            issues.append(MergeIssue(
                "high", "OBJECT_UNCLAIMED", occurrence.occ_id, occurrence.kind,
            ))

    assignments = tuple(
        Assignment("object" if source_id in source._occurrences else "node",
                   source_id, role,
                   tuple(evidence for _, evidence in claims.get(source_id, ()))
                   + (("discard-review:approved",)
                      if source_id in discard_approved else ()))
        for source_id, role in sorted(
            resolved.items(),
            key=lambda item: (
                source.node(item[0]).order if item[0] in source._nodes
                else source.node(source.occurrence(item[0]).node_id).order,
                item[0],
            )
        )
    )
    return DocumentAssignment(assignments, references, tuple(issues), tuple(audit))
