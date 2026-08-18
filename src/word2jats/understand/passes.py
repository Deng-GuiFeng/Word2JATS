"""理解层专项任务调度：真实预算切窗、全量并发、独立路由与限次重问。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from typing import Iterable, Optional

from .ground import ground
from .prompts import (
    BODY_SYSTEM, DISCARD_REVIEW_SYSTEM, FLATTENED_TABLE_SYSTEM, FRONT_SYSTEM,
    MERGE_JUDGE_SYSTEM,
    REFERENCE_FIELDS_SYSTEM, REF_BOUNDARY_A_SYSTEM,
    REF_BOUNDARY_B_SYSTEM, REF_BOUNDARY_JUDGE_SYSTEM,
    judge_message, user_message,
)
from .serialize import SerializedDocument


MAX_REASK = 1


@dataclass(frozen=True)
class UnderstandConfig:
    max_workers: int = 32
    input_token_budget: int = 90_000
    boundary_token_budget: int = 4_000
    output_token_budget: int = 20_000

    def __post_init__(self):
        for name in (
            "max_workers", "input_token_budget", "boundary_token_budget",
            "output_token_budget",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} 必须是正整数")
        if 2 * self.boundary_token_budget >= self.input_token_budget:
            raise ValueError("input_token_budget 必须大于两侧 boundary_token_budget 之和")


@dataclass(frozen=True)
class Window:
    index: int
    context_indices: tuple[int, ...]
    center_indices: tuple[int, ...]
    center_keys: tuple[str, ...]


@dataclass(frozen=True)
class PassPayload:
    window: Window
    response: dict
    audit: tuple[dict, ...]


@dataclass(frozen=True)
class TaskResult:
    task: str
    prompt_version: str
    payloads: tuple[PassPayload, ...]
    issues: tuple[str, ...] = ()

    def combined(self) -> dict:
        return collapse_payloads(self.payloads)

    @property
    def audit(self) -> tuple[dict, ...]:
        return tuple(item for payload in self.payloads for item in payload.audit)


def _token_upper_bound(value: str) -> int:
    """返回受支持字节级分词器的严格 token 数上界。

    云端商业模型没有公开可调的本地精确分词器，不得拿其他模型的
    tokenizer 代替，也不得用“字节数除以经验常数”猜测。当前受支持的
    Qwen/DeepSeek/OpenAI 兼容后端均以 UTF-8 字节为未知字符的最细回退单位，
    因此真实 token 数不会超过 UTF-8 字节数。用这个上界切窗可能更保守，
    但不会把超限输入误判为可发送。
    """
    return max(1, len(value.encode("utf-8")))


def make_windows(view: SerializedDocument, config: UnderstandConfig) -> tuple[Window, ...]:
    records = view.records
    costs = [_token_upper_bound(item.render()) + 2 for item in records]
    total = sum(costs)
    if total <= config.input_token_budget:
        indices = tuple(range(len(records)))
        return (Window(0, indices, indices, tuple(item.key for item in records)),)

    center_budget = max(1, config.input_token_budget - 2 * config.boundary_token_budget)
    centers = []
    start = 0
    while start < len(records):
        end = start
        used = 0
        while end < len(records) and (used + costs[end] <= center_budget or end == start):
            used += costs[end]
            end += 1
        centers.append((start, end))
        start = end

    windows = []
    for number, (lo, hi) in enumerate(centers):
        left = lo
        used = 0
        while left > 0 and (
            used + costs[left - 1] <= config.boundary_token_budget or left == lo
        ):
            left -= 1
            used += costs[left]
        right = hi
        used = 0
        while right < len(records) and (
            used + costs[right] <= config.boundary_token_budget or right == hi
        ):
            used += costs[right]
            right += 1
        context = tuple(range(left, right))
        center = tuple(range(lo, hi))
        windows.append(Window(
            number, context, center, tuple(records[index].key for index in center)
        ))
    return tuple(windows)


def _request(llm, system: str, user: str, *, route: str, max_tokens: int):
    if hasattr(llm, "request_json"):
        return llm.request_json(system, user, max_tokens=max_tokens, route=route)
    result = llm.extract_json(system, user, max_tokens=max_tokens, route=route)
    return result, {
        "provider": getattr(llm, "provider", None), "model": getattr(llm, "model", None),
        "route": route, "cache_hit": None, "network_call": None,
        "ok": isinstance(result, dict),
    }


def _response_node_ids(view: SerializedDocument, raw) -> tuple[str, ...]:
    """把模型看到的清单地址机械还原为源节点。"""
    if not isinstance(raw, str):
        return ()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    # 清单中表头显示为 ``[doc/tbl1|表]``；协议要求返回
    # ``doc/tbl1``，但仍容忍模型把显示后缀一并抄回。
    key = raw[:-2] if raw.endswith("|表") else raw
    if key in view.source._nodes:
        return (key,)
    record = view.by_key(key)
    return record.source_nodes if record else ()


def _owner_table(view: SerializedDocument, node_id: str) -> Optional[str]:
    """返回节点所在的最外层原生表；非表内节点返回 None。"""
    current = node_id
    table = None
    while current in view.source._nodes:
        node = view.source.node(current)
        if node.kind == "table":
            table = current
        if not node.parent:
            break
        current = node.parent
    return table


def body_contract_failures(view: SerializedDocument, window: Window,
                           response: dict) -> list[str]:
    """
    验证 body 专项的最小完整性契约。

    这里不判断语义对错，只验证模型是否对本窗中的可见块、
    对象与已判为 table 的原生表交付了协议规定的指针。
    多窗时只查中心区，避免把上下文重叠区误当成必答区。
    """
    failures = []
    blocks = response.get("blocks")
    if not isinstance(blocks, list):
        return ["blocks is not an array"]

    required_nodes = set()
    required_tables = set()
    for index in window.center_indices:
        record = view.records[index]
        if record.kind == "table":
            required_nodes.update(record.source_nodes)
            required_tables.update(record.source_nodes)
            continue
        if record.kind == "table-row":
            for node_id in record.source_nodes:
                table = _owner_table(view, node_id)
                if table:
                    required_nodes.add(table)
                    required_tables.add(table)
            continue
        for node_id in record.source_nodes:
            node = view.source.node(node_id)
            if node.text.strip() or node.objects:
                required_nodes.add(node_id)

    covered = set()
    table_roles = set()
    block_roles: dict[str, set[str]] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        node_ids = set()
        for raw in block.get("nodes") or []:
            node_ids.update(_response_node_ids(view, raw))
        covered.update(node_ids)
        role = block.get("role")
        if isinstance(role, str):
            for node_id in node_ids:
                block_roles.setdefault(node_id, set()).add(role)
        if role == "table":
            table_roles.update(
                node_id for node_id in node_ids
                if node_id in view.source._nodes
                and view.source.node(node_id).kind == "table"
            )
        if role == "declaration":
            title = block.get("title_quote")
            title_node = None
            if isinstance(title, dict):
                title_node = next(iter(_response_node_ids(
                    view, title.get("node_hint")
                )), None)
            title_range = ground(
                title.get("quote") or "", view.source,
                block_hint=title_node,
            ) if isinstance(title, dict) else None
            if title_range is None:
                failures.append("declaration title_quote is not uniquely grounded")
            content_nodes = {
                node_id for raw in block.get("content_nodes") or []
                for node_id in _response_node_ids(view, raw)
            }
            if not content_nodes:
                failures.append("declaration content_nodes is empty")
            if title_range and title_range[0] in content_nodes:
                failures.append("declaration title node is repeated in content_nodes")
            required_parts = set(content_nodes)
            if title_range:
                required_parts.add(title_range[0])
            missing_parts = sorted(required_parts - node_ids)
            if missing_parts:
                failures.append(
                    "declaration block nodes omit title/content nodes: "
                    + ", ".join(missing_parts)
                )

    missing = sorted(required_nodes - covered)
    if missing:
        failures.append("blocks missing source nodes: " + ", ".join(missing[:40]))

    table_specs = response.get("tables")
    if not isinstance(table_specs, list):
        failures.append("tables is not an array")
        table_specs = []
    specified_tables = set()
    specified_flattened = set()

    def require_consistent_block_role(raw_values, role, source_kind):
        for raw in raw_values:
            for node_id in _response_node_ids(view, raw):
                actual = block_roles.get(node_id)
                if actual and role not in actual:
                    failures.append(
                        f"{source_kind} {node_id} requires block role {role}, got "
                        + ",".join(sorted(actual))
                    )

    for spec in table_specs:
        if not isinstance(spec, dict):
            continue
        node_ids = _response_node_ids(view, spec.get("table_node"))
        specified_tables.update(
            node_id for node_id in node_ids
            if node_id in view.source._nodes
            and view.source.node(node_id).kind == "table"
        )
        flattened_raw = spec.get("flattened_row_nodes") or []
        flattened_ids = {
            node_id for raw in flattened_raw
            for node_id in _response_node_ids(view, raw)
        }
        specified_flattened.update(flattened_ids)
        modes = sum(bool(value) for value in (
            node_ids, flattened_raw, spec.get("graphic"),
        ))
        if modes != 1:
            failures.append(
                "each table spec must identify exactly one native table, flattened row set, or graphic"
            )
        header_rows = spec.get("header_rows")
        if (isinstance(header_rows, bool) or not isinstance(header_rows, int)
                or header_rows < 0):
            failures.append("table header_rows must be one non-negative integer")
        for item in spec.get("row_header_cells") or []:
            if (not isinstance(item, dict)
                    or isinstance(item.get("row"), bool)
                    or not isinstance(item.get("row"), int)
                    or item["row"] < 1
                    or isinstance(item.get("column"), bool)
                    or not isinstance(item.get("column"), int)
                    or item["column"] < 1):
                failures.append(
                    "table row_header_cells must contain positive 1-based row/column integers"
                )
                break
        unknown_flattened = [
            raw for raw in flattened_raw
            if view.by_key(_display_key(raw) or "") is None
        ]
        if unknown_flattened:
            failures.append(
                "flattened_row_nodes contain unknown displayed row addresses: "
                + ", ".join(map(str, unknown_flattened[:20]))
            )
        require_consistent_block_role(
            [spec.get("table_node"), *(spec.get("flattened_row_nodes") or [])],
            "table", "table source",
        )
        require_consistent_block_role(
            spec.get("caption_nodes") or [], "table-caption", "table caption",
        )
        footnotes = spec.get("footnotes") or []
        footnote_nodes = spec.get("footnote_nodes") or []
        if footnotes and not footnote_nodes:
            failures.append("table with footnotes is missing footnote_nodes")
        invalid_footnote_nodes = [
            raw for raw in footnote_nodes if not _response_node_ids(view, raw)
        ]
        if invalid_footnote_nodes:
            failures.append(
                "table footnote_nodes contain unknown addresses: "
                + ", ".join(map(str, invalid_footnote_nodes[:20]))
            )
        require_consistent_block_role(
            footnote_nodes, "table-footnote", "table footnote",
        )
    missing_table_roles = sorted(required_tables - table_roles)
    if missing_table_roles:
        failures.append(
            "native tables missing table block role: "
            + ", ".join(missing_table_roles[:40])
        )
    missing_specs = sorted((table_roles & required_tables) - specified_tables)
    if missing_specs:
        failures.append("table blocks missing table specs: " + ", ".join(missing_specs))
    flattened_table_roles = {
        node_id for node_id, roles in block_roles.items()
        if "table" in roles and node_id in view.source._nodes
        and view.source.node(node_id).kind != "table"
    }
    missing_flattened_specs = sorted(flattened_table_roles - specified_flattened)
    if missing_flattened_specs:
        failures.append(
            "flattened table blocks missing table specs: "
            + ", ".join(missing_flattened_specs[:40])
        )

    for spec in response.get("figures") or []:
        if isinstance(spec, dict):
            require_consistent_block_role(
                spec.get("caption_nodes") or [], "figure-caption", "figure caption",
            )
    for spec in response.get("figure_groups") or []:
        if not isinstance(spec, dict):
            continue
        require_consistent_block_role(
            spec.get("caption_nodes") or [], "figure-caption", "figure-group caption",
        )
        for member in spec.get("members") or []:
            if isinstance(member, dict):
                require_consistent_block_role(
                    member.get("caption_nodes") or [], "figure-caption",
                    "figure-group member caption",
                )
    for spec in response.get("special_blocks") or []:
        if isinstance(spec, dict) and spec.get("role") in {"glossary", "definition-list"}:
            require_consistent_block_role(
                spec.get("nodes") or [], spec["role"], "special block",
            )

    def ids_for(raw_values):
        return {
            node_id for raw in raw_values for node_id in _response_node_ids(view, raw)
        }

    figure_graphics = {
        occurrence_id
        for spec in response.get("figures") or [] if isinstance(spec, dict)
        for occurrence_id in spec.get("graphics") or []
    }
    figure_captions = ids_for((
        raw for spec in response.get("figures") or [] if isinstance(spec, dict)
        for raw in spec.get("caption_nodes") or []
    ))
    for spec in response.get("figure_groups") or []:
        if not isinstance(spec, dict):
            continue
        figure_captions.update(ids_for(spec.get("caption_nodes") or []))
        for member in spec.get("members") or []:
            if not isinstance(member, dict):
                continue
            figure_captions.update(ids_for(member.get("caption_nodes") or []))
            figure_graphics.update(member.get("graphics") or [])
    table_graphics = {
        spec.get("graphic") for spec in table_specs
        if isinstance(spec, dict) and isinstance(spec.get("graphic"), str)
    }
    formula_graphics = {
        spec.get("occurrence_id") for spec in response.get("formulas") or []
        if isinstance(spec, dict) and isinstance(spec.get("occurrence_id"), str)
    }
    for item in response.get("objects") or []:
        if not isinstance(item, dict):
            continue
        occurrence_id = item.get("occurrence_id")
        role = item.get("role")
        if role == "figure" and occurrence_id not in figure_graphics:
            failures.append(f"figure object missing figure/group spec: {occurrence_id}")
        elif role == "table-image" and occurrence_id not in table_graphics:
            failures.append(f"table-image object missing table spec: {occurrence_id}")
        elif role in {"display-formula", "inline-formula", "ole-formula"} \
                and occurrence_id not in formula_graphics:
            failures.append(f"formula object missing formula spec: {occurrence_id}")

    caption_blocks = {
        node_id for node_id, roles in block_roles.items() if "figure-caption" in roles
    }
    missing_figure_specs = sorted(caption_blocks - figure_captions)
    if missing_figure_specs:
        failures.append(
            "figure-caption blocks missing figure/group specs: "
            + ", ".join(missing_figure_specs[:40])
        )
    for role in ("glossary", "definition-list"):
        role_nodes = {node_id for node_id, roles in block_roles.items() if role in roles}
        specified = {
            node_id
            for spec in response.get("special_blocks") or []
            if isinstance(spec, dict) and spec.get("role") == role
            for raw in spec.get("nodes") or []
            for node_id in _response_node_ids(view, raw)
        }
        missing_special = sorted(role_nodes - specified)
        if missing_special:
            failures.append(
                f"{role} blocks missing special_blocks spec: "
                + ", ".join(missing_special[:40])
            )

    citations = response.get("bibliographic_citations", [])
    if not isinstance(citations, list):
        failures.append("bibliographic_citations is not an array")
    else:
        for citation_index, item in enumerate(citations):
            if not isinstance(item, dict):
                failures.append(
                    f"bibliographic_citations[{citation_index}] is not an object"
                )
                continue
            raw_quote = item.get("citation_quote")
            citation_range = ground(
                raw_quote.get("quote") or "", view.source,
                block_hint=raw_quote.get("node_hint"),
            ) if isinstance(raw_quote, dict) else None
            if citation_range is None:
                failures.append(
                    f"bibliographic_citations[{citation_index}].citation_quote "
                    "is not uniquely grounded"
                )
            targets = item.get("target_reference_head_quotes")
            if not isinstance(targets, list) or not targets:
                failures.append(
                    f"bibliographic_citations[{citation_index}] has no target pointers"
                )
                continue
            for target_index, target in enumerate(targets):
                target_range = ground(
                    target.get("quote") or "", view.source,
                    block_hint=target.get("node_hint"),
                ) if isinstance(target, dict) else None
                if target_range is None:
                    failures.append(
                        f"bibliographic_citations[{citation_index}]."
                        f"target_reference_head_quotes[{target_index}] "
                        "is not uniquely grounded"
                    )

    # 一次对象出现是台账的最小单位；表内对象随所在表窗口检查。
    required_objects = set()
    for occurrence in view.source.occurrences:
        owner = occurrence.node_id
        table = _owner_table(view, owner)
        if owner in required_nodes or (table and table in required_tables):
            required_objects.add(occurrence.occ_id)
    returned_objects = {
        item.get("occurrence_id") for item in response.get("objects") or []
        if isinstance(item, dict) and isinstance(item.get("occurrence_id"), str)
    }
    missing_objects = sorted(required_objects - returned_objects)
    if missing_objects:
        failures.append("objects missing occurrences: " + ", ".join(missing_objects[:40]))
    return failures


def _body_entity_key(view: SerializedDocument, field: str, item):
    if not isinstance(item, dict):
        return None
    if field in {"objects", "formulas"}:
        return item.get("occurrence_id")
    if field == "tables":
        nodes = _response_node_ids(view, item.get("table_node"))
        return ("native", nodes[0]) if nodes else (
            "graphic", item.get("graphic")
        ) if item.get("graphic") else (
            # 重问常会把误吞的表注从 flattened_row_nodes 尾端删掉。
            # 行集合因此不是对象身份；同一张表应优先由稳定的题注锚点
            # 识别。无题注表才退到首个物理数据行。
            "flattened-caption", tuple(
                node_id
                for raw in item.get("caption_nodes") or []
                for node_id in _response_node_ids(view, raw)
            )[:1]
        ) if item.get("caption_nodes") else (
            "flattened-row", tuple(
                node_id
                for raw in item.get("flattened_row_nodes") or []
                for node_id in _response_node_ids(view, raw)
            )[:1]
        ) if item.get("flattened_row_nodes") else None
    if field == "figures":
        graphics = tuple(item.get("graphics") or [])
        return ("graphics", graphics) if graphics else None
    if field == "figure_groups":
        graphics = tuple(
            occurrence_id
            for member in item.get("members") or [] if isinstance(member, dict)
            for occurrence_id in member.get("graphics") or []
            if isinstance(occurrence_id, str)
        )
        return ("figure-group", graphics) if graphics else None
    if field == "special_blocks":
        title_quote = item.get("title_quote")
        title_hint = title_quote.get("node_hint") if isinstance(title_quote, dict) else None
        title_nodes = tuple(
            node_id
            for node_id in _response_node_ids(view, title_hint)
        )
        nodes = tuple(
            node_id for raw in item.get("nodes") or []
            for node_id in _response_node_ids(view, raw)
        )
        anchor = title_nodes[:1] or nodes[:1]
        return (item.get("role"), anchor) if anchor else None
    if field == "bibliographic_citations":
        quote = item.get("citation_quote")
        if not isinstance(quote, dict):
            return None
        nodes = _response_node_ids(view, quote.get("node_hint"))
        text = quote.get("quote")
        return ("citation", nodes[:1], text) if nodes and isinstance(text, str) else None
    return None


def merge_body_retry(view: SerializedDocument, previous: dict,
                     corrected: dict) -> dict:
    """
    用重问结果更新已回答区域，保留它没有再回答的首答区域。

    重问的目的是修正点名失败，不是授权把首答中其他已闭合指针
    悄然删除。合并只处理指针结构，不拼接模型文字。
    """
    result = dict(previous)
    result.update(corrected)

    corrected_blocks = [item for item in corrected.get("blocks") or []
                        if isinstance(item, dict)]
    covered = {
        node_id for item in corrected_blocks for raw in item.get("nodes") or []
        for node_id in _response_node_ids(view, raw)
    }
    blocks = list(corrected_blocks)
    for item in previous.get("blocks") or []:
        if not isinstance(item, dict):
            continue
        missing_raw = []
        for raw in item.get("nodes") or []:
            node_ids = _response_node_ids(view, raw)
            if node_ids and not any(node_id in covered for node_id in node_ids):
                missing_raw.append(raw)
                covered.update(node_ids)
        if missing_raw:
            blocks.append({**item, "nodes": missing_raw})
    result["blocks"] = blocks

    for field in (
        "objects", "figures", "figure_groups", "tables", "formulas",
        "special_blocks", "bibliographic_citations",
    ):
        current = [item for item in corrected.get(field) or []]
        keys = {_body_entity_key(view, field, item) for item in current}
        for item in previous.get(field) or []:
            key = _body_entity_key(view, field, item)
            if key is not None and key not in keys:
                current.append(item)
                keys.add(key)
        result[field] = current
    result["issues"] = list(dict.fromkeys(
        [str(item) for item in previous.get("issues") or []]
        + [str(item) for item in corrected.get("issues") or []]
    ))
    return result


def _one_window(view: SerializedDocument, llm, window: Window, *,
                task: str, prompt_version: str, system: str,
                config: UnderstandConfig) -> PassPayload:
    source_view = view.render(window.context_indices)
    audits = []
    response = None
    previous = None
    best_response = None
    best_failure_count = None
    for attempt in range(MAX_REASK + 1):
        correction = "" if attempt == 0 else (
            "The previous response failed the mechanical response contract: "
            + "; ".join(contract_failures)
            + ". Return the COMPLETE required JSON object, not a patch. Use null/[] only "
              "for genuinely uncertain semantic values; do not omit source blocks or objects."
        )
        route = f"v2:{task}:{prompt_version}:w{window.index}:try{attempt}"
        response, meta = _request(
            llm, system, user_message(source_view, instruction=correction),
            route=route, max_tokens=config.output_token_budget,
        )
        raw_response = response
        raw_failures = []
        if not isinstance(raw_response, dict) or not raw_response:
            raw_failures.append("response was missing or not one non-empty JSON object")
        elif task == "body":
            raw_failures.extend(body_contract_failures(view, window, raw_response))
        if (task == "body" and previous and isinstance(raw_response, dict)
                and raw_response):
            response = merge_body_retry(view, previous, raw_response)
            contract_failures = body_contract_failures(view, window, response)
        else:
            contract_failures = list(raw_failures)
        audits.append({**meta, "task": task, "prompt_version": prompt_version,
                       "window": window.index, "attempt": attempt,
                       "raw_contract_failures": list(raw_failures),
                       "contract_failures": list(contract_failures)})
        # 网络失败、截断或一次更差的重问都不能抹掉较好的首答。
        # 只在非空 JSON 候选之间按机械契约失败数择优；同分保留先答，
        # 使结果不受偶发重试退化影响。
        if isinstance(response, dict) and response:
            failure_count = len(contract_failures)
            if best_failure_count is None or failure_count < best_failure_count:
                best_response = response
                best_failure_count = failure_count
        if not contract_failures:
            break
        if isinstance(response, dict) and response:
            previous = response
    return PassPayload(window, best_response or {}, tuple(audits))


def run_windowed(view: SerializedDocument, llm, *, task: str,
                 prompt_version: str, system: str,
                 config: UnderstandConfig) -> TaskResult:
    windows = make_windows(view, config)
    workers = min(config.max_workers, len(windows))
    args = dict(task=task, prompt_version=prompt_version, system=system, config=config)
    if workers <= 1:
        payloads = [_one_window(view, llm, item, **args) for item in windows]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_one_window, view, llm, item, **args)
                       for item in windows]
            # 合并永远按窗号，与返回先后无关。
            payloads = [future.result() for future in futures]
    issues = tuple(
        f"{task} 窗口 {item.window.index} 无有效 JSON"
        for item in payloads if not item.response
    )
    return TaskResult(task, prompt_version, tuple(payloads), issues)


def front_pass(view, llm, config=UnderstandConfig()):
    return run_windowed(
        view, llm, task="front", prompt_version="front-v3.1",
        system=FRONT_SYSTEM, config=config,
    )


def body_pass(view, llm, config=UnderstandConfig()):
    return run_windowed(
        view, llm, task="body", prompt_version="body-v2.11",
        system=BODY_SYSTEM, config=config,
    )


def reference_boundary_pass(view, llm, route: str, config=UnderstandConfig()):
    if route not in {"A", "B"}:
        raise ValueError("文献切条路由只能是 A/B")
    return run_windowed(
        view, llm, task=f"refs-boundary-{route}",
        prompt_version=f"refs-boundary-{route.lower()}-v2.1",
        system=REF_BOUNDARY_A_SYSTEM if route == "A" else REF_BOUNDARY_B_SYSTEM,
        config=config,
    )


def adjudicate_boundaries(view: SerializedDocument, llm, left: dict, right: dict,
                          config=UnderstandConfig()):
    route = "v2:refs-boundary-judge:judge-v2.1"
    response, meta = _request(
        llm, REF_BOUNDARY_JUDGE_SYSTEM,
        judge_message(view.render(), left, right), route=route,
        max_tokens=config.output_token_budget,
    )
    return (response if isinstance(response, dict) else {}), {
        **meta, "task": "refs-boundary-judge", "prompt_version": "judge-v2.1"
    }


@dataclass(frozen=True)
class ReferenceInput:
    index: int
    view: str


@dataclass(frozen=True)
class FlattenedSegment:
    segment_id: str
    node_id: str
    start: int
    end: int
    row_index: int
    segment_index: int


@dataclass(frozen=True)
class FlattenedRow:
    index: int
    node_id: str
    start: int
    end: int
    source_text: str
    segments: tuple[FlattenedSegment, ...]


def _display_key(raw) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return raw[:-2] if raw.endswith("|表") else raw


def _line_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """返回不含软换行符的显示行区间，空行仍有稳定地址。"""
    result = []
    start = 0
    for index, char in enumerate(text):
        if char == "\n":
            result.append((start, index))
            start = index + 1
    result.append((start, len(text)))
    return tuple(result)


def flattened_rows(view: SerializedDocument, raw_nodes) -> tuple[FlattenedRow, ...]:
    """把模型指出的显示行机械切成带源坐标的非制表符片段。"""
    scopes = []
    seen = set()
    for raw in raw_nodes or []:
        key = _display_key(raw)
        if not key:
            continue
        record = view.by_key(key)
        if record is None or record.kind != "paragraph" or not record.source_nodes:
            continue
        node_id = record.source_nodes[0]
        node = view.source.node(node_id)
        line_no = 0
        if key != node_id:
            head, dot, tail = key.rpartition(".")
            if not (dot and head == node_id and tail.isdigit() and int(tail) >= 2):
                continue
            line_no = int(tail) - 1
        lines = _line_ranges(node.text)
        if line_no >= len(lines):
            continue
        start, end = lines[line_no]
        identity = (node_id, start, end)
        if identity not in seen:
            seen.add(identity)
            scopes.append(identity)

    scopes.sort(key=lambda item: (view.source.node(item[0]).order, item[1], item[2]))
    rows = []
    for row_index, (node_id, start, end) in enumerate(scopes):
        text = view.source.node(node_id).text
        segments = []
        cursor = start
        segment_index = 0
        while cursor < end:
            if text[cursor] == "\t":
                cursor += 1
                continue
            segment_start = cursor
            while cursor < end and text[cursor] != "\t":
                cursor += 1
            segments.append(FlattenedSegment(
                f"r{row_index}s{segment_index}", node_id,
                segment_start, cursor, row_index, segment_index,
            ))
            segment_index += 1
        rows.append(FlattenedRow(
            row_index, node_id, start, end, text[start:end], tuple(segments)
        ))
    return tuple(rows)


def _flattened_view(rows: tuple[FlattenedRow, ...], view: SerializedDocument) -> str:
    lines = [
        "Artificial labels in ⟦...⟧ are segment IDs, not manuscript text. "
        "⇥ represents one source tab."
    ]
    for row in rows:
        node = view.source.node(row.node_id)
        cursor = row.start
        rendered = []
        for segment in row.segments:
            rendered.append(node.text[cursor:segment.start].replace("\t", "⇥"))
            rendered.append(f"⟦{segment.segment_id}⟧")
            rendered.append(node.text[segment.start:segment.end])
            cursor = segment.end
        rendered.append(node.text[cursor:row.end].replace("\t", "⇥"))
        lines.append(
            f"ROW {row.index} [{row.node_id}:{row.start}-{row.end}] "
            + "".join(rendered)
        )
    return "\n".join(lines)


def validate_flattened_layout(rows: tuple[FlattenedRow, ...], response: dict) -> tuple[dict, list[str]]:
    """验证逻辑格网，并归一为只含源区间和格子几何的装配说明。"""
    failures = []
    if not isinstance(response, dict) or not response:
        response = {}
        failures.append("response was missing or not one non-empty JSON object")
    # issues 是审计说明，不等于未决。只有模型明确声明 resolved=false
    # 才表示它无法从当前源文判定逻辑网格；完整映射仍由下方机械规则
    # 独立验证，不能靠 resolved=true 绕过。
    if response.get("resolved") is not True:
        failures.append("resolved must be true for an actionable table layout")

    n_rows = response.get("n_rows")
    if (isinstance(n_rows, bool) or not isinstance(n_rows, int)
            or not 1 <= n_rows <= len(rows)):
        failures.append(
            "n_rows must be a positive integer no greater than the physical line count"
        )
        n_rows = max(1, len(rows))
    n_cols = response.get("n_cols")
    max_physical_slots = max((row.source_text.count("\t") + 1 for row in rows), default=1)
    if (isinstance(n_cols, bool) or not isinstance(n_cols, int)
            or not 1 <= n_cols <= max_physical_slots):
        failures.append(
            "n_cols must be a positive integer no greater than the largest physical tab-slot count"
        )
        n_cols = 1
    header_rows = response.get("header_rows")
    if (isinstance(header_rows, bool) or not isinstance(header_rows, int)
            or not 0 <= header_rows <= n_rows):
        failures.append("header_rows must be an integer within the logical row count")
        header_rows = 0

    expected = {
        segment.segment_id: segment for row in rows for segment in row.segments
    }
    source_rank = {
        segment.segment_id: rank for rank, segment in enumerate(
            segment for row in rows for segment in row.segments
        )
    }
    raw_cells = response.get("cells")
    if not isinstance(raw_cells, list):
        failures.append("cells is not an array")
        raw_cells = []
    assigned = {}
    duplicates = set()
    unknown = set()
    occupied = [[None for _ in range(n_cols)] for _ in range(n_rows)]
    normalized_cells = [[] for _ in range(n_rows)]
    segment_positions = {}
    for cell_index, item in enumerate(raw_cells):
        if not isinstance(item, dict):
            failures.append("a cell is not an object")
            continue
        row_number = item.get("row")
        column = item.get("column")
        rowspan = item.get("rowspan")
        colspan = item.get("colspan")
        row_header = item.get("row_header")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (
            row_number, column, rowspan, colspan
        )):
            failures.append(f"cell {cell_index} has non-integer geometry")
            continue
        if not (1 <= row_number <= n_rows and 1 <= column <= n_cols
                and rowspan >= 1 and colspan >= 1
                and row_number + rowspan - 1 <= n_rows
                and column + colspan - 1 <= n_cols):
            failures.append(f"cell {cell_index} lies outside the logical grid")
            continue
        if not isinstance(row_header, bool):
            failures.append(f"cell {cell_index} row_header must be boolean")
            continue
        segment_ids = item.get("segment_ids")
        if not isinstance(segment_ids, list) or not segment_ids:
            failures.append(f"cell {cell_index} must contain source segment IDs")
            continue
        valid_ids = []
        for segment_id in segment_ids:
            if segment_id not in expected:
                unknown.add(str(segment_id))
                continue
            if segment_id in assigned:
                duplicates.add(segment_id)
                continue
            assigned[segment_id] = cell_index
            segment_positions[segment_id] = (row_number, column)
            valid_ids.append(segment_id)
        if [source_rank[value] for value in valid_ids] != sorted(
            source_rank[value] for value in valid_ids
        ):
            failures.append(f"cell {cell_index} segment IDs are not in source order")
        for grid_row in range(row_number - 1, row_number - 1 + rowspan):
            for grid_col in range(column - 1, column - 1 + colspan):
                if occupied[grid_row][grid_col] is not None:
                    failures.append(
                        f"cell {cell_index} overlaps cell {occupied[grid_row][grid_col]}"
                    )
                else:
                    occupied[grid_row][grid_col] = cell_index
        normalized_cells[row_number - 1].append({
            "column": column, "rowspan": rowspan, "colspan": colspan,
            "row_header": row_header,
            "ranges": [[expected[value].node_id, expected[value].start,
                        expected[value].end] for value in valid_ids],
        })
    if unknown:
        failures.append("cells contain unknown segment IDs: " + ", ".join(sorted(unknown)))
    if duplicates:
        failures.append("segments assigned more than once: " + ", ".join(sorted(duplicates)))
    missing = sorted(set(expected) - set(assigned))
    if missing:
        failures.append("segments not assigned exactly once: " + ", ".join(missing))

    source_order = [segment.segment_id for row in rows for segment in row.segments]
    positions = [segment_positions[value] for value in source_order
                 if value in segment_positions]
    if positions != sorted(positions):
        failures.append("logical cell positions decrease in source segment order")

    for column in range(n_cols):
        if not any(occupied[row][column] is not None for row in range(n_rows)):
            failures.append(f"logical column {column + 1} has no source-backed cell or span")
    for row_index in range(n_rows):
        if not any(value is not None for value in occupied[row_index]):
            failures.append(f"logical row {row_index + 1} has no source-backed cell or span")

    for row in rows:
        # 独立核对机械分段没有改写、遗漏或换序。制表符仍留在原位，
        # 编号段逐一放回后必须逐字符等于原行。
        cursor = row.start
        rebuilt = []
        for segment in row.segments:
            if segment.node_id != row.node_id or segment.start < cursor or segment.end > row.end:
                failures.append(f"row {row.index} contains an invalid source segment range")
                continue
            rebuilt.append(row.source_text[cursor - row.start:segment.start - row.start])
            rebuilt.append(row.source_text[segment.start - row.start:segment.end - row.start])
            cursor = segment.end
        rebuilt.append(row.source_text[cursor - row.start:])
        if "".join(rebuilt) != row.source_text:
            failures.append(f"row {row.index} cannot be reconstructed byte-for-byte")

    normalized_rows = []
    for row_index in range(n_rows):
        cells = sorted(normalized_cells[row_index], key=lambda value: value["column"])
        # 未被跨度覆盖的坐标是源中明确缺省的空格子；机械补空格子不创造文字。
        for column in range(1, n_cols + 1):
            if occupied[row_index][column - 1] is None:
                cells.append({
                    "column": column, "rowspan": 1, "colspan": 1,
                    "row_header": False, "ranges": [],
                })
        normalized_rows.append({
            "index": row_index,
            "cells": sorted(cells, key=lambda value: value["column"]),
        })
    layout = {
        "valid": not failures,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "header_rows": header_rows,
        "rows": normalized_rows,
        "source_rows": [
            {"node_id": row.node_id, "start": row.start, "end": row.end}
            for row in rows
        ],
        "failures": list(failures),
    }
    return layout, failures


def flattened_tables_pass(view: SerializedDocument, body: dict, llm,
                          config=UnderstandConfig()):
    """对 body 已识别的压平表逐表并发执行纯格子归属。"""
    items = []
    for table_index, spec in enumerate(body.get("tables") or []):
        if not isinstance(spec, dict) or spec.get("table_node") or spec.get("graphic"):
            continue
        rows = flattened_rows(view, spec.get("flattened_row_nodes") or [])
        if rows:
            items.append((table_index, rows))

    def one(item):
        table_index, rows = item
        audits = []
        best_layout = None
        best_failure_count = None
        failures = []
        table_view = _flattened_view(rows, view)
        for attempt in range(MAX_REASK + 1):
            instruction = "Assign every supplied source segment to one logical column."
            if attempt:
                instruction += (
                    " The previous answer failed mechanical validation: "
                    + "; ".join(failures)
                    + ". Return the complete corrected object, not a patch."
                )
            route = f"v2:flattened-table:flat-v3.1:t{table_index}:try{attempt}"
            response, meta = _request(
                llm, FLATTENED_TABLE_SYSTEM,
                user_message(table_view, instruction=instruction), route=route,
                max_tokens=config.output_token_budget,
            )
            layout, failures = validate_flattened_layout(rows, response)
            audits.append({
                **meta, "task": "flattened-table", "prompt_version": "flat-v3.1",
                "table": table_index, "attempt": attempt,
                "contract_failures": list(failures),
            })
            if best_failure_count is None or len(failures) < best_failure_count:
                best_layout = layout
                best_failure_count = len(failures)
            if not failures:
                break
        return table_index, best_layout, tuple(audits)

    workers = min(config.max_workers, len(items))
    if workers <= 1:
        values = [one(item) for item in items]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            values = list(executor.map(one, items))
    return tuple(sorted(values, key=lambda item: item[0]))


def reference_contract_failures(view: str, response: dict) -> list[str]:
    """核对单条著录响应中的每个摘抄确实逐字来自本条源文。"""
    if not isinstance(response, dict) or not response:
        return ["response was missing or not one non-empty JSON object"]
    if response.get("structured") is False:
        return []
    failures = []
    if response.get("structured") is not True:
        failures.append("structured must be true or false")
    if not isinstance(response.get("publication_type"), str) \
            or not response["publication_type"]:
        failures.append("publication_type is required for a structured citation")
    if not isinstance(response.get("person_groups"), list):
        failures.append("person_groups is not an array")
    else:
        for group_index, group in enumerate(response["person_groups"]):
            if not isinstance(group, dict) or not isinstance(group.get("members"), list):
                failures.append(f"person_groups[{group_index}].members is not an array")
                continue
            for member_index, member in enumerate(group["members"]):
                path = f"person_groups[{group_index}].members[{member_index}]"
                if not isinstance(member, dict) or not isinstance(
                    member.get("member_quote"), dict
                ):
                    failures.append(f"{path}.member_quote is required")
                    continue
                if "collab_quote" in member:
                    if not isinstance(member.get("collab_quote"), dict):
                        failures.append(f"{path}.collab_quote is required")
                elif not all(isinstance(member.get(name), dict) for name in (
                    "surname_quote", "given_quote",
                )):
                    failures.append(f"{path} requires surname_quote and given_quote")
    if not isinstance(response.get("fields"), dict):
        failures.append("fields is not an object")

    def check_quote_like(item, path):
        if item is None or isinstance(item, dict):
            return
        if isinstance(item, str):
            if not item or item not in view:
                failures.append(f"{path} is not verbatim source text: {item!r}")
            return
        failures.append(f"{path} is not Q, string, or null")

    fields = response.get("fields")
    if isinstance(fields, dict):
        for name, item in fields.items():
            if name == "comments":
                if not isinstance(item, list):
                    failures.append("response.fields.comments is not an array")
                else:
                    for index, comment in enumerate(item):
                        check_quote_like(comment, f"response.fields.comments[{index}]")
            else:
                check_quote_like(item, f"response.fields.{name}")

    def visit(value, path="response"):
        if isinstance(value, dict):
            if "quote" in value:
                quote = value.get("quote")
                if not isinstance(quote, str) or not quote:
                    failures.append(f"{path}.quote is not a non-empty string")
                elif quote not in view:
                    failures.append(f"{path}.quote is not verbatim source text: {quote!r}")
            for key, item in value.items():
                if key.endswith("_quote") and isinstance(item, str):
                    # 单条著录本身已是严格限定的落锚范围；裸摘抄
                    # 不会丢失跨节点定位信息，但仍须逐字校验。
                    check_quote_like(item, f"{path}.{key}")
                elif key.endswith("_quote") and item is not None and not isinstance(item, dict):
                    failures.append(f"{path}.{key} is not Q, string, or null")
                visit(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(response)
    return failures


def reference_fields_pass(items: Iterable[ReferenceInput], llm,
                          config=UnderstandConfig()):
    items = tuple(items)

    def one(item: ReferenceInput):
        audits = []
        result = None
        best_result = None
        best_failure_count = None
        failures = []
        for attempt in range(MAX_REASK + 1):
            instruction = "Parse this one bounded entry."
            if attempt:
                instruction += (
                    " Previous output failed the mechanical source-quote contract: "
                    + "; ".join(failures)
                    + ". Re-read the bounded entry and return the complete schema with exact "
                      "verbatim excerpts. Do not return a patch."
                )
            route = f"v2:reference-fields:fields-v2.6:r{item.index}:try{attempt}"
            result, meta = _request(
                llm, REFERENCE_FIELDS_SYSTEM,
                user_message(item.view, instruction=instruction), route=route,
                max_tokens=config.output_token_budget,
            )
            failures = reference_contract_failures(item.view, result)
            audits.append({**meta, "task": "reference-fields",
                           "prompt_version": "fields-v2.6", "entry": item.index,
                           "attempt": attempt,
                           "contract_failures": list(failures)})
            if isinstance(result, dict) and result:
                failure_count = len(failures)
                if best_failure_count is None or failure_count < best_failure_count:
                    best_result = result
                    best_failure_count = failure_count
            if not failures:
                break
        return item.index, best_result or {}, tuple(audits)

    workers = min(config.max_workers, len(items))
    if workers <= 1:
        values = [one(item) for item in items]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            values = list(executor.map(one, items))
    return sorted(values, key=lambda value: value[0])


def role_conflict_judge(view: str, conflicts: list[dict], llm,
                        config=UnderstandConfig()):
    instruction = "CONFLICTS:\n" + json.dumps(conflicts, ensure_ascii=False)
    response, meta = _request(
        llm, MERGE_JUDGE_SYSTEM, user_message(view, instruction=instruction),
        route="v2:merge-judge:merge-v2.4",
        max_tokens=config.output_token_budget,
    )
    return (response if isinstance(response, dict) else {}), meta


def discard_review(view: str, candidates: list[dict], llm,
                   config=UnderstandConfig()):
    """独立复核非空文字或对象的弃置决定。"""
    instruction = "DISCARD CANDIDATES:\n" + json.dumps(
        candidates, ensure_ascii=False
    )
    response, meta = _request(
        llm, DISCARD_REVIEW_SYSTEM,
        user_message(view, instruction=instruction),
        route="v2:discard-review:discard-v1.0",
        max_tokens=config.output_token_budget,
    )
    return (response if isinstance(response, dict) else {}), meta


def _evidence_nodes(value) -> set[str]:
    result = set()

    def normalize(raw):
        text = str(raw)
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return text[:-2] if text.endswith("|表") else text

    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"node", "node_hint", "owner_node", "table_node",
                       "reference_title_node", "first_non_reference_after",
                       "body_start_node"} and isinstance(item, str):
                result.add(normalize(item))
            elif key in {"nodes", "front_nodes", "non_reference_nodes",
                         "flattened_row_nodes"} and isinstance(item, list):
                result.update(normalize(part) for part in item)
            else:
                result.update(_evidence_nodes(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_evidence_nodes(item))
    return result


def _merge_values(values, centers):
    nonempty = [(value, center) for value, center in zip(values, centers)
                if value not in (None, "", [], {})]
    if not nonempty:
        return values[0] if values else None
    first = nonempty[0][0]
    if isinstance(first, list):
        result = []
        fingerprints = set()
        for value, center in nonempty:
            for item in value:
                evidence = _evidence_nodes(item)
                if evidence and not evidence.intersection(center):
                    continue
                fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if fingerprint not in fingerprints:
                    fingerprints.add(fingerprint)
                    result.append(item)
        return result
    if isinstance(first, dict):
        keys = []
        for value, _ in nonempty:
            for key in value:
                if key not in keys:
                    keys.append(key)
        return {
            key: _merge_values(
                [value.get(key) for value, _ in nonempty],
                [center for _, center in nonempty],
            )
            for key in keys
        }
    # 标量不依赖返回时序，按窗号取首个非空结论。
    return first


def collapse_payloads(payloads: Iterable[PassPayload]) -> dict:
    payloads = tuple(sorted(payloads, key=lambda item: item.window.index))
    if not payloads:
        return {}
    return _merge_values(
        [item.response for item in payloads],
        [set(item.window.center_keys) for item in payloads],
    )
