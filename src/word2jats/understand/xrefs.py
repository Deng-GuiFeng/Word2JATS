"""把理解层指明的正文引用区间连到参考文献实体。

引用的可见写法属于开放集，本模块不用正则或词形规则猜测。
它只做封闭的机械工作：落锚两端源指针、核对目标实体、保持可见原文不变。
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from ..model.source import SourceDocument, SourceText, TextRange
from ..semantic import model as sm
from .ground import (
    _mapped_record_range, ground_quote_anywhere, ground_record_quote,
)
from .serialize import SerializedDocument, serialize


def _quote_range(raw, view: SerializedDocument) -> TextRange | None:
    if not isinstance(raw, dict):
        return None
    quote = raw.get("quote")
    if not isinstance(quote, str) or not quote:
        return None
    grounded = ground_record_quote(
        quote, view, record_key=raw.get("record_key"),
        left_context=raw.get("left_context"),
        right_context=raw.get("right_context"),
    )
    if grounded is not None:
        return grounded
    # 模型转写上下文是长文档的常见失误；完整上下文在全文唯一时
    # 仍可无歧义落锚，不唯一立即放弃。
    grounded = ground_quote_anywhere(
        quote, view,
        left_context=raw.get("left_context") or "",
        right_context=raw.get("right_context") or "",
    )
    if grounded is not None:
        return grounded
    # 纯数字引文编号是自识别的：在模型指明的记录里，该数字作为独立
    # token（前后均非字母数字，排除 2024、[18F] 这类嵌入形态）恰好
    # 出现一次时，即为无歧义位置；多处或零处一律不认。
    if quote.isdigit():
        return _record_unique_digit(quote, raw.get("record_key"), view)
    return None


def _record_unique_digit(quote: str, record_key, view) -> TextRange | None:
    if not isinstance(record_key, str) or not record_key:
        return None
    record = view.by_key(record_key)
    if record is None or len(record.source_map) != len(record.text):
        return None
    text = record.text
    positions = []
    start = 0
    while True:
        index = text.find(quote, start)
        if index < 0:
            break
        before = text[index - 1] if index > 0 else ""
        after = text[index + len(quote)] if index + len(quote) < len(text) else ""
        if not (before.isalnum() or after.isalnum()):
            positions.append(index)
            if len(positions) > 1:
                return None
        start = index + 1
    if len(positions) != 1:
        return None
    return _mapped_record_range(
        record.source_map, positions[0], positions[0] + len(quote)
    )


def _resolved_occurrences(raw_items, reference_list, spans, source, view):
    issues = []
    resolved = []
    references = reference_list.references if reference_list else ()
    seen = set()
    for number, raw in enumerate(raw_items, 1):
        citation = _quote_range(
            raw.get("citation_quote") if isinstance(raw, dict) else None, view
        )
        if citation is None:
            issues.append(("citation", 0, 0, f"第 {number} 个正文引用未唯一落锚"))
            continue
        targets = raw.get("target_reference_ids") if isinstance(raw, dict) else None
        if not isinstance(targets, list) or not targets:
            issues.append((*citation, f"第 {number} 个正文引用没有目标文献指针"))
            continue
        target_ids = []
        valid = True
        known = {item.entity_id: item for item in references}
        for target_id in targets:
            if not isinstance(target_id, str) or target_id not in known:
                valid = False
                break
            if target_id not in target_ids:
                target_ids.append(target_id)
        if not valid:
            issues.append((*citation, f"第 {number} 个正文引用的目标文献未唯一落锚"))
            continue
        fingerprint = (citation, tuple(target_ids))
        if fingerprint not in seen:
            seen.add(fingerprint)
            resolved.append(fingerprint)

    resolved.sort(key=lambda item: (
        source.node(item[0][0]).order, item[0][1], item[0][2]
    ))
    accepted = []
    for item in resolved:
        citation = item[0]
        if any(citation[0] == old[0][0]
               and citation[1] < old[0][2] and old[0][1] < citation[2]
               for old in accepted):
            issues.append((*citation, "正文引用源区间互相重叠"))
            continue
        accepted.append(item)
    return accepted, issues


def _link_source(value: SourceText, occurrences, used):
    parts = []
    for node_id, start, end in value.ranges:
        selected = [
            item for item in occurrences
            if item[0][0] == node_id and start <= item[0][1] < item[0][2] <= end
        ]
        cursor = start
        for source_range, target_ids, ref_type in selected:
            left, right = source_range[1], source_range[2]
            if left > cursor:
                parts.append(sm.Text(SourceText(((node_id, cursor, left),))))
            content = sm.RichText.from_source(SourceText((source_range,)))
            parts.append(sm.CrossReference(ref_type, target_ids, content, source_range))
            used.add((source_range, target_ids, ref_type))
            cursor = right
        if cursor < end:
            parts.append(sm.Text(SourceText(((node_id, cursor, end),))))
    return sm.RichText(tuple(parts))


def _rich(value, occurrences, used):
    parts = []
    for part in value.parts:
        if isinstance(part, sm.Text):
            parts.extend(_link_source(part.source, occurrences, used).parts)
        elif isinstance(part, sm.Styled):
            parts.append(replace(part, content=_rich(part.content, occurrences, used)))
        elif isinstance(part, (sm.ExternalLink, sm.EmailInline, sm.CitationFieldInline)):
            parts.append(replace(part, content=_rich(part.content, occurrences, used)))
        else:
            parts.append(part)
    return sm.RichText(tuple(parts))


def _caption(value, occurrences, used, captions=True):
    if value is None or not captions:
        return value
    return replace(
        value,
        title=_rich(value.title, occurrences, used) if value.title else None,
        paragraphs=tuple(_block(item, occurrences, used) for item in value.paragraphs),
    )


def _block(value, occurrences, used, captions=True):
    if isinstance(value, sm.Paragraph):
        return replace(value, content=_rich(value.content, occurrences, used))
    if isinstance(value, sm.Section):
        return replace(value, blocks=tuple(
            _block(item, occurrences, used, captions) for item in value.blocks
        ))
    if isinstance(value, sm.Figure):
        return replace(
            value, caption=_caption(value.caption, occurrences, used, captions)
        )
    if isinstance(value, sm.FigureGroup):
        return replace(
            value,
            caption=_caption(value.caption, occurrences, used, captions),
            figures=tuple(
                _block(item, occurrences, used, captions) for item in value.figures
            ),
        )
    if isinstance(value, sm.TableBlock):
        def row(item):
            return replace(item, cells=tuple(
                replace(cell, content=_rich(cell.content, occurrences, used))
                for cell in item.cells
            ))
        return replace(
            value, caption=_caption(value.caption, occurrences, used, captions),
            header_rows=tuple(row(item) for item in value.header_rows),
            body_rows=tuple(row(item) for item in value.body_rows),
        )
    return value


def link_bibliographic_citations(blocks, reference_list, reference_spans,
                                 source: SourceDocument, raw_items: Iterable[dict], *,
                                 view: SerializedDocument | None = None):
    """仅按已落锚的源到源关系包装 xref，不解析引用字面语法。"""
    view = view or serialize(source)
    accepted, issues = _resolved_occurrences(
        tuple(raw_items), reference_list, tuple(reference_spans), source, view
    )
    occurrences = [
        (source_range, target_ids, "bibr") for source_range, target_ids in accepted
    ]
    used = set()
    linked = tuple(_block(item, occurrences, used) for item in blocks)
    for item in occurrences:
        if item not in used:
            issues.append((*item[0], "已落锚引用不在任一正文输出区间内"))
    return linked, tuple(issues)


# 图表提及的英文书写是个很小的约定集合，与样例无关；除这些通用书写外
# 一律不认。编号来自实体自身印出的 label，认不出或同号歧义宁可不链。
_CALLOUT_WORDS = {"fig": r"Fig(?:ure)?s?", "table": r"Tables?"}
_NUMBER_TOKEN = re.compile(r"\d+")
_CALLOUT_SEPARATOR = re.compile(
    r"(?:[A-Za-z](?![A-Za-z]))?\s*(?:,|;|&|and|to|–|—|-)\s*", re.IGNORECASE
)
# 补充材料的图表不是文内实体，其提及不得链到文内同号图表。
_SUPPLEMENTARY_BEFORE = re.compile(
    r"(?:supplementar|suppl|supp)\w*\.?\s*$", re.IGNORECASE
)
# 文字式数词提及（如 "table eight"）：整个短语包进 xref（与结构参考口径一致），
# 且不做序列展开。
_WORD_NUMBERS = {
    word: str(index) for index, word in enumerate((
        "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    ), 1)
}
_WORD_NUMBER_TOKEN = re.compile(
    r"(%s)\b" % "|".join(_WORD_NUMBERS), re.IGNORECASE
)


def _callout_occurrences(source: SourceDocument, numbers_by_kind):
    occurrences = []
    for kind, numbers in numbers_by_kind.items():
        if not any(numbers.values()):
            continue
        prefix = re.compile(
            rf"\b{_CALLOUT_WORDS[kind]}\.?[\s ]*", re.IGNORECASE
        )
        for node in source.nodes:
            text = node.text
            for match in prefix.finditer(text):
                if _SUPPLEMENTARY_BEFORE.search(text, 0, match.start()):
                    continue
                position = match.end()
                if not _NUMBER_TOKEN.match(text, position):
                    word = _WORD_NUMBER_TOKEN.match(text, position)
                    if word:
                        target = numbers.get(_WORD_NUMBERS[word.group(1).lower()])
                        if target:
                            occurrences.append((
                                (node.node_id, match.start(), word.end()),
                                (target,), kind,
                            ))
                    continue
                while True:
                    token = _NUMBER_TOKEN.match(text, position)
                    if not token:
                        break
                    target = numbers.get(token.group())
                    if target:
                        occurrences.append((
                            (node.node_id, token.start(), token.end()),
                            (target,), kind,
                        ))
                    separator = _CALLOUT_SEPARATOR.match(text, token.end())
                    if not separator:
                        break
                    position = separator.end()
    occurrences.sort(key=lambda item: (
        source.node(item[0][0]).order, item[0][1], item[0][2]
    ))
    accepted = []
    for item in occurrences:
        left = item[0]
        if accepted and left[0] == accepted[-1][0][0] \
                and left[1] < accepted[-1][0][2]:
            continue
        accepted.append(item)
    return accepted


def link_display_object_callouts(blocks, source: SourceDocument):
    """把正文里印出的图表提及机械连到图表实体，xref 只包印出的编号字符。

    编号取自实体 label 中唯一的数字串；图组是可引用单元，组内成员不注册；
    查无对应实体或同号歧义的提及一律保持纯文本。
    """
    numbers = {"fig": {}, "table": {}}

    def register(kind: str, entity) -> None:
        if entity.label is None:
            return
        runs = _NUMBER_TOKEN.findall(entity.label.plain_text(source))
        if len(runs) != 1:
            return
        number = runs[0]
        if number in numbers[kind] and numbers[kind][number] != entity.entity_id:
            numbers[kind][number] = None
        else:
            numbers[kind][number] = entity.entity_id

    def collect(items) -> None:
        for item in items:
            if isinstance(item, sm.Section):
                collect(item.blocks)
            elif isinstance(item, sm.FigureGroup):
                register("fig", item)
            elif isinstance(item, sm.Figure):
                register("fig", item)
            elif isinstance(item, sm.TableBlock):
                register("table", item)

    collect(blocks)
    occurrences = _callout_occurrences(source, numbers)
    used = set()
    # 题注不参与图表提及链接（与结构参考口径一致）；表格格内正文照常。
    return tuple(
        _block(item, occurrences, used, captions=False) for item in blocks
    )
