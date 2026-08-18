"""候选包归档与可恢复的正式交付。"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CandidateRun:
    run_id: str
    root: Path
    package: Path
    xml: Path
    report: Path


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    xml: Path
    media: Path
    error: str | None = None


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "article").strip(".")
    return cleaned or "article"


def create_staging(out_dir: str, article_id: str) -> tuple[str, Path]:
    """创建本次运行独占的候选包暂存目录。"""
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    staging = root / (".staging-%s-%s" % (_safe_name(article_id), run_id))
    staging.mkdir()
    return run_id, staging


def archive_candidate(staging: Path, out_dir: str, article_id: str, run_id: str,
                      failed: bool) -> CandidateRun:
    """将完整候选包移入稳定运行目录；报告与包并列，不混入评测包。"""
    category = "failed" if failed else "candidates"
    run_root = Path(out_dir).resolve() / category / ("%s-%s" % (_safe_name(article_id), run_id))
    package = run_root / "candidate"
    run_root.mkdir(parents=True, exist_ok=False)
    staging.replace(package)
    return CandidateRun(
        run_id=run_id,
        root=run_root,
        package=package,
        xml=package / (article_id + ".xml"),
        report=run_root / "report.json",
    )


def reclassify_failed(run: CandidateRun, out_dir: str, article_id: str) -> CandidateRun:
    """交付回滚后，把已归档的候选包整体转入 failed。"""
    target = Path(out_dir).resolve() / "failed" / run.root.name
    target.parent.mkdir(parents=True, exist_ok=True)
    run.root.replace(target)
    return CandidateRun(
        run_id=run.run_id,
        root=target,
        package=target / "candidate",
        xml=target / "candidate" / (article_id + ".xml"),
        report=target / "report.json",
    )


def write_report(run: CandidateRun, report: dict) -> None:
    """原子替换运行报告，避免读到半截 JSON。"""
    temp = run.report.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(run.report)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _install(source: Path, destination: Path) -> None:
    """把候选路径复制到交付临时路径，再在同文件系统内改名就位。"""
    temp = destination.parent / (".%s.install-%s" % (destination.name, uuid.uuid4().hex))
    try:
        if source.is_dir():
            shutil.copytree(source, temp)
        else:
            shutil.copy2(source, temp)
        os.replace(temp, destination)
    finally:
        _remove(temp)


def deliver_candidate(candidate_dir: Path, out_dir: str, article_id: str,
                      run_id: str, _installer=_install) -> DeliveryResult:
    """
    在文章级跨进程锁内替换并列的 XML/媒体路径。

    两条路径无法一次 rename，因此这里承诺的是“失败后恢复旧交付”，不冒称原子替换。
    """
    root = Path(out_dir).resolve()
    final_xml = root / (article_id + ".xml")
    final_media = root / article_id
    candidate_xml = candidate_dir / (article_id + ".xml")
    candidate_media = candidate_dir / article_id
    lock_dir = root / ".locks"
    backup = root / ".delivery-backups" / ("%s-%s" % (_safe_name(article_id), run_id))
    lock_dir.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)

    if not candidate_xml.is_file():
        return DeliveryResult(False, final_xml, final_media, "候选包缺主 XML")

    lock_path = lock_dir / ("%s.lock" % _safe_name(article_id))
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        backup.mkdir()
        backed_up = []
        try:
            for current in (final_xml, final_media):
                if current.exists() or current.is_symlink():
                    os.replace(current, backup / current.name)
                    backed_up.append(current)
            _installer(candidate_xml, final_xml)
            if candidate_media.exists():
                _installer(candidate_media, final_media)
        except Exception as exc:  # 交付边界：必须先恢复旧产物，再上报原因
            _remove(final_xml)
            _remove(final_media)
            for current in backed_up:
                saved = backup / current.name
                if saved.exists() or saved.is_symlink():
                    os.replace(saved, current)
            return DeliveryResult(False, final_xml, final_media, "%s: %s" % (type(exc).__name__, exc))
        finally:
            _remove(backup)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return DeliveryResult(True, final_xml, final_media)
