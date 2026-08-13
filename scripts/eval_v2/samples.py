"""V2 的样例登记：从 `样例数据/样例登记.json` 读取唯一名单，构建 V2 自己的数据类。

名单是数据、不是代码，故只存一份（那个 JSON），两套评测器各自读、各自建模型。V2 与 V1
的判分逻辑保持完全独立、互不引用——同时用两把尺子的价值就在于此；但样例名单再各存一份
纯属隐患：增删样例改漏一边，两器就在评不是同一批东西。

正式赛题 10 例（main + supp）与外部扩展 4 例（external）分组保留，全部进入 V2 回归与可选评测。
"""

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
