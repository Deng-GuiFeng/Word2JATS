"""施工图阶段 0 的确定性安全回归测试。"""

from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from word2jats.build.ids import DocIdAllocator
from word2jats.build.jats import E
from word2jats.build.xref import XrefResolver
from word2jats.model.blocks import TextRun
from word2jats.render.context import RenderContext
from word2jats.render.tables import render_table
from word2jats.semantic.model import SemanticDoc, TableBlock
from word2jats.validate.checks import Issue
from word2jats.verify import verify as verify_module
from word2jats.verify import delivery as delivery_module


_MINIMAL_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<article><front/><body><p>text</p></body></article>
"""


def test_verify_high_issue_is_a_real_gate(monkeypatch):
    """阶段 0.1：结构检查的 high 问题必须使出口报告失败。"""
    monkeypatch.setattr(
        verify_module,
        "Validator",
        lambda: SimpleNamespace(validate_bytes=lambda _: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        verify_module.conservation,
        "check",
        lambda *_: {"n_fab": 0, "n_lost": 0, "fabricated": {}},
    )
    monkeypatch.setattr(
        verify_module,
        "run_checks",
        lambda _: [Issue("duplicate_id", "high", "id 重复")],
    )

    report = verify_module.verify(_MINIMAL_XML, "unused.docx")

    assert report["ok"] is False
    assert report["checks"] == {"high": 1}
    assert report["blocking_issues"] == [
        {"code": "duplicate_id", "severity": "high", "detail": "id 重复"}
    ]


def test_verify_medium_issue_does_not_block_delivery(monkeypatch):
    """阶段 0.1：medium 诊断留在报告中，但不冒充 high 硬门。"""
    monkeypatch.setattr(
        verify_module,
        "Validator",
        lambda: SimpleNamespace(validate_bytes=lambda _: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        verify_module.conservation,
        "check",
        lambda *_: {"n_fab": 0, "n_lost": 0, "fabricated": {}},
    )
    monkeypatch.setattr(
        verify_module,
        "run_checks",
        lambda _: [Issue("no_authors", "medium", "没有作者")],
    )

    report = verify_module.verify(_MINIMAL_XML, "unused.docx")

    assert report["ok"] is True
    assert report["checks"] == {"medium": 1}
    assert report["blocking_issues"] == []


def test_document_id_allocator_keeps_kinds_globally_unique():
    """阶段 0.2：各类对象独立计数，但发出的 ID 在全文档中仍不重复。"""
    ids = DocIdAllocator()
    values = [ids.take(kind) for kind in (
        "section", "paragraph", "figure", "graphic", "table", "formula",
        "reference", "affiliation", "correspondence", "footnote",
    )]
    values += [ids.take("table"), ids.take("figure"), ids.take("reference")]

    assert len(values) == len(set(values))
    assert ids.issued == frozenset(values)


def test_duplicate_visible_table_numbers_do_not_duplicate_ids(tmp_path):
    """阶段 0.2：显示号相同或缺失的表仍须拥有不同身份。"""
    ctx = RenderContext(None, "article", str(tmp_path))
    cell = [[TextRun(text="cell")]]
    first = render_table(TableBlock(
        number=0, table_id="T000", body_rows=[cell]
    ), ctx)
    second = render_table(TableBlock(
        number=0, table_id="T000", body_rows=[cell]
    ), ctx)

    assert first.get("id") != second.get("id")
    assert ctx.table_number_to_id[0] == first.get("id")


def test_xref_uses_display_number_to_real_id_mapping():
    """阶段 0.2：引用保留原显示文字，rid 指向发号器分配的真实身份。"""
    paragraph = E("p", "Table 7 and [3]")
    resolver = XrefResolver(table_targets={7: "T001"}, ref_targets={3: "b1"})

    resolver.process(paragraph)

    xrefs = paragraph.findall("xref")
    assert [(x.get("rid"), x.text) for x in xrefs] == [("T001", "7"), ("b1", "3")]
    assert "".join(paragraph.itertext()) == "Table 7 and [3]"


def test_delivery_failure_restores_both_old_paths(tmp_path):
    """阶段 0.3：第一条路径就位后失败，旧 XML 和旧媒体必须一起恢复。"""
    candidate = tmp_path / "candidate"
    (candidate / "ART").mkdir(parents=True)
    (candidate / "ART.xml").write_bytes(b"new xml")
    (candidate / "ART" / "new.bin").write_bytes(b"new media")
    (tmp_path / "ART.xml").write_bytes(b"old xml")
    (tmp_path / "ART").mkdir()
    (tmp_path / "ART" / "old.bin").write_bytes(b"old media")
    calls = 0

    def fail_on_media(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("注入媒体替换失败")
        delivery_module._install(source, destination)

    result = delivery_module.deliver_candidate(
        candidate, str(tmp_path), "ART", "run1", _installer=fail_on_media
    )

    assert result.delivered is False
    assert (tmp_path / "ART.xml").read_bytes() == b"old xml"
    assert (tmp_path / "ART" / "old.bin").read_bytes() == b"old media"
    assert not (tmp_path / "ART" / "new.bin").exists()


def test_same_article_concurrent_delivery_never_mixes_runs(tmp_path):
    """阶段 0.3：同文章并发交付可以后来者覆盖，但 XML/媒体不得串运行。"""
    candidates = []
    for marker in (b"A", b"B"):
        package = tmp_path / ("candidate-" + marker.decode())
        (package / "ART").mkdir(parents=True)
        (package / "ART.xml").write_bytes(marker + b" xml")
        (package / "ART" / "image.bin").write_bytes(marker + b" media")
        candidates.append(package)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda item: delivery_module.deliver_candidate(
                item[1], str(tmp_path), "ART", "run-" + item[0]
            ),
            [("A", candidates[0]), ("B", candidates[1])],
        ))

    assert all(result.delivered for result in results)
    xml_marker = (tmp_path / "ART.xml").read_bytes()[:1]
    media_marker = (tmp_path / "ART" / "image.bin").read_bytes()[:1]
    assert xml_marker == media_marker


def _mock_conversion(monkeypatch, do_validate, report):
    """给候选/交付状态测试提供不调模型的最小管线。"""
    from word2jats import pipeline
    from word2jats.verify import repair
    from word2jats.validate import validator

    monkeypatch.setattr(pipeline, "read_docx", lambda _: SimpleNamespace(blocks=[]))

    class FakeLLM:
        def __init__(self, **_):
            self.stats = {"provider": "fake"}

    monkeypatch.setattr(pipeline, "LLMClient", FakeLLM)
    monkeypatch.setattr(pipeline, "understand", lambda *_: (SemanticDoc(), None))
    context = SimpleNamespace(
        figures=SimpleNamespace(exported=[]), table_numbers=[],
        formula=SimpleNamespace(stats={"disp": 0, "inline": 0}), n_xref=0,
    )
    xml = b'<?xml version="1.0"?><article><front/><body/></article>'
    monkeypatch.setattr(repair, "render_and_verify", lambda *_, **__: (xml, context, report))
    monkeypatch.setattr(
        validator,
        "Validator",
        lambda: SimpleNamespace(validate_bytes=lambda _: SimpleNamespace(
            well_formed=True, dtd_valid=True, ok=True, errors=[]
        )),
    )
    return pipeline, do_validate


def test_validation_disabled_keeps_candidate_but_never_delivers(tmp_path, monkeypatch):
    """阶段 0.3：do_validate=False 只供调试，不得绕过交付门。"""
    pipeline, _ = _mock_conversion(monkeypatch, False, None)

    result = pipeline.convert(pipeline.ConvertOptions(
        docx_path="unused.docx", out_dir=str(tmp_path), doi="10.1/ART",
        llm="fake", do_validate=False,
    ))

    assert result.delivered is False
    assert Path(result.candidate_xml).is_file()
    assert Path(result.candidate_dir).parent.parent.name == "failed"
    assert not (tmp_path / "ART.xml").exists()
    assert result.stats["delivery"]["reason"] == "validation_disabled"


def test_passing_candidate_is_archived_and_delivered(tmp_path, monkeypatch):
    """阶段 0.3：候选包过门后保留独立快照，并替换兼容布局的正式产物。"""
    report = {
        "ok": True, "dtd_ok": True, "dtd_errors": [],
        "conservation": {"n_fab": 0, "n_lost": 0, "fabricated": {}},
        "checks": {}, "blocking_issues": [],
    }
    pipeline, _ = _mock_conversion(monkeypatch, True, report)

    result = pipeline.convert(pipeline.ConvertOptions(
        docx_path="unused.docx", out_dir=str(tmp_path), doi="10.1/ART",
        llm="fake", do_validate=True,
    ))

    assert result.delivered is True
    assert Path(result.candidate_xml).is_file()
    assert Path(result.candidate_dir).parent.parent.name == "candidates"
    assert Path(result.xml_path) == tmp_path / "ART.xml"
    assert Path(result.xml_path).read_bytes() == Path(result.candidate_xml).read_bytes()
