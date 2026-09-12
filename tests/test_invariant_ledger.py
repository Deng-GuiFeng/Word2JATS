"""源覆盖账的区间、复用与弃置不变量。"""

from word2jats.model.source import (
    OBJECT_REPLACEMENT,
    ObjectAnchor,
    ObjectOccurrence,
    SourceDocument,
    SourceNode,
    SourcePart,
    SourceText,
)
from word2jats.verify.ledger import SourceCoverageLedger


def _source():
    text = "shared address" + OBJECT_REPLACEMENT
    node = SourceNode(
        "doc/p1", "document", "para", None, 0, text,
        objects=[ObjectAnchor(len(text) - 1, "o1")],
    )
    return SourceDocument(
        parts=[SourcePart(
            "document", "document", "/word/document.xml", node_ids=("doc/p1",)
        )],
        nodes=[node],
        occurrences=[ObjectOccurrence("o1", "image", "doc/p1", len(text) - 1)],
    )


def test_ledger_accepts_explicit_multi_author_address_reuse_and_object_discard():
    source = _source()
    text = SourceText((("doc/p1", 0, len("shared address")),))
    ledger = SourceCoverageLedger(source)
    ledger.consume_text(text, usage_id="author-1-address", role="address")
    ledger.consume_text(
        text, usage_id="author-2-address", role="address",
        reuse_reason="两位作者在源稿共用同一地址",
    )
    ledger.discard_object(
        "o1", usage_id="image-fallback", reason="fallback_superseded"
    )
    assert ledger.audit().ok


def test_ledger_points_to_one_character_gap_instead_of_hiding_it():
    source = _source()
    ledger = SourceCoverageLedger(source)
    ledger.consume_text(
        ("doc/p1", 0, 7), usage_id="left", role="paragraph"
    )
    ledger.consume_text(
        ("doc/p1", 8, len("shared address")), usage_id="right", role="paragraph"
    )
    ledger.discard_object("o1", usage_id="fallback", reason="fallback_superseded")
    report = ledger.audit()
    issue = next(item for item in report.issues if item.code == "TEXT_UNCOVERED")
    assert (issue.source_id, issue.start, issue.end, issue.detail) == (
        "doc/p1", 7, 8, repr("a"),
    )


def test_ledger_exempts_pure_whitespace_gap_by_policy():
    """既定口径：纯排版空白允许无去向，不作为覆盖缺口。"""
    source = _source()
    ledger = SourceCoverageLedger(source)
    ledger.consume_text(("doc/p1", 0, 6), usage_id="left", role="paragraph")
    ledger.consume_text(
        ("doc/p1", 7, len("shared address")), usage_id="right", role="paragraph"
    )
    ledger.discard_object("o1", usage_id="fallback", reason="fallback_superseded")
    codes = {item.code for item in ledger.audit().issues}
    assert "TEXT_UNCOVERED" not in codes


def test_ledger_rejects_unexplained_reuse_and_unapproved_discard_reason():
    source = _source()
    ledger = SourceCoverageLedger(source)
    whole = ("doc/p1", 0, len("shared address"))
    ledger.consume_text(whole, usage_id="one", role="paragraph")
    ledger.consume_text(whole, usage_id="two", role="paragraph")
    ledger.discard_object("o1", usage_id="drop", reason="looks_unimportant")
    codes = {item.code for item in ledger.audit().issues}
    assert "REUSE_WITHOUT_BASIS" in codes
    assert "DISCARD_REASON_NOT_ALLOWED" in codes
