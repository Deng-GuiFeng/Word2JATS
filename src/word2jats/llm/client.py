"""统一 LLM 客户端（理解层的模型接入；方法必需，非可选）。

理解层由本客户端驱动——docx 的语义结构判定全部由模型完成，是方法的核心而非增强项。

设计要点：
- **provider 无关**：DeepSeek / DashScope 均为 OpenAI 兼容接口（已实测确认），统一用
  openai SDK + base_url 调用 chat.completions；model-agnostic，可换更强模型。
- **强约束 + 低温**：temperature=0、response_format=json_object，出口再做守恒/DTD 校验。
- **流式传输**：逐片接收后拼回完整 JSON；生成时间不被无依据的固定
  截止时间误杀，中断的半截响应绝不进入理解层。
- **缓存 + 计量**：磁盘缓存重复请求（temp=0 下确定复现）；记录调用次数/tokens/失败数。
- **缓存优先**：先用原 provider/model 身份查缓存，只有未命中时才需要
  API 密钥和网络客户端。因此已录制响应可在无密钥环境重放。
- 接口健壮性：缓存未命中且模型不可达 / 缺 Key 时 :meth:`enabled` 为
  False、调用返回 None。

注意：模型名 / base_url 均经官方文档核对 + 联网实测（deepseek-chat、qwen-plus、
qwen-flash 均可用，json_object 与 temperature 受支持）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import suppress
from typing import Optional
import weakref

from .cache import DiskCache

_log = logging.getLogger(__name__)

# 各 provider 默认配置（base_url 缺省值仅作兜底，优先读 .env）
# local    = 本机 sglang/vLLM(OpenAI 兼容)多卡服务,见 qwen36_deploy。无需 Key、无外网成本。
# dashscope= 阿里云百炼(OpenAI 兼容模式)。默认 qwen3.7-plus:**文本+视觉同一模型**
#            (实测列模型 + 文本/视觉/json/thinking 开关均通过,见 scratchpad/probe_dashscope.py),
#            故 GPU 被占用时可整体替代 local 跑 Agent 视觉闭环。
# thinking_extra_body = 关闭 Qwen thinking 思维链的 extra_body(求确定、短输出);各家写法不同:
#            local(sglang) 用 chat_template_kwargs;dashscope 兼容模式用顶层 enable_thinking。
_PROVIDERS = {
    "deepseek": {
        # DeepSeek V4(v4-pro/v4-flash)默认在「思考模式」,该模式下 temperature 被忽略、且吐思维链
        # 推高输出费用。结构化抽取要确定性,故关思考(thinking.type=disabled)——此时官方对确定性
        # 场景(代码/数学)推荐 temperature=0.0,与本方法一致。deepseek-chat 旧名 2026-07-24 下线,
        # 默认模型改现役 v4-flash。核实见 api-docs.deepseek.com/guides/thinking_mode 与 parameter_settings。
        "key_env": "DEEPSEEK_API_KEY", "url_env": "DEEPSEEK_BASE_URL",
        "default_url": "https://api.deepseek.com", "default_model": "deepseek-flash",
        "needs_key": True, "thinking_extra_body": {"thinking": {"type": "disabled"}},
        "response_format": True,
    },
    "dashscope": {
        "key_env": "DASHSCOPE_API_KEY", "url_env": "DASHSCOPE_BASE_URL",
        "default_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.7-plus",
        # 文首流程单独用 qwen3.8-max。2026-08-23 拿 X01–X04 双模型实跑逐例核对：
        # 同一提示词下 3.8-max 六项更好(空 <day></day> 不再出现、关键词粘连修复、
        # 只含空格的加粗 run 不再吐成 <bold> </bold>、马来语系姓名切分与出版方
        # Crossref 著录一致、重复邮箱减少、摘要整块丢失降为并入上一节)，零项更差。
        # 只覆盖文首，正文/参考文献/引文仍走 default_model，未做对照不擅自铺开。
        "front_model": "qwen3.8-max",
        "needs_key": True, "thinking_extra_body": {"enable_thinking": False},
        "response_format": True,
    },
    "local": {
        "key_env": "LOCAL_LLM_API_KEY", "url_env": "LOCAL_LLM_BASE_URL",
        "default_url": "http://localhost:30000/v1", "default_model": None,
        "needs_key": False,
        "thinking_extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "response_format": False,
    },
}


class _ProcessRequestLimiter:
    """同一进程内所有在线客户端共用的并发闸门。"""

    def __init__(self):
        self._condition = threading.Condition()
        self._limits: dict[int, int] = {}
        self._next_token = 0
        self._active = 0

    def register(self, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("max_inflight 必须是正整数")
        with self._condition:
            self._next_token += 1
            token = self._next_token
            self._limits[token] = limit
            self._condition.notify_all()
            return token

    def unregister(self, token: int) -> None:
        with self._condition:
            self._limits.pop(token, None)
            self._condition.notify_all()

    @property
    def capacity(self) -> int:
        with self._condition:
            return min(self._limits.values(), default=32)

    def acquire(self) -> tuple[float, int]:
        started = time.monotonic()
        with self._condition:
            while self._active >= min(self._limits.values(), default=32):
                self._condition.wait()
            self._active += 1
            return time.monotonic() - started, min(self._limits.values(), default=32)

    def release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("LLM 并发闸门释放次数超过获取次数")
            self._active -= 1
            self._condition.notify_all()


_PROCESS_GATE = _ProcessRequestLimiter()


class LLMClient:
    def __init__(self, provider: str = "off", model: Optional[str] = None,
                 cache_dir: Optional[str] = None, env_path: Optional[str] = None,
                 temperature: float = 0, top_p: Optional[float] = None,
                 seed: Optional[int] = None, max_inflight: int = 32,
                 request_timeout: Optional[float] = None,
                 transport_retries: int = 2,
                 retry_backoff: float = 1.0,
                 retry_backoff_max: float = 8.0):
        if isinstance(max_inflight, bool) or not isinstance(max_inflight, int) \
                or max_inflight < 1:
            raise ValueError("max_inflight 必须是正整数")
        if request_timeout is not None and (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or request_timeout <= 0
        ):
            raise ValueError("request_timeout 必须是正数或 None")
        if isinstance(transport_retries, bool) or not isinstance(transport_retries, int) \
                or transport_retries < 0:
            raise ValueError("transport_retries 必须是非负整数")
        for name, value in (
            ("retry_backoff", retry_backoff),
            ("retry_backoff_max", retry_backoff_max),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} 必须是非负数")
        if retry_backoff_max < retry_backoff:
            raise ValueError("retry_backoff_max 不得小于 retry_backoff")
        self.provider = (provider or "off").lower()
        self.max_inflight = max_inflight
        self.request_timeout = request_timeout
        self.transport_retries = transport_retries
        self.retry_backoff = float(retry_backoff)
        self.retry_backoff_max = float(retry_backoff_max)
        self.temperature = temperature
        self.top_p = top_p            # None=用服务端默认;做控制变量消融时显式固定
        self.seed = seed              # 采样种子:temp>0 多种子取平均+可复现
        self.calls = 0
        self.tokens = 0
        self.prompt_tokens = 0       # 累计输入 tokens(成本审计)
        self.completion_tokens = 0   # 累计输出 tokens
        self.failures = 0   # 模型调用失败/空响应次数(可观测,避免静默)
        self.transport_retry_count = 0
        self.rate_limit_retry_count = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.usage_records = []
        self.reused_usage_records = []
        self._stats_lock = threading.Lock()  # 并发调用下计数不丢更新(client 本身线程安全)
        self._client = None
        self.model = model
        # 构造后 self.model 会被 default_model 填上，届时无法再区分"调用方显式点名"
        # 与"用了后端默认"。for_front() 要靠这个区分:显式点名的模型不该被覆盖。
        self._explicit_model = model is not None
        self._init_kwargs = dict(
            cache_dir=cache_dir, env_path=env_path, temperature=temperature,
            top_p=top_p, seed=seed, max_inflight=max_inflight,
            request_timeout=request_timeout, transport_retries=transport_retries,
            retry_backoff=retry_backoff, retry_backoff_max=retry_backoff_max,
        )
        # 缓存必须在密钥判定前就可用；否则离线环境永远走不到缓存。
        self._cache = DiskCache(cache_dir)
        self.cfg = None
        self._gate_token = None
        self._gate_finalizer = None
        if self.provider == "off" or self.provider not in _PROVIDERS:
            return
        self._load_env(env_path)
        self.cfg = _PROVIDERS[self.provider]
        key = os.getenv(self.cfg["key_env"]) or ("EMPTY" if not self.cfg["needs_key"] else None)
        url = os.getenv(self.cfg["url_env"]) or self.cfg["default_url"]
        self.model = model or self.cfg["default_model"]
        if not key:
            # 保留原 provider/model 身份以查找同一份缓存，仅禁用网络客户端。
            return
        try:
            from openai import OpenAI
            os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
            self._client = OpenAI(api_key=key, base_url=url,
                                  timeout=request_timeout, max_retries=0)
            if self.model is None:  # 本地服务:取已加载模型 id(按 id 排序取首,避免多模型时顺序漂移)
                models = sorted(self._client.models.list().data, key=lambda m: m.id)
                self.model = models[0].id
            self._gate_token = _PROCESS_GATE.register(self.max_inflight)
            self._gate_finalizer = weakref.finalize(
                self, _PROCESS_GATE.unregister, self._gate_token
            )
        except Exception:
            self._client = None
            return

    def for_front(self) -> "LLMClient":
        """派生文首流程专用客户端；用不上时返回自身，调用方无须分情况处理。

        两种情况原样返回 self：本后端没有单配 front_model(deepseek/local/off)，
        或调用方已经显式点名了模型——显式指定优先于我们的默认选择。

        新实例只换模型，端点、密钥、温度、缓存目录、并发上限等一律沿用。多注册
        一个客户端不会放大并发：进程级闸门取所有已注册上限的最小值，活跃计数也是
        全局共享的。缓存 key 含 model，两个模型的结果不会串。
        """
        front = (self.cfg or {}).get("front_model")
        if not front or self._explicit_model or front == self.model:
            return self
        return LLMClient(provider=self.provider, model=front, **self._init_kwargs)

    def close(self) -> None:
        """转换结束后立即解除本客户端对进程级并发上限的约束。"""
        if self._gate_finalizer is not None and self._gate_finalizer.alive:
            self._gate_finalizer()

    @staticmethod
    def _load_env(env_path):
        try:
            from dotenv import find_dotenv, load_dotenv
            if env_path and os.path.exists(env_path):
                load_dotenv(env_path)
                return
            # 从当前工作目录向上逐级查找 .env:支持从 scripts/ 等子目录运行时
            # 仍能找到仓库根的 .env(否则云端 provider 拿不到 Key 会静默置 off、只出空壳)
            found = find_dotenv(usecwd=True)
            if found:
                load_dotenv(found)
        except Exception:
            pass

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _payload(self, system: str, user: str, route: Optional[str] = None,
                 max_tokens: Optional[int] = 4096,
                 response_format: Optional[dict] = None,
                 messages: Optional[list] = None) -> dict:
        payload = {"provider": self.provider, "model": self.model,
                   "system": system, "user": user}
        if messages is not None:
            # 多轮对话必须整体进缓存键：同一 system/user 起头的第 2 轮与第 1 轮
            # payload 其余部分完全相同,不带上完整对话就会命中上一轮的缓存。
            payload["messages"] = messages
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if self.temperature:
            payload["temperature"] = self.temperature
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if self.seed is not None:
            payload["seed"] = self.seed
        if route is not None:
            payload["route"] = route
        if response_format is not None:
            payload["response_format"] = response_format
        return payload

    def extract_json(self, system: str, user: str,
                     max_tokens: Optional[int] = 4096,
                     route: Optional[str] = None,
                     response_format: Optional[dict] = None):
        """调用 LLM 返回 JSON 对象（dict/list）；失败或未启用时返回 None。"""
        return self.request_json(
            system, user, max_tokens=max_tokens, route=route,
            response_format=response_format,
        )[0]

    def request_json(self, system: str, user: str,
                     max_tokens: Optional[int] = 4096,
                     route: Optional[str] = None,
                     response_format: Optional[dict] = None):
        """与 :meth:`extract_json` 同义，并返回该路请求的独立审计元数据。"""
        content, meta = self._request_content(
            system, user, max_tokens=max_tokens, route=route,
            response_format=response_format, json_mode=True,
        )
        value = _safe_json(content)
        meta["ok"] = value is not None
        return value, meta

    def request_text(self, system: str, user: str,
                     max_tokens: Optional[int] = 4096,
                     route: Optional[str] = None,
                     messages: Optional[list] = None):
        """返回模型的完整文本响应，不启用 JSON 响应模式。

        给了 ``messages``（完整多轮对话）就照它发；不给时按 system+user 单轮发，
        与旧行为完全一致。多轮对话整体参与缓存键,见 ``_payload``。
        """
        content, meta = self._request_content(
            system, user, max_tokens=max_tokens, route=route,
            response_format=None, json_mode=False, messages=messages,
        )
        value = content.strip() if isinstance(content, str) and content.strip() else None
        meta["ok"] = value is not None
        return value, meta

    def _request_content(self, system: str, user: str, *,
                         max_tokens: Optional[int], route: Optional[str],
                         response_format: Optional[dict], json_mode: bool,
                         messages: Optional[list] = None):
        payload = self._payload(
            system, user, route, max_tokens, response_format=response_format,
            messages=messages,
        )
        schema_via_prompt = (self.provider == "deepseek" and response_format is not None
                             and response_format.get("type") == "json_schema")
        if schema_via_prompt:
            payload["schema_transport"] = "json-object-with-schema-v1"
        if not json_mode:
            # 原始文本与相同提示的 JSON 请求必须使用不同缓存键。
            payload["response_mode"] = "text"
        meta = {
            "provider": self.provider, "model": self.model, "route": route,
            "temperature": self.temperature, "top_p": self.top_p,
            "seed": self.seed, "cache_hit": False, "network_call": False,
            "ok": False,
        }
        if response_format is not None:
            meta["response_format"] = response_format.get("type")
        cached_entry = self._cache.get_entry(payload)
        if cached_entry is not None:
            from .cache import cache_key
            cached = cached_entry["response"]
            record = {"provider": self.provider, "model": self.model, "route": route,
                      "status": "reused", "cache_key": cache_key(payload),
                      "usage": cached_entry.get("usage"),
                      **(cached_entry.get("response_meta") or {})}
            with self._stats_lock:
                self.cache_hits += 1
                self.reused_usage_records.append(record)
            meta.update(cache_hit=True, ok=bool(cached.strip()))
            return cached, meta
        with self._stats_lock:
            self.cache_misses += 1
        if not self.enabled:
            return None, meta
        kwargs = dict(
            model=self.model,
            # 给了完整对话就照发；没给时退回 system+user 两条,与旧行为逐字相同。
            messages=(list(messages) if messages else
                      [{"role": "system", "content": system},
                       {"role": "user", "content": user}]),
            temperature=self.temperature,
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.seed is not None:
            kwargs["seed"] = self.seed
        if response_format is not None:
            kwargs["response_format"] = response_format
        elif json_mode and self.cfg["response_format"]:
            kwargs["response_format"] = {"type": "json_object"}
        if schema_via_prompt:
            # DeepSeek Chat Completions 接受 JSON Mode，不接受 json_schema 参数。
            # 同一 schema 转为明确的输出约束；理解层仍执行相同的本地契约检查。
            kwargs["response_format"] = {"type": "json_object"}
            schema = response_format["json_schema"]["schema"]
            instruction = "\n\nReturn a JSON object conforming to this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
            kwargs["messages"] = [dict(message) for message in kwargs["messages"]]
            system_message = next((m for m in kwargs["messages"] if m.get("role") == "system"), None)
            if system_message is None:
                kwargs["messages"].insert(0, {"role": "system", "content": instruction})
            else:
                system_message["content"] += instruction
            meta["schema_transport"] = "json-object-with-schema-v1"
        if self.cfg.get("thinking_extra_body"):  # Qwen thinking 模型:关思维链以求确定、短输出
            kwargs["extra_body"] = self.cfg["thinking_extra_body"]
        # 百炼和 DeepSeek 的官方接口均支持 stream + JSON Mode。
        # 逐片接收使读超时表示“连接长时间没有新数据”，
        # 而不是“模型还没生成完”。默认 None 不设无依据的客户端截止时间。
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        meta["network_call"] = True
        meta["network_attempts"] = 0
        meta["retry_delays"] = []
        meta["transport_failures"] = []
        content = None
        usage = None
        stream_meta = None
        for attempt in range(self.transport_retries + 1):
            attempt_started = time.monotonic()
            try:
                wait_seconds, capacity = _PROCESS_GATE.acquire()
                meta["concurrency_wait_seconds"] = (
                    meta.get("concurrency_wait_seconds", 0.0) + wait_seconds
                )
                meta["concurrency_capacity"] = capacity
                meta["network_attempts"] += 1
                try:
                    stream = self._client.chat.completions.create(**kwargs)
                    content, usage, stream_meta = _consume_stream(stream)
                finally:
                    _PROCESS_GATE.release()
                self._record_usage(usage, route, attempt + 1, attempt_started,
                                   "completed", stream_meta)
                break
            except Exception as error:
                self._record_usage(
                    getattr(error, "_word2jats_usage", None), route, attempt + 1,
                    attempt_started, "failed", {
                        **getattr(error, "_word2jats_response_meta", {}),
                        "status_code": _status_code(error),
                    },
                )
                detail = _error_detail(error)
                meta["transport_failures"].append({
                    "attempt": attempt + 1, **detail,
                })
                if attempt >= self.transport_retries or not _retryable_transport(error):
                    with self._stats_lock:
                        self.failures += 1
                    _log.warning(
                        "LLM 流式调用失败: %s (%s/%s)",
                        error, detail["error_type"], detail.get("cause_type"),
                    )
                    meta.update(detail)
                    return None, meta
                delay = _retry_delay(
                    error, attempt, self.retry_backoff, self.retry_backoff_max
                )
                with self._stats_lock:
                    self.transport_retry_count += 1
                    if _status_code(error) == 429:
                        self.rate_limit_retry_count += 1
                meta["retry_delays"].append(delay)
                if delay:
                    time.sleep(delay)
        try:
            meta.update(stream_meta or {})
            from .usage import normalize_usage, usage_dict
            raw_usage = usage_dict(usage)
            meta["usage"] = raw_usage
            meta["usage_normalized"] = normalize_usage(raw_usage)
            with self._stats_lock:
                self.calls += 1
                if usage:
                    self.tokens += meta["usage_normalized"]["total_tokens"] or 0
                    self.prompt_tokens += meta["usage_normalized"]["input_tokens"] or 0
                    self.completion_tokens += meta["usage_normalized"]["output_tokens"] or 0
                if not (content and content.strip()):
                    self.failures += 1
            if content and content.strip():
                self._cache.put(payload, content, usage=raw_usage,
                                response_meta=stream_meta)  # 不缓存空响应
            else:
                _log.warning("LLM 返回空内容(model=%s)", self.model)
            meta["ok"] = bool(content and content.strip())
            return content, meta
        except Exception as e:
            with self._stats_lock:
                self.failures += 1
            _log.warning("LLM 响应处理失败: %s", e)
            meta.update(_error_detail(e))
            return None, meta

    def _record_usage(self, usage, route, attempt, started, status, response_meta):
        from .usage import usage_dict
        record = {
            "provider": self.provider, "model": self.model, "route": route,
            "attempt": attempt, "status": status,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "started_at_unix": round(time.time() - (time.monotonic() - started), 6),
            "usage": usage_dict(usage), **(response_meta or {}),
        }
        with self._stats_lock:
            self.usage_records.append(record)

    @property
    def stats(self) -> dict:
        from .usage import summarize_usage
        return {"provider": self.provider, "model": self.model,
                "calls": self.calls, "tokens": self.tokens,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "failures": self.failures,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "transport_retries": self.transport_retry_count,
                "rate_limit_retries": self.rate_limit_retry_count,
                "usage": summarize_usage(self.usage_records),
                "reused_usage_records": list(self.reused_usage_records),
                "usage_records": list(self.usage_records)}


def _status_code(error) -> Optional[int]:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(getattr(error, "response", None), "status_code", None)
    return value if isinstance(value, int) else None


def _error_detail(error: Exception) -> dict:
    """保留 SDK 统一异常背后的 HTTP 失败阶段。"""
    cause = getattr(error, "__cause__", None)
    return {
        "error": str(error),
        "error_type": type(error).__name__,
        "cause": str(cause) if cause is not None else None,
        "cause_type": type(cause).__name__ if cause is not None else None,
        "status_code": _status_code(error),
        "provider_code": getattr(error, "code", None),
        "provider_body": getattr(error, "body", None),
        "stream_chunks_before_failure": getattr(
            error, "_word2jats_stream_chunks", None
        ),
        "stream_seconds_before_failure": getattr(
            error, "_word2jats_stream_seconds", None
        ),
    }


def _consume_stream(stream):
    """完整消费一次 SSE 响应；流未正常结束就不返回半截内容。"""
    started = time.monotonic()
    first_chunk_at = None
    chunks = 0
    parts = []
    usage = None
    finish_reason = None
    response_meta = {}
    try:
        for chunk in stream:
            chunks += 1
            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
                raw_chunk = chunk.model_dump(mode="json") if hasattr(chunk, "model_dump") else vars(chunk)
                response_meta["response_cost"] = {
                    k: v for k, v in raw_chunk.items()
                    if "cost" in k.lower() or k.lower() == "currency"
                }
            for field, key in (("id", "request_id"), ("model", "returned_model")):
                value = getattr(chunk, field, None)
                if value:
                    response_meta[key] = value
            choices = getattr(chunk, "choices", None) or ()
            if not choices:
                continue
            choice = choices[0]
            reason = getattr(choice, "finish_reason", None)
            if reason is not None:
                finish_reason = reason
            delta = getattr(choice, "delta", None)
            value = getattr(delta, "content", None) if delta is not None else None
            if value:
                parts.append(value)
    except Exception as error:
        # SDK 只上报统一异常时，仍保留“建连前失败”与
        # “已收到部分响应后中断”的可观测区别。
        with suppress(Exception):
            setattr(error, "_word2jats_stream_chunks", chunks)
            setattr(error, "_word2jats_stream_seconds", time.monotonic() - started)
            setattr(error, "_word2jats_usage", usage)
            setattr(error, "_word2jats_response_meta", response_meta)
        raise
    finally:
        close = getattr(stream, "close", None)
        if close:
            with suppress(Exception):
                close()
    if finish_reason != "stop":
        error = RuntimeError(f"模型流未正常完成: finish_reason={finish_reason!r}")
        error._word2jats_usage = usage
        error._word2jats_response_meta = response_meta
        raise error
    finished = time.monotonic()
    return "".join(parts), usage, {
        **response_meta,
        "stream": True,
        "stream_chunks": chunks,
        "stream_first_chunk_seconds": (
            first_chunk_at - started if first_chunk_at is not None else None
        ),
        "stream_total_seconds": finished - started,
        "finish_reason": finish_reason,
    }


def _retryable_transport(error: Exception) -> bool:
    """只重试瞬时传输故障，不用重试掩盖请求本身的错误。"""
    status = _status_code(error)
    if status is not None:
        return status in {408, 409, 429} or status >= 500
    # 请求已被接受且 SSE 已返回过分片，随后才由提供方中止生成，
    # 这与建连前的非法请求不同：已收到的半截内容必须丢弃，整次请求可重试。
    if getattr(error, "_word2jats_stream_chunks", None) is not None:
        return True
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    name = type(error).__name__.lower()
    return "timeout" in name or "connection" in name or "ratelimit" in name


def _retry_after_seconds(error: Exception) -> Optional[float]:
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
        value = float(raw)
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _retry_delay(error: Exception, attempt: int, base: float, maximum: float) -> float:
    generated = min(maximum, base * (2 ** attempt))
    requested = _retry_after_seconds(error)
    return max(generated, requested) if requested is not None else generated


def _safe_json(text: str):
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        # 容错：截取首个 { 或 [ 到末尾
        import re
        m = re.search(r"[\{\[].*[\}\]]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None
