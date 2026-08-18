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
