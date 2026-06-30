"""LLM 响应磁盘缓存。

按 (provider, model, messages) 的 SHA256 缓存响应，使重复运行零额外成本、结果可复现
（对评测复现与控成本都很关键）。缓存目录默认 ``.llm_cache/``，已在 .gitignore 忽略。
"""

from __future__ import annotations

import hashlib
import json
import os

_DEFAULT_DIR = os.path.join(os.getcwd(), ".llm_cache")


class DiskCache:
    def __init__(self, directory: str = None):
        self.dir = directory or _DEFAULT_DIR
        os.makedirs(self.dir, exist_ok=True)

    def _key(self, payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, payload: dict):
        path = os.path.join(self.dir, self._key(payload) + ".json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)["response"]
            except Exception:
                return None
        return None

    def put(self, payload: dict, response: str):
        path = os.path.join(self.dir, self._key(payload) + ".json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"payload": payload, "response": response}, f,
                          ensure_ascii=False)
        except Exception:
            pass
