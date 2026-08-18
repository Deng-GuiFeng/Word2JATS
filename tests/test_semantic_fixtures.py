"""不经金标准装载器的 SemanticDoc v2 手写夹具。"""

import json

from lxml import etree
import pytest

from word2jats.model.source import (
    BinaryResource,
    ObjectAnchor,
    ObjectOccurrence,
    RunRef,
    RunSpan,
    SourceDocument,
    SourceNode,
    SourcePart,
    SourceText,
)
from word2jats.render.v2 import V2RenderError, V2Renderer, render_v2
from word2jats.semantic.model import (
    Affiliation,
    Caption,
    Contributor,
    ContributorGroup,
    CrossReference,
    Figure,
    InlineGraphic,
    JournalMeta,
    MixedCitation,
    Note,
    Paragraph,
    PersonName,
    Reference,
    ReferenceIdentity,
    ReferenceList,
    RichText,
    SemanticDoc,
    Styled,
    Text,
)
from word2jats.semantic.snapshot import semantic_from_dict, semantic_to_dict


class _SourceBuilder:
    def __init__(self):
        self.nodes = []

    def text(self, value: str, *, bold=False, italic=False) -> SourceText:
        node_id = f"doc/p{len(self.nodes) + 1}"
        spans = []
        if value:
            spans.append(RunSpan(
                0, len(value), RunRef(
                    f"r{len(self.nodes) + 1}", "document", node_id,
                    bold=bold, italic=italic,
                )
            ))
        self.nodes.append(SourceNode(
            node_id, "document", "para", None, len(self.nodes), value,
            run_spans=spans,
        ))
        return SourceText(((node_id, 0, len(value)),))

    def finish(self, *, occurrence=None, resource=None) -> SourceDocument:
        occurrences = [] if occurrence is None else [occurrence]
        resources = [] if resource is None else [resource]
        document = SourceDocument(
            parts=[SourcePart(
                "document", "document", "/word/document.xml",
                "application/xml", tuple(item.node_id for item in self.nodes),
            )],
            nodes=self.nodes, occurrences=occurrences, resources=resources,
        )
        document.validate()
        return document


def _fixture_document() -> SemanticDoc:
    source = _SourceBuilder()
    title = source.text("源标题", bold=True, italic=True)
    surname = source.text("Ji")
    given = source.text("Yinze")
    aff_text = source.text("Institute A")
    aff_mark = source.text("1")
    note_mark = source.text("†")
    note_text = source.text("†These authors contributed equally.")
    lead = source.text("See ")
    citation = source.text("[1]")
    styled = source.text("important")
    ending = source.text(" result.")
    ref_text = source.text("Original reference text.")
    label = source.text("1")
    fig_label = source.text("Fig. 1")
    fig_caption = source.text("An image")

    media_node = SourceNode(
        "doc/p-media", "document", "para", None, len(source.nodes), "\ufffc",
        objects=[ObjectAnchor(0, "o1")],
    )
    source.nodes.append(media_node)
    resource = BinaryResource(
        "res1", "word/media/image1.png", "image/png",
        b"\x89PNG\r\n\x1a\nfixture", "png",
    )
    occurrence = ObjectOccurrence(
        "o1", "image", "doc/p-media", 0, resource_id="res1",
        properties={"inline": False, "emit_id": True},
    )
    source_document = source.finish(occurrence=occurrence, resource=resource)

    aff_xref = CrossReference("aff", ("aff-source",), RichText.from_source(aff_mark))
    note_xref = CrossReference("fn", ("note-source",), RichText.from_source(note_mark))
    contributor = Contributor(
        "contributor-source", "author", PersonName(surname, given),
        affiliation_ids=("aff-source",), references=(aff_xref, note_xref),
        child_order=("name", "reference:0", "reference:1"),
    )
    note = Note(
        "note-source", None, None, (RichText.from_source(note_text),),
        "contrib-group", target_ids=("contributor-source",),
    )
    reference = Reference(
        "reference-source", RichText.from_source(label),
        MixedCitation(None, RichText.from_source(ref_text)), ReferenceIdentity(),
    )
    paragraph = Paragraph(
        "paragraph-source",
        RichText((
            Text(lead),
            CrossReference("bibr", ("reference-source",), RichText.from_source(citation)),
            Text(source.text(" ")),
            Styled("italic", RichText.from_source(styled)),
            Text(ending),
        )),
    )
    figure = Figure(
        "figure-source", RichText.from_source(fig_label),
        Caption(None, (Paragraph(None, RichText.from_source(fig_caption)),)),
        ("o1",),
    )
    return SemanticDoc(
        source=source_document, journal=JournalMeta(),
        title=RichText.from_source(title),
        contributor_groups=(ContributorGroup(None, (contributor,)),),
        affiliations=(Affiliation(
            "aff-source", None, RichText.from_source(aff_text)
        ),),
        notes=(note,), body=(paragraph, figure),
        reference_list=ReferenceList(None, (reference,)),
    )


def test_handwritten_semantic_fixture_closes_ids_mixed_content_and_media():
    result = render_v2(_fixture_document())
    root = etree.fromstring(result.xml_bytes)
    identities = {element.get("id") for element in root.xpath("//*[@id]")}
    targets = {
        target
        for value in root.xpath("//@rid")
        for target in value.split()
    }
    assert targets <= identities
    assert len(identities) == len(root.xpath("//@id"))
    assert root.xpath("string(.//article-title)").strip() == "源标题"
    assert not root.xpath(".//article-title/bold")
    assert root.xpath(".//article-title/italic")
    paragraph = root.find(".//body/p")
    assert "".join(paragraph.itertext()) == "See [1] important result."
    assert paragraph.find("italic") is not None
    assert list(result.media.values()) == [b"\x89PNG\r\n\x1a\nfixture"]
    assert any(item.origin_kind == "object" for item in result.provenance)


def test_unknown_semantic_shapes_fail_explicitly():
    with pytest.raises(ValueError, match="不支持的内联样式"):
        Styled("underline", RichText())
    renderer = V2Renderer(_fixture_document())
    with pytest.raises(V2RenderError, match="未支持的块类型"):
        renderer.block(object())


def test_semantic_snapshot_roundtrip_is_versioned_and_keeps_media_bytes():
    original = _fixture_document()
    payload = semantic_to_dict(original)
    restored = semantic_from_dict(json.loads(json.dumps(payload, ensure_ascii=False)))
    assert semantic_to_dict(restored) == payload
    assert render_v2(restored).xml_bytes == render_v2(original).xml_bytes
