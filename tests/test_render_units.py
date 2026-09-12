"""v2 确定机械层的小型单元测试。"""

import pytest
from lxml import etree

from word2jats.model.source import (
    LinkSpan, RunRef, RunSpan, SourceDocument, SourceNode, SourcePart, SourceText,
)
from word2jats.render.v2 import V2RenderError, V2Renderer, render_v2
from word2jats.semantic import model as sm
from word2jats.understand.merge import ReferenceSpan
from word2jats.understand.xrefs import link_bibliographic_citations
from word2jats.validate.validator import Validator
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
        "citation_quote": {
            "quote": "1,3-4", "record_key": "doc/p1",
            "left_context": "work [", "right_context": "] remains",
        },
        "target_reference_ids": ["reference:1", "reference:3", "reference:4"],
    },)
    linked, issues = link_bibliographic_citations(
        (paragraph,), reference_list, spans, source, raw
    )
    xrefs = [part for part in linked[0].content.parts
             if isinstance(part, sm.CrossReference)]
    assert [item.target_ids for item in xrefs] == [(
        "reference:1", "reference:3", "reference:4",
    )]
    assert [item.content.plain_text(source) for item in xrefs] == ["1,3-4"]
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
        "citation_quote": {
            "quote": "Smith (2020)", "record_key": "doc/p1",
            "left_context": "", "right_context": " reported",
        },
        "target_reference_ids": ["reference:1"],
    },)
    linked, issues = link_bibliographic_citations(
        (paragraph,), reference_list, spans, source, raw
    )
    xref = next(part for part in linked[0].content.parts
                if isinstance(part, sm.CrossReference))
    assert xref.target_ids == ("reference:1",)
    assert xref.content.plain_text(source) == "Smith (2020)"
    assert issues == ()


def test_repeated_identical_xrefs_use_distinct_source_occurrences():
    text = "Reed (2022) reported one result; Reed (2022) later revised it."
    source, spans, reference_list = _xref_source(
        text, ("Reed. A deliberately invented study. 2022.",)
    )
    paragraph = sm.Paragraph(None, sm.RichText.from_source(
        SourceText((("doc/p1", 0, len(text)),))
    ))
    raw = tuple({
        "citation_quote": {
            "quote": "Reed (2022)", "record_key": "doc/p1",
            "left_context": left, "right_context": right,
        },
        "target_reference_ids": ["reference:1"],
    } for left, right in (("", " reported"), ("result; ", " later")))

    linked, issues = link_bibliographic_citations(
        (paragraph,), reference_list, spans, source, raw
    )
    xrefs = [part for part in linked[0].content.parts
             if isinstance(part, sm.CrossReference)]
    assert len(xrefs) == 2
    assert [item.source_occurrence[1] for item in xrefs] == [0, 33]
    assert [item.content.plain_text(source) for item in xrefs] == [
        "Reed (2022)", "Reed (2022)",
    ]
    assert linked[0].content.plain_text(source) == text
    assert issues == ()


def test_xref_rejects_a_target_pointer_outside_reference_spans():
    source, spans, reference_list = _xref_source(
        "An unusual citation mark points here.", ("Alpha reference",)
    )
    paragraph = sm.Paragraph(None, sm.RichText.from_source(
        SourceText((("doc/p1", 0, len(source.node("doc/p1").text)),))
    ))
    raw = ({
        "citation_quote": {
            "quote": "An unusual citation mark", "record_key": "doc/p1",
            "left_context": "", "right_context": " points",
        },
        "target_reference_ids": ["reference:99"],
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


def test_jats_simple_text_projection_preserves_visible_source_without_illegal_links():
    text = "Contact: editor@example.org"
    email_start = text.index("editor")
    node = SourceNode(
        "doc/p1", "document", "para", None, 0, text,
        run_spans=[
            RunSpan(0, email_start, RunRef(
                "r1", "document", "/p/r1", bold=True,
            )),
            RunSpan(email_start, len(text), RunRef(
                "r2", "document", "/p/r2", italic=True,
            )),
        ],
        links=[LinkSpan(email_start, len(text), "mailto:editor@example.org")],
    )
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))], [node],
    )
    renderer = V2Renderer(sm.SemanticDoc(source))
    address = renderer.address(sm.Address(
        "address:1",
        (sm.RichText.from_source(SourceText((("doc/p1", 0, len(text)),))),),
    ))

    line = address.find("addr-line")
    assert line is not None
    assert "".join(line.itertext()) == text
    assert line.find("ext-link") is None
    assert line.find("bold") is not None
    assert line.find("italic") is not None
    validation = Validator().validate_bytes(etree.tostring(address))
    assert validation.dtd_valid, validation.errors


def test_jats_simple_text_projection_rejects_semantic_structures_it_cannot_hold():
    source = _source("visible")
    content = sm.RichText.from_source(SourceText((("doc/p1", 0, 7),)))
    invalid_parts = (
        sm.Break(),
        sm.ExternalLink("uri", "https://example.invalid", content),
        sm.CrossReference("bibr", ("reference:1",), content),
        sm.EmailInline(content),
        sm.CitationFieldInline("source", content),
        sm.InlineGraphic("object:1", display=True),
    )
    for part in invalid_parts:
        renderer = V2Renderer(sm.SemanticDoc(source))
        with pytest.raises(V2RenderError, match="JATS simple-text"):
            renderer.address(sm.Address(
                "address:1", (sm.RichText((part,)),),
            ))


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


def _callout_fixture(body_text, labels):
    texts = [body_text, *labels]
    nodes = [
        SourceNode(f"doc/p{index + 1}", "document", "para", None, index, text)
        for index, text in enumerate(texts)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )

    def rich(node_index, start=None, end=None):
        text = texts[node_index]
        left = 0 if start is None else start
        right = len(text) if end is None else end
        return sm.RichText.from_source(
            SourceText(((f"doc/p{node_index + 1}", left, right),))
        )

    return source, rich


def test_display_object_callouts_wrap_only_printed_numbers():
    from word2jats.understand.xrefs import link_display_object_callouts

    body = "As Figure 1 and Table 2 show, Figs. 1 and 3 differ; Fig. 9 is absent."
    source, rich = _callout_fixture(body, ["Figure 1", "Figure 3", "Table 2"])
    blocks = (
        sm.Paragraph(None, rich(0)),
        sm.Figure("figure:1", rich(1), None, ()),
        sm.Figure("figure:3", rich(2), None, ()),
        sm.TableBlock("table:2", rich(3), None, (), (), ()),
    )
    linked = link_display_object_callouts(blocks, source)
    xrefs = [
        part for part in linked[0].content.parts
        if isinstance(part, sm.CrossReference)
    ]
    assert [(item.ref_type, item.target_ids, item.source_occurrence)
            for item in xrefs] == [
        ("fig", ("figure:1",), ("doc/p1", 10, 11)),
        ("table", ("table:2",), ("doc/p1", 22, 23)),
        ("fig", ("figure:1",), ("doc/p1", 36, 37)),
        ("fig", ("figure:3",), ("doc/p1", 42, 43)),
    ]
    # 查无实体的 Fig. 9 保持纯文本；正文其余文字原样保留。
    plain = "".join(
        part.source.text(source) if isinstance(part, sm.Text) else
        part.content.plain_text(source)
        for part in linked[0].content.parts
    )
    assert plain == body


def test_display_object_callouts_skip_ambiguous_numbers_and_group_members():
    from word2jats.understand.xrefs import link_display_object_callouts

    body = "See Figure 1 and Table 1."
    source, rich = _callout_fixture(
        body, ["Figure 1", "Figure 1", "Table 1"]
    )
    duplicated = (
        sm.Paragraph(None, rich(0)),
        sm.Figure("figure:a", rich(1), None, ()),
        sm.Figure("figure:b", rich(2), None, ()),
        sm.TableBlock("table:1", rich(3), None, (), (), ()),
    )
    linked = link_display_object_callouts(duplicated, source)
    xrefs = [
        part for part in linked[0].content.parts
        if isinstance(part, sm.CrossReference)
    ]
    # 同号歧义的图一律不链；表 1 唯一，正常链接。
    assert [(item.ref_type, item.target_ids) for item in xrefs] == [
        ("table", ("table:1",)),
    ]

    grouped = (
        sm.Paragraph(None, rich(0)),
        sm.FigureGroup(
            "figure-group:1", rich(1), None,
            (sm.Figure("figure:member", rich(2), None, ()),),
        ),
        sm.TableBlock("table:1", rich(3), None, (), (), ()),
    )
    linked = link_display_object_callouts(grouped, source)
    xrefs = [
        part for part in linked[0].content.parts
        if isinstance(part, sm.CrossReference)
    ]
    # 图组是可引用单元：组注册、成员不注册，同号不构成歧义。
    assert [(item.ref_type, item.target_ids) for item in xrefs] == [
        ("fig", ("figure-group:1",)),
        ("table", ("table:1",)),
    ]


def test_display_object_callouts_skip_supplementary_and_captions():
    from word2jats.understand.xrefs import link_display_object_callouts

    body = "Supplementary Fig. 1 and Suppl. Table 1 are external; Figure 1 links."
    caption_text = "Details listed in Table 1"
    source, rich = _callout_fixture(
        body, ["Figure 1", "Table 1", caption_text]
    )
    blocks = (
        sm.Paragraph(None, rich(0)),
        sm.Figure("figure:1", rich(1), None, ()),
        sm.TableBlock(
            "table:1", rich(2),
            sm.Caption(None, (sm.Paragraph(None, rich(3)),)),
            (), (), (),
        ),
    )
    linked = link_display_object_callouts(blocks, source)
    xrefs = [
        part for part in linked[0].content.parts
        if isinstance(part, sm.CrossReference)
    ]
    # Supplementary 家族的提及指向补充材料，不得链到文内同号实体。
    assert [(item.ref_type, item.target_ids) for item in xrefs] == [
        ("fig", ("figure:1",)),
    ]
    # 题注内的提及保持纯文本（与结构参考口径一致）。
    caption_parts = linked[2].caption.paragraphs[0].content.parts
    assert all(not isinstance(part, sm.CrossReference) for part in caption_parts)


def test_display_object_callouts_wrap_word_number_phrase_whole():
    from word2jats.understand.xrefs import link_display_object_callouts

    body = "Rates are shown in table three; Table nine is absent."
    source, rich = _callout_fixture(body, ["Table 3"])
    blocks = (
        sm.Paragraph(None, rich(0)),
        sm.TableBlock("table:3", rich(1), None, (), (), ()),
    )
    linked = link_display_object_callouts(blocks, source)
    xrefs = [
        part for part in linked[0].content.parts
        if isinstance(part, sm.CrossReference)
    ]
    # 文字式数词把整个短语包进 xref；查无实体的 Table nine 保持纯文本。
    assert [(item.ref_type, item.target_ids,
             item.content.plain_text(source)) for item in xrefs] == [
        ("table", ("table:3",), "table three"),
    ]


def test_person_group_type_outside_dtd_enum_projects_to_custom():
    """person-group-type 是封闭枚举；越界语义角色走 custom+custom-type，不写非法属性。"""
    source = _source("Collaboration Group")
    renderer = V2Renderer(sm.SemanticDoc(source))
    collab = sm.RichText.from_source(SourceText((("doc/p1", 0, 19),)))

    invalid = renderer.person_group(sm.ReferencePersonGroup(
        "collaboration", collaborations=(collab,),
        child_order=("collaboration:0",),
    ))
    assert invalid.get("person-group-type") == "custom"
    assert invalid.get("custom-type") == "collaboration"
    assert invalid.find("collab") is not None

    valid = renderer.person_group(sm.ReferencePersonGroup(
        "author", collaborations=(collab,), child_order=("collaboration:0",),
    ))
    assert valid.get("person-group-type") == "author"
    assert valid.get("custom-type") is None


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


def test_direct_head_ids_are_reserved_for_the_rest_of_the_article():
    source = _source("content")
    document = sm.SemanticDoc(
        source,
        body=(sm.Section(
            "section:1", None,
            (sm.Paragraph(None, sm.RichText.from_source(
                SourceText((("doc/p1", 0, 7),))
            )),),
        ),),
    )
    head = (
        '<article><front><article-meta><title-group>'
        '<article-title id="S1">An invented title</article-title>'
        '</title-group></article-meta></front></article>'
    )
    root = etree.fromstring(render_v2(document, head_jats_xml=head).xml_bytes)
    assert root.find(".//body/sec").get("id") == "S2"
