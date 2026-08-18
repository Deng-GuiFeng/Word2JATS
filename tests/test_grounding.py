from word2jats.model.source import SourceDocument, SourceNode, SourcePart
from word2jats.understand.ground import (
    GroundRequest, Position, find_candidates, ground, ground_joint,
)


def _doc(*texts):
    nodes = [SourceNode(f"doc/p{i + 1}", "document", "para", None, i, text)
             for i, text in enumerate(texts)]
    return SourceDocument(
        parts=[SourcePart("document", "document", "/word/document.xml",
                          node_ids=tuple(item.node_id for item in nodes))],
        nodes=nodes,
    )


def test_ground_exact_normalized_and_ambiguity():
    doc = _doc("Alpha\u00a0 beta \u201cword\u201d\u2014tail", "Alpha beta")
    assert ground("\u201cword\u201d\u2014tail", doc) == ("doc/p1", 12, 23)
    assert ground('beta "word"-tail', doc) == ("doc/p1", 7, 23)
    # 精确唯一命中优先于其他节点上的归一命中。
    assert ground("Alpha beta", doc) == ("doc/p2", 0, 10)
    assert ground("Alpha beta", doc, block_hint="doc/p2") == ("doc/p2", 0, 10)


def test_ground_does_not_casefold_nfkc_or_cross_object():
    doc = _doc("ABC", "ＡＢＣ", "left\ufffcright")
    assert ground("abc", doc) is None
    assert ground("ABC", doc) == ("doc/p1", 0, 3)
    assert ground("left\ufffcright", doc) is None
    assert ground("left\ufffcright", doc, allow_object=True) == ("doc/p3", 0, 10)


def test_after_is_only_an_explicit_sequence_constraint():
    doc = _doc("same", "same")
    assert ground("same", doc) is None
    assert ground("same", doc, after=Position("doc/p1", 4)) == ("doc/p2", 0, 4)


def test_reference_fields_use_unique_joint_assignment():
    doc = _doc("Smith. 2020. Title. Journal. 2020:10-20.")
    scope = ("doc/p1", 0, len(doc.node("doc/p1").text))
    # 年份摘抄重复，即使页码唯一，整体仍有两解，必须拒绝。
    assert ground_joint([
        GroundRequest("year", "2020"),
        GroundRequest("fpage", "10"),
    ], doc, scope=scope) is None
    unique = ground_joint([
        GroundRequest("author", "Smith"),
        GroundRequest("title", "Title"),
        GroundRequest("fpage", "10"),
    ], doc, scope=scope)
    assert unique == {
        "author": ("doc/p1", 0, 5),
        "title": ("doc/p1", 13, 18),
        "fpage": ("doc/p1", 34, 36),
    }
    ordered = ground_joint([
        GroundRequest("year", "2020"),
        GroundRequest("title", "Title"),
        GroundRequest("fpage", "10"),
    ], doc, scope=scope, source_order=("title", "year", "fpage"))
    assert ordered["year"] == ("doc/p1", 29, 33)


def test_joint_grounding_does_not_encode_bibliographic_field_shapes():
    """落锚层只验证源指针，不得用年份、页码或标识符的字面规则
    代替理解层的语义判断。
    """
    doc = _doc("undated. volume Ⅷ. page Appendix-A. identifier local:alpha.")
    scope = ("doc/p1", 0, len(doc.node("doc/p1").text))
    assert ground_joint([
        GroundRequest("year", "undated"),
        GroundRequest("volume", "Ⅷ"),
        GroundRequest("fpage", "Appendix-A"),
        GroundRequest("doi", "local:alpha"),
    ], doc, scope=scope) == {
        "year": ("doc/p1", 0, 7),
        "volume": ("doc/p1", 16, 17),
        "fpage": ("doc/p1", 24, 34),
        "doi": ("doc/p1", 47, 58),
    }


def test_exact_candidates_dominate_normalized_candidates():
    doc = _doc("A  B", "A B")
    assert find_candidates("A B", doc) == [("doc/p2", 0, 3)]


def test_visible_tab_dialect_grounds_back_to_the_source_tab():
    doc = _doc("before\tafter")
    assert ground("before⇥after", doc) == ("doc/p1", 0, 12)
