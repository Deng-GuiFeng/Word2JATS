"""SemanticDoc v2 的版本化、可重放负载。"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from ..model.source import SourceDocument
from . import model


SEMANTIC_SCHEMA = "word2jats.semantic-document"
SEMANTIC_VERSION = 1


def _registry() -> dict[str, type]:
    result = {}
    for name, value in vars(model).items():
        if isinstance(value, type) and is_dataclass(value):
            result[name] = value
    return result


_TYPES = _registry()


def _encode(value: Any) -> Any:
    if isinstance(value, SourceDocument):
        return {"$source": value.to_dict()}
    if is_dataclass(value):
        name = type(value).__name__
        if name not in _TYPES or _TYPES[name] is not type(value):
            raise TypeError(f"未登记的语义类型: {type(value)!r}")
        return {
            "$type": name,
            "fields": {item.name: _encode(getattr(value, item.name))
                       for item in fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return {"$list": [_encode(item) for item in value]}
    if isinstance(value, dict):
        return {"$dict": {str(key): _encode(item) for key, item in value.items()}}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"语义快照不支持的值: {type(value)!r}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        raise ValueError("语义快照中不允许无类型列表")
    if not isinstance(value, dict):
        return value
    if set(value) == {"$source"}:
        return SourceDocument.from_dict(value["$source"])
    if set(value) == {"$tuple"}:
        return tuple(_decode(item) for item in value["$tuple"])
    if set(value) == {"$list"}:
        return [_decode(item) for item in value["$list"]]
    if set(value) == {"$dict"}:
        return {key: _decode(item) for key, item in value["$dict"].items()}
    if set(value) == {"$type", "fields"}:
        cls = _TYPES.get(value["$type"])
        if cls is None:
            raise ValueError(f"未知语义类型: {value['$type']}")
        expected = {item.name for item in fields(cls)}
        actual = set(value["fields"])
        if actual != expected:
            raise ValueError(
                f"{value['$type']} 字段不符: "
                f"缺 {sorted(expected - actual)}，多 {sorted(actual - expected)}"
            )
        return cls(**{
            name: _decode(item) for name, item in value["fields"].items()
        })
    raise ValueError("语义快照含未知结构")


def semantic_to_dict(document: model.SemanticDoc) -> dict[str, Any]:
    document.validate()
    return {
        "schema": SEMANTIC_SCHEMA,
        "version": SEMANTIC_VERSION,
        "document": _encode(document),
    }


def semantic_from_dict(raw: dict[str, Any]) -> model.SemanticDoc:
    if raw.get("schema") != SEMANTIC_SCHEMA:
        raise ValueError("不是 word2jats 语义快照")
    if raw.get("version") != SEMANTIC_VERSION:
        raise ValueError(f"不支持的语义快照版本: {raw.get('version')}")
    document = _decode(raw.get("document"))
    if not isinstance(document, model.SemanticDoc):
        raise ValueError("语义快照顶层不是 SemanticDoc")
    document.validate()
    return document
