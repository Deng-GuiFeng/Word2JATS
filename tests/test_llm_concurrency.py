import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from word2jats.llm.client import LLMClient


def test_online_clients_share_one_process_wide_concurrency_gate(monkeypatch, tmp_path):
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
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
                    usage=None,
                )
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
