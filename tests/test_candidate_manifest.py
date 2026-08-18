"""阶段 0.7：两套评测器共用候选包运行清单的接口测试。"""

from pathlib import Path
from types import SimpleNamespace

from scripts import evalsuite
from scripts.eval_v1 import run as v1_run
from scripts.eval_v1 import samples as v1_samples
from scripts.output_manifest import record_from_result, resolve_output, write_manifest


def _candidate(root: Path, relative: str, xml: bytes, with_media=True) -> tuple[Path, Path]:
    package = root / relative
    package.mkdir(parents=True)
    xml_path = package / "ART.xml"
    xml_path.write_bytes(xml)
    if with_media:
        media = package / "ART" / "fig.png"
        media.parent.mkdir()
        media.write_bytes(b"media")
    return package, xml_path


def _write_one(root: Path, package: Path, xml: Path, delivered: bool) -> None:
    result = SimpleNamespace(
        article_id="ART", candidate_dir=str(package), candidate_xml=str(xml),
        delivered=delivered, stats={"delivery": {"run_id": package.parent.name}},
    )
    write_manifest(root, {"01": record_from_result(result, root)})


def _assert_v1_v2_same_candidate(root: Path, expected: Path, report_dir: Path,
                                 monkeypatch) -> None:
    xml_path, media_root = v1_run.find_output(v1_samples.get("01"), str(root))
    assert Path(xml_path) == expected / "ART.xml"
    assert Path(media_root) == expected

    seen = []

    def fake_evaluate(key, candidate):
        seen.append((key, Path(candidate)))
        return SimpleNamespace(
            sample=key, passed=False, statistics={"issues": {}}, issues=[]
        )

    monkeypatch.setattr(evalsuite, "v2_evaluate", fake_evaluate)
    monkeypatch.setattr(evalsuite, "write_reports_safe", lambda *_: None)
    evalsuite.run_v2(["01"], str(root), str(report_dir))
    assert seen == [("01", expected)]


def test_manifest_routes_normally_delivered_candidate_to_both_evaluators(tmp_path, monkeypatch):
    package, xml = _candidate(
        tmp_path, "01/candidates/ART-run/candidate", b"<article/>"
    )
    _write_one(tmp_path, package, xml, delivered=True)
    _assert_v1_v2_same_candidate(tmp_path, package, tmp_path / "reports", monkeypatch)
    assert resolve_output(tmp_path, "01").delivered is True


def test_manifest_routes_hard_gate_failure_candidate_to_both_evaluators(tmp_path, monkeypatch):
    package, xml = _candidate(
        tmp_path, "01/failed/ART-run/candidate", b"<article/>"
    )
    _write_one(tmp_path, package, xml, delivered=False)
    _assert_v1_v2_same_candidate(tmp_path, package, tmp_path / "reports", monkeypatch)
    assert resolve_output(tmp_path, "01").delivered is False


def test_manifest_does_not_hide_readable_xml_with_missing_media(tmp_path, monkeypatch):
    xml_bytes = (
        b'<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><fig>'
        b'<graphic xlink:href="ART/missing.png"/></fig></body></article>'
    )
    package, xml = _candidate(
        tmp_path, "01/failed/ART-run/candidate", xml_bytes, with_media=False
    )
    _write_one(tmp_path, package, xml, delivered=False)
    _assert_v1_v2_same_candidate(tmp_path, package, tmp_path / "reports", monkeypatch)


def test_same_article_concurrent_runs_remain_distinct_and_manifest_selects_one(tmp_path):
    first, first_xml = _candidate(
        tmp_path, "01/candidates/ART-run-a/candidate", b"<article><body>A</body></article>"
    )
    second, second_xml = _candidate(
        tmp_path, "01/candidates/ART-run-b/candidate", b"<article><body>B</body></article>"
    )
    first_result = SimpleNamespace(
        article_id="ART", candidate_dir=str(first), candidate_xml=str(first_xml),
        delivered=True, stats={"delivery": {"run_id": "run-a"}},
    )
    second_result = SimpleNamespace(
        article_id="ART", candidate_dir=str(second), candidate_xml=str(second_xml),
        delivered=True, stats={"delivery": {"run_id": "run-b"}},
    )
    first_record = record_from_result(first_result, tmp_path)
    second_record = record_from_result(second_result, tmp_path)
    assert first_record["candidate_dir"] != second_record["candidate_dir"]

    write_manifest(tmp_path, {"01": second_record})

    resolved = resolve_output(tmp_path, "01")
    assert resolved.candidate_dir == second
    assert resolved.candidate_xml.read_bytes() == b"<article><body>B</body></article>"


def test_no_manifest_keeps_historical_top_level_layout(tmp_path):
    package = tmp_path / "01"
    package.mkdir()
    xml = package / "legacy.xml"
    xml.write_bytes(b"<article/>")
    resolved = resolve_output(tmp_path, "01")
    assert resolved.candidate_dir == package
    assert resolved.candidate_xml == xml
    assert resolved.delivered is None


def test_v1_checkpoint_sample_group_aliases_match_registry():
    """检查点 A/B/C 使用 --samples all，V1 入口必须按登记表解析分组。"""
    assert v1_run.sample_keys("all") == [sample.key for sample in v1_samples.SAMPLES]
    assert v1_run.sample_keys("main") == [
        sample.key for sample in v1_samples.SAMPLES if sample.group == "main"
    ]
    assert v1_run.sample_keys(None) == [sample.key for sample in v1_samples.EVAL_SET]
