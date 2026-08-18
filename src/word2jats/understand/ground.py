"""模型摘抄到源字符区间的精度优先落锚器。

模型返回的文字只是定位证据，从不直接进入 SemanticDoc。
只有唯一且可证明的匹配才会返回源地址；歧义一律拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional

from ..model.source import OBJECT_REPLACEMENT, SourceDocument, TextRange


@dataclass(frozen=True, order=True)
class Position:
    node_id: str
    offset: int


@dataclass(frozen=True)
class GroundRequest:
    name: str
    quote: str
    block_hint: Optional[str] = None
    allow_object: bool = False


_QUOTES = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-",
})


def _ordered_nodes(doc: SourceDocument):
    return sorted(doc.nodes, key=lambda item: (item.order, item.node_id))


def _position_key(doc: SourceDocument, value: Position | TextRange):
    order = {node.node_id: node.order for node in doc.nodes}
    if isinstance(value, Position):
        return order[value.node_id], value.offset
    return order[value[0]], value[1]


def _normal_form(text: str) -> tuple[str, list[tuple[int, int]]]:
    """返回定死的初版归一结果及归一字符到原区间的映射。"""
    out: list[str] = []
    mapping: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        char = text[index]
        # 序列化清单把源 Tab 显示为可见的 ⇥。模型只能逐字抄回
        # 这个公开方言；落锚时它与源 Tab 同属一个空白归一类。
        if char.isspace() or char == "⇥":
            end = index + 1
            while end < len(text) and (text[end].isspace() or text[end] == "⇥"):
                end += 1
            out.append(" ")
            mapping.append((index, end))
            index = end
            continue
        out.append(char.translate(_QUOTES))
        mapping.append((index, index + 1))
        index += 1
    return "".join(out), mapping


def _spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    result = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return result
        result.append((index, index + len(needle)))
        start = index + 1


def _scopes(doc: SourceDocument, scope: Optional[TextRange], block_hint):
    if scope is not None:
        node_id, start, end = scope
        doc.slice_text(scope)
        return [(doc.node(node_id), start, end)]
    hints = set()
    if isinstance(block_hint, str):
        hints.add(block_hint)
    elif block_hint:
        hints.update(block_hint)
    nodes = _ordered_nodes(doc)
    if hints:
        selected = [node for node in nodes if node.node_id in hints]
        if selected:
            nodes = selected
    return [(node, 0, len(node.text)) for node in nodes if node.text]


def _after_filter(doc: SourceDocument, ranges: Iterable[TextRange],
                  after: Optional[Position]):
    if after is None:
        return list(ranges)
    bound = _position_key(doc, after)
    return [item for item in ranges if _position_key(doc, item) >= bound]


def _does_not_cross_object(doc: SourceDocument, item: TextRange,
                           allow_object: bool) -> bool:
    return allow_object or OBJECT_REPLACEMENT not in doc.slice_text(item)


def find_candidates(quote: str, doc: SourceDocument, *,
                    scope: Optional[TextRange] = None,
                    after: Optional[Position] = None,
                    block_hint=None,
                    allow_object: bool = False) -> list[TextRange]:
    """收集最高优先级的全部候选：有精确候选时不混入归一候选。"""
    if not isinstance(quote, str) or not quote:
        return []
    exact = []
    scopes = _scopes(doc, scope, block_hint)
    for node, lo, hi in scopes:
        for left, right in _spans(node.text[lo:hi], quote):
            exact.append((node.node_id, lo + left, lo + right))
    exact = _after_filter(doc, exact, after)
    exact = [item for item in exact if _does_not_cross_object(doc, item, allow_object)]
    if exact:
        return exact

    normalized_quote, _ = _normal_form(quote)
    if not normalized_quote:
        return []
    normalized = []
    for node, lo, hi in scopes:
        source, mapping = _normal_form(node.text[lo:hi])
        for left, right in _spans(source, normalized_quote):
            if right <= left:
                continue
            original_left = lo + mapping[left][0]
            original_right = lo + mapping[right - 1][1]
            normalized.append((node.node_id, original_left, original_right))
    normalized = _after_filter(doc, normalized, after)
    return [item for item in normalized
            if _does_not_cross_object(doc, item, allow_object)]


def ground(quote: str, doc: SourceDocument, *,
           scope: Optional[TextRange] = None,
           after: Optional[Position] = None,
           block_hint=None,
           allow_object: bool = False) -> Optional[TextRange]:
    """只有候选唯一时落锚，否则返回 ``None``。"""
    candidates = find_candidates(
        quote, doc, scope=scope, after=after, block_hint=block_hint,
        allow_object=allow_object,
    )
    return candidates[0] if len(candidates) == 1 else None


def ground_sequence(items: Iterable[tuple[str, Optional[str]]],
                    doc: SourceDocument) -> list[Optional[TextRange]]:
    """对确有源文顺序的摘抄序列施加 ``after`` 约束。"""
    result = []
    after = None
    for quote, hint in items:
        match = ground(quote, doc, after=after, block_hint=hint)
        result.append(match)
        if match is not None:
            after = Position(match[0], match[2])
    return result


def ground_ordered(requests: Iterable[GroundRequest], doc: SourceDocument, *,
                   scopes: Iterable[TextRange]) -> Optional[dict[str, TextRange]]:
    """对作者名单等确有顺序的序列做唯一全局分配。"""
    reqs = tuple(requests)
    scopes = tuple(scopes)
    order = {node.node_id: node.order for node in doc.nodes}
    candidates = {}
    for request in reqs:
        found = []
        for scope in scopes:
            found.extend(find_candidates(
                request.quote, doc, scope=scope,
                allow_object=request.allow_object,
            ))
        if not found:
            return None
        candidates[request.name] = sorted(
            set(found), key=lambda item: (order[item[0]], item[1], item[2])
        )
    solutions = []

    def key(item):
        return order[item[0]], item[1]

    def search(index, after, chosen):
        if len(solutions) > 1:
            return
        if index == len(reqs):
            solutions.append(dict(chosen))
            return
        request = reqs[index]
        for candidate in candidates[request.name]:
            if after is not None and key(candidate) < after:
                continue
            chosen[request.name] = candidate
            search(index + 1, (order[candidate[0]], candidate[2]), chosen)
            chosen.pop(request.name, None)

    search(0, None, {})
    return solutions[0] if len(solutions) == 1 else None


def ground_joint(requests: Iterable[GroundRequest], doc: SourceDocument, *,
                 scope: TextRange | Iterable[TextRange],
                 source_order: Iterable[str] = ()) -> Optional[dict[str, TextRange]]:
    """
    对一条参考文献的字段一次联合分配。

    各字段顺序和字面形态都不被预设；程序只核对模型指明的
    原文区间是否互不重叠，且整体分配是否唯一。
    """
    reqs = list(requests)
    if len({item.name for item in reqs}) != len(reqs):
        raise ValueError("联合落锚字段名重复")
    candidates: dict[str, list[TextRange]] = {}
    scopes = (scope,) if (
        isinstance(scope, tuple) and len(scope) == 3 and isinstance(scope[0], str)
    ) else tuple(scope)
    for request in reqs:
        values = []
        for text_scope in scopes:
            values.extend(find_candidates(
                request.quote, doc, scope=text_scope,
                block_hint=request.block_hint,
                allow_object=request.allow_object,
            ))
        if not values:
            return None
        candidates[request.name] = values

    solutions: list[dict[str, TextRange]] = []
    ordered = sorted(reqs, key=lambda item: len(candidates[item.name]))
    source_order = tuple(source_order)
    if len(source_order) != len(set(source_order)):
        raise ValueError("联合落锚源文次序字段重复")
    unknown_order = set(source_order) - set(candidates)
    if unknown_order:
        raise ValueError(f"联合落锚源文次序引用未知字段: {sorted(unknown_order)}")
    node_order = {node.node_id: node.order for node in doc.nodes}

    def overlaps(left: TextRange, right: TextRange) -> bool:
        return left[0] == right[0] and left[1] < right[2] and right[1] < left[2]

    def search(index: int, chosen: dict[str, TextRange]):
        if len(solutions) > 1:
            return
        if index == len(ordered):
            positions = [
                (node_order[chosen[name][0]], chosen[name][1])
                for name in source_order
            ]
            if positions != sorted(positions):
                return
            solutions.append(dict(chosen))
            return
        request = ordered[index]
        for candidate in candidates[request.name]:
            if any(overlaps(candidate, old) for old in chosen.values()):
                continue
            chosen[request.name] = candidate
            search(index + 1, chosen)
            chosen.pop(request.name, None)

    search(0, {})
    return solutions[0] if len(solutions) == 1 else None
