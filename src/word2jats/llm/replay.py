"""只读 LLM 响应录制与无网络重放。"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Optional

from .cache import cache_key
from .client import _safe_json
from ..snapshot import read_snapshot, write_snapshot


REPLAY_LAYER = "llm.raw-responses"
REPLAY_VERSION = 2


def record_cache_snapshot(cache_dir: str | Path, target: str | Path, *,
                          strict: bool = True) -> Path:
    """把 DiskCache 录制为一份可移植的只读快照。"""
    entries = []
    invalid = []
    for path in sorted(Path(cache_dir).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            payload = raw["payload"]
            response = raw["response"]
            if not isinstance(payload, dict) or not isinstance(response, str):
                raise TypeError("payload/response 类型错误")
            key = cache_key(payload)
            if key != path.stem:
                # 缓存文件名也是证据的一部分，不收录被改名的记录。
                raise ValueError("文件名与请求摘要不符")
            entries.append({"key": key, "payload": payload, "response": response})
        except (OSError, ValueError, KeyError, TypeError) as error:
            invalid.append(f"{path.name}: {error}")
    if invalid and strict:
        raise ValueError("LLM 缓存录制发现无效记录: " + "; ".join(invalid))
    return write_snapshot(
        target, REPLAY_LAYER, {"entries": entries}, payload_version=REPLAY_VERSION,
        metadata={"entry_count": len(entries), "invalid_count": len(invalid)},
    )


class ReplayLLM:
    """与 LLMClient 共用 extract_json 介面，但永远不创建网络客户端。"""

    def __init__(self, recording: str | Path, *, provider: str, model: str,
                 temperature: float = 0, top_p: Optional[float] = None,
                 seed: Optional[int] = None):
        envelope = read_snapshot(
            recording, expected_layer=REPLAY_LAYER, payload_version=REPLAY_VERSION
        )
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.seed = seed
        self._responses: dict[str, str] = {}
        for entry in envelope["payload"].get("entries", []):
            key = cache_key(entry["payload"])
            if key != entry.get("key"):
                raise ValueError(f"LLM 录制键不符: {entry.get('key')}")
            response = entry["response"]
            old = self._responses.get(key)
            if old is not None and old != response:
                raise ValueError(f"LLM 录制含冲突响应: {key}")
            self._responses[key] = response
        self.calls = 0
        self.hits = 0
        self.misses = 0
        self.failures = 0
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return True

    def _payload(self, system: str, user: str, route: Optional[str],
                 max_tokens: Optional[int],
                 response_format: Optional[dict] = None,
                 messages: Optional[list] = None) -> dict:
        payload = {"provider": self.provider, "model": self.model,
                   "system": system, "user": user}
        if messages is not None:
            # 与 client._payload 一致：多轮对话必须整体进缓存键，否则第 2 轮
            # 会命中第 1 轮录下的响应。
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
        return self.request_json(
            system, user, max_tokens=max_tokens, route=route,
            response_format=response_format,
        )[0]

    def request_json(self, system: str, user: str,
                     max_tokens: Optional[int] = 4096,
                     route: Optional[str] = None,
                     response_format: Optional[dict] = None):
        key = cache_key(self._payload(
            system, user, route, max_tokens, response_format=response_format,
        ))
        response = self._responses.get(key)
        with self._lock:
            self.calls += 1
            if response is None:
                self.misses += 1
                return None, {
                    "provider": self.provider, "model": self.model,
                    "route": route, "cache_hit": False,
                    "network_call": False, "ok": False, "backend": "replay",
                    "response_format": (
                        response_format.get("type") if response_format else None
                    ),
                }
            self.hits += 1
        parsed = _safe_json(response)
        if parsed is None:
            with self._lock:
                self.failures += 1
        return parsed, {
            "provider": self.provider, "model": self.model,
            "route": route, "cache_hit": True,
            "network_call": False, "ok": parsed is not None, "backend": "replay",
            "response_format": (
                response_format.get("type") if response_format else None
            ),
        }

    def request_text(self, system: str, user: str,
                     max_tokens: Optional[int] = 4096,
                     route: Optional[str] = None,
                     messages: Optional[list] = None):
        payload = self._payload(system, user, route, max_tokens,
                                messages=messages)
        payload["response_mode"] = "text"
        response = self._responses.get(cache_key(payload))
        with self._lock:
            self.calls += 1
            if response is None:
                self.misses += 1
                return None, {
                    "provider": self.provider, "model": self.model,
                    "route": route, "cache_hit": False,
                    "network_call": False, "ok": False, "backend": "replay",
                }
            self.hits += 1
        value = response.strip() or None
        if value is None:
            with self._lock:
                self.failures += 1
        return value, {
            "provider": self.provider, "model": self.model,
            "route": route, "cache_hit": True,
            "network_call": False, "ok": value is not None, "backend": "replay",
        }

    @property
    def stats(self) -> dict:
        return {
            "provider": self.provider, "model": self.model, "backend": "replay",
            "calls": self.calls, "cache_hits": self.hits,
            "cache_misses": self.misses, "failures": self.failures,
            "tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
        }
