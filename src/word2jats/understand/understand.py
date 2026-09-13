"""理解层 v2 唯一编排入口。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import re

from .assemble import AssemblyResult, SemanticSourceUse, assemble
from .merge import (
    MergeIssue, build_reference_spans, merge_assignments, project_body_to_assignment,
    reconcile_boundaries,
)
from .passes import (
    ReferenceInput, UnderstandConfig, _quote_value, body_pass, citation_pass,
    head_jats_pass, flattened_tables_pass, reference_boundary_pass,
    reference_fields_pass,
)
from .serialize import serialize


def _reference_view(span, source):
    lines = []
    for node_id, start, end in span.source.ranges:
        lines.append(f"[{node_id}] {source.slice_text((node_id, start, end))}")
    return "\n".join(lines)


def _entity_by_printed_number(spans, fields, source=None) -> dict[str, str]:
    """建立「正文印出的编号 → 参考文献实体」的对照表。

    先按各条印出的标号建表；标号摘不到的，若该条首节点带 Word 自动编号，
    用机械还原的编号值（自动编号的印出值是文档事实，不随切条位置漂移）；
    仍取不到的再按文末列表次序补进来，不覆盖已有的。稿件跳号、重号一律
    在这里消化，模型不必知道。
    """
    from ..parse.numbering import restored_number

    table: dict[str, str] = {}
    pending = []
    for position, span in enumerate(spans):
        entity_id = f"reference:{span.index}"
        raw = fields[position] if position < len(fields) else {}
        label = _quote_value(raw.get("label_quote")) if isinstance(raw, dict) else None
        number = re.search(r"\d+", label) if isinstance(label, str) else None
        restored = None
        if not number and source is not None and span.source.ranges:
            restored = restored_number(source, span.source.ranges[0][0])
        if number:
            table.setdefault(number.group(), entity_id)
        elif restored:
            table.setdefault(restored, entity_id)
        else:
            pending.append((str(span.index), entity_id))
    for number, entity_id in pending:
        table.setdefault(number, entity_id)
    return table


def _entity_identities(spans, fields) -> list[tuple[str, str, str, str]]:
    """逐条参考文献的（实体、首作者姓、年份、年份后缀）身份表。"""
    out = []
    for position, span in enumerate(spans):
        raw = fields[position] if position < len(fields) else {}
        if not isinstance(raw, dict):
            continue
        raw_fields = raw.get("fields") or {}
        year = _quote_value(raw_fields.get("year"))
        suffix = _quote_value(raw_fields.get("year_suffix"))
        surname = None
        for group in raw.get("person_groups") or []:
            if not isinstance(group, dict):
                continue
            for member in group.get("members") or []:
                if not isinstance(member, dict):
                    continue
                surname = (_quote_value(member.get("surname_quote"))
                           or _quote_value(member.get("collab_quote")))
                if surname:
                    break
            if surname:
                break
        out.append((f"reference:{span.index}", surname or "", year or "",
                    suffix or ""))
    return out


def _citation_relations(response: dict, entity_by_number: dict[str, str],
                        identities=()) -> list[dict]:
    """把模型交的每处引用换算成内部边。

    编号制：模型给正文印出的编号，这里换成参考文献实体；换不出来的
    （稿件引了文末没有的编号）整处丢掉。作者—年份制：模型按契约不给
    编号，这里用「首作者姓（词边界）+年份都出现在引文原文里」做机械
    身份匹配，唯一命中才连，多个候选一律不认。
    """
    result = []
    for item in response.get("citations") or []:
        if not isinstance(item, dict):
            continue
        numbers = item.get("target_reference_ids")
        if numbers:
            targets = [entity_by_number.get(str(x)) for x in numbers]
            if not all(targets):
                continue
            result.append({
                "citation_quote": item.get("citation_quote"),
                "target_reference_ids": targets,
            })
            continue
        quote_raw = item.get("citation_quote")
        quote = quote_raw.get("quote") if isinstance(quote_raw, dict) else (
            quote_raw if isinstance(quote_raw, str) else None
        )
        if not quote:
            continue
        folded = quote.casefold()
        candidates = []
        for entity_id, surname, year, suffix in identities:
            if not surname or not year or year not in quote:
                continue
            if suffix and f"{year}{suffix}" not in quote:
                continue
            pattern = rf"(?<![a-z]){re.escape(surname.casefold())}(?![a-z])"
            if re.search(pattern, folded):
                candidates.append(entity_id)
        if len(candidates) == 1:
            result.append({
                "citation_quote": quote_raw,
                "target_reference_ids": candidates,
            })
    return result


def understand(source, llm, config: UnderstandConfig | None = None,
               head_llm=None):
    """
    SourceDocument -> SemanticDoc v2。

    头部、正文、文献切条、正文引文并发启动。切条完成即提取文献字段；
    头部与正文完成后归并角色，随即处理表格。只等待实际依赖，最后统一组装。

    ``head_llm`` 只供文首流程使用，不传就与其余流程共用 ``llm``。文首对模型
    的要求与正文不同(见 llm/client.py 里 dashscope 的 front_model 注释)，由
    调用方决定是否分开；这里不猜、不自建客户端。
    """
    config = config or UnderstandConfig()
    view = serialize(source)
    # 引文识别只依赖全文清单，和头部/正文/文献切条同时开始。
    # 让独立执行器活到归并结束，避免先等引文返回才开始文献字段提取。
    with ThreadPoolExecutor(max_workers=1) as executor:
        citation_future = executor.submit(citation_pass, view, llm, config)
        return _remaining_passes(source, view, llm, config, head_llm, citation_future)


def _remaining_passes(source, view, llm, config, head_llm, citation_future):
    with ThreadPoolExecutor(max_workers=4) as executor:
        head_future = executor.submit(head_jats_pass, view, head_llm or llm, config)
        body_future = executor.submit(body_pass, view, llm, config)
        left_future = executor.submit(reference_boundary_pass, view, llm, "A", config)
        right_future = executor.submit(reference_boundary_pass, view, llm, "B", config)
        left_task = left_future.result()
        right_task = right_future.result()
        boundary, boundary_issues, boundary_audit = reconcile_boundaries(
            view, llm, left_task.combined(), right_task.combined(), config
        )
        spans = build_reference_spans(view, boundary)
        reference_inputs = [
            ReferenceInput(item.index, _reference_view(item, source)) for item in spans
        ]
        # 切条结束后立刻启动字段提取，不等较慢的文首或正文任务。
        fields_future = executor.submit(
            reference_fields_pass, reference_inputs, llm, config
        )
        head_task = head_future.result()
        body_task = body_future.result()
        head = {**head_task.content, "front_nodes": list(head_task.front_nodes)}
        body = body_task.combined()
        assignment = merge_assignments(
            view, head, body, boundary, spans, llm, config,
            boundary_issues, boundary_audit,
        )
        body = project_body_to_assignment(view, body, assignment)
        # 表格只依赖角色归并，处理表格期间文献字段和引文继续并发。
        flattened_results = flattened_tables_pass(view, body, llm, config)
        field_results = fields_future.result()
    fields = [item[1] for item in field_results]
    field_audit = tuple(meta for item in field_results for meta in item[2])
    citation_task = citation_future.result()
    flattened_audit = tuple(meta for item in flattened_results for meta in item[2])
    citation_response = citation_task.combined()
    body = {
        **body,
        # 模型给的是正文印出的编号，在这里换成参考文献实体。
        "bibliographic_citations": _citation_relations(
            citation_response, _entity_by_printed_number(spans, fields, source),
            _entity_identities(spans, fields),
        ),
    }

    if flattened_results:
        tables = [dict(item) if isinstance(item, dict) else item
                  for item in body.get("tables") or []]
        for table_index, layout, _ in flattened_results:
            if table_index < len(tables) and isinstance(tables[table_index], dict):
                tables[table_index]["flattened_layout"] = layout
        body = {**body, "tables": tables}

    built = assemble(
        source, view, head, body, spans, fields, assignment
    )
    head_source_uses = tuple(
        SemanticSourceUse(
            node_id, 0, len(source.node(node_id).text),
            f"head-jats:{node_id}", "model-head",
        )
        for node_id in head_task.metadata_nodes
        if source.node(node_id).text
    )
    built = AssemblyResult(
        built.document, built.issues, built.source_uses + head_source_uses
    )
    if head_task.issues:
        built = AssemblyResult(
            built.document,
            built.issues + tuple(
                MergeIssue(
                    "review_blocking", "HEAD_JATS_UNAVAILABLE", "head", detail,
                )
                for detail in head_task.issues
            ),
            built.source_uses,
        )
    audits = (
        head_task.audit + body_task.audit + left_task.audit + right_task.audit
        + citation_task.audit + tuple(boundary_audit)
        + field_audit + flattened_audit
        + assignment.audit
    )
    meta = {
        "schema": "word2jats.understanding-report",
        "version": 2,
        "view_records": len(view.records),
        "reference_count": len(spans),
        "head_jats": {
            "xml": head_task.xml,
            "metadata_nodes": list(head_task.metadata_nodes),
            "content_nodes": list(head_task.content_nodes),
            "front_nodes": list(head_task.front_nodes),
            "content": head_task.content,
            "prompt_version": head_task.prompt_version,
        },
        "body": body,
        "reference_boundaries": boundary,
        "reference_fields": fields,
        "assignments": [asdict(item) for item in assignment.assignments],
        "source_uses": [asdict(item) for item in built.source_uses],
        "issues": [asdict(item) for item in built.issues],
        "audit": list(audits),
        "blocking": any(item.severity in {"high", "review_blocking"}
                        for item in built.issues),
    }
    return built.document, meta
