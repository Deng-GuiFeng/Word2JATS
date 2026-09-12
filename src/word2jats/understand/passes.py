"""理解层专项任务调度：真实预算切窗、全量并发、独立路由与限次重问。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from typing import Iterable, Optional

from ..build.jats import PERSON_GROUP_TYPES
from ..validate import dtd
from .ground import ground_record_quote, record_source_range
from .prompts import (
    BODY_SYSTEM, CITATION_RESPONSE_FORMAT, CITATION_SYSTEM, DISCARD_REVIEW_SYSTEM,
    FLATTENED_TABLE_SYSTEM, FRONT_CONTENT_SYSTEM,
    HEAD_BOUNDARY_RESPONSE_FORMAT, HEAD_BOUNDARY_SYSTEM,
    HEAD_JATS_SYSTEM,
    MERGE_JUDGE_SYSTEM,
    REFERENCE_FIELDS_SYSTEM, REF_BOUNDARY_A_SYSTEM,
    REF_BOUNDARY_B_SYSTEM, REF_BOUNDARY_JUDGE_SYSTEM,
    body_user_message, citation_user_message, discard_review_user_message,
    flattened_table_user_message, front_content_user_message,
    head_boundary_user_message, judge_message, merge_judge_user_message,
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

    return failures


def citation_contract_failures(view: SerializedDocument, window: Window,
                               response: dict) -> list[str]:
    """只核对摘抄能唯一落锚、编号形态是一串数字、条目之间不重叠。"""
    del window
    failures = []
    items = response.get("citations")
    if not isinstance(items, list):
        return ["citations 不是数组"]
    resolved_ranges = []
    for index, item in enumerate(items):
        path = f"citations[{index}]"
        if not isinstance(item, dict):
            failures.append(f"{path} 不是对象")
            continue
        raw_quote = item.get("citation_quote")
        if not isinstance(raw_quote, dict):
            failures.append(
                f"{path}.citation_quote 必须是对象，含 quote、record_key、"
                "left_context、right_context 四个字符串字段；裸字符串无效"
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
                    f"{path}.citation_quote 的 quote 与 record_key 必须是非空"
                    "字符串，left_context 与 right_context 必须是字符串"
                )
            else:
                citation_range = ground_record_quote(
                    quote, view, record_key=record_key,
                    left_context=left_context, right_context=right_context,
                )
                if citation_range is None:
                    failures.append(
                        f"{path}.citation_quote 不能在 record_key 指定的记录中"
                        "唯一定位"
                    )
                elif any(
                    citation_range[0] == prior[0]
                    and citation_range[1] < prior[2]
                    and prior[1] < citation_range[2]
                    for prior in resolved_ranges
                ):
                    failures.append(
                        f"{path}.citation_quote 与另一处引用的源区间重叠"
                    )
                else:
                    resolved_ranges.append(citation_range)
        # 编号只核形态。它对应文末哪一条参考文献，由归并阶段按印出的标号解析。
        # 正文没印编号时整个字段缺席，这是允许的，不是错。
        if "target_reference_ids" not in item:
            continue
        targets = item.get("target_reference_ids")
        if not isinstance(targets, list) or not targets:
            failures.append(f"{path} 的 target_reference_ids 不能是空数组；"
                            f"正文没印编号就整个不要这个字段")
        elif any(not isinstance(target, str) or not target.isdigit()
                 for target in targets):
            failures.append(f"{path} 含有不是一串数字的参考文献编号")
        elif len(set(targets)) != len(targets):
            failures.append(f"{path} 含有重复的参考文献编号")
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
        else:
            build = retry_message_builder or _zh_contract_retry_message
            correction = build(contract_failures)
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
            raw_failures.append("返回结果不是一个非空 JSON 对象")
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
                  structural_facts=False, message_builder=user_message,
                  retry_message_builder=None):
    workers = min(config.max_workers, len(windows))
    args = dict(
        task=task, prompt_version=prompt_version, system=system, config=config,
        contract_validator=contract_validator, response_format=response_format,
        structural_facts=structural_facts, message_builder=message_builder,
        retry_message_builder=retry_message_builder,
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
                 structural_facts: bool = False,
                 message_builder=user_message,
                 retry_message_builder=None) -> TaskResult:
    windows = make_windows(view, config, structural_facts=structural_facts)
    payloads = _run_payloads(
        view, llm, windows, task=task, prompt_version=prompt_version,
        system=system, config=config, contract_validator=contract_validator,
        response_format=response_format, structural_facts=structural_facts,
        message_builder=message_builder,
        retry_message_builder=retry_message_builder,
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


def head_boundary_response_failures(view: SerializedDocument, response: dict,
                                    visible_keys: tuple[str, ...]) -> list[str]:
    """只核对两个任务范围的源地址，不用程序猜测文首语义。"""
    if not isinstance(response, dict) or not response:
        return ["返回结果不是一个非空 JSON 对象"]

    positions = {key: index for index, key in enumerate(visible_keys)}
    failures = []

    def task_range(name):
        raw = response.get(name)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            failures.append(f"{name} 既不是对象也不是 null")
            return None
        endpoints = []
        for field in ("first_node", "last_node"):
            node = _display_key(raw.get(field))
            path = f"{name}.{field}"
            if not isinstance(node, str) or node not in positions:
                failures.append(f"{path} 不是首窗内可见的记录地址")
                endpoints.append(None)
                continue
            record = view.by_key(node)
            if record is None or not record.text.strip():
                failures.append(f"{path} 指向的记录没有文字")
                endpoints.append(None)
                continue
            endpoints.append(positions[node])
        first, last = endpoints
        if first is not None and last is not None and first > last:
            failures.append(f"{name} 的 first_node 排在 last_node 之后")
        return tuple(endpoints)

    task_range("metadata_range")
    task_range("front_content_range")
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


def _zh_contract_retry_message(failures) -> str:
    """正文各任务的中文重问消息，与英文版逐条对应。"""
    return (
        "上一次返回未通过机械核对："
        + "；".join(failures)
        + "。请返回完整的 JSON 对象，不要只返回改动部分。"
        "只有语义确实无法确定时才使用 null 或空数组；不得遗漏源记录或对象。"
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
            route = (f"v2:head-jats:head-jats-v2.5:range-{window_key}"
                     f":try{attempt}")
            response, meta = _request_text(
                llm, HEAD_JATS_SYSTEM, user_text, route=route,
                max_tokens=config.output_token_budget, messages=messages,
            )
            xml = response if isinstance(response, str) and response.strip() else None
            report = dtd.validate_head_fragment(xml)
            audits.append({
                **meta, "task": "head-jats", "prompt_version": "head-jats-v2.5",
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
    audits = (boundary_audit,) + tuple(metadata_audits) + tuple(content_audits)
    return HeadJatsResult(
        "head", "head-ranges-v2.0", xml, metadata_nodes, content,
        content_nodes, audits, tuple(issues),
    )


def body_pass(view, llm, config=UnderstandConfig()):
    # 不设响应自洽校验：实测该校验既不充分也不必要（重问 12 例 0 例闭合，
    # 失败条数与最终缺陷无正相关，且近半数要求程序本就不依赖）。正文的
    # 真实防线在下游两本账——未覆盖的文字与对象一律阻断。此处只保留
    # 「没拿到 JSON 就重问一次」这条与任务无关的兜底。
    return run_windowed(
        view, llm, task="body", prompt_version="body-v2.14",
        system=BODY_SYSTEM, config=config,
        structural_facts=True,
        message_builder=body_user_message,
    )


def _quote_value(raw):
    if isinstance(raw, str):
        # 裸摘抄与 Q 对象同义（F7 契约放宽后成员摘抄可为字符串）。
        return raw or None
    return raw.get("quote") if isinstance(raw, dict) and isinstance(
        raw.get("quote"), str
    ) else None


def citation_pass(view, llm, config=UnderstandConfig()):
    """正文引用识别：只找出哪段文字是引用、它印的是哪个编号。

    编号对应文末哪一条参考文献，由归并阶段按印出的标号解析（见
    understand._citation_relations），所以这一路不依赖参考文献流程的结果，
    也不再往 system 里拼「参考文献身份」。
    """
    return run_windowed(
        view, llm, task="citations", prompt_version="citations-v3.0",
        system=CITATION_SYSTEM, config=config,
        contract_validator=lambda current_view, window, response: (
            citation_contract_failures(current_view, window, response)
        ),
        response_format=CITATION_RESPONSE_FORMAT,
        message_builder=citation_user_message,
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
        "⟦...⟧ 中的标记是程序给片段编的号，不是稿件文字；⇥ 代表原文中的一个制表符。"
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
        failures.append("返回结果不是一个非空 JSON 对象")
    # 只有模型明确声明 resolved=false 才表示它无法从当前源文判定逻辑网格；
    # 完整映射仍由下方机械规则独立验证，不能靠 resolved=true 绕过。
    if response.get("resolved") is not True:
        failures.append("resolved 必须为 true，否则这份版式不能用于装配")

    n_rows = response.get("n_rows")
    if (isinstance(n_rows, bool) or not isinstance(n_rows, int)
            or not 1 <= n_rows <= len(rows)):
        failures.append(
            "n_rows 必须是正整数，且不超过物理行数"
        )
        n_rows = max(1, len(rows))
    n_cols = response.get("n_cols")
    max_physical_slots = max((row.source_text.count("\t") + 1 for row in rows), default=1)
    if (isinstance(n_cols, bool) or not isinstance(n_cols, int)
            or not 1 <= n_cols <= max_physical_slots):
        failures.append(
            "n_cols 必须是正整数，且不超过单行制表位数量的最大值"
        )
        n_cols = 1
    header_rows = response.get("header_rows")
    if (isinstance(header_rows, bool) or not isinstance(header_rows, int)
            or not 0 <= header_rows <= n_rows):
        failures.append("header_rows 必须是整数，且不超过逻辑行数")
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
        failures.append("cells 不是数组")
        raw_cells = []
    assigned = {}
    duplicates = set()
    unknown = set()
    occupied = [[None for _ in range(n_cols)] for _ in range(n_rows)]
    normalized_cells = [[] for _ in range(n_rows)]
    segment_positions = {}
    for cell_index, item in enumerate(raw_cells):
        if not isinstance(item, dict):
            failures.append("有单元格不是对象")
            continue
        row_number = item.get("row")
        column = item.get("column")
        rowspan = item.get("rowspan")
        colspan = item.get("colspan")
        row_header = item.get("row_header")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (
            row_number, column, rowspan, colspan
        )):
            failures.append(f"单元格 {cell_index} 的行列与跨度不是整数")
            continue
        if not (1 <= row_number <= n_rows and 1 <= column <= n_cols
                and rowspan >= 1 and colspan >= 1
                and row_number + rowspan - 1 <= n_rows
                and column + colspan - 1 <= n_cols):
            failures.append(f"单元格 {cell_index} 超出逻辑格网范围")
            continue
        if not isinstance(row_header, bool):
            failures.append(f"单元格 {cell_index} 的 row_header 不是布尔值")
            continue
        segment_ids = item.get("segment_ids")
        if not isinstance(segment_ids, list) or not segment_ids:
            failures.append(f"单元格 {cell_index} 必须给出源片段编号")
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
            failures.append(f"单元格 {cell_index} 的源片段编号没有按原文顺序排列")
        for grid_row in range(row_number - 1, row_number - 1 + rowspan):
            for grid_col in range(column - 1, column - 1 + colspan):
                if occupied[grid_row][grid_col] is not None:
                    failures.append(
                        f"单元格 {cell_index} 与单元格 "
                        f"{occupied[grid_row][grid_col]} 重叠"
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
        failures.append("cells 含有未知的源片段编号：" + "、".join(sorted(unknown)))
    if duplicates:
        failures.append("以下源片段被归入了不止一个单元格：" + "、".join(sorted(duplicates)))
    missing = sorted(set(expected) - set(assigned))
    if missing:
        failures.append("以下源片段没有恰好归入一个单元格：" + "、".join(missing))

    source_order = [segment.segment_id for row in rows for segment in row.segments]
    positions = [segment_positions[value] for value in source_order
                 if value in segment_positions]
    if positions != sorted(positions):
        failures.append("单元格位置没有随源片段的原文顺序递增")

    for column in range(n_cols):
        if not any(occupied[row][column] is not None for row in range(n_rows)):
            failures.append(f"逻辑第 {column + 1} 列没有任何有源片段支撑的单元格或跨度")
    for row_index in range(n_rows):
        if not any(value is not None for value in occupied[row_index]):
            failures.append(f"逻辑第 {row_index + 1} 行没有任何有源片段支撑的单元格或跨度")

    for row in rows:
        # 独立核对机械分段没有改写、遗漏或换序。制表符仍留在原位，
        # 编号段逐一放回后必须逐字符等于原行。
        cursor = row.start
        rebuilt = []
        for segment in row.segments:
            if segment.node_id != row.node_id or segment.start < cursor or segment.end > row.end:
                failures.append(f"第 {row.index} 行含有无效的源片段区间")
                continue
            rebuilt.append(row.source_text[cursor - row.start:segment.start - row.start])
            rebuilt.append(row.source_text[segment.start - row.start:segment.end - row.start])
            cursor = segment.end
        rebuilt.append(row.source_text[cursor - row.start:])
        if "".join(rebuilt) != row.source_text:
            failures.append(f"第 {row.index} 行无法逐字符还原为原文")

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
            instruction = "把给出的每一个源片段都归入一个逻辑列。"
            if attempt:
                instruction += (
                    "上一次返回未通过机械校验："
                    + "；".join(failures)
                    + "。请返回完整的修正结果，不要只返回改动部分。"
                )
            route = f"v2:flattened-table:flat-v3.1:t{table_index}:try{attempt}"
            response, meta = _request(
                llm, FLATTENED_TABLE_SYSTEM,
                flattened_table_user_message(table_view, instruction=instruction),
                route=route, max_tokens=config.output_token_budget,
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
            kind = group.get("kind")
            if kind is not None and (
                not isinstance(kind, str) or kind not in PERSON_GROUP_TYPES
            ):
                # person-group-type 是 JATS 封闭枚举；越界值引导模型重答。
                failures.append(
                    f"person_groups[{group_index}].kind {kind!r} is not a JATS "
                    "person-group-type; allowed values: "
                    + ", ".join(sorted(PERSON_GROUP_TYPES))
                )
            def _member_quote_ok(item) -> bool:
                # 单条著录本身已是严格限定的落锚范围；成员摘抄与字段摘抄
                # 同理，裸字符串不丢失定位信息，但仍须逐字来自本条源文。
                if isinstance(item, dict):
                    return True
                return isinstance(item, str) and bool(item) and item in view

            for member_index, member in enumerate(group["members"]):
                path = f"person_groups[{group_index}].members[{member_index}]"
                if not isinstance(member, dict) or not _member_quote_ok(
                    member.get("member_quote")
                ):
                    failures.append(f"{path}.member_quote is required")
                    continue
                if "collab_quote" in member:
                    if not _member_quote_ok(member.get("collab_quote")):
                        failures.append(f"{path}.collab_quote is required")
                elif not all(_member_quote_ok(member.get(name)) for name in (
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
    instruction = "角色冲突清单：\n" + json.dumps(conflicts, ensure_ascii=False)
    response, meta = _request(
        llm, MERGE_JUDGE_SYSTEM,
        merge_judge_user_message(view, instruction=instruction),
        route="v2:merge-judge:merge-v2.4",
        max_tokens=config.output_token_budget,
    )
    return (response if isinstance(response, dict) else {}), meta


def discard_review(view: str, candidates: list[dict], llm,
                   config=UnderstandConfig()):
    """独立复核非空文字或对象的弃置决定。"""
    instruction = "待复核的丢弃提议：\n" + json.dumps(
        candidates, ensure_ascii=False
    )
    response, meta = _request(
        llm, DISCARD_REVIEW_SYSTEM,
        discard_review_user_message(view, instruction=instruction),
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
