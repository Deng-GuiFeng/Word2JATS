"""理解层 v2 唯一编排入口。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from .assemble import AssemblyResult, SemanticSourceUse, assemble
from .merge import (
    MergeIssue, build_reference_spans, merge_assignments, project_body_to_assignment,
    reconcile_boundaries,
)
from .passes import (
    ReferenceInput, UnderstandConfig, body_pass, citation_pass, head_jats_pass,
    flattened_tables_pass, reference_boundary_pass, reference_fields_pass,
)
from .serialize import serialize


def _reference_view(span, source):
    lines = []
    for node_id, start, end in span.source.ranges:
        lines.append(f"[{node_id}] {source.slice_text((node_id, start, end))}")
    return "\n".join(lines)


def _citation_relations(response: dict) -> list[dict]:
    """把模型的单目标/紧凑范围两种关系投影成统一内部边。"""
    result = []
    for item in response.get("single_target_citations") or []:
        if isinstance(item, dict):
            result.append({
                "citation_quote": item.get("citation_quote"),
                "target_reference_ids": [item.get("target_reference_id")],
            })
    for item in response.get("compact_range_citations") or []:
        if isinstance(item, dict):
            result.append({
                "citation_quote": item.get("citation_quote"),
                "target_reference_ids": item.get("target_reference_ids"),
            })
    return result


def understand(source, llm, config: UnderstandConfig | None = None,
               head_llm=None):
    """
    SourceDocument -> SemanticDoc v2。

    真实依赖关系为：head-jats/body/refs-A/refs-B 并发；切条后，文献逐条
    析字段与全局归并并发；最后组装。任何并发结果均按源地址排序。

    ``head_llm`` 只供文首流程使用，不传就与其余流程共用 ``llm``。文首对模型
    的要求与正文不同(见 llm/client.py 里 dashscope 的 front_model 注释)，由
    调用方决定是否分开；这里不猜、不自建客户端。
    """
    config = config or UnderstandConfig()
    view = serialize(source)

    with ThreadPoolExecutor(max_workers=4) as executor:
        head_future = executor.submit(head_jats_pass, view, head_llm or llm, config)
        body_future = executor.submit(body_pass, view, llm, config)
        left_future = executor.submit(reference_boundary_pass, view, llm, "A", config)
        right_future = executor.submit(reference_boundary_pass, view, llm, "B", config)
        head_task = head_future.result()
        body_task = body_future.result()
        left_task = left_future.result()
        right_task = right_future.result()

    head = {
        **head_task.content,
        "front_nodes": list(head_task.front_nodes),
    }
    body = body_task.combined()
    left = left_task.combined()
    right = right_task.combined()
    boundary, boundary_issues, boundary_audit = reconcile_boundaries(
        view, llm, left, right, config
    )
    spans = build_reference_spans(view, boundary)

    reference_inputs = [
        ReferenceInput(item.index, _reference_view(item, source)) for item in spans
    ]
    # 字段细化与已有三路结论的全局归并只依赖切条结果，同批并发。
    with ThreadPoolExecutor(max_workers=2) as executor:
        fields_future = executor.submit(
            reference_fields_pass, reference_inputs, llm, config
        )
        merge_future = executor.submit(
            merge_assignments, view, head, body, boundary, spans, llm, config,
            boundary_issues, boundary_audit,
        )
        field_results = fields_future.result()
        assignment = merge_future.result()
    fields = [item[1] for item in field_results]
    field_audit = tuple(meta for item in field_results for meta in item[2])
    body = project_body_to_assignment(view, body, assignment)
    # 引用实体匹配依赖逐条字段形成的身份；压平表归属依赖全局主角色。
    # 二者互不依赖，可同批并发。
    with ThreadPoolExecutor(max_workers=2) as executor:
        citation_future = executor.submit(
            citation_pass, view, spans, fields, llm, config
        )
        flattened_future = executor.submit(
            flattened_tables_pass, view, body, llm, config
        )
        citation_task = citation_future.result()
        flattened_results = flattened_future.result()
    flattened_audit = tuple(meta for item in flattened_results for meta in item[2])
    citation_response = citation_task.combined()
    body = {
        **body,
        "bibliographic_citations": _citation_relations(citation_response),
        "bibliographic_citation_issues": [
            *citation_task.issues,
            *(citation_response.get("issues") or []),
        ],
    }

    if flattened_results:
        tables = [dict(item) if isinstance(item, dict) else item
                  for item in body.get("tables") or []]
        for table_index, layout, _ in flattened_results:
            if table_index < len(tables) and isinstance(tables[table_index], dict):
                tables[table_index]["flattened_layout"] = layout
        body = {**body, "tables": tables}

    built = assemble(
        source, view, head, body, spans, fields, assignment, direct_head=True
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
