"""各层可重放产物的版本化 JSON 快照封装。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA = "word2jats.snapshot"
SNAPSHOT_VERSION = 1


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def make_snapshot(layer: str, payload: Any, *, payload_version: int = 1,
                  metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成一个自校验快照；时间不进快照，保证相同输入字节稳定。"""
    if not layer:
        raise ValueError("快照 layer 不能为空")
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    return {
        "schema": SNAPSHOT_SCHEMA,
        "version": SNAPSHOT_VERSION,
        "layer": layer,
        "payload_version": int(payload_version),
        "payload_sha256": digest,
        "metadata": dict(metadata or {}),
        "payload": payload,
    }


def snapshot_bytes(layer: str, payload: Any, *, payload_version: int = 1,
                   metadata: dict[str, Any] | None = None) -> bytes:
    envelope = make_snapshot(
        layer, payload, payload_version=payload_version, metadata=metadata
    )
    return json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"


def parse_snapshot(data: bytes | str, *, expected_layer: str | None = None,
                   payload_version: int | None = None) -> dict[str, Any]:
    raw = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    if raw.get("schema") != SNAPSHOT_SCHEMA or raw.get("version") != SNAPSHOT_VERSION:
        raise ValueError("快照封装格式或版本不受支持")
    if expected_layer is not None and raw.get("layer") != expected_layer:
        raise ValueError(f"快照层不符: {raw.get('layer')} != {expected_layer}")
    if payload_version is not None and raw.get("payload_version") != payload_version:
        raise ValueError(
            f"负载版本不符: {raw.get('payload_version')} != {payload_version}"
        )
    digest = hashlib.sha256(_canonical(raw.get("payload"))).hexdigest()
    if digest != raw.get("payload_sha256"):
        raise ValueError("快照负载摘要不符")
    return raw


def write_snapshot(path: str | os.PathLike[str], layer: str, payload: Any, *,
                   payload_version: int = 1,
                   metadata: dict[str, Any] | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f".{os.getpid()}.tmp")
    temp.write_bytes(snapshot_bytes(
        layer, payload, payload_version=payload_version, metadata=metadata
    ))
    os.replace(temp, target)
    return target


def read_snapshot(path: str | os.PathLike[str], *, expected_layer: str | None = None,
                  payload_version: int | None = None) -> dict[str, Any]:
    return parse_snapshot(
        Path(path).read_bytes(), expected_layer=expected_layer,
        payload_version=payload_version,
    )
