"""期刊元数据查表。

从 ``resources/journals.yaml`` 加载 IMR Press 期刊固定模板，按 journal-id 查询。
journal-meta 在 docx 中通常不存在，属出版系统注入，故用查表 + 可扩展配置实现。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import yaml

_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "resources", "journals.yaml")


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class JournalRegistry:
    def __init__(self, path: Optional[str] = None):
        self.data = _load(os.path.abspath(path or _DEFAULT))

    @property
    def publisher(self) -> str:
        return self.data.get("publisher", "IMR Press")

    @property
    def doi_prefix(self) -> str:
        return self.data.get("doi_prefix", "10.31083")

    def get(self, journal_id: Optional[str]) -> Optional[dict]:
        if not journal_id:
            return None
        return self.data.get("journals", {}).get(journal_id.upper())

    def guess_from_doi(self, doi: Optional[str]) -> Optional[str]:
        """从 DOI（如 10.31083/JIN49347）推断 journal-id 前缀字母。"""
        if not doi:
            return None
        tail = doi.split("/")[-1]
        # 取前导字母部分
        letters = ""
        for ch in tail:
            if ch.isalpha():
                letters += ch
            else:
                break
        return letters.upper() or None

    def article_id_from_doi(self, doi: Optional[str]) -> Optional[str]:
        """文章号 = DOI 去前缀，如 10.31083/JIN49347 -> JIN49347。"""
        if not doi:
            return None
        return doi.split("/")[-1]
