"""转换运行清单：让两套评测编排层定位同一份候选包。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class OutputLocation:
    candidate_dir: Path
    candidate_xml: Path | None
    delivered: bool | None
    article_id: str | None


def _stored_path(path: str | Path, root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("候选产物不在本批输出根目录内: %s" % resolved) from exc


def record_from_result(result, out_root: str | Path) -> dict:
    root = Path(out_root)
    return {
        "article_id": result.article_id,
        "candidate_dir": _stored_path(result.candidate_dir, root),
        "candidate_xml": _stored_path(result.candidate_xml, root),
        "delivered": bool(result.delivered),
        "run_id": (result.stats.get("delivery") or {}).get("run_id"),
    }


def write_manifest(out_root: str | Path, records: dict[str, dict]) -> Path:
    """整批转换全部结束后一次原子写入，不让评测器读到半份清单。"""
    root = Path(out_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_NAME
    temp = root / (MANIFEST_NAME + ".tmp")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "samples": {key: records[key] for key in sorted(records)},
    }
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)
    return path


def clear_manifest(out_root: str | Path) -> None:
    """新批次开始时删掉上批清单，避免运行中误评旧候选。"""
    path = Path(out_root).resolve() / MANIFEST_NAME
    if path.is_file():
        path.unlink()


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def resolve_output(out_root: str | Path, sample_key: str) -> OutputLocation:
    """有清单就严格按清单取候选；无清单才兼容历史顶层 XML 布局。"""
    root = Path(out_root).resolve()
    manifest = root / MANIFEST_NAME
    if manifest.is_file():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的转换运行清单版本")
        entry = (payload.get("samples") or {}).get(sample_key)
        if entry is None:
            missing = root / ".manifest-missing" / sample_key
            return OutputLocation(missing, None, None, None)
        package = _resolve(root, entry["candidate_dir"])
        xml = _resolve(root, entry["candidate_xml"])
        try:
            package.relative_to(root)
            xml.relative_to(package)
        except ValueError as exc:
            raise ValueError("运行清单的候选路径越界") from exc
        return OutputLocation(
            package, xml, bool(entry.get("delivered")), entry.get("article_id")
        )

    package = root / sample_key
    xmls = sorted(path for path in package.glob("*.xml") if path.is_file()) \
        if package.is_dir() else []
    return OutputLocation(package, xmls[0] if xmls else None, None, None)
