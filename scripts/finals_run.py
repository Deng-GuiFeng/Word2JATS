"""决赛复验：每篇独立进程计时，或严格只读已有响应重放。

python -m scripts.finals_run --tag NAME [--samples all]
python -m scripts.finals_run --tag NAME --replay-cache fin-final-r1
冷跑拒绝使用已有缓存；重放缺任何响应即失败，不会补发模型请求。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import json
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

from scripts.eval_v1.run import CACHE_ROOT, OUTPUT_ROOT, sample_keys
from scripts.eval_v1.samples import get
from scripts.output_manifest import record_from_result, write_manifest


def run_one(key, tag, replay_cache):
    from word2jats.llm.cache import DiskCache
    from word2jats.llm.client import LLMClient
    from word2jats.pipeline import ConvertOptions, convert

    root = Path(OUTPUT_ROOT) / tag
    cache = Path(CACHE_ROOT) / (replay_cache or tag) / key
    if replay_cache:
        if not cache.is_dir():
            raise ValueError(f"缺少重放缓存：{cache}")
    elif cache.exists() and any(cache.iterdir()):
        raise ValueError(f"冷跑缓存已有内容：{cache}")
    original_get = DiskCache.get

    def strict_get(self, payload):
        value = original_get(self, payload)
        if value is None:
            raise RuntimeError(f"重放缺少响应：{key} {payload.get('route')}")
        return value

    sample = get(key)
    with (root / f"{key}.log").open("w", encoding="utf-8") as log, ExitStack() as stack:
        stack.enter_context(redirect_stdout(log))
        stack.enter_context(redirect_stderr(log))
        if replay_cache:
            stack.enter_context(patch.object(DiskCache, "get", strict_get))
            stack.enter_context(patch.object(LLMClient, "enabled", property(lambda _: False)))
        started = time.perf_counter()
        result = convert(ConvertOptions(
            docx_path=sample.docx, out_dir=str(root / key), journal_id=sample.journal,
            doi=sample.doi, llm="dashscope", llm_cache_dir=str(cache),
        ))
        elapsed = time.perf_counter() - started
    report = json.loads((Path(result.candidate_dir).parent / "report.json").read_text())
    audit = report["understanding"]["audit"]
    row = {
        "sample": key, "wall_seconds": round(elapsed, 3),
        "mode": "replay" if replay_cache else "cold",
        "cache_tag": replay_cache or tag,
        "delivered": result.delivered,
        "gates": report["gates"], "llm": result.stats.get("llm"),
        "network_calls": sum(bool(a.get("network_call")) for a in audit),
        "rate_limit_events": sum(
            f.get("status_code") == 429 for a in audit
            for f in a.get("transport_failures", [])
        ),
        "queue_seconds": round(sum(a.get("concurrency_wait_seconds", 0) for a in audit), 3),
        "counts": {k: v for k, v in result.stats.items() if type(v) is int},
        "manifest": record_from_result(result, root),
    }
    if replay_cache and (row["network_calls"] or row["llm"].get("cache_misses")):
        raise RuntimeError(f"{key}: 重放发生网络调用或缓存未命中")
    (root / f"{key}.timing.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--samples", default="all")
    parser.add_argument("--replay-cache")
    parser.add_argument("--workers", type=int, default=14)
    args = parser.parse_args()
    for name in (args.tag, args.replay_cache):
        if name and (Path(name).name != name or name in {".", ".."}):
            parser.error("tag 必须是单个目录名")
    keys = sample_keys(args.samples)
    root = Path(OUTPUT_ROOT) / args.tag
    root.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    rows, failures = {}, {}
    with ProcessPoolExecutor(max_workers=min(args.workers, len(keys))) as executor:
        pending = {executor.submit(run_one, key, args.tag, args.replay_cache): key for key in keys}
        for future in as_completed(pending):
            key = pending[future]
            try:
                row = future.result()
                rows[key] = row
                print(f"{key}: {row['wall_seconds']}s delivered={row['delivered']} "
                      f"network_calls={row['network_calls']}", flush=True)
            except Exception as error:
                failures[key] = str(error)
                print(f"{key}: FAILED {error}", flush=True)
    write_manifest(root, {key: row["manifest"] for key, row in rows.items()})
    summary = {"code_revision": revision, "samples": rows, "failures": failures}
    (root / "timing.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
