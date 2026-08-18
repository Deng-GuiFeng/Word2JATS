"""LLM 响应磁盘缓存。

按 (provider, model, messages) 的 SHA256 缓存响应，使重复运行零额外成本、结果可复现
（对评测复现与控成本都很关键）。缓存目录默认 ``.llm_cache/``，已在 .gitignore 忽略。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading

_DEFAULT_DIR = os.path.join(os.getcwd(), ".llm_cache")


def cache_key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, directory: str = None):
        self.dir = directory or _DEFAULT_DIR
        os.makedirs(self.dir, exist_ok=True)

    def _key(self, payload: dict) -> str:
        return cache_key(payload)

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
        # 原子写：先写临时文件再 rename——并发多线程写不同 key 各写各的文件互不干扰，
        # 且任何读者要么读到旧文件、要么读到完整新文件，绝不会读到半截 JSON。
        tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"payload": payload, "response": response}, f,
                          ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
