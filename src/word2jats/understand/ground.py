"""模型摘抄到源字符区间的精度优先落锚器。

模型返回的文字只是定位证据，从不直接进入 SemanticDoc。
只有唯一且可证明的匹配才会返回源地址；歧义一律拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional, TYPE_CHECKING

from ..model.source import OBJECT_REPLACEMENT, SourceDocument, TextRange

if TYPE_CHECKING:
    from .serialize import SerializedDocument


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


def find_context_candidates(quote: str, doc: SourceDocument, *,
                            left_context: str, right_context: str,
                            scope: Optional[TextRange] = None,
                            block_hint=None,
                            allow_object: bool = False) -> list[TextRange]:
    """用同一源节点中的紧邻上下文区分重复短摘抄。

    上下文只是定位证据，返回区间仍仅覆盖 ``quote``。
    它们必须是模型所见原文的逐字摘抄；这里不做语义
    补全或格式推测。
    """
    if not all(isinstance(item, str) for item in (
        quote, left_context, right_context,
    )) or not quote:
        return []
    quote_candidates = find_candidates(
        quote, doc, scope=scope, block_hint=block_hint,
        allow_object=allow_object,
    )
    # 上下文只是消歧证据，不能推翻已经由“节点 + 原文 + 语义范围”
    # 唯一确定的区间。只有存在多个候选时才需要核对左右上下文。
    if len(quote_candidates) <= 1 or not (left_context or right_context):
        return quote_candidates

    def inside(candidate: TextRange) -> bool:
        return scope is None or (
            candidate[0] == scope[0]
            and scope[1] <= candidate[1] <= candidate[2] <= scope[2]
        )

    # scope 约束最终摘抄，而非消歧上下文。作者末尾的角标可以
    # 借助紧随作者范围之外的名单分隔符定位，但角标自身仍须在作者内。
    search_hint = block_hint or (scope[0] if scope is not None else None)
    needle = left_context + quote + right_context
    results = []
    for node, lo, hi in _scopes(doc, None, search_hint):
        for start, _ in _spans(node.text[lo:hi], needle):
            quote_start = lo + start + len(left_context)
            candidate = (node.node_id, quote_start, quote_start + len(quote))
            if inside(candidate) and _does_not_cross_object(doc, candidate, allow_object):
                results.append(candidate)
    if results:
        return results

    # 与普通落锚保持同一口径：Word 空白字符和智能引号/
    # 破折号可以经已冻结的 _normal_form 定位，但返回的
    # 仍是原始字符区间。除这些封闭对应外不做模糊匹配。
    normalized_left, _ = _normal_form(left_context)
    normalized_quote, _ = _normal_form(quote)
    normalized_right, _ = _normal_form(right_context)
    normalized_needle = normalized_left + normalized_quote + normalized_right
    normalized_results = []
    for node, lo, hi in _scopes(doc, None, search_hint):
        normalized_source, mapping = _normal_form(node.text[lo:hi])
        for start, _ in _spans(normalized_source, normalized_needle):
            q_start = start + len(normalized_left)
            q_end = q_start + len(normalized_quote)
            if q_end <= q_start:
                continue
            candidate = (
                node.node_id,
                lo + mapping[q_start][0],
                lo + mapping[q_end - 1][1],
            )
            if inside(candidate) and _does_not_cross_object(doc, candidate, allow_object):
                normalized_results.append(candidate)
    return normalized_results


def ground_context(quote: str, doc: SourceDocument, *,
                   left_context: str, right_context: str,
                   scope: Optional[TextRange] = None,
                   block_hint=None,
                   allow_object: bool = False) -> Optional[TextRange]:
    """只有“摘抄＋紧邻上下文”在指定范围内唯一时落锚。"""
    candidates = find_context_candidates(
        quote, doc, left_context=left_context, right_context=right_context,
        scope=scope, block_hint=block_hint, allow_object=allow_object,
    )
    return candidates[0] if len(candidates) == 1 else None


def _record_key(raw: str) -> str:
    key = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
    return key[:-2] if key.endswith("|表") else key


def _mapped_record_range(mapping, start: int, end: int) -> Optional[TextRange]:
    """把清单中一段连续文字还原成一段连续 Word 源字符。"""
    values = mapping[start:end]
    if not values or any(item is None for item in values):
        return None
    first = values[0]
    node_id = first[0]
    expected = first[1]
    previous = None
    for item in values:
        # 一个 Word 对象占位符在模型视图中会展开为“⟦公式#o1⟧”
        # 一类多字符标记，这些字符合法地重复映射到同一个源字符。
        if item == previous:
            continue
        if item[0] != node_id or item[1] != expected or item[2] != expected + 1:
            return None
        expected += 1
        previous = item
    return node_id, first[1], values[-1][2]


def ground_record_quote(quote: str, view: "SerializedDocument", *,
                        record_key: str, left_context: str,
                        right_context: str) -> Optional[TextRange]:
    """用“清单地址＋紧邻上下文＋原文”唯一定位源区间。

    左右上下文只是定位证据，不进入输出。查找完全基于模型
    所见的可寻址清单，因此同一机制可处理普通段落、软换行分段
    和表格行；不解析引文符号，也不根据文本含义猜位置。
    """
    if not all(isinstance(item, str) for item in (
        quote, record_key, left_context, right_context,
    )) or not quote or not record_key:
        return None
    record = view.by_key(_record_key(record_key))
    if record is None or len(record.source_map) != len(record.text):
        return None
    needle = left_context + quote + right_context
    results = []
    for start, _ in _spans(record.text, needle):
        quote_start = start + len(left_context)
        quote_end = quote_start + len(quote)
        mapped = _mapped_record_range(record.source_map, quote_start, quote_end)
        if mapped is not None and mapped not in results:
            results.append(mapped)
    if results:
        return results[0] if len(results) == 1 else None

    # 与全文落锚共用同一套封闭归一口径：仅等价视觉空白、
    # 智能引号和破折号，然后依靠 source_map 返回原始 Word 区间。
    normalized_record, normalized_map = _normal_form(record.text)
    normalized_left, _ = _normal_form(left_context)
    normalized_quote, _ = _normal_form(quote)
    normalized_right, _ = _normal_form(right_context)
    normalized_needle = normalized_left + normalized_quote + normalized_right
    normalized_results = []
    for start, _ in _spans(normalized_record, normalized_needle):
        quote_start = start + len(normalized_left)
        quote_end = quote_start + len(normalized_quote)
        if quote_end <= quote_start:
            continue
        display_start = normalized_map[quote_start][0]
        display_end = normalized_map[quote_end - 1][1]
        mapped = _mapped_record_range(record.source_map, display_start, display_end)
        if mapped is not None and mapped not in normalized_results:
            normalized_results.append(mapped)
    return normalized_results[0] if len(normalized_results) == 1 else None


def record_source_range(view: "SerializedDocument", record_key: str) -> Optional[TextRange]:
    """把一条模型可见记录还原为它在 Word 源节点中的完整字符区间。

    整条记录的地址本身已经是无歧义指针，无需再把其文字复制为 quote。
    一条记录若映射到多个源节点（如用于展示的表格行），就不能被当作
    一个连续文字段返回。
    """
    if not isinstance(record_key, str) or not record_key:
        return None
    record = view.by_key(_record_key(record_key))
    if record is None:
        return None
    mapped = [item for item in record.source_map if item is not None]
    if not mapped:
        return None
    node_ids = {item[0] for item in mapped}
    if len(node_ids) != 1:
        return None
    node_id = next(iter(node_ids))
    return node_id, min(item[1] for item in mapped), max(item[2] for item in mapped)


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
