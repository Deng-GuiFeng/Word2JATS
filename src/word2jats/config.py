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


def decide_publication_year(explicit: str | None, dates) -> YearDecision:
    """
    取值顺序固定为：显式配置 → accepted 年 → 源日期最晚年 → 无。

    后两者是出版工作流近似，不冒充源稿明示的出版年。
    """
    if explicit is not None:
        year = _valid_year(explicit)
        if year is None:
            raise ValueError("publication_year 必须是四位年份")
        return YearDecision(year, "explicit_config", "PubConfig.publication_year", False)

    accepted = _valid_year(dates.accepted[0]) if dates.accepted else None
    if accepted:
        return YearDecision(
            accepted, "accepted_year_approximation", "SemanticDoc.dates.accepted", True
        )

    candidates = []
    for name in ("received", "revised", "accepted"):
        value = getattr(dates, name, None)
        year = _valid_year(value[0]) if value else None
        if year:
            candidates.append((int(year), name, year))
    if candidates:
        _, name, year = max(candidates)
        return YearDecision(
            year, "latest_source_date_approximation", "SemanticDoc.dates.%s" % name, True
        )
    return YearDecision(None, "unavailable", None, False)
