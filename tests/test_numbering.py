"""OOXML 自动编号确定性还原（口径⑪）的单元测试。"""

import io
import os
import sys
from zipfile import ZipFile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from word2jats.model.source import (  # noqa: E402
    SourceDocument, SourceNode, SourcePart, SourceText,
)
from word2jats.parse.numbering import (  # noqa: E402
    assign_rendered_numbers, load_numbering, restored_number,
)
from word2jats.understand.merge import ReferenceSpan  # noqa: E402
from word2jats.understand.understand import _entity_by_printed_number  # noqa: E402

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NUMBERING = f"""<w:numbering xmlns:w="{_W}">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0">
      <w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>
    </w:lvl>
    <w:lvl w:ilvl="1">
      <w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/>
    </w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="1">
    <w:lvl w:ilvl="0">
      <w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>
    </w:lvl>
  </w:abstractNum>
  <w:num w:numId="5"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="7"><w:abstractNumId w:val="1"/></w:num>
  <w:num w:numId="9">
    <w:abstractNumId w:val="0"/>
    <w:lvlOverride w:ilvl="0"><w:startOverride w:val="4"/></w:lvlOverride>
  </w:num>
</w:numbering>"""


def _archive():
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/numbering.xml", _NUMBERING)
    return ZipFile(io.BytesIO(buffer.getvalue()))


def _node(node_id, order, numbering, text="entry"):
    return SourceNode(
        node_id, "document", "para", None, order, text,
        properties={"numbering": numbering} if numbering else {},
    )


def test_decimal_numbering_counts_resets_and_skips_bullet():
    table = load_numbering(_archive())
    nodes = [
        _node("doc/p1", 0, ["5", "0"]),      # 1.
        _node("doc/p2", 1, ["5", "1"]),      # 1.1
        _node("doc/p3", 2, ["5", "1"]),      # 1.2
        _node("doc/p4", 3, ["5", "0"]),      # 2. （深层重置）
        _node("doc/p5", 4, ["5", "1"]),      # 2.1
        _node("doc/p6", 5, ["7", "0"]),      # bullet 不渲染
        _node("doc/p7", 6, ["9", "0"]),      # startOverride → 4.
        _node("doc/p8", 7, None),
    ]
    assign_rendered_numbers(nodes, table)
    rendered = [item.properties.get("numbering_rendered") for item in nodes]
    assert rendered == ["1.", "1.1", "1.2", "2.", "2.1", None, "4.", None]


def test_restored_number_extracts_digits():
    nodes = [_node("doc/p1", 0, ["5", "0"])]
    assign_rendered_numbers(nodes, load_numbering(_archive()))
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))], nodes,
    )
    assert restored_number(source, "doc/p1") == "1"
    assert restored_number(source, "doc/p404") is None


def test_author_year_citations_link_by_first_surname_and_year():
    """作者—年份制：首作者姓(词边界)+年份唯一命中才连，多候选不认。"""
    from word2jats.understand.understand import (
        _citation_relations, _entity_identities,
    )

    spans = (
        ReferenceSpan(1, ("doc/p2", 0, 10), SourceText((("doc/p2", 0, 10),))),
        ReferenceSpan(2, ("doc/p3", 0, 10), SourceText((("doc/p3", 0, 10),))),
        ReferenceSpan(3, ("doc/p4", 0, 10), SourceText((("doc/p4", 0, 10),))),
    )
    fields = [
        {"person_groups": [{"members": [{"surname_quote": "Anderson"}]}],
         "fields": {"year": "2020"}},
        {"person_groups": [{"members": [{"surname_quote": {"quote": "Hou"}}]}],
         "fields": {"year": {"quote": "2020"}}},
        {"person_groups": [{"members": [{"surname_quote": "Hou"}]}],
         "fields": {"year": "2019"}},
    ]
    identities = _entity_identities(spans, fields)
    response = {"citations": [
        {"citation_quote": {"quote": "Austen R. Anderson & Fowers, 2020"},
         "target_reference_ids": []},
        {"citation_quote": {"quote": "Wai Kai Hou, et al., 2020"},
         "target_reference_ids": []},
        # "Houston" 不含词边界上的 "Hou"，不得误配。
        {"citation_quote": {"quote": "Houston, 2019"},
         "target_reference_ids": []},
    ]}
    edges = _citation_relations(response, {}, identities)
    assert [item["target_reference_ids"] for item in edges] == [
        ["reference:1"], ["reference:2"],
    ]


def test_entity_map_prefers_restored_auto_number_over_position():
    """自动编号的印出值是文档事实：切条位置有偏差时编号映射不得漂移。"""
    table = load_numbering(_archive())
    nodes = [
        _node("doc/p1", 0, ["5", "0"], "First entry"),
        _node("doc/p2", 1, ["5", "0"], "Second entry"),
        _node("doc/p3", 2, ["5", "0"], "Third entry"),
    ]
    assign_rendered_numbers(nodes, table)
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    # 模拟切条瑕疵：第一条被拆成两个 span，位置序号整体后移。
    spans = (
        ReferenceSpan(1, ("doc/p1", 0, 5),
                      SourceText((("doc/p1", 0, 5),))),
        ReferenceSpan(2, ("doc/p2", 0, 12),
                      SourceText((("doc/p2", 0, 12),))),
        ReferenceSpan(3, ("doc/p3", 0, 11),
                      SourceText((("doc/p3", 0, 11),))),
    )
    fields = [{"label_quote": None}] * 3
    table_by_number = _entity_by_printed_number(spans, fields, source)
    # 编号来自各 span 首节点的还原值，而不是位置序号。
    assert table_by_number["1"] == "reference:1"
    assert table_by_number["2"] == "reference:2"
    assert table_by_number["3"] == "reference:3"
