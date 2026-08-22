"""理解层专项任务调度：真实预算切窗、全量并发、独立路由与限次重问。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from typing import Iterable, Optional

from ..semantic.normalize import canonical_orcid
from ..validate import dtd
from .ground import (
    ground, ground_context, ground_record_quote, record_source_range,
)
from .prompts import (
    BODY_SYSTEM, CITATION_RESPONSE_FORMAT, CITATION_SYSTEM, DISCARD_REVIEW_SYSTEM,
    FLATTENED_TABLE_SYSTEM, FRONT_RESPONSE_FORMAT, FRONT_SYSTEM,
    FRONT_CONTENT_SYSTEM,
    HEAD_BOUNDARY_RESPONSE_FORMAT, HEAD_BOUNDARY_SYSTEM,
    HEAD_JATS_SYSTEM,
    MERGE_JUDGE_SYSTEM,
    REFERENCE_FIELDS_SYSTEM, REF_BOUNDARY_A_SYSTEM,
    REF_BOUNDARY_B_SYSTEM, REF_BOUNDARY_JUDGE_SYSTEM,
    front_content_user_message, head_boundary_user_message, judge_message,
    user_message, xml_user_message,
)
from .serialize import SerializedDocument


MAX_REASK = 1

# 头部直出 XML 的 DTD 自修复上限:首答之外最多再问 4 次。停机条件只有两条——
# 校验通过则成功退出,问满则失败退出。不设"违规数不再下降"之类的启发式停机:
# 那是拿一个可能算错的指标去替代唯一权威的判定。
MAX_DTD_REPAIR = 4


@dataclass(frozen=True)
class UnderstandConfig:
    max_workers: int = 32
    input_token_budget: int = 90_000
    boundary_token_budget: int = 4_000
    output_token_budget: Optional[int] = 128_000

    def __post_init__(self):
        for name in (
            "max_workers", "input_token_budget", "boundary_token_budget",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} 必须是正整数")
        if self.output_token_budget is not None and (
            isinstance(self.output_token_budget, bool)
            or not isinstance(self.output_token_budget, int)
            or self.output_token_budget < 1
        ):
            raise ValueError("output_token_budget 必须是正整数或 None")
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
    contract_failures: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class HeadJatsResult:
    """两个文首任务的交付物。

    ``xml`` 是元信息任务生成的 JATS 片段；``content`` 只保存摘要和
    关键词的源指针与结构关系。两类节点分别保留，避免把程序从原文
    生成的摘要错误记成模型生成文字。
    """

    task: str
    prompt_version: str
    xml: Optional[str]
    metadata_nodes: tuple[str, ...]
    content: dict
    content_nodes: tuple[str, ...]
    audit: tuple[dict, ...]
    issues: tuple[str, ...] = ()

    @property
    def front_nodes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.metadata_nodes + self.content_nodes))


def _token_upper_bound(value: str) -> int:
    """返回受支持字节级分词器的严格 token 数上界。

    云端商业模型没有公开可调的本地精确分词器，不得拿其他模型的
    tokenizer 代替，也不得用“字节数除以经验常数”猜测。当前受支持的
    Qwen/DeepSeek/OpenAI 兼容后端均以 UTF-8 字节为未知字符的最细回退单位，
    因此真实 token 数不会超过 UTF-8 字节数。用这个上界切窗可能更保守，
    但不会把超限输入误判为可发送。
    """
    return max(1, len(value.encode("utf-8")))


def make_windows(view: SerializedDocument, config: UnderstandConfig, *,
                 structural_facts: bool = False) -> tuple[Window, ...]:
    records = view.records
    costs = [
        _token_upper_bound(view.render((index,), structural_facts=structural_facts)) + 2
        for index in range(len(records))
    ]
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


def _request(llm, system: str, user: str, *, route: str,
             max_tokens: Optional[int], response_format: Optional[dict] = None):
    kwargs = {"max_tokens": max_tokens, "route": route}
    if response_format is not None:
        kwargs["response_format"] = response_format
    if hasattr(llm, "request_json"):
        return llm.request_json(system, user, **kwargs)
    result = llm.extract_json(system, user, **kwargs)
    return result, {
        "provider": getattr(llm, "provider", None), "model": getattr(llm, "model", None),
        "route": route, "cache_hit": None, "network_call": None,
        "ok": isinstance(result, dict),
    }


def _request_text(llm, system: str, user: str, *, route: str,
                  max_tokens: Optional[int], messages: Optional[list] = None):
    if hasattr(llm, "request_text"):
        return llm.request_text(
            system, user, max_tokens=max_tokens, route=route, messages=messages
        )
    raise TypeError("LLM 客户端不支持原始文本响应")


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


def front_response_failures(response: dict, source) -> list[str]:
    """验证统一的“实体 + 关系 + 原文指针”契约，不判断版式套路。"""
    failures = []
    checked_quotes = set()
    resolved_quotes = {}

    def source_hint(raw):
        """把模型可见的清单地址还原为底层源节点。"""
        if not isinstance(raw, str):
            return None
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        if raw.endswith("|表"):
            raw = raw[:-2]
        if raw in source._nodes:
            return raw
        head, dot, tail = raw.rpartition(".")
        return head if dot and tail.isdigit() and head in source._nodes else None

    def quote(path, raw, *, scope=None):
        if not isinstance(raw, dict):
            failures.append(f"{path} is not a quote pointer")
            return None
        checked_quotes.add(id(raw))
        value = raw.get("quote")
        hint = raw.get("node_hint")
        if not isinstance(value, str) or not value or not isinstance(hint, str):
            failures.append(f"{path} has no non-empty quote/node_hint")
            return None
        resolved_hint = source_hint(hint)
        if resolved_hint is None:
            failures.append(f"{path} points to an unknown source node")
            return None
        left = raw.get("left_context", "")
        right = raw.get("right_context", "")
        if not isinstance(left, str) or not isinstance(right, str):
            failures.append(f"{path} has invalid left/right context")
            return None
        if any("\n" in item or "\r" in item for item in (value, left, right)):
            failures.append(
                f"{path} crosses displayed source records; return one Q per record "
                "in the containing array"
            )
            return None
        match = ground_context(
            value, source, left_context=left, right_context=right,
            scope=scope, block_hint=resolved_hint,
        )
        if match is None:
            failures.append(f"{path} is not uniquely grounded")
        else:
            resolved_quotes[id(raw)] = match
        return match

    front_content_fields = (
        "title_quotes", "authors", "affiliations", "addresses",
        "correspondences", "editors", "abstracts", "contributor_notes",
        "author_note_quotes", "front_nodes",
    )
    front_present = response.get("category_quote") is not None \
        or response.get("keywords") is not None \
        or any(response.get(field) for field in front_content_fields) \
        or bool((response.get("dates") or {}).get("items"))
    article_types = {
        "research-article", "review-article", "case-report", "editorial", "other",
    }
    if front_present and response.get("article_type") not in article_types:
        failures.append("article_type is missing or invalid")
    if not front_present and response.get("article_type") is not None:
        failures.append("an empty front window must use null article_type")
    titles = response.get("title_quotes")
    if not isinstance(titles, list) or (front_present and not titles):
        failures.append("title_quotes is empty or not an array")

    known_objects = {item.occ_id for item in source.occurrences}

    def source_nodes(path, values, *, require_nonempty=False):
        if not isinstance(values, list):
            failures.append(f"{path} is not an array")
            return
        if require_nonempty and not values:
            failures.append(f"{path} is empty")
        for index, value in enumerate(values):
            if source_hint(value) is None:
                failures.append(f"{path}[{index}] points to an unknown source node")

    collections = (
        ("authors", "author"),
        ("affiliations", "affiliation"),
        ("addresses", "address"),
        ("correspondences", "correspondence"),
        ("contributor_notes", "note"),
    )
    entities = {}
    author_scopes = {}
    for field, entity_type in collections:
        values = response.get(field)
        if not isinstance(values, list):
            failures.append(f"{field} is not an array")
            continue
        for index, value in enumerate(values):
            path = f"{field}[{index}]"
            if not isinstance(value, dict):
                failures.append(f"{path} is not an object")
                continue
            entity_id = value.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                failures.append(f"{path}.entity_id is empty or invalid")
                continue
            if entity_id in entities:
                failures.append(f"duplicate entity_id: {entity_id}")
                continue
            entities[entity_id] = (entity_type, value, path)

            if entity_type == "author":
                whole = quote(f"{path}.author_quote", value.get("author_quote"))
                author_scopes[entity_id] = whole
                quote(f"{path}.surname_quote", value.get("surname_quote"), scope=whole)
                quote(f"{path}.given_quote", value.get("given_quote"), scope=whole)
                if value.get("suffix_quote") is not None:
                    quote(f"{path}.suffix_quote", value.get("suffix_quote"), scope=whole)
                if value.get("orcid_quote") is not None:
                    orcid_range = quote(
                        f"{path}.orcid_quote", value.get("orcid_quote")
                    )
                    if (orcid_range is not None
                            and canonical_orcid(source.slice_text(orcid_range)) is None):
                        failures.append(
                            f"{path}.orcid_quote is not a complete valid ORCID iD"
                        )
            elif entity_type in {"affiliation", "correspondence"}:
                if not value.get("content_quotes"):
                    failures.append(f"{path}.content_quotes is empty")
            elif entity_type == "address":
                source_nodes(f"{path}.source_nodes", value.get("source_nodes"),
                             require_nonempty=True)
                if not any(value.get(name) for name in (
                    "line_quotes", "postal_quote", "phone_quote",
                )):
                    failures.append(f"{path} has no address content")
            elif entity_type == "note" and not value.get("paragraph_quotes"):
                failures.append(f"{path}.paragraph_quotes is empty")

    endpoint_types = {
        "author-affiliation": ("author", "affiliation"),
        "author-correspondence": ("author", "correspondence"),
        "author-address": ("author", "address"),
        "affiliation-address": ("affiliation", "address"),
        "author-note": ("author", "note"),
    }
    relations = response.get("relations")
    if not isinstance(relations, list):
        failures.append("relations is not an array")
        relations = []
    seen_relations = set()
    for index, relation in enumerate(relations):
        path = f"relations[{index}]"
        if not isinstance(relation, dict):
            failures.append(f"{path} is not an object")
            continue
        kind = relation.get("kind")
        source_id = relation.get("source_id")
        target_id = relation.get("target_id")
        expected = endpoint_types.get(kind)
        source_entity = entities.get(source_id)
        target_entity = entities.get(target_id)
        if expected is None:
            failures.append(f"{path}.kind is invalid")
        elif source_entity is None or target_entity is None:
            failures.append(f"{path} points to an unknown entity")
        elif (source_entity[0], target_entity[0]) != expected:
            failures.append(
                f"{path} endpoints do not match relation kind {kind}"
            )
        identity = (kind, source_id, target_id)
        if identity in seen_relations:
            failures.append(f"{path} duplicates an existing relation")
        seen_relations.add(identity)

        marker = relation.get("marker_quote")
        if marker is not None:
            scope = author_scopes.get(source_id)
            if expected is None or expected[0] != "author":
                failures.append(f"{path} has a marker but its source is not an author")
            quote(f"{path}.marker_quote", marker, scope=scope)

    # 日期分量的定位范围是它所属的整条日期，不是整份文档。
    # 否则两个日期恰好同年时，会把本来唯一的指针误判为歧义。
    dates = response.get("dates")
    if not isinstance(dates, dict):
        failures.append("dates is not an object")
    else:
        if dates.get("format") not in {"dmy", "mdy", "ymd", "unknown"}:
            failures.append("dates.format is invalid")
        date_items = dates.get("items")
        if not isinstance(date_items, list):
            failures.append("dates.items is not an array")
        else:
            for date_index, item in enumerate(date_items):
                path = f"dates.items[{date_index}]"
                if not isinstance(item, dict):
                    failures.append(f"{path} is not an object")
                    continue
                if item.get("kind") not in {"received", "revised", "accepted"}:
                    failures.append(f"{path}.kind is invalid")
                whole = quote(f"{path}.whole_quote", item.get("whole_quote"))
                year = quote(
                    f"{path}.year_quote", item.get("year_quote"), scope=whole,
                )
                for component in ("month", "day"):
                    raw_component = item.get(f"{component}_quote")
                    if raw_component is not None:
                        quote(
                            f"{path}.{component}_quote", raw_component, scope=whole,
                        )
                if year is not None:
                    year_text = source.slice_text(year).strip()
                    if not year_text or not year_text.isdecimal():
                        failures.append(
                            f"{path}.year_quote is not a decimal calendar year; "
                            "omit a placeholder/status item instead of treating it as a date"
                        )

    # 所有内容性字段统一使用 Q；递归检查每一个 Q，而不是逐字段写规则。
    def all_quotes(value, path="front"):
        if isinstance(value, dict):
            quote_keys = {"quote", "node_hint", "left_context", "right_context"}
            if {"quote", "node_hint"} <= set(value) <= quote_keys:
                if id(value) not in checked_quotes:
                    quote(path, value)
                return
            for key, item in value.items():
                all_quotes(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                all_quotes(item, f"{path}[{index}]")

    all_quotes(response)

    # 通讯实体必须有可追溯的实质内容。孤立角标只是关系证据，
    # 既不表示“与谁通讯”，也不表示“如何通讯”，不能冒充实体。
    for entity_type, value, path in entities.values():
        if entity_type != "correspondence":
            continue
        content_ranges = [
            resolved_quotes[id(raw)]
            for raw in value.get("content_quotes") or []
            if id(raw) in resolved_quotes
        ]
        if content_ranges and not any(
            character.isalnum()
            for item in content_ranges
            for character in source.slice_text(item)
        ):
            failures.append(
                f"{path}.content_quotes has no substantive correspondence text"
            )

    source_nodes(
        "front_nodes", response.get("front_nodes"), require_nonempty=front_present
    )
    body_start = response.get("body_start_node")
    if body_start is not None and source_hint(body_start) is None:
        failures.append("body_start_node points to an unknown source node")
    for index, abstract in enumerate(response.get("abstracts") or []):
        if not isinstance(abstract, dict):
            continue
        source_nodes(
            f"abstracts[{index}].source_nodes", abstract.get("source_nodes"),
            require_nonempty=True,
        )
        for graphic_index, occurrence_id in enumerate(abstract.get("graphics") or []):
            if occurrence_id not in known_objects:
                failures.append(
                    f"abstracts[{index}].graphics[{graphic_index}] "
                    "points to an unknown object occurrence"
                )
        for section_index, section in enumerate(abstract.get("sections") or []):
            if (isinstance(section, dict) and section.get("wrapped") is True
                    and section.get("title_quote") is None):
                failures.append(
                    f"abstracts[{index}].sections[{section_index}] "
                    "is wrapped but has no title_quote"
                )
        abstract_ranges = []
        for section_index, section in enumerate(abstract.get("sections") or []):
            if not isinstance(section, dict):
                continue
            fields = [
                ("title_quote", section.get("title_quote")),
                *[(f"paragraph_quotes[{paragraph_index}]", paragraph)
                  for paragraph_index, paragraph in enumerate(
                      section.get("paragraph_quotes") or [])],
            ]
            for field, raw_quote in fields:
                if raw_quote is None:
                    continue
                current = resolved_quotes.get(id(raw_quote))
                if current is None:
                    continue
                for prior_path, prior in abstract_ranges:
                    if (current[0] == prior[0] and current[1] < prior[2]
                            and prior[1] < current[2]):
                        failures.append(
                            f"abstracts[{index}].sections[{section_index}].{field} "
                            f"overlaps {prior_path}; each title and paragraph must use "
                            "a distinct source span"
                        )
                        break
                abstract_ranges.append((
                    f"abstracts[{index}].sections[{section_index}].{field}", current,
                ))
    keywords = response.get("keywords")
    if isinstance(keywords, dict):
        source_nodes("keywords.source_nodes", keywords.get("source_nodes"),
                     require_nonempty=True)
    issues = response.get("issues")
    if not isinstance(issues, list):
        failures.append("issues is not an array")
    elif issues:
        failures.append("model reported unresolved front-matter issues: "
                        + "; ".join(map(str, issues)))
    return failures


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
        title = item.get("title_quote")
        if title is not None:
            if not isinstance(title, dict):
                failures.append(f"object title_quote is not a Q object: {occurrence_id}")
            else:
                match = ground_record_quote(
                    title.get("quote"), view,
                    record_key=title.get("node_hint"),
                    left_context=title.get("left_context"),
                    right_context=title.get("right_context"),
                )
                if match is None:
                    failures.append(
                        f"object title_quote is not uniquely grounded: {occurrence_id}"
                    )

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


def front_content_response_failures(view: SerializedDocument, window: Window,
                                    response: dict) -> list[str]:
    """只查返回形式和源指针，不用程序复判摘要语义。"""
    if not isinstance(response, dict) or not response:
        return ["返回结果不是一个非空 JSON 对象"]

    visible_keys = {view.records[index].key for index in window.context_indices}
    failures = []

    def canonical_key(raw):
        if not isinstance(raw, str):
            return None
        key = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
        return key[:-2] if key.endswith("|表") else key

    def whole_record(path, raw):
        key = canonical_key(raw)
        if key not in visible_keys:
            failures.append(f"{path} 不是本次输入中的记录地址")
            return None
        value = record_source_range(view, key)
        if value is None:
            failures.append(f"{path} 不能还原为一个连续 Word 字符区间")
        return value

    def quote(path, raw):
        if not isinstance(raw, dict):
            failures.append(f"{path} 不是 Q 对象")
            return None
        value = raw.get("quote")
        hint = raw.get("node_hint")
        left = raw.get("left_context")
        right = raw.get("right_context")
        if (not isinstance(value, str) or not value or "\n" in value
                or not isinstance(hint, str)
                or not isinstance(left, str) or "\n" in left
                or not isinstance(right, str) or "\n" in right):
            failures.append(
                f"{path} 必须含有非空 quote、有效 node_hint 以及字符串上下文"
            )
            return None
        key = canonical_key(hint)
        if key not in visible_keys:
            failures.append(f"{path}.node_hint 不是本次输入中的记录地址")
            return None
        match = ground_record_quote(
            value, view, record_key=key,
            left_context=left, right_context=right,
        )
        if match is None:
            failures.append(f"{path} 不能在 node_hint 指定的记录中唯一定位")
        return match

    abstracts = response.get("abstracts")
    if not isinstance(abstracts, list):
        failures.append("abstracts 不是数组")
        abstracts = []
    for abstract_index, abstract in enumerate(abstracts):
        path = f"abstracts[{abstract_index}]"
        if not isinstance(abstract, dict):
            failures.append(f"{path} 不是对象")
            continue
        if abstract.get("container_quote") is not None:
            quote(f"{path}.container_quote", abstract.get("container_quote"))
        sections = abstract.get("sections")
        if not isinstance(sections, list) or not sections:
            failures.append(f"{path}.sections 必须是非空数组")
            continue
        for section_index, section in enumerate(sections):
            section_path = f"{path}.sections[{section_index}]"
            if not isinstance(section, dict):
                failures.append(f"{section_path} 不是对象")
                continue
            title = None
            if section.get("title_quote") is not None:
                title = quote(
                    f"{section_path}.title_quote", section.get("title_quote")
                )
            wrapped = section.get("wrapped")
            if not isinstance(wrapped, bool):
                failures.append(f"{section_path}.wrapped 不是布尔值")
            elif wrapped and title is None:
                failures.append(f"{section_path} 要生成 sec，但没有可定位的小节标题")
            paragraphs = section.get("paragraphs")
            if not isinstance(paragraphs, list) or not paragraphs:
                failures.append(f"{section_path}.paragraphs 必须是非空数组")
                continue
            for paragraph_index, paragraph in enumerate(paragraphs):
                paragraph_path = f"{section_path}.paragraphs[{paragraph_index}]"
                if isinstance(paragraph, str):
                    whole_record(paragraph_path, paragraph)
                else:
                    current = quote(paragraph_path, paragraph)
                    key = canonical_key(paragraph.get("node_hint")) \
                        if isinstance(paragraph, dict) else None
                    whole = record_source_range(view, key) if key else None
                    if current is not None and current == whole:
                        failures.append(
                            f"{paragraph_path} 已经是整条记录，应直接返回记录地址"
                        )

    keyword_groups = response.get("keyword_groups")
    if not isinstance(keyword_groups, list):
        failures.append("keyword_groups 不是数组")
        keyword_groups = []
    for group_index, group in enumerate(keyword_groups):
        path = f"keyword_groups[{group_index}]"
        if not isinstance(group, dict):
            failures.append(f"{path} 不是对象")
            continue
        if group.get("container_quote") is not None:
            quote(f"{path}.container_quote", group.get("container_quote"))
        keywords = group.get("keyword_quotes")
        if not isinstance(keywords, list) or not keywords:
            failures.append(f"{path}.keyword_quotes 必须是非空数组")
            continue
        for keyword_index, item in enumerate(keywords):
            keyword_path = f"{path}.keyword_quotes[{keyword_index}]"
            quote(keyword_path, item)

    if not isinstance(response.get("issues"), list):
        failures.append("issues 不是数组")
    return failures


def citation_contract_failures(view: SerializedDocument, window: Window,
                               response: dict, reference_ids: set[str]) -> list[str]:
    """只核对引文关系的两端是否为已知、可唯一定位的实体。"""
    del window
    failures = []
    groups = []
    for key, kind in (
        ("compact_range_citations", "range"),
        ("single_target_citations", "single"),
    ):
        values = response.get(key)
        if not isinstance(values, list):
            failures.append(f"{key} is not an array")
        else:
            groups.extend((key, kind, index, item)
                          for index, item in enumerate(values))
    if failures:
        return failures
    resolved_ranges = []
    for key, kind, citation_index, item in groups:
        path = f"{key}[{citation_index}]"
        if not isinstance(item, dict):
            failures.append(f"{path} is not an object")
            continue
        raw_quote = item.get("citation_quote")
        if not isinstance(raw_quote, dict):
            failures.append(
                f"{path}.citation_quote "
                "must be an object with string fields quote, record_key, "
                "left_context, and right_context; "
                "a bare string is invalid"
            )
        else:
            quote = raw_quote.get("quote")
            record_key = raw_quote.get("record_key")
            left_context = raw_quote.get("left_context")
            right_context = raw_quote.get("right_context")
            if not isinstance(quote, str) or not quote \
                    or not isinstance(record_key, str) or not record_key \
                    or not isinstance(left_context, str) \
                    or not isinstance(right_context, str):
                failures.append(
                    f"{path}.citation_quote "
                    "must contain non-empty string fields quote and record_key, "
                    "plus string fields left_context and right_context"
                )
            else:
                citation_range = ground_record_quote(
                    quote, view, record_key=record_key,
                    left_context=left_context, right_context=right_context,
                )
                if citation_range is None:
                    failures.append(
                        f"{path}.citation_quote "
                        "does not identify one exact source span in its record_key"
                    )
                elif any(
                    citation_range[0] == prior[0]
                    and citation_range[1] < prior[2]
                    and prior[1] < citation_range[2]
                    for prior in resolved_ranges
                ):
                    failures.append(
                        f"{path}.citation_quote "
                        "overlaps another citation source span"
                    )
                else:
                    resolved_ranges.append(citation_range)
        if kind == "single":
            target = item.get("target_reference_id")
            if not isinstance(target, str) or target not in reference_ids:
                failures.append(f"{path} has an unknown target entity")
        else:
            targets = item.get("target_reference_ids")
            if not isinstance(targets, list) or len(targets) < 2:
                failures.append(f"{path} must contain at least two target entities")
            elif any(not isinstance(target, str) or target not in reference_ids
                     for target in targets):
                failures.append(f"{path} contains unknown target entities")
            elif len(set(targets)) != len(targets):
                failures.append(f"{path} contains duplicate target entities")
    return failures


def _one_window(view: SerializedDocument, llm, window: Window, *,
                task: str, prompt_version: str, system: str,
                config: UnderstandConfig, contract_validator=None,
                response_format: Optional[dict] = None,
                structural_facts: bool = False,
                message_builder=user_message,
                retry_message_builder=None) -> PassPayload:
    source_view = view.render(
        window.context_indices, structural_facts=structural_facts
    )
    audits = []
    response = None
    best_response = None
    best_failure_count = None
    best_failures = ()
    for attempt in range(MAX_REASK + 1):
        if attempt == 0:
            correction = ""
        elif retry_message_builder is not None:
            correction = retry_message_builder(contract_failures)
        else:
            correction = (
                "The previous response failed the mechanical response contract: "
                + "; ".join(contract_failures)
                + ". Return the COMPLETE required JSON object, not a patch. Use null/[] only "
                  "for genuinely uncertain semantic values; do not omit source blocks or objects."
            )
        window_key = f"{window.center_indices[0]}-{window.center_indices[-1]}"
        route = f"v2:{task}:{prompt_version}:w{window_key}:try{attempt}"
        response, meta = _request(
            llm, system, message_builder(source_view, instruction=correction),
            route=route, max_tokens=config.output_token_budget,
            response_format=response_format,
        )
        raw_response = response
        raw_failures = []
        if not isinstance(raw_response, dict) or not raw_response:
            raw_failures.append("response was missing or not one non-empty JSON object")
        elif contract_validator is not None:
            raw_failures.extend(contract_validator(view, window, raw_response))
        contract_failures = list(raw_failures)
        audits.append({**meta, "task": task, "prompt_version": prompt_version,
                       "window": window_key, "attempt": attempt,
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
                best_failures = tuple(contract_failures)
        if not contract_failures:
            break
    return PassPayload(
        window, best_response or {}, tuple(audits),
        best_failures if best_response else tuple(contract_failures),
    )


def _run_payloads(view, llm, windows, *, task, prompt_version, system,
                  config, contract_validator, response_format=None,
                  structural_facts=False):
    workers = min(config.max_workers, len(windows))
    args = dict(
        task=task, prompt_version=prompt_version, system=system, config=config,
        contract_validator=contract_validator, response_format=response_format,
        structural_facts=structural_facts,
    )
    if workers <= 1:
        return [_one_window(view, llm, item, **args) for item in windows]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_one_window, view, llm, item, **args)
                   for item in windows]
        return [future.result() for future in futures]


def run_windowed(view: SerializedDocument, llm, *, task: str,
                 prompt_version: str, system: str,
                 config: UnderstandConfig, contract_validator=None,
                 response_format: Optional[dict] = None,
                 structural_facts: bool = False) -> TaskResult:
    if contract_validator is None and task == "body":
        contract_validator = body_contract_failures
    windows = make_windows(view, config, structural_facts=structural_facts)
    payloads = _run_payloads(
        view, llm, windows, task=task, prompt_version=prompt_version,
        system=system, config=config, contract_validator=contract_validator,
        response_format=response_format, structural_facts=structural_facts,
    )
    issues = tuple(
        f"{task} 窗口 {item.window.center_indices[0]}-{item.window.center_indices[-1]} "
        "无有效 JSON"
        for item in payloads if not item.response
    ) + tuple(
        f"{task} 窗口 {item.window.center_indices[0]}-{item.window.center_indices[-1]} "
        "契约未闭合: " + "; ".join(item.contract_failures)
        for item in payloads if item.contract_failures
    )
    return TaskResult(task, prompt_version, tuple(payloads), issues)


def front_pass(view, llm, config=UnderstandConfig()):
    # 挂牌保留（2026-08-21）：指针制头部任务，现行生产走 head_jats_pass（模型直出
    # XML，见检查点 B 清单 B-20）；本函数是退回指针制的退路，头部方向裁决前不删。
    return run_windowed(
        view, llm, task="front", prompt_version="front-v4.10-context-relations",
        system=FRONT_SYSTEM, config=config,
        contract_validator=lambda current_view, _window, response: (
            front_response_failures(response, current_view.source)
        ),
        response_format=FRONT_RESPONSE_FORMAT,
    )


def head_boundary_response_failures(view: SerializedDocument, response: dict,
                                    visible_keys: tuple[str, ...]) -> list[str]:
    """只核对两个任务范围的源地址，不用程序猜测文首语义。"""
    if not isinstance(response, dict) or not response:
        return ["response was missing or not one non-empty JSON object"]

    positions = {key: index for index, key in enumerate(visible_keys)}
    failures = []

    def task_range(name):
        raw = response.get(name)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            failures.append(f"{name} is not an object or null")
            return None
        endpoints = []
        for field in ("first_node", "last_node"):
            node = _display_key(raw.get(field))
            path = f"{name}.{field}"
            if not isinstance(node, str) or node not in positions:
                failures.append(f"{path} is not a visible first-window node")
                endpoints.append(None)
                continue
            record = view.by_key(node)
            if record is None or not record.text.strip():
                failures.append(f"{path} points to an empty record")
                endpoints.append(None)
                continue
            endpoints.append(positions[node])
        first, last = endpoints
        if first is not None and last is not None and first > last:
            failures.append(f"{name}.first_node follows last_node")
        return tuple(endpoints)

    task_range("metadata_range")
    task_range("front_content_range")
    issues = response.get("issues")
    if not isinstance(issues, list):
        failures.append("issues is not an array")
    elif issues:
        failures.append("model reported unresolved ranges: " + "; ".join(map(str, issues)))
    return failures


def _head_range_indices(view: SerializedDocument, prefix_indices: tuple[int, ...],
                        raw: Optional[dict]) -> tuple[int, ...]:
    """把模型返回的一对端点机械还原为一个连续记录范围。"""
    if not isinstance(raw, dict):
        return ()
    positions = {
        view.records[index].key: offset
        for offset, index in enumerate(prefix_indices)
    }
    first = positions.get(_display_key(raw.get("first_node")))
    last = positions.get(_display_key(raw.get("last_node")))
    if first is None or last is None or first > last:
        return ()
    return prefix_indices[first:last + 1]


def _front_content_source_nodes(view: SerializedDocument, response: dict) -> tuple[str, ...]:
    """从模型明确返回的源指针收集任务二实际消费的节点。"""
    result = []

    def add_hint(raw):
        for node_id in _response_node_ids(view, raw):
            if node_id not in result:
                result.append(node_id)

    for abstract in response.get("abstracts") or []:
        if not isinstance(abstract, dict):
            continue
        container = abstract.get("container_quote")
        if isinstance(container, dict):
            add_hint(container.get("node_hint"))
        for section in abstract.get("sections") or []:
            if not isinstance(section, dict):
                continue
            title = section.get("title_quote")
            if isinstance(title, dict):
                add_hint(title.get("node_hint"))
            for paragraph in section.get("paragraphs") or []:
                if isinstance(paragraph, str):
                    add_hint(paragraph)
                elif isinstance(paragraph, dict):
                    add_hint(paragraph.get("node_hint"))
    for group in response.get("keyword_groups") or []:
        if not isinstance(group, dict):
            continue
        for raw in [group.get("container_quote"), *(group.get("keyword_quotes") or [])]:
            if isinstance(raw, dict):
                add_hint(raw.get("node_hint"))
    return tuple(result)


def _front_content_retry_message(failures) -> str:
    return (
        "上一次返回的源指针无法按要求还原："
        + "；".join(failures)
        + "。请重新返回完整 JSON，不要只返回修改部分。"
        "语义确实无法确定时可使用 null 或空数组，但不能伪造或省略源指针。"
    )


def _head_jats_repair_message(report) -> str:
    """把 DTD 判定结果写成下一轮的 user 消息。

    只转达判定,不替模型想改法。要求重发完整 XML 而不是补丁:模型返回补丁时
    无法机械拼回原文,拼错了会把上一轮正确的部分一并毁掉。
    """
    return (
        "上一次返回的 XML 不符合 JATS Publishing 1.3 DTD。校验器报告如下：\n\n"
        + report.render()
        + "\n\n请逐条修正后重新返回完整的 XML，不要只返回改动部分，也不要附加"
          "解释、Markdown 代码块、XML 声明或 DOCTYPE。修正只允许调整元素与属性的"
          "名称、位置、层级和取值；Word 原文中的可见文字一个都不能增删或改写。"
          "某处格式或结构确实无法合法表示时，保留可见文字、舍弃无法表示的结构。"
    )


def _dtd_failure_digest(report, limit: int = 3) -> str:
    """失败时给人看的摘要。完整报错在 audit 里,这里只要够定位。"""
    if report is None:
        return "没有取得校验结果"
    if not report.well_formed:
        return "返回的不是良构 XML: %s" % report.parse_error
    items = ["%s %s" % (v.code, v.path or "/") for v in report.violations[:limit]]
    if len(report.violations) > limit:
        items.append("等 %d 条" % len(report.violations))
    return "；".join(items)


def head_jats_pass(view: SerializedDocument, llm,
                   config=UnderstandConfig()) -> HeadJatsResult:
    """共享定位后并行执行元信息任务和正文式文首内容任务。"""
    prefix_indices, prefix_view = view.head_prefix(config.input_token_budget)
    prefix_keys = tuple(view.records[index].key for index in prefix_indices)
    prefix_last = prefix_indices[-1] if prefix_indices else 0
    boundary_route = (
        f"v2:head-boundary:head-ranges-v2.0:first-0-{prefix_last}"
    )
    boundary, boundary_meta = _request(
        llm, HEAD_BOUNDARY_SYSTEM, head_boundary_user_message(prefix_view),
        route=boundary_route, max_tokens=config.output_token_budget,
        response_format=HEAD_BOUNDARY_RESPONSE_FORMAT,
    )
    boundary = boundary if isinstance(boundary, dict) else {}
    boundary_failures = tuple(head_boundary_response_failures(
        view, boundary, prefix_keys
    ))
    boundary_audit = {
        **boundary_meta, "task": "head-boundary",
        "prompt_version": "head-ranges-v2.0",
        "window": f"0-{prefix_last}", "attempt": 0,
        "response_issues": list(boundary_failures),
    }

    if boundary_failures:
        issues = tuple(
            f"head-boundary 无法确定头部范围: {item}"
            for item in boundary_failures
        )
        return HeadJatsResult(
            "head", "head-ranges-v2.0", None, (), {}, (),
            (boundary_audit,), issues,
        )

    metadata_indices = _head_range_indices(
        view, prefix_indices, boundary.get("metadata_range")
    )
    content_indices = _head_range_indices(
        view, prefix_indices, boundary.get("front_content_range")
    )
    metadata_nodes = tuple(dict.fromkeys(
        node_id
        for index in metadata_indices
        for node_id in view.records[index].source_nodes
    ))

    def request_metadata():
        """直出 XML,并以 DTD 判定驱动自修复。

        停机条件只有两条:片段通过 JATS 1.3 DTD 校验则成功退出;问满
        ``MAX_DTD_REPAIR`` 次仍不合法则失败退出,记 issue 交由交付门拦截。
        失败时仍把最后一轮的 XML 交出去——丢弃它会让整个 front 塌成空
        article-meta,比留下几条 DTD 违反糟糕得多,而交付门本就拦住了它。
        """
        if not metadata_indices:
            return None, (), ()
        source_view = "\n".join(
            view.render_head_record(index) for index in metadata_indices
        )
        first, last = metadata_indices[0], metadata_indices[-1]
        window_key = f"{first}-{last}"
        user_text = xml_user_message(source_view)
        messages = [{"role": "system", "content": HEAD_JATS_SYSTEM},
                    {"role": "user", "content": user_text}]
        audits = []
        xml = None
        report = None
        for attempt in range(MAX_DTD_REPAIR + 1):
            route = (f"v2:head-jats:head-jats-v2.4:range-{window_key}"
                     f":try{attempt}")
            response, meta = _request_text(
                llm, HEAD_JATS_SYSTEM, user_text, route=route,
                max_tokens=config.output_token_budget, messages=messages,
            )
            xml = response if isinstance(response, str) and response.strip() else None
            report = dtd.validate_head_fragment(xml)
            audits.append({
                **meta, "task": "head-jats", "prompt_version": "head-jats-v2.4",
                "window": window_key, "attempt": attempt,
                "dtd_ok": report.ok,
                "dtd_violations": [v.code for v in report.violations],
            })
            if report.ok:
                return xml, tuple(audits), ()
            if xml is None:
                # 没拿到任何文本就没有可供模型修正的对象。原样再问一次,
                # 不往对话里塞空的 assistant 轮次。
                continue
            messages = messages + [
                {"role": "assistant", "content": xml},
                {"role": "user", "content": _head_jats_repair_message(report)},
            ]
        return xml, tuple(audits), (
            "head-jats 经 %d 轮仍不符合 JATS 1.3 DTD: %s"
            % (MAX_DTD_REPAIR + 1, _dtd_failure_digest(report)),
        )

    def request_content():
        if not content_indices:
            return {}, (), ()
        payload = _one_window(
            view, llm,
            Window(
                0, content_indices, content_indices,
                tuple(view.records[index].key for index in content_indices),
            ),
            task="front-content", prompt_version="front-content-v1.1",
            system=FRONT_CONTENT_SYSTEM, config=config,
            contract_validator=front_content_response_failures,
            structural_facts=True,
            message_builder=front_content_user_message,
            retry_message_builder=_front_content_retry_message,
        )
        return payload.response, payload.audit, payload.contract_failures

    with ThreadPoolExecutor(max_workers=2) as executor:
        metadata_future = executor.submit(request_metadata)
        content_future = executor.submit(request_content)
        xml, metadata_audits, metadata_issues = metadata_future.result()
        content, content_audits, content_failures = content_future.result()

    content_nodes = _front_content_source_nodes(view, content)
    issues = []
    if metadata_indices and xml is None:
        issues.append("head-jats 没有返回可用的 XML 文本")
    issues.extend(metadata_issues)
    if content_indices and not content:
        issues.append("front-content 没有返回可用的 JSON")
    if content_failures:
        issues.append(
            "front-content 源指针核对未通过: " + "; ".join(content_failures)
        )
    for item in content.get("issues") or []:
        issues.append(f"front-content 无法确定内容结构: {item}")
    audits = (boundary_audit,) + tuple(metadata_audits) + tuple(content_audits)
    return HeadJatsResult(
        "head", "head-ranges-v2.0", xml, metadata_nodes, content,
        content_nodes, audits, tuple(issues),
    )


def body_pass(view, llm, config=UnderstandConfig()):
    return run_windowed(
        view, llm, task="body", prompt_version="body-v2.14",
        system=BODY_SYSTEM, config=config, contract_validator=body_contract_failures,
        structural_facts=True,
    )


def _quote_value(raw):
    return raw.get("quote") if isinstance(raw, dict) and isinstance(
        raw.get("quote"), str
    ) else None


def _reference_identity(index: int, raw: dict) -> dict:
    fields = raw.get("fields") if isinstance(raw, dict) else None
    fields = fields if isinstance(fields, dict) else {}
    surnames = []
    for group in raw.get("person_groups") or [] if isinstance(raw, dict) else []:
        if not isinstance(group, dict) or group.get("kind") != "author":
            continue
        for member in group.get("members") or []:
            if not isinstance(member, dict):
                continue
            surname = _quote_value(member.get("surname_quote"))
            if surname and surname not in surnames:
                surnames.append(surname)
    return {
        "entity_id": f"reference:{index}",
        "label": _quote_value(raw.get("label_quote")) if isinstance(raw, dict) else None,
        "surnames": surnames,
        "year": _quote_value(fields.get("year")),
        "year_suffix": _quote_value(fields.get("year_suffix")),
        "title": (
            _quote_value(fields.get("article_title"))
            or _quote_value(fields.get("chapter_title"))
        ),
    }


def citation_pass(view, references, reference_fields, llm,
                  config=UnderstandConfig()):
    references = tuple(references)
    reference_fields = tuple(reference_fields)
    catalog = [
        _reference_identity(
            span.index,
            reference_fields[position] if position < len(reference_fields) else {},
        )
        for position, span in enumerate(references)
    ]
    ids = {item["entity_id"] for item in catalog}
    system = (
        CITATION_SYSTEM + "\n\nREFERENCE IDENTITIES:\n"
        + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    )
    return run_windowed(
        view, llm, task="citations", prompt_version="citations-v2.7",
        system=system, config=config,
        contract_validator=lambda current_view, window, response: (
            citation_contract_failures(current_view, window, response, ids)
        ),
        response_format=CITATION_RESPONSE_FORMAT,
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
            route = f"v2:reference-fields:fields-v2.7:r{item.index}:try{attempt}"
            result, meta = _request(
                llm, REFERENCE_FIELDS_SYSTEM,
                user_message(item.view, instruction=instruction), route=route,
                max_tokens=config.output_token_budget,
            )
            failures = reference_contract_failures(item.view, result)
            audits.append({**meta, "task": "reference-fields",
                           "prompt_version": "fields-v2.7", "entry": item.index,
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
    payloads = tuple(sorted(
        payloads, key=lambda item: item.window.center_indices[0]
    ))
    if not payloads:
        return {}
    return _merge_values(
        [item.response for item in payloads],
        [set(item.window.center_keys) for item in payloads],
    )
