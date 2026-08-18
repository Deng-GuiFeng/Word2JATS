"""v2 确定机械层的小型单元测试。"""

from lxml import etree

from word2jats.model.source import (
    RunRef, RunSpan, SourceDocument, SourceNode, SourcePart, SourceText,
)
from word2jats.render.v2 import V2Renderer, render_v2
from word2jats.semantic import model as sm
from word2jats.understand.merge import ReferenceSpan
from word2jats.understand.xrefs import link_bibliographic_citations
from word2jats.verify.audit import audit_provenance, audit_source_coverage


def _source(text):
    node = SourceNode("doc/p1", "document", "para", None, 0, text)
    return SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=(node.node_id,))], [node],
    )


def _xref_source(body, reference_texts):
    texts = [body, *reference_texts]
    nodes = [
        SourceNode(f"doc/p{index + 1}", "document", "para", None, index, text)
        for index, text in enumerate(texts)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    spans = tuple(
        ReferenceSpan(index, (f"doc/p{index + 1}", 0, len(text)),
                      SourceText(((f"doc/p{index + 1}", 0, len(text)),)))
        for index, text in enumerate(reference_texts, 1)
    )
    references = tuple(
        sm.Reference(f"reference:{index}", None,
                     sm.MixedCitation(None, sm.RichText()))
        for index in range(1, len(reference_texts) + 1)
    )
    return source, spans, sm.ReferenceList(None, references)


def test_xref_uses_grounded_source_relations_without_parsing_notation():
    source, spans, reference_list = _xref_source(
        "Prior work [1,3-4] remains relevant.",
        ("Alpha reference", "Beta reference", "Gamma reference", "Delta reference"),
    )
    paragraph = sm.Paragraph(None, sm.RichText.from_source(
        SourceText((("doc/p1", 0, len(source.node("doc/p1").text)),))
    ))
    raw = ({
        "citation_quote": {"quote": "[1,3-4]", "node_hint": "doc/p1"},
        "target_reference_head_quotes": [
            {"quote": "Alpha", "node_hint": "doc/p2"},
            {"quote": "Gamma", "node_hint": "doc/p4"},
            {"quote": "Delta", "node_hint": "doc/p5"},
        ],
    },)
    linked, issues = link_bibliographic_citations(
        (paragraph,), reference_list, spans, source, raw
    )
    xrefs = [part for part in linked[0].content.parts
             if isinstance(part, sm.CrossReference)]
    assert [item.target_ids for item in xrefs] == [(
        "reference:1", "reference:3", "reference:4",
    )]
    assert [item.content.plain_text(source) for item in xrefs] == ["[1,3-4]"]
    assert linked[0].content.plain_text(source) == source.node("doc/p1").text
    assert issues == ()


def test_author_year_xref_requires_unique_entity():
    source, spans, reference_list = _xref_source(
        "Smith (2020) reported this result.", ("Smith. Exact study. 2020.",)
    )
    paragraph = sm.Paragraph(None, sm.RichText.from_source(
        SourceText((("doc/p1", 0, len(source.node("doc/p1").text)),))
    ))
    raw = ({
        "citation_quote": {"quote": "Smith (2020)", "node_hint": "doc/p1"},
        "target_reference_head_quotes": [
            {"quote": "Smith. Exact", "node_hint": "doc/p2"},
        ],
    },)
    linked, issues = link_bibliographic_citations(
        (paragraph,), reference_list, spans, source, raw
    )
    xref = next(part for part in linked[0].content.parts
                if isinstance(part, sm.CrossReference))
    assert xref.target_ids == ("reference:1",)
    assert xref.content.plain_text(source) == "Smith (2020)"
    assert issues == ()


def test_xref_rejects_a_target_pointer_outside_reference_spans():
    source, spans, reference_list = _xref_source(
        "An unusual citation mark points here.", ("Alpha reference",)
    )
    paragraph = sm.Paragraph(None, sm.RichText.from_source(
        SourceText((("doc/p1", 0, len(source.node("doc/p1").text)),))
    ))
    raw = ({
        "citation_quote": {"quote": "An unusual citation mark", "node_hint": "doc/p1"},
        "target_reference_head_quotes": [
            {"quote": "points here", "node_hint": "doc/p1"},
        ],
    },)
    linked, issues = link_bibliographic_citations(
        (paragraph,), reference_list, spans, source, raw
    )
    assert not any(isinstance(part, sm.CrossReference) for part in linked[0].content.parts)
    assert len(issues) == 1


def test_plain_projection_obeys_pcdata_slots_and_prevents_nested_links():
    source = _source("et al.DOI")
    renderer = V2Renderer(sm.SemanticDoc(source))
    etal = sm.RichText((sm.Styled(
        "italic", sm.RichText.from_source(SourceText((("doc/p1", 0, 6),)))
    ),))
    group_xml = renderer.person_group(sm.ReferencePersonGroup("author", et_al=etal))
    assert etree.tostring(group_xml, encoding="unicode") == (
        '<person-group person-group-type="author"><etal>et al.</etal></person-group>'
    )

    visible = sm.RichText((sm.ExternalLink(
        "uri", "https://example.invalid/doi",
        sm.RichText.from_source(SourceText((("doc/p1", 6, 9),))),
    ),))
    identifier = sm.ReferenceIdentifier(
        "doi", visible, carrier="url", href="https://example.invalid/doi"
    )
    link_xml = renderer.reference_identifier(identifier)
    assert link_xml.tag == "ext-link"
    assert link_xml.xpath("count(.//ext-link)") == 0.0
    assert "".join(link_xml.itertext()) == "DOI"


def test_reference_slot_projection_is_dtd_driven_not_whole_line_formatting():
    text = "[7]TitleJournal2024132nd"
    node = SourceNode(
        "doc/p1", "document", "para", None, 0, text,
        run_spans=[
            RunSpan(0, 8, RunRef("r1", "document", "/p/r1", bold=True)),
            RunSpan(8, 15, RunRef("r2", "document", "/p/r2", italic=True)),
            RunSpan(15, 21, RunRef("r3", "document", "/p/r3", bold=True)),
            RunSpan(21, 22, RunRef("r4", "document", "/p/r4")),
            RunSpan(22, 24, RunRef(
                "r5", "document", "/p/r5", superscript=True
            )),
        ],
    )
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))], [node],
    )

    def rich(start, end):
        return sm.RichText.from_source(SourceText((("doc/p1", start, end),)))

    citation = sm.StructuredCitation(
        "journal",
        article_title=rich(3, 8),
        source=rich(8, 15),
        year=rich(15, 19),
        volume=rich(19, 21),
        edition=rich(21, 24),
        field_order=("article_title", "source", "year", "volume", "edition"),
    )
    reference = sm.Reference(
        "reference:7", rich(0, 3), citation,
    )
    renderer = V2Renderer(sm.SemanticDoc(
        source, reference_list=sm.ReferenceList(None, (reference,))
    ))
    xml = renderer.reference(reference)
    assert xml.find("label").text == "[7]"
    assert len(xml.find("label")) == 0
    assert etree.tostring(xml.find("element-citation/article-title"),
                          encoding="unicode") == "<article-title>Title</article-title>"
    assert etree.tostring(xml.find("element-citation/source"),
                          encoding="unicode") == "<source><italic>Journal</italic></source>"
    assert len(xml.find("element-citation/year")) == 0
    assert len(xml.find("element-citation/volume")) == 0
    assert etree.tostring(xml.find("element-citation/edition"),
                          encoding="unicode") == "<edition>2<sup>nd</sup></edition>"


def test_unicode_script_character_remains_literal_source_text():
    source = _source("²")
    source_text = SourceText((("doc/p1", 0, 1),))
    resolved = source_text.runs(source)[0]
    assert resolved.text == "²"
    document = sm.SemanticDoc(
        source, body=(sm.Paragraph(
            None, sm.RichText.from_source(source_text)
        ),),
    )
    result = render_v2(document)
    root = etree.fromstring(result.xml_bytes)
    paragraph = root.find(".//body/p")
    assert paragraph is not None and paragraph.text == "²" and len(paragraph) == 0
    assert not any(item.origin_kind == "transform" for item in result.provenance)
    assert audit_provenance(result.xml_bytes, result.provenance, source).ok
    assert audit_source_coverage(source, result.provenance).ok


def test_renderer_keeps_sec_structurally_valid_without_inventing_visible_title():
    source = _source("content")
    renderer = V2Renderer(sm.SemanticDoc(source))
    section = sm.Section(
        None, None,
        (sm.Paragraph(None, sm.RichText.from_source(
            SourceText((("doc/p1", 0, 7),))
        )),),
    )
    xml = renderer.section(section)
    assert [child.tag for child in xml] == ["title", "p"]
    assert xml[0].text is None
