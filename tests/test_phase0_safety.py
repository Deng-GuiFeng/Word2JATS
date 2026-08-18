"""施工图阶段 0 的确定性安全回归测试。"""

from types import SimpleNamespace

from word2jats.build.ids import DocIdAllocator
from word2jats.build.jats import E
from word2jats.build.xref import XrefResolver
from word2jats.model.blocks import TextRun
from word2jats.render.context import RenderContext
from word2jats.render.tables import render_table
from word2jats.semantic.model import TableBlock
from word2jats.validate.checks import Issue
from word2jats.verify import verify as verify_module


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
