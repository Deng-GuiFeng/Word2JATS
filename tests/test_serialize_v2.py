from pathlib import Path

from word2jats.parse.docx_reader import read_source_docx
from word2jats.understand.serialize import serialize


ROOT = Path(__file__).resolve().parents[1]


def test_every_document_object_is_visible_and_tables_are_not_duplicated():
    source = read_source_docx(str(ROOT / "样例数据" / "01" / "初始文件.docx"))
    view = serialize(source)
    rendered = view.render()
    for occurrence in source.occurrences:
        if source.node(occurrence.node_id).part == "document":
            marker = (f"⟦公式#{occurrence.occ_id}⟧" if occurrence.kind == "omml"
                      else f"⟦图#{occurrence.occ_id}⟧")
            assert rendered.count(marker) == 1
    table_paragraphs = {
        node_id for record in view.records if record.kind == "table-row"
        for node_id in record.source_nodes
    }
    standalone = {
        node_id for record in view.records if record.kind == "paragraph"
        for node_id in record.source_nodes
    }
    assert table_paragraphs.isdisjoint(standalone)


def test_soft_break_and_tab_have_visible_notation():
    source = read_source_docx(str(ROOT / "样例数据" / "03" / "初始文件.docx"))
    rendered = serialize(source).render()
    assert ".2]" in rendered
    assert "⇥" in rendered
