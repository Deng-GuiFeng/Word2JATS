from word2jats.model.source import SourceDocument, SourceNode, SourcePart
from word2jats.understand.ground import (
    GroundRequest, Position, find_candidates, ground, ground_context,
    ground_joint, ground_quote_anywhere, ground_record_quote,
)
from word2jats.understand.serialize import serialize


def test_ground_quote_anywhere_requires_global_uniqueness():
    doc = _doc(
        "Epilepsy background text.",
        "Recurrent seizures [1,2]. Seizures persist.",
        "Other seizures [3,4]. More text.",
    )
    view = serialize(doc)
    # 完整上下文全文唯一：即便 record_key 报偏也能无歧义落锚。
    assert ground_quote_anywhere(
        "1", view, left_context="seizures [", right_context=",2]."
    ) == ("doc/p2", 20, 21)
    # 上下文不足以全文唯一（两段都有 "seizures [1,"）：一律不认。
    ambiguous = _doc(
        "Recurrent seizures [1,2]. Seizures persist.",
        "Other seizures [1,3]. More text.",
    )
    assert ground_quote_anywhere(
        "1", serialize(ambiguous), left_context="seizures [", right_context=","
    ) is None
    # 归一兜底：弯引号等视觉等价折算后唯一命中。
    curly = _doc("She said “ignore” this [5].")
    assert ground_quote_anywhere(
        "5", serialize(curly), left_context='said "ignore" this [',
        right_context="].",
    ) == ("doc/p1", 24, 25)


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


def test_adjacent_context_identifies_a_repeated_source_occurrence():
    doc = _doc("Ora Lume, D.Sc.; Taro Nix, D.Sc.", "2042/3/31")
    assert ground("D.Sc.", doc, block_hint="doc/p1") is None
    assert ground_context(
        "D.Sc.", doc, block_hint="doc/p1",
        left_context="Taro Nix, ", right_context="",
    ) == ("doc/p1", 27, 32)
    assert ground_context(
        "3", doc, block_hint="doc/p2",
        left_context="2042/", right_context="/31",
    ) == ("doc/p2", 5, 6)
    assert ground_context(
        "3", doc, block_hint="doc/p2",
        left_context="2042/3/", right_context="1",
    ) == ("doc/p2", 7, 8)

    owner = ("doc/p1", 17, 32)
    assert ground_context(
        "D.Sc.", doc, scope=owner,
        left_context="Taro Nix, ", right_context="",
    ) == ("doc/p1", 27, 32)

    # 原文本身在节点中唯一时，多余上下文即使抄错也不应推翻它；
    # 上下文只负责解决重复原文，不能成为第二套内容门槛。
    assert ground_context(
        "Ora Lume", doc, block_hint="doc/p1",
        left_context="not source text", right_context="",
    ) == ("doc/p1", 0, 8)


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


def test_record_quote_maps_visual_space_back_to_original_word_space():
    doc = _doc("学编：Salvatore\u00a0De\u00a0Rosa")
    view = serialize(doc)
    assert ground_record_quote(
        "学编：Salvatore De Rosa", view, record_key="doc/p1",
        left_context="", right_context="",
    ) == ("doc/p1", 0, len(doc.node("doc/p1").text))
