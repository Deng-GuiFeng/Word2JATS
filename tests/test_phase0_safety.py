"""施工图阶段 0 的确定性安全回归测试。"""

from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile
import hashlib

from word2jats.build.ids import DocIdAllocator
from word2jats.build.figures import ext_for_blob, media_format
from word2jats.build.jats import E, append_title_inline
from word2jats.build.xref import XrefResolver
from word2jats.config import decide_publication_year
from word2jats.enrich.journals import JournalRegistry
from word2jats.model.blocks import TextRun
from word2jats.verify import delivery as delivery_module
from word2jats.verify.media import validate_blob, verify_package


_MINIMAL_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<article><front/><body><p>text</p></body></article>
"""


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


def test_all_sample_media_formats_are_recognized_and_structurally_valid():
    """阶段 0.4：14 例 docx 内的全部媒体都须由对应格式验证器真实验过。"""
    sample_root = Path(__file__).resolve().parent.parent / "样例数据"
    checked = 0
    formats = set()
    for docx in sample_root.glob("*/初始文件.docx"):
        with ZipFile(docx) as archive:
            for name in archive.namelist():
                if not name.startswith("word/media/") or name.endswith("/"):
                    continue
                blob = archive.read(name)
                fmt = media_format(blob)
                assert fmt is not None, (docx.parent.name, name)
                assert validate_blob(blob, fmt), (docx.parent.name, name, fmt)
                formats.add(fmt)
                checked += 1
    assert checked > 0
    assert {"jpeg", "png", "tiff", "wmf", "emf", "svg"} <= formats


def test_unknown_media_is_not_disguised_as_jpeg():
    """阶段 0.4：未知字节使用 .bin 暴露未决，不许假冒 .jpg。"""
    blob = b"not an image"
    assert media_format(blob) is None
    assert ext_for_blob(blob) == ".bin"


def test_media_package_gate_checks_hash_format_and_redundancy(tmp_path):
    """阶段 0.4：候选包同时核对引用闭合、源字节、格式与冗余文件。"""
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buffer, format="PNG")
    blob = buffer.getvalue()
    media = tmp_path / "ART" / "fig-01.png"
    media.parent.mkdir()
    media.write_bytes(blob)
    xml = (
        b'<article xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<body><fig><graphic xlink:href="ART/fig-01.png"/></fig></body></article>'
    )
    expected = {"ART/fig-01.png": hashlib.sha256(blob).hexdigest()}

    assert verify_package(xml, tmp_path, expected).ok
    (tmp_path / "extra.bin").write_bytes(b"extra")
    report = verify_package(xml, tmp_path, expected)
    assert not report.ok
    assert {issue["code"] for issue in report.issues} == {"media_unreferenced"}


def test_publication_year_requires_explicit_workflow_input():
    """出版年不能由稿件历史日期推测。"""
    dates = SimpleNamespace(
        received=("2024", "1", "2"), revised=("2026", "1", "15"),
        accepted=("2025", "1", "16"),
    )
    explicit = decide_publication_year("2030", dates)
    inferred = decide_publication_year(None, dates)

    assert explicit.as_dict() == {
        "year": "2030", "basis": "explicit_config",
        "source": "PubConfig.publication_year", "approximate": False,
    }
    assert inferred.as_dict() == {
        "year": None, "basis": "unavailable", "source": None,
        "approximate": False,
    }


def test_publication_year_stays_empty_for_every_source_date_combination():
    """不管稿件日期是否齐全，无显式出版年时都留空。"""
    latest = decide_publication_year(
        None, SimpleNamespace(received=("2023", "2", "1"), revised=("2024", "3", "2"))
    )
    missing = decide_publication_year(None, SimpleNamespace())
    assert latest.year is None and latest.basis == "unavailable"
    assert latest.approximate is False
    assert missing.year is None and missing.basis == "unavailable"


def test_publisher_note_default_comes_from_publication_registry():
    """阶段 0.5：出版社声明是期刊配置，不对所有文档无条件注入。"""
    registry = JournalRegistry()
    assert registry.get("RCM")["include-publisher-note"] is True
    assert registry.get("BP").get("include-publisher-note", False) is False


def test_xref_wrapping_never_rewrites_visible_citation_text():
    """阶段 0.6：引用可以新增关系，但区间、分隔符和空格不得被展开或规范化。"""
    original = "See [ 8–10,  12 ] and Figures 1–3."
    paragraph = E("p", original)
    resolver = XrefResolver(
        ref_targets={number: "b%d" % number for number in range(8, 13)},
        fig_targets={number: "F%03d" % number for number in range(1, 4)},
    )

    resolver.process(paragraph)

    assert "".join(paragraph.itertext()) == original
    ranges = [(xref.text, xref.get("rid")) for xref in paragraph.findall("xref")]
    assert ("8–10", "b8 b9 b10") in ranges
    assert ("1–3", "F001 F002 F003") in ranges


def test_title_semantics_do_not_repeat_word_bold_formatting():
    """阶段 0.6：标题容器不重复输出 Word 整段粗体，但保留斜体信息。"""
    title = E("article-title")
    append_title_inline(title, [
        TextRun(text="Plain", bold=True),
        TextRun(text=" gene", bold=True, italic=True),
    ])
    assert title.find("bold") is None
    assert title.find("italic") is not None
    assert "".join(title.itertext()) == "Plain gene"


def _mock_conversion(monkeypatch, do_validate, report):
    """给 v2 候选/交付状态测试提供不调模型的最小管线。"""
    from word2jats import pipeline
    from word2jats.model.source import SourceDocument
    from word2jats.semantic.model import SemanticDoc as SemanticDocV2
    from word2jats.validate import validator

    source = SourceDocument()
    monkeypatch.setattr(pipeline, "read_source_docx", lambda _: source)

    class FakeLLM:
        def __init__(self, **_):
            self.stats = {"provider": "fake"}

    monkeypatch.setattr(pipeline, "LLMClient", FakeLLM)
    monkeypatch.setattr(
        pipeline, "understand",
        lambda *_, **__: (SemanticDocV2(source), {"blocking": False, "issues": [],
                                                  "reference_count": 0}),
    )
    xml = b'<?xml version="1.0"?><article><front/><body/></article>'
    monkeypatch.setattr(
        pipeline, "render_v2",
        lambda *_, **__: SimpleNamespace(xml_bytes=xml, media={}, provenance=()),
    )
    monkeypatch.setattr(
        pipeline.conservation, "check",
        lambda *_: {"n_fab": 0, "n_lost": 0, "fabricated": {}, "lost": {}},
    )
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
    stages = []

    result = pipeline.convert(pipeline.ConvertOptions(
        docx_path="unused.docx", out_dir=str(tmp_path), doi="10.1/ART",
        llm="fake", do_validate=True,
        progress=lambda key, _label: stages.append(key),
    ))

    assert result.delivered is True
    assert Path(result.candidate_xml).is_file()
    assert Path(result.candidate_dir).parent.parent.name == "candidates"
    assert Path(result.xml_path) == tmp_path / "ART.xml"
    assert Path(result.xml_path).read_bytes() == Path(result.candidate_xml).read_bytes()
    assert stages == ["parse", "understand", "render", "validate"]


def test_independent_conservation_separates_new_words_from_legal_reuse(monkeypatch):
    """第九门：新词阻断，源文原有词的额外出现单列复用报告。"""
    from collections import Counter
    from word2jats import pipeline
    from word2jats.model.source import SourceDocument
    from word2jats.semantic.model import SemanticDoc as SemanticDocV2

    monkeypatch.setattr(
        pipeline.conservation, "check",
        lambda *_: {
            "fabricated": {"existing": 2, "invented": 1},
            "lost": {}, "n_fab": 2, "n_lost": 0,
        },
    )
    monkeypatch.setattr(
        pipeline.conservation, "docx_tokens",
        lambda *_: (Counter({"existing": 1}), Counter()),
    )
    report = pipeline._independent_conservation(
        "unused.docx", E("article"), SemanticDocV2(SourceDocument())
    )

    assert report["fabricated"] == {"invented": 1}
    assert report["overproduced_source_tokens"] == {"existing": 2}
    assert report["n_fab"] == 1


def test_pipeline_blocks_delivery_when_rendered_provenance_does_not_cover_source(
        tmp_path, monkeypatch):
    """第八门必须重建最终来源覆盖，不能采信理解层自报“已分类”。"""
    from word2jats.model.source import SourceDocument, SourceNode, SourcePart
    from word2jats.semantic.model import SemanticDoc as SemanticDocV2

    pipeline, _ = _mock_conversion(monkeypatch, True, None)
    node = SourceNode("doc/p1", "document", "para", None, 0, "must survive")
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))], [node],
    )
    monkeypatch.setattr(pipeline, "read_source_docx", lambda _: source)
    monkeypatch.setattr(
        pipeline, "understand",
        lambda *_, **__: (SemanticDocV2(source), {
            "blocking": False, "issues": [], "reference_count": 0,
            "assignments": [{"source_id": "doc/p1", "role": "body-paragraph"}],
        }),
    )

    result = pipeline.convert(pipeline.ConvertOptions(
        docx_path="unused.docx", out_dir=str(tmp_path), doi="10.1/ART",
        llm="fake", do_validate=True,
    ))

    assert result.delivered is False
    assert result.stats["verify"]["gates"]["source_coverage"] is False
    issues = result.stats["verify"]["source_coverage"]["issues"]
    assert any(item["code"] == "TEXT_UNCOVERED" for item in issues)


def test_pipeline_gives_the_front_passes_their_own_client(tmp_path, monkeypatch):
    """文首拿到自己那档客户端，收尾时两个客户端都关且不重复关。

    文首与正文用不同模型（见 llm/client.py 里 dashscope 的 front_model）。派生出
    来的客户端各自占着进程级并发闸门的一个名额，漏关一个就等于把名额一直攥着。
    """
    from word2jats.model.source import SourceDocument
    from word2jats.semantic.model import SemanticDoc as SemanticDocV2

    report = {
        "ok": True, "dtd_ok": True, "dtd_errors": [],
        "conservation": {"n_fab": 0, "n_lost": 0, "fabricated": {}},
        "checks": {}, "blocking_issues": [],
    }
    pipeline, _ = _mock_conversion(monkeypatch, True, report)
    source = SourceDocument()
    monkeypatch.setattr(pipeline, "read_source_docx", lambda _: source)

    closed = []

    class FrontClient:
        def close(self):
            closed.append("front")

    class MainClient:
        def __init__(self, **_):
            self.stats = {"provider": "fake"}
            self.front = FrontClient()

        def for_front(self):
            return self.front

        def close(self):
            closed.append("main")

    seen = {}

    def fake_understand(src, llm, config=None, head_llm=None):
        seen["llm"], seen["head_llm"] = llm, head_llm
        return SemanticDocV2(src), {"blocking": False, "issues": [],
                                    "reference_count": 0}

    monkeypatch.setattr(pipeline, "LLMClient", MainClient)
    monkeypatch.setattr(pipeline, "understand", fake_understand)

    pipeline.convert(pipeline.ConvertOptions(
        docx_path="unused.docx", out_dir=str(tmp_path), doi="10.1/ART",
        llm="fake", do_validate=True,
    ))

    assert seen["head_llm"] is seen["llm"].front      # 文首拿到的是派生客户端
    assert seen["head_llm"] is not seen["llm"]        # 与主客户端不是同一个
    assert closed == ["main", "front"]                # 两个都关，各关一次


def test_pipeline_closes_one_client_once_when_front_is_not_separate(
    tmp_path, monkeypatch
):
    """for_front() 返回自身时只关一次——按对象身份去重，别关两遍。"""
    from word2jats.model.source import SourceDocument
    from word2jats.semantic.model import SemanticDoc as SemanticDocV2

    report = {
        "ok": True, "dtd_ok": True, "dtd_errors": [],
        "conservation": {"n_fab": 0, "n_lost": 0, "fabricated": {}},
        "checks": {}, "blocking_issues": [],
    }
    pipeline, _ = _mock_conversion(monkeypatch, True, report)
    source = SourceDocument()
    monkeypatch.setattr(pipeline, "read_source_docx", lambda _: source)

    closed = []

    class SoleClient:
        def __init__(self, **_):
            self.stats = {"provider": "fake"}

        def for_front(self):
            return self

        def close(self):
            closed.append("main")

    seen = {}

    def fake_understand(src, llm, config=None, head_llm=None):
        seen["llm"], seen["head_llm"] = llm, head_llm
        return SemanticDocV2(src), {"blocking": False, "issues": [],
                                    "reference_count": 0}

    monkeypatch.setattr(pipeline, "LLMClient", SoleClient)
    monkeypatch.setattr(pipeline, "understand", fake_understand)

    pipeline.convert(pipeline.ConvertOptions(
        docx_path="unused.docx", out_dir=str(tmp_path), doi="10.1/ART",
        llm="fake", do_validate=True,
    ))

    assert seen["head_llm"] is seen["llm"]
    assert closed == ["main"]
