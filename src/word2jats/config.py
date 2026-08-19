"""出版工作流显式配置及其可审计的近似推导。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PubConfig:
    journal_id: str | None
    doi: str | None
    publication_year: str | None
    include_publisher_note: bool


@dataclass(frozen=True)
class YearDecision:
    year: str | None
    basis: str
    source: str | None
    approximate: bool

    def as_dict(self) -> dict:
        return {
            "year": self.year,
            "basis": self.basis,
            "source": self.source,
            "approximate": self.approximate,
        }


def _valid_year(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if re.fullmatch(r"[12]\d{3}", text) else None


def decide_publication_year(explicit: str | None, dates, source=None) -> YearDecision:
    """
    出版年只接受出版工作流的显式输入。

    收稿、修回和录用日期是稿件历史，不是出版日期。两者即使经常
    落在同一年，也没有可以保证的推导关系；稿件未明示的出版年必须留空。

    ``dates`` 和 ``source`` 仅保留在函数签名中以兼容调用端，不参与决策。
    """
    if explicit is not None:
        year = _valid_year(explicit)
        if year is None:
            raise ValueError("publication_year 必须是四位年份")
        return YearDecision(year, "explicit_config", "PubConfig.publication_year", False)
    return YearDecision(None, "unavailable", None, False)
