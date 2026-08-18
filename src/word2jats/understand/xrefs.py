"""把理解层指明的正文引用区间连到参考文献实体。

引用的可见写法属于开放集，本模块不用正则或词形规则猜测。
它只做封闭的机械工作：落锚两端源指针、核对目标实体、保持可见原文不变。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from ..model.source import SourceDocument, SourceText, TextRange
from ..semantic import model as sm
from .ground import ground


def _quote_range(raw, source: SourceDocument) -> TextRange | None:
    if not isinstance(raw, dict):
        return None
    quote = raw.get("quote")
    hint = raw.get("node_hint")
    if not isinstance(quote, str) or not quote:
        return None
    return ground(quote, source, block_hint=hint)


def _resolved_occurrences(raw_items, reference_list, spans, source):
    issues = []
    resolved = []
    references = reference_list.references if reference_list else ()
    seen = set()
    for number, raw in enumerate(raw_items, 1):
        citation = _quote_range(
            raw.get("citation_quote") if isinstance(raw, dict) else None, source
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
        for source_range, target_ids in selected:
            left, right = source_range[1], source_range[2]
            if left > cursor:
                parts.append(sm.Text(SourceText(((node_id, cursor, left),))))
            content = sm.RichText.from_source(SourceText((source_range,)))
            parts.append(sm.CrossReference("bibr", target_ids, content, source_range))
            used.add((source_range, target_ids))
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


def _caption(value, occurrences, used):
    if value is None:
        return None
    return replace(
        value,
        title=_rich(value.title, occurrences, used) if value.title else None,
        paragraphs=tuple(_block(item, occurrences, used) for item in value.paragraphs),
    )


def _block(value, occurrences, used):
    if isinstance(value, sm.Paragraph):
        return replace(value, content=_rich(value.content, occurrences, used))
    if isinstance(value, sm.Section):
        return replace(value, blocks=tuple(
            _block(item, occurrences, used) for item in value.blocks
        ))
    if isinstance(value, sm.Figure):
        return replace(value, caption=_caption(value.caption, occurrences, used))
    if isinstance(value, sm.FigureGroup):
        return replace(
            value,
            caption=_caption(value.caption, occurrences, used),
            figures=tuple(_block(item, occurrences, used) for item in value.figures),
        )
    if isinstance(value, sm.TableBlock):
        def row(item):
            return replace(item, cells=tuple(
                replace(cell, content=_rich(cell.content, occurrences, used))
                for cell in item.cells
            ))
        return replace(
            value, caption=_caption(value.caption, occurrences, used),
            header_rows=tuple(row(item) for item in value.header_rows),
            body_rows=tuple(row(item) for item in value.body_rows),
        )
    return value


def link_bibliographic_citations(blocks, reference_list, reference_spans,
                                 source: SourceDocument, raw_items: Iterable[dict]):
    """仅按已落锚的源到源关系包装 xref，不解析引用字面语法。"""
    occurrences, issues = _resolved_occurrences(
        tuple(raw_items), reference_list, tuple(reference_spans), source
    )
    used = set()
    linked = tuple(_block(item, occurrences, used) for item in blocks)
    for source_range, target_ids in occurrences:
        if (source_range, target_ids) not in used:
            issues.append((*source_range, "已落锚引用不在任一正文输出区间内"))
    return linked, tuple(issues)
