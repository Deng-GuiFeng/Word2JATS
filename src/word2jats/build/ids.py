"""文档级 JATS ID 分配器。

ID 是对象身份，不是图、表、文献的显示编号。同一种对象独立计数，不同种类使用
不同前缀，因而每次发号都在全文档范围唯一。
"""

from __future__ import annotations

from collections import defaultdict


class DocIdAllocator:
    """按对象种类分配稳定、全文档唯一的 XML ID。"""

    _PREFIX = {
        "section": "S",
        "paragraph": "P",
        "figure": "F",
        "figure-group": "FG",
        "graphic": "G",
        "table": "T",
        "formula": "E",
        "math": "M",
        "reference": "b",
        "affiliation": "aff",
        "glossary": "glossary",
        "correspondence": "cor",
        "footnote": "fn",
    }
    _PADDED = {"figure", "table", "formula"}

    def __init__(self):
        self._counters = defaultdict(int)
        self._issued: set[str] = set()

    def take(self, kind: str) -> str:
        """领取一个 ID；未登记的对象种类立即拒绝，不暗猜前缀。"""
        if kind not in self._PREFIX:
            raise ValueError("未登记的 ID 种类: %s" % kind)
        while True:
            self._counters[kind] += 1
            number = self._counters[kind]
            suffix = "%03d" % number if kind in self._PADDED else str(number)
            value = self._PREFIX[kind] + suffix
            if value not in self._issued:
                break
        self._issued.add(value)
        return value

    def reserve(self, values) -> None:
        """保留已存在于同一文档片段中的 ID，后续发号自动避开。"""
        for value in values:
            if isinstance(value, str) and value:
                self._issued.add(value)

    @property
    def issued(self) -> frozenset[str]:
        return frozenset(self._issued)
