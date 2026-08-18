from word2jats.model.source import (
    OBJECT_REPLACEMENT, ObjectAnchor, ObjectOccurrence,
    SourceDocument, SourceNode, SourcePart,
)
from word2jats.verify.audit import (
    audit_provenance, audit_source_coverage, audit_structure,
)
from word2jats.verify.provenance import ProvenanceEntry


def _source(text="kept missing", *, occurrence=False):
    objects = [ObjectAnchor(0, "o1")] if occurrence else []
    node = SourceNode("doc/p1", "document", "para", None, 0, text,
                      objects=objects)
    return SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))],
        [node],
        [ObjectOccurrence("o1", "omml", "doc/p1", 0)] if occurrence else [],
    )


def test_structure_audit_separates_duplicate_ids_and_dangling_rids():
    report = audit_structure(b"""
      <article><body><p id="x"/><p id="x"><xref rid="missing">1</xref></p></body></article>
    """)
    assert {item.code for item in report.issues} == {"DUPLICATE_ID", "DANGLING_RID"}


def test_provenance_audit_reads_the_final_xml_not_the_semantic_claim():
    source = _source()
    xml = b"<article><body><p>kept</p></body></article>"
    good = ProvenanceEntry(
        "/article/body/p", "text", None, 0, 4, "kept", "source",
        source_ranges=(("doc/p1", 0, 4),),
    )
    assert audit_provenance(xml, (good,), source).ok
    missing = audit_provenance(xml, (), source)
    assert [item.code for item in missing.issues] == [
        "OUTPUT_TEXT_WITHOUT_PROVENANCE"
    ]


def test_source_coverage_is_driven_by_rendered_ranges_and_objects():
    source = _source()
    entry = ProvenanceEntry(
        "/article/body/p", "text", None, 0, 4, "kept", "source",
        source_ranges=(("doc/p1", 0, 4),),
    )
    report = audit_source_coverage(source, (entry,), ())
    gap = next(item for item in report.issues if item.code == "TEXT_UNCOVERED")
    assert (gap.source_id, gap.start, gap.end) == ("doc/p1", 4, 12)

    object_source = _source(OBJECT_REPLACEMENT, occurrence=True)
    transformed = ProvenanceEntry(
        "/article/body/inline-formula/mml:math", "transform", None,
        None, None, "", "transform", source_object="o1",
        transform="omml-to-mathml",
    )
    assert audit_source_coverage(object_source, (transformed,), ()).ok


def test_flattened_table_tabs_are_layout_but_cell_text_must_be_covered():
    source = _source("left\tright")
    entries = (
        ProvenanceEntry(
            "/article/body/table-wrap/table/tbody/tr/td[1]", "text", None,
            0, 4, "left", "source", source_ranges=(("doc/p1", 0, 4),),
        ),
        ProvenanceEntry(
            "/article/body/table-wrap/table/tbody/tr/td[2]", "text", None,
            0, 5, "right", "source", source_ranges=(("doc/p1", 5, 10),),
        ),
    )
    assignments = ({"source_id": "doc/p1", "role": "table"},)
    assert audit_source_coverage(source, entries, assignments).ok

    missing = audit_source_coverage(source, entries[:1], assignments)
    assert any(item.code == "TEXT_UNCOVERED" and item.start == 5
               for item in missing.issues)


def test_non_output_semantic_label_requires_an_explicit_restricted_use():
    source = _source("Received: 2024")
    entry = ProvenanceEntry(
        "/article/front/article-meta/history/date/year", "text", None,
        0, 4, "2024", "source", source_ranges=(("doc/p1", 10, 14),),
    )
    uses = ({
        "source_id": "doc/p1", "start": 0, "end": 10,
        "usage_id": "date:received:notation", "role": "semantic-label",
    },)
    assert audit_source_coverage(source, (entry,), (), uses).ok

    invalid = ({
        **uses[0], "role": "looks-unimportant",
    },)
    report = audit_source_coverage(source, (entry,), (), invalid)
    assert any(item.code == "SEMANTIC_USE_INVALID" for item in report.issues)


def test_flattened_table_delimiters_are_consumed_as_layout_not_discarded():
    source = _source("left\tright")
    entries = (
        ProvenanceEntry(
            "/article/body/table-wrap/table/tbody/tr/td[1]", "text", None,
            0, 4, "left", "source", source_ranges=(("doc/p1", 0, 4),),
        ),
        ProvenanceEntry(
            "/article/body/table-wrap/table/tbody/tr/td[2]", "text", None,
            0, 5, "right", "source", source_ranges=(("doc/p1", 5, 10),),
        ),
    )
    report = audit_source_coverage(
        source, entries, ({"source_id": "doc/p1", "role": "table"},)
    )
    tab = next(item for item in report.records if item.start == 4 and item.end == 5)
    assert tab.action == "consume" and tab.role == "layout-notation"


def test_independently_approved_decoration_is_the_only_nonempty_discard_path():
    source = _source("Visible ornament")
    approved = audit_source_coverage(source, (), ({
        "source_id": "doc/p1", "role": "decorative",
        "evidence": ["body:block", "discard-review:approved"],
    },))
    assert approved.ok
    assert any(item.action == "discard" and item.reason == "decorative"
               for item in approved.records)

    unreviewed = audit_source_coverage(source, (), ({
        "source_id": "doc/p1", "role": "decorative",
        "evidence": ["body:block"],
    },))
    assert any(item.code == "TEXT_UNCOVERED" for item in unreviewed.issues)
