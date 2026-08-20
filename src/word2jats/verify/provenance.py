"""输出来源映射。

渲染器在写入每段可见文字、媒体引用和源推导属性时同时登记来源。
记录以最终 XML 路径和 text/tail 偏移定位，不依赖 Python 对象地址，
因而可序列化、可离线重放。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from lxml import etree

from ..model.source import TextRange


@dataclass(frozen=True)
class ProvenanceEntry:
    output_path: str
    target_kind: str  # text | tail | attribute | media
    target_name: Optional[str]
    start: Optional[int]
    end: Optional[int]
    value: str
    origin_kind: str  # source | object | config | transform | model
    source_ranges: tuple[TextRange, ...] = ()
    source_object: Optional[str] = None
    config_key: Optional[str] = None
    config_version: Optional[str] = None
    transform: Optional[str] = None


@dataclass(frozen=True)
class _Pending:
    element: etree._Element
    target_kind: str
    target_name: Optional[str]
    start: Optional[int]
    end: Optional[int]
    value: str
    origin_kind: str
    source_ranges: tuple[TextRange, ...] = ()
    source_object: Optional[str] = None
    config_key: Optional[str] = None
    config_version: Optional[str] = None
    transform: Optional[str] = None


class ProvenanceBuilder:
    """边渲染边记录，树完成后固化为稳定 XPath。"""

    def __init__(self):
        self._pending: list[_Pending] = []

    def source_text(self, element: etree._Element, slot: str, start: int,
                    value: str, source_range: TextRange) -> None:
        self._pending.append(_Pending(
            element, slot, None, start, start + len(value), value, "source",
            source_ranges=(source_range,),
        ))

    def source_attribute(self, element: etree._Element, name: str, value: str,
                         ranges: tuple[TextRange, ...]) -> None:
        self._pending.append(_Pending(
            element, "attribute", name, None, None, value, "source",
            source_ranges=ranges,
        ))

    def object(self, element: etree._Element, name: str, value: str,
               occurrence_id: str) -> None:
        self._pending.append(_Pending(
            element, "media", name, None, None, value, "object",
            source_object=occurrence_id,
        ))

    def config(self, element: etree._Element, slot: str, value: str,
               key: str, *, start: Optional[int] = None,
               end: Optional[int] = None) -> None:
        self._pending.append(_Pending(
            element, slot, None, start, end, value, "config", config_key=key,
            config_version="publication-config-v1",
        ))

    def config_attribute(self, element: etree._Element, name: str, value: str,
                         key: str) -> None:
        self._pending.append(_Pending(
            element, "attribute", name, None, None, value, "config",
            config_key=key, config_version="publication-config-v1",
        ))

    def transform(self, element: etree._Element, slot: str, value: str,
                  name: str, ranges: tuple[TextRange, ...] = (),
                  start: Optional[int] = None,
                  end: Optional[int] = None) -> None:
        self._pending.append(_Pending(
            element, slot, None, start, end, value, "transform",
            source_ranges=ranges, transform=name,
        ))

    def model_text(self, element: etree._Element, slot: str, value: str,
                   *, start: int = 0) -> None:
        """登记模型直接交付的可见文本，不伪造逐字源区间。"""
        self._pending.append(_Pending(
            element, slot, None, start, start + len(value), value, "model",
        ))

    def object_transform(self, element: etree._Element, occurrence_id: str,
                         name: str) -> None:
        """登记对象经具名白名单变换后生成的非媒体结构。"""
        self._pending.append(_Pending(
            element, "transform", None, None, None, "", "transform",
            source_object=occurrence_id, transform=name,
        ))

    def finalize(self, root: etree._Element) -> tuple[ProvenanceEntry, ...]:
        tree = root.getroottree()
        records = []
        for item in self._pending:
            records.append(ProvenanceEntry(
                output_path=tree.getpath(item.element),
                target_kind=item.target_kind,
                target_name=item.target_name,
                start=item.start, end=item.end, value=item.value,
                origin_kind=item.origin_kind,
                source_ranges=item.source_ranges,
                source_object=item.source_object,
                config_key=item.config_key, config_version=item.config_version,
                transform=item.transform,
            ))
        return tuple(records)


def provenance_payload(entries: tuple[ProvenanceEntry, ...]) -> dict:
    """转换为版本化快照可用的 JSON 负载。"""
    return {
        "schema": "word2jats.provenance",
        "version": 1,
        "entries": [asdict(item) for item in entries],
    }
