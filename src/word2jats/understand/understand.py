"""理解层 v2 唯一编排入口。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from .assemble import assemble
from .ground import ground
from .merge import (
    build_reference_spans, merge_assignments, reconcile_boundaries,
)
from .passes import (
    ReferenceInput, UnderstandConfig, body_pass, front_pass,
    flattened_tables_pass, reference_boundary_pass, reference_fields_pass,
    run_windowed,
)
from .prompts import FRONT_SYSTEM
from .serialize import serialize


def _front_failures(front, source):
    failures = []
    if not isinstance(front.get("article_type"), str) or not front["article_type"]:
        failures.append("missing article_type")
    titles = front.get("title_quotes") or []
    if not titles:
        failures.append("missing title")
    for index, raw in enumerate(titles):
        if not isinstance(raw, dict) or not ground(
            raw.get("quote") or "", source, block_hint=raw.get("node_hint")
        ):
            failures.append(f"title quote {index} not uniquely grounded")
    for index, author in enumerate(front.get("authors") or []):
        whole = author.get("author_quote") if isinstance(author, dict) else None
        whole_match = None
        if isinstance(whole, dict):
            whole_match = ground(
                whole.get("quote") or "", source, block_hint=whole.get("node_hint")
            )
        if whole_match is None:
            failures.append(f"author {index} author_quote not uniquely grounded")
        for field in ("surname_quote", "given_quote"):
            raw = author.get(field) if isinstance(author, dict) else None
            if not isinstance(raw, dict) or not ground(
                raw.get("quote") or "", source, scope=whole_match,
                block_hint=raw.get("node_hint"),
            ):
                failures.append(f"author {index} {field} not uniquely grounded")
        labels = {str(item) for item in author.get("affiliation_labels") or []}
        for marker_index, marker in enumerate(author.get("affiliation_markers") or []):
            if not isinstance(marker, dict) or str(marker.get("label")) not in labels:
                failures.append(
                    f"author {index} affiliation marker {marker_index} has no target label"
                )
                continue
            raw = marker.get("marker_quote")
            if not isinstance(raw, dict) or not ground(
                raw.get("quote") or "", source, scope=whole_match,
                block_hint=raw.get("node_hint"),
            ):
                failures.append(
                    f"author {index} affiliation marker {marker_index} not grounded"
                )
        correspondence_marker = author.get("correspondence_marker_quote")
        if correspondence_marker and (
            not isinstance(correspondence_marker, dict) or not ground(
                correspondence_marker.get("quote") or "", source, scope=whole_match,
                block_hint=correspondence_marker.get("node_hint"),
            )
        ):
            failures.append(
                f"author {index} correspondence marker not grounded"
            )
    keywords = front.get("keywords")
    if isinstance(keywords, dict):
        if keywords.get("keyword_quotes") and not keywords.get("source_nodes"):
            failures.append("keywords missing source_nodes")
        for index, raw in enumerate(keywords.get("keyword_quotes") or []):
            quote = raw.get("quote") if isinstance(raw, dict) else None
            if not quote or not ground(
                quote, source, block_hint=raw.get("node_hint")
            ):
                failures.append(f"keyword {index} is not one uniquely grounded keyword")
    for index, address in enumerate(front.get("addresses") or []):
        if not isinstance(address, dict) or not address.get("source_nodes"):
            failures.append(f"address {index} missing source_nodes")
    for index, abstract in enumerate(front.get("abstracts") or []):
        if not isinstance(abstract, dict) or not abstract.get("source_nodes"):
            failures.append(f"abstract {index} missing source_nodes")
            continue
        container_title = abstract.get("container_title_quote")
        if container_title and (
            not isinstance(container_title, dict) or not ground(
                container_title.get("quote") or "", source,
                block_hint=container_title.get("node_hint"),
            )
        ):
            failures.append(f"abstract {index} container title not uniquely grounded")
        for section_index, section in enumerate(abstract.get("sections") or []):
            if (isinstance(section, dict) and section.get("wrapped", True)
                    and not section.get("title_quote")):
                failures.append(
                    f"abstract {index} section {section_index} is wrapped but has no title"
                )
            if (isinstance(section, dict) and section.get("wrapped", True)
                    and container_title
                    and section.get("title_quote") == container_title):
                failures.append(
                    f"abstract {index} section {section_index} reuses its container title"
                )
    for index, note in enumerate(front.get("contributor_notes") or []):
        if not isinstance(note, dict) or not note.get("paragraph_quotes"):
            failures.append(f"contributor note {index} missing printed paragraph")
            continue
        targets = {
            value for value in note.get("author_indexes") or []
            if isinstance(value, int)
        }
        markers = note.get("author_marker_quotes") or []
        if markers:
            marked = {
                item.get("author_index") for item in markers
                if isinstance(item, dict)
            }
            if marked != targets:
                failures.append(
                    f"contributor note {index} author marker targets do not match"
                )
            authors = front.get("authors") or []
            for marker_index, item in enumerate(markers):
                author_index = item.get("author_index") if isinstance(item, dict) else None
                raw = item.get("marker_quote") if isinstance(item, dict) else None
                author = authors[author_index] if (
                    isinstance(author_index, int) and 0 <= author_index < len(authors)
                ) else None
                whole = author.get("author_quote") if isinstance(author, dict) else None
                whole_match = ground(
                    whole.get("quote") or "", source,
                    block_hint=whole.get("node_hint"),
                ) if isinstance(whole, dict) else None
                if not isinstance(raw, dict) or not ground(
                    raw.get("quote") or "", source, scope=whole_match,
                    block_hint=raw.get("node_hint"),
                ):
                    failures.append(
                        f"contributor note {index} author marker {marker_index} not grounded"
                    )
    return failures


def _reference_view(span, source):
    lines = []
    for node_id, start, end in span.source.ranges:
        lines.append(f"[{node_id}] {source.slice_text((node_id, start, end))}")
    return "\n".join(lines)


def understand(source, llm, config: UnderstandConfig | None = None):
    """
    SourceDocument -> SemanticDoc v2。

    真实依赖关系为：front/body/refs-A/refs-B 并发；切条后，文献逐条
    析字段与全局归并并发；最后组装。任何并发结果均按源地址排序。
    """
    config = config or UnderstandConfig()
    view = serialize(source)

    with ThreadPoolExecutor(max_workers=4) as executor:
        front_future = executor.submit(front_pass, view, llm, config)
        body_future = executor.submit(body_pass, view, llm, config)
        left_future = executor.submit(reference_boundary_pass, view, llm, "A", config)
        right_future = executor.submit(reference_boundary_pass, view, llm, "B", config)
        front_task = front_future.result()
        body_task = body_future.result()
        left_task = left_future.result()
        right_task = right_future.result()

    front = front_task.combined()
    failures = _front_failures(front, source)
    reask_audit = ()
    if failures:
        feedback = FRONT_SYSTEM + (
            "\nGROUNDING FEEDBACK: the prior answer failed mechanical grounding: "
            + "; ".join(failures)
            + ". Re-read exact source spelling and return corrected quotes once."
        )
        retry = run_windowed(
            view, llm, task="front-ground-reask", prompt_version="front-v3.1-reask1",
            system=feedback, config=config,
        )
        corrected = retry.combined()
        # 重问是一次改进机会，不是用一次更差或不完整的回答覆盖首答的
        # 授权。只接受机械失败数严格减少的完整候选。
        if corrected and len(_front_failures(corrected, source)) < len(failures):
            front = corrected
        reask_audit = retry.audit

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
    # 字段细化与已有三路结论的全局归并无依赖，同批并发。
    with ThreadPoolExecutor(max_workers=3) as executor:
        fields_future = executor.submit(
            reference_fields_pass, reference_inputs, llm, config
        )
        flattened_future = executor.submit(
            flattened_tables_pass, view, body, llm, config
        )
        merge_future = executor.submit(
            merge_assignments, view, front, body, boundary, spans, llm, config,
            boundary_issues, boundary_audit,
        )
        field_results = fields_future.result()
        flattened_results = flattened_future.result()
        assignment = merge_future.result()
    fields = [item[1] for item in field_results]
    field_audit = tuple(meta for item in field_results for meta in item[2])
    flattened_audit = tuple(meta for item in flattened_results for meta in item[2])

    if flattened_results:
        tables = [dict(item) if isinstance(item, dict) else item
                  for item in body.get("tables") or []]
        for table_index, layout, _ in flattened_results:
            if table_index < len(tables) and isinstance(tables[table_index], dict):
                tables[table_index]["flattened_layout"] = layout
        body = {**body, "tables": tables}

    built = assemble(source, view, front, body, spans, fields, assignment)
    audits = (
        front_task.audit + body_task.audit + left_task.audit + right_task.audit
        + reask_audit + tuple(boundary_audit) + field_audit + flattened_audit
        + assignment.audit
    )
    meta = {
        "schema": "word2jats.understanding-report",
        "version": 2,
        "view_records": len(view.records),
        "reference_count": len(spans),
        "front": front,
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
