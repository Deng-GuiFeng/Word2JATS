"""从唯一登记表读取 10 例赛事样例和 4 例扩展样例。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "样例数据"
REGISTRY = DATA_ROOT / "样例登记.json"


@dataclass(frozen=True)
class Sample:
    key: str
    group: str
    journal: str
    doi: str

    @property
    def directory(self) -> Path:
        return DATA_ROOT / self.key

    @property
    def docx(self) -> Path:
        return self.directory / "初始文件.docx"

    @property
    def gold_xml(self) -> Path:
        return self.directory / "结构参考.xml"

    @property
    def figures_zip(self) -> Path:
        return self.directory / "figures.zip"


def _load() -> Tuple[Sample, ...]:
    with REGISTRY.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return tuple(
        Sample(entry["key"], entry["group"], entry["journal"], entry["doi"])
        for entry in raw["样例"]
    )


ALL_SAMPLES: Tuple[Sample, ...] = _load()

SAMPLES_BY_KEY: Dict[str, Sample] = {sample.key: sample for sample in ALL_SAMPLES}


def get_sample(key: str) -> Sample:
    try:
        return SAMPLES_BY_KEY[key]
    except KeyError as exc:
        raise KeyError("未知样例 %r；可选值：%s" % (key, ", ".join(SAMPLES_BY_KEY))) from exc
