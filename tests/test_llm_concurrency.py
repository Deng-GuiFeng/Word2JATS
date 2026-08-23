import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from word2jats.llm.client import LLMClient


def _chunk(content=None, finish_reason=None, usage=None):
    choices = []
    if content is not None or finish_reason is not None:
        choices.append(SimpleNamespace(
            delta=SimpleNamespace(content=content), finish_reason=finish_reason,
        ))
    return SimpleNamespace(choices=choices, usage=usage)


def _stream(content='{"ok": true}', usage=None):
    return [_chunk(content), _chunk(finish_reason="stop"), _chunk(usage=usage)]


def test_online_clients_share_one_process_wide_concurrency_gate(monkeypatch, tmp_path):
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()

    class Completions:
        def create(self, **kwargs):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                time.sleep(0.02)
                assert kwargs["stream"] is True
                assert kwargs["stream_options"] == {"include_usage": True}
                return _stream()
            finally:
                with lock:
                    state["active"] -= 1

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    clients = [
        LLMClient("local", model="fixture", cache_dir=str(tmp_path / f"cache-{index}"),
                  max_inflight=2)
        for index in range(2)
    ]
    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            values = list(executor.map(
                lambda index: clients[index % 2].request_json(
                    "system", f"request {index}", route=f"route-{index}"
                )[0],
                range(12),
            ))
        assert all(value == {"ok": True} for value in values)
        assert state["peak"] == 2
    finally:
        for client in clients:
            client.close()


def test_concurrency_limit_rejects_non_positive_values():
    for value in (0, -1, True):
        try:
            LLMClient("off", max_inflight=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid limit accepted: {value!r}")


def test_explicitly_unlimited_output_omits_max_tokens_from_request(
    monkeypatch, tmp_path,
):
    state = {"request_kwargs": None}

    class Completions:
        def create(self, **kwargs):
            state["request_kwargs"] = kwargs
            return _stream()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
    )
    try:
        value, meta = client.request_json(
            "system", "request", max_tokens=None, route="unlimited",
        )
    finally:
        client.close()

    assert value == {"ok": True}
    assert meta["ok"] is True
    assert "max_tokens" not in state["request_kwargs"]


def test_raw_text_request_does_not_enable_json_mode(monkeypatch, tmp_path):
    state = {"request_kwargs": None}
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture")

    class Completions:
        def create(self, **kwargs):
            state["request_kwargs"] = kwargs
            return _stream("<article><front/></article>")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    client = LLMClient(
        "dashscope", model="fixture", cache_dir=str(tmp_path / "cache"),
    )
    try:
        value, meta = client.request_text("system", "request", route="raw")
    finally:
        client.close()

    assert value == "<article><front/></article>"
    assert meta["ok"] is True
    assert "response_format" not in state["request_kwargs"]


def test_explicit_json_schema_is_sent_and_enters_cache_identity(
    monkeypatch, tmp_path,
):
    state = {"request_kwargs": None}

    class Completions:
        def create(self, **kwargs):
            state["request_kwargs"] = kwargs
            return _stream('{"value": "grounded"}')

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "fixture",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    }
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    cache_dir = tmp_path / "cache"
    client = LLMClient("dashscope", cache_dir=str(cache_dir))
    try:
        value, meta = client.request_json(
            "system", "request", max_tokens=128_000, route="strict-schema",
            response_format=response_format,
        )
    finally:
        client.close()

    assert value == {"value": "grounded"}
    assert meta["response_format"] == "json_schema"
    assert state["request_kwargs"]["response_format"] == response_format
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1
    import json
    cached = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cached["payload"]["response_format"] == response_format


def test_transient_failures_use_configured_backoff_without_sdk_hidden_retries(
    monkeypatch, tmp_path,
):
    state = {"attempts": 0, "client_kwargs": None}

    class Completions:
        def create(self, **kwargs):
            del kwargs
            state["attempts"] += 1
            if state["attempts"] < 3:
                raise TimeoutError("temporary timeout")
            return _stream()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            state["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=Completions())

    delays = []
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(time, "sleep", delays.append)
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
        request_timeout=17, transport_retries=2,
        retry_backoff=0.25, retry_backoff_max=0.4,
    )
    try:
        value, meta = client.request_json("system", "request", route="retry")
    finally:
        client.close()
    assert value == {"ok": True}
    assert state["attempts"] == 3
    assert state["client_kwargs"]["timeout"] == 17
    assert state["client_kwargs"]["max_retries"] == 0
    assert delays == [0.25, 0.4]
    assert meta["network_attempts"] == 3
    assert meta["retry_delays"] == delays
    assert client.stats["transport_retries"] == 2


def test_non_transient_request_error_is_not_retried(monkeypatch, tmp_path):
    state = {"attempts": 0}

    class BadRequest(Exception):
        status_code = 400

    class Completions:
        def create(self, **kwargs):
            del kwargs
            state["attempts"] += 1
            raise BadRequest("invalid request")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
        transport_retries=5, retry_backoff=0, retry_backoff_max=0,
    )
    try:
        value, meta = client.request_json("system", "request", route="bad-request")
    finally:
        client.close()
    assert value is None
    assert state["attempts"] == 1
    assert meta["network_attempts"] == 1
    assert client.stats["transport_retries"] == 0


def test_rate_limit_retry_honors_server_retry_after(monkeypatch, tmp_path):
    state = {"attempts": 0}

    class RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"retry-after": "3.5"})

    class Completions:
        def create(self, **kwargs):
            del kwargs
            state["attempts"] += 1
            if state["attempts"] == 1:
                raise RateLimited("slow down")
            return _stream()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    delays = []
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(time, "sleep", delays.append)
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
        transport_retries=1, retry_backoff=0.25, retry_backoff_max=1,
    )
    try:
        value, _ = client.request_json("system", "request", route="rate-limit")
    finally:
        client.close()
    assert value == {"ok": True}
    assert delays == [3.5]
    assert client.stats["rate_limit_retries"] == 1


def test_resource_bounds_reject_invalid_values():
    invalid = [
        {"request_timeout": 0},
        {"transport_retries": -1},
        {"retry_backoff": -0.1},
        {"retry_backoff": 2, "retry_backoff_max": 1},
    ]
    for kwargs in invalid:
        try:
            LLMClient("off", **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid resource bounds accepted: {kwargs!r}")


def test_stream_is_joined_only_after_normal_completion(monkeypatch, tmp_path):
    usage = SimpleNamespace(total_tokens=9, prompt_tokens=6, completion_tokens=3)
    state = {"client_kwargs": None}

    class Completions:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            return [
                _chunk('{"answer"'), _chunk(': "source"}'),
                _chunk(finish_reason="stop"), _chunk(usage=usage),
            ]

    class FakeOpenAI:
        def __init__(self, **kwargs):
            state["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
    )
    try:
        value, meta = client.request_json("system", "request", route="stream")
    finally:
        client.close()
    assert value == {"answer": "source"}
    assert state["client_kwargs"]["timeout"] is None
    assert meta["stream"] is True
    assert meta["stream_chunks"] == 4
    assert meta["finish_reason"] == "stop"
    assert client.stats["tokens"] == 9


def test_interrupted_partial_stream_is_discarded_before_retry(monkeypatch, tmp_path):
    state = {"attempts": 0}

    class InterruptedStream:
        def __iter__(self):
            yield _chunk('{"wrong":')
            raise TimeoutError("stream stalled")

    class Completions:
        def create(self, **kwargs):
            del kwargs
            state["attempts"] += 1
            return InterruptedStream() if state["attempts"] == 1 else _stream(
                '{"right": true}'
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
        transport_retries=1, retry_backoff=0, retry_backoff_max=0,
    )
    try:
        value, meta = client.request_json("system", "request", route="interrupted")
    finally:
        client.close()
    assert value == {"right": True}
    assert state["attempts"] == 2
    assert meta["network_attempts"] == 2
    assert meta["transport_failures"][0]["stream_chunks_before_failure"] == 1
    assert meta["transport_failures"][0]["stream_seconds_before_failure"] >= 0


def test_provider_generation_abort_after_stream_start_is_retried(monkeypatch, tmp_path):
    state = {"attempts": 0}

    class ProviderGenerationAbort(Exception):
        code = "provider-generation-aborted"
        body = {"message": "generation stopped; retry the request"}

    class AbortedStream:
        def __iter__(self):
            yield _chunk('{"partial":')
            raise ProviderGenerationAbort("generation aborted")

    class Completions:
        def create(self, **kwargs):
            del kwargs
            state["attempts"] += 1
            return AbortedStream() if state["attempts"] == 1 else _stream(
                '{"complete": true}'
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
        transport_retries=1, retry_backoff=0, retry_backoff_max=0,
    )
    try:
        value, meta = client.request_json("system", "request", route="provider-abort")
    finally:
        client.close()
    assert value == {"complete": True}
    assert state["attempts"] == 2
    failure = meta["transport_failures"][0]
    assert failure["provider_code"] == "provider-generation-aborted"
    assert failure["stream_chunks_before_failure"] == 1


def test_non_stop_stream_never_enters_json_parser(monkeypatch, tmp_path):
    class Completions:
        def create(self, **kwargs):
            del kwargs
            return [_chunk('{"partial": true}'), _chunk(finish_reason="length")]

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    client = LLMClient(
        "local", model="fixture", cache_dir=str(tmp_path / "cache"),
        transport_retries=0,
    )
    try:
        value, meta = client.request_json("system", "request", route="length")
    finally:
        client.close()
    assert value is None
    assert meta["error_type"] == "RuntimeError"
    assert "finish_reason='length'" in meta["error"]


def test_front_client_switches_model_only_where_configured(monkeypatch, tmp_path):
    """文首客户端只在两个条件同时成立时才另起一档：后端配了 front_model，
    且调用方没有点名模型。点名优先于我们的默认选择，别的后端一律不受影响。"""
    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: _stream())
            )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    cases = [
        (dict(provider="dashscope"), "qwen3.8-max", True),
        (dict(provider="dashscope", model="qwen3.7-plus"), "qwen3.7-plus", False),
        (dict(provider="dashscope", model="qwen3.8-max"), "qwen3.8-max", False),
        (dict(provider="deepseek"), "deepseek-v4-flash", False),
        (dict(provider="off"), None, False),
    ]
    for index, (kwargs, expected_model, expect_new) in enumerate(cases):
        client = LLMClient(cache_dir=str(tmp_path / f"cache-{index}"), **kwargs)
        front = client.for_front()
        try:
            assert front.model == expected_model, kwargs
            assert (front is not client) is expect_new, kwargs
            if expect_new:
                # 只换模型：其余身份与资源参数必须原样沿用
                assert front.provider == client.provider
                assert front.temperature == client.temperature
                assert front.max_inflight == client.max_inflight
                assert front.request_timeout == client.request_timeout
                assert front.transport_retries == client.transport_retries
        finally:
            for item in dict.fromkeys((client, front)):
                item.close()


def test_front_client_shares_the_process_gate_with_its_parent(monkeypatch, tmp_path):
    """派生文首客户端不得放大并发：两个客户端合起来仍受同一个上限约束。"""
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()

    class Completions:
        def create(self, **kwargs):
            del kwargs
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                time.sleep(0.02)
                return _stream()
            finally:
                with lock:
                    state["active"] -= 1

    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = LLMClient(provider="dashscope", cache_dir=str(tmp_path / "cache"),
                       max_inflight=2)
    front = client.for_front()
    pair = (client, front)
    try:
        assert front is not client and front.model != client.model
        with ThreadPoolExecutor(max_workers=12) as executor:
            values = list(executor.map(
                lambda index: pair[index % 2].request_json(
                    "system", f"request {index}", route=f"route-{index}"
                )[0],
                range(12),
            ))
        assert all(value == {"ok": True} for value in values)
        assert state["peak"] == 2
    finally:
        for item in dict.fromkeys(pair):
            item.close()


def test_front_client_keeps_its_own_cache_identity(monkeypatch, tmp_path):
    """两档模型共用一个缓存目录也不会串味：缓存身份里含 model。"""
    class FakeOpenAI:
        def __init__(self, **kwargs):
            del kwargs
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: _stream())
            )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = LLMClient(provider="dashscope", cache_dir=str(tmp_path / "cache"))
    front = client.for_front()
    try:
        client.request_json("system", "same request", route="r")
        front.request_json("system", "same request", route="r")
        # 同一份请求、同一个缓存目录，两档模型各存各的，谁也没命中对方
        assert client.cache_hits == 0 and front.cache_hits == 0
        assert len(list((tmp_path / "cache").glob("*.json"))) == 2
    finally:
        for item in dict.fromkeys((client, front)):
            item.close()
