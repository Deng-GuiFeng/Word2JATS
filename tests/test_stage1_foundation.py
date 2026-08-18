"""施工图阶段 1：源对象图、版本化快照与无密钥重放。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from word2jats.llm.cache import DiskCache
from word2jats.llm.client import LLMClient
from word2jats.llm.replay import ReplayLLM, record_cache_snapshot
from word2jats.model.source import (
    OBJECT_REPLACEMENT,
    BinaryResource,
    LinkSpan,
    ObjectAnchor,
    ObjectOccurrence,
    RunRef,
    RunSpan,
    SourceDocument,
    SourceNode,
    SourcePart,
    SourceText,
)
from word2jats.parse.docx_reader import read_source_docx
from word2jats.snapshot import parse_snapshot, snapshot_bytes


ROOT = Path(__file__).resolve().parent.parent


def _small_source_document() -> SourceDocument:
    run1 = RunRef("r1", "document", "doc/p1", italic=True)
    run2 = RunRef("r2", "document", "doc/p1", bold=True)
    text = "alpha beta" + OBJECT_REPLACEMENT + " gamma"
    node = SourceNode(
        node_id="doc/p1", part="document", kind="para", parent=None, order=0,
        text=text,
        run_spans=[RunSpan(0, 5, run1), RunSpan(5, 10, run2),
                   RunSpan(11, len(text), run2)],
        links=[LinkSpan(6, 10, "https://example.test")],
        objects=[ObjectAnchor(10, "o1")],
    )
    return SourceDocument(
        parts=[SourcePart("document", "document", "/word/document.xml",
                          node_ids=("doc/p1",))],
        nodes=[node],
        occurrences=[ObjectOccurrence(
            "o1", "image", "doc/p1", 10, resource_id="res1"
        )],
        resources=[BinaryResource(
            "res1", "/word/media/image1.png", "image/png", b"\x89PNG", "png"
        )],
    )


def test_source_text_slices_partial_runs_without_expanding_boundaries():
    doc = _small_source_document()
    source = SourceText((("doc/p1", 2, 9), ("doc/p1", 12, 17)))

    assert source.text(doc) == "pha betgamma"
    runs = source.runs(doc)
    assert [item.text for item in runs] == ["pha", " ", "bet", "gamma"]
    assert [item.hyperlink for item in runs] == [None, None, "https://example.test", None]
    assert runs[0].source_range == ("doc/p1", 2, 5)
    assert runs[-1].source_range == ("doc/p1", 12, 17)


def test_occurrence_identity_is_independent_from_reused_resource():
    doc = _small_source_document()
    second = SourceNode(
        node_id="doc/p2", part="document", kind="para", parent=None, order=1,
        text=OBJECT_REPLACEMENT, objects=[ObjectAnchor(0, "o2")],
    )
    doc.nodes.append(second)
    doc.parts[0] = SourcePart(
        "document", "document", "/word/document.xml",
        node_ids=("doc/p1", "doc/p2"),
    )
    doc.occurrences.append(ObjectOccurrence(
        "o2", "image", "doc/p2", 0, resource_id="res1"
    ))

    doc.validate()

    assert len(doc.occurrences) == 2
    assert len(doc.resources) == 1
    assert {occ.resource_id for occ in doc.occurrences} == {"res1"}


def test_source_snapshot_roundtrip_keeps_bytes_ranges_and_relations():
    original = _small_source_document()
    raw = original.to_dict()
    restored = SourceDocument.from_dict(json.loads(json.dumps(raw)))

    assert restored.to_dict() == raw
    assert restored.resource("res1").blob == b"\x89PNG"
    assert restored.node("doc/p1").objects == [ObjectAnchor(10, "o1")]


def test_all_fourteen_sample_parse_snapshots_roundtrip():
    """阶段 1 验收：14 例解析快照均能序列化并无损读回。"""
    sample_dirs = sorted(
        path for path in (ROOT / "样例数据").iterdir()
        if (path / "初始文件.docx").is_file()
    )
    assert len(sample_dirs) == 14
    for directory in sample_dirs:
        source = read_source_docx(str(directory / "初始文件.docx"))
        payload = source.to_dict()
        encoded = snapshot_bytes(
            "parse-ir", payload, payload_version=1,
            metadata={"sample": directory.name},
        )
        envelope = parse_snapshot(
            encoded, expected_layer="parse-ir", payload_version=1
        )
        restored = SourceDocument.from_dict(envelope["payload"])
        assert restored.to_dict() == payload, directory.name


@pytest.mark.parametrize("layer", [
    "parse-ir", "llm.raw-response", "grounding", "semantic-v2", "ledger-report",
])
def test_every_pipeline_layer_uses_a_versioned_self_checking_snapshot(layer):
    encoded = snapshot_bytes(layer, {"value": [1, "原文"]}, payload_version=3)
    parsed = parse_snapshot(encoded, expected_layer=layer, payload_version=3)
    assert parsed["payload"] == {"value": [1, "原文"]}

    damaged = json.loads(encoded)
    damaged["payload"]["value"][0] = 2
    with pytest.raises(ValueError, match="摘要不符"):
        parse_snapshot(json.dumps(damaged), expected_layer=layer)


def test_llm_client_reads_cache_before_requiring_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMClient, "_load_env", staticmethod(lambda _: None))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    payload = {
        "provider": "dashscope", "model": "qwen3.7-plus",
        "system": "system", "user": "user", "max_tokens": 4096,
    }
    DiskCache(str(tmp_path)).put(payload, '{"answer":"cached"}')

    client = LLMClient(provider="dashscope", model="qwen3.7-plus",
                       cache_dir=str(tmp_path))

    assert client.enabled is False
    assert client.extract_json("system", "user") == {"answer": "cached"}
    assert client.stats["cache_hits"] == 1
    assert client.extract_json("system", "miss") is None
    assert client.stats["cache_misses"] == 1


def test_replay_llm_is_read_only_offline_and_keeps_routes_independent(tmp_path):
    cache_dir = tmp_path / "cache"
    cache = DiskCache(str(cache_dir))
    common = {
        "provider": "dashscope", "model": "qwen3.7-plus",
        "system": "find references", "user": "same document",
        "max_tokens": 4096,
    }
    cache.put({**common, "route": "A"}, '{"heads":["A"]}')
    cache.put({**common, "route": "B"}, '{"heads":["B"]}')
    recording = record_cache_snapshot(cache_dir, tmp_path / "baseline-replay.json")
    replay = ReplayLLM(
        recording, provider="dashscope", model="qwen3.7-plus"
    )

    assert replay.extract_json(
        "find references", "same document", route="A"
    ) == {"heads": ["A"]}
    assert replay.extract_json(
        "find references", "same document", route="B"
    ) == {"heads": ["B"]}
    assert replay.extract_json(
        "find references", "same document", route="judge"
    ) is None
    assert replay.stats["cache_hits"] == 2
    assert replay.stats["cache_misses"] == 1


def test_replay_recording_never_silently_drops_damaged_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ("0" * 64 + ".json")).write_text(
        '{"payload":{},"response":"{}"}', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="无效记录"):
        record_cache_snapshot(cache_dir, tmp_path / "replay.json")
