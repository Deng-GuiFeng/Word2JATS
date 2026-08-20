from lxml import etree

from word2jats.model.source import (
    BinaryResource, ObjectAnchor, ObjectOccurrence, RunRef, RunSpan,
    SourceDocument, SourceNode, SourcePart,
)
from word2jats.semantic import model as sm
from word2jats.understand.assemble import assemble
from word2jats.understand.merge import Assignment, DocumentAssignment
from word2jats.understand.passes import (
    front_response_failures, head_boundary_response_failures,
)
from word2jats.understand.serialize import serialize
from word2jats.understand.understand import understand
from word2jats.understand.passes import flattened_rows, validate_flattened_layout


class StubLLM:
    provider = "stub"
    model = "fixture"

    def __init__(self):
        self.requests = []

    def request_json(self, system, user, max_tokens=4096, route=None,
                     response_format=None):
        del system, max_tokens
        self.requests.append((route, user))
        if ":citations:" in route or ":head-boundary:" in route:
            assert response_format is not None
            assert response_format["type"] == "json_schema"
        else:
            assert response_format is None
        if ":head-boundary:" in route:
            value = {
                "last_head_node": "doc/p2",
                "first_outside_head_node": "doc/p3",
                "issues": [],
            }
        elif ":body:" in route:
            value = {"blocks": [
                {"nodes": ["doc/p1", "doc/p2"], "role": "front"},
                {"nodes": ["doc/p3"], "role": "section-title", "level": 1},
                {"nodes": ["doc/p4"], "role": "body-paragraph"},
                {"nodes": ["doc/p5"], "role": "reference-title"},
                {"nodes": ["doc/p6"], "role": "reference-entry"},
            ], "objects": [], "figures": [], "tables": [], "formulas": []}
        elif "refs-boundary" in route and "judge" not in route:
            value = {
                "reference_title_node": "doc/p5",
                "entries": [{"head_quote": "Smith J.", "node_hint": "doc/p6"}],
                "first_non_reference_after": None, "non_reference_nodes": [],
            }
        elif "reference-fields" in route:
            value = {
                "structured": True, "publication_type": "journal",
                "label_quote": None,
                "person_groups": [{"kind": "author", "members": [{
                    "member_quote": {"quote": "Smith J.", "node_hint": "doc/p6"},
                    "surname_quote": {"quote": "Smith", "node_hint": "doc/p6"},
                    "given_quote": {"quote": "J", "node_hint": "doc/p6"},
                    "suffix_quote": None,
                }], "etal_quote": None, "child_order": ["person:0"]}],
                "fields": {
                    "article_title": {"quote": "Paper", "node_hint": "doc/p6"},
                    "chapter_title": None,
                    "source": {"quote": "Journal", "node_hint": "doc/p6"},
                    "year": {"quote": "2020a", "node_hint": "doc/p6"},
                    "year_suffix": {"quote": "a", "node_hint": "doc/p6"},
                    "month": None, "day": None, "volume": None, "issue": None,
                    "fpage": None, "lpage": None, "elocation_id": None,
                    "edition": None, "publisher_name": None,
                    "publisher_location": None, "doi": None, "pmid": None,
                    "comments": [],
                },
                "field_order": ["person_group:0", "article_title", "source", "year"],
            }
        elif ":citations:" in route:
            value = {
                "single_target_citations": [],
                "compact_range_citations": [],
                "issues": [],
            }
        else:
            value = {}
        return value, {"route": route, "cache_hit": False, "ok": True}

    def request_text(self, system, user, max_tokens=4096, route=None):
        del system, max_tokens
        self.requests.append((route, user))
        assert ":head-jats:" in route
        return (
            '<article article-type="research-article"><front><article-meta>'
            '<title-group><article-title>Exact title</article-title></title-group>'
            '<contrib-group><contrib contrib-type="author"><name>'
            '<surname>Smith</surname><given-names>John</given-names>'
            '</name></contrib></contrib-group>'
            '</article-meta></front></article>',
            {"route": route, "cache_hit": False, "ok": True},
        )


def _source():
    texts = [
        "Exact title", "John Smith", "Introduction", "Body text.",
        "References", "Smith J. Paper. Journal. 2020a.",
    ]
    nodes = []
    for index, text in enumerate(texts, 1):
        run = RunRef(f"r{index}", "document", f"/p[{index}]")
        nodes.append(SourceNode(
            f"doc/p{index}", "document", "para", None, index - 1, text,
            [RunSpan(0, len(text), run)],
        ))
    return SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )


def test_understand_builds_typed_source_anchored_document():
    llm = StubLLM()
    semantic, meta = understand(_source(), llm)
    assert semantic.visible_title() == ""
    assert semantic.contributor_groups == ()
    assert isinstance(semantic.body[0], sm.Section)
    assert semantic.body[0].blocks[0].content.plain_text(semantic.source) == "Body text."
    citation = semantic.reference_list.references[0].citation
    identity = semantic.reference_list.references[0].identity
    assert isinstance(citation, sm.StructuredCitation)
    assert citation.source.plain_text(semantic.source) == "Journal"
    assert identity.surnames == ("Smith",)
    assert identity.year == "2020a"
    assert identity.year_suffix == "a"
    assert identity.title_key == "Paper"
    assert not meta["blocking"]
    boundary_user = next(
        user for route, user in llm.requests if ":head-boundary:" in route
    )
    metadata_user = next(
        user for route, user in llm.requests if ":head-jats:" in route
    )
    assert "doc/p3" in boundary_user
    assert "doc/p3" not in metadata_user
    assert "doc/p2" in metadata_user


def test_head_boundary_contract_checks_grounding_and_order_only():
    view = serialize(_source())
    keys = tuple(item.key for item in view.records)
    valid = {
        "last_head_node": "doc/p2",
        "first_outside_head_node": "doc/p3",
        "issues": [],
    }
    assert not head_boundary_response_failures(view, valid, keys)

    reversed_boundary = {
        **valid,
        "last_head_node": "doc/p3",
        "first_outside_head_node": "doc/p2",
    }
    assert head_boundary_response_failures(view, reversed_boundary, keys) == [
        "last_head must precede first_outside_head"
    ]


def test_head_prefix_preserves_word_inline_format_and_stops_after_first_budget():
    text = "Nur Adibah Rosland"
    normal = RunRef("r1", "document", "/p[1]")
    superscript = RunRef("r2", "document", "/p[1]/r[2]", superscript=True)
    nodes = [
        SourceNode(
            "doc/p1", "document", "para", None, 0, text,
            [RunSpan(0, len(text) - 1, normal),
             RunSpan(len(text) - 1, len(text), superscript)],
        ),
        SourceNode("doc/p2", "document", "para", None, 1, "x" * 200),
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1", "doc/p2"))], nodes,
    )
    view = serialize(source)
    indices, rendered = view.head_prefix(150)
    assert indices == (0,)
    assert '"text":"Nur Adibah Roslan","styles":[]' in rendered
    assert '"text":"d","styles":["superscript"]' in rendered
    assert "doc/p2" not in rendered


def test_direct_head_jats_is_not_reassembled_from_custom_fields():
    texts = ["Source title", "Ada Able²*", "2 Institute", "* Correspondence: Ada"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    view = serialize(source)
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "front", ()) for node in nodes
    ), (), ())
    result = assemble(
        source, view, {"front_nodes": [item.node_id for item in nodes]},
        {}, (), [], assignment, direct_head=True,
    )
    from word2jats.config import PubConfig
    from word2jats.enrich.journals import JournalRegistry
    from word2jats.render.v2 import render_v2
    from word2jats.semantic.enrich import apply_publication_config
    apply_publication_config(
        result.document, JournalRegistry(), PubConfig("RCM", None, None, False)
    )
    direct = (
        '<article article-type="research-article"><front><article-meta>'
        '<title-group><article-title>Source title</article-title></title-group>'
        '<contrib-group><contrib contrib-type="author"><name>'
        '<surname>Able</surname><given-names>Ada</given-names></name>'
        '<xref ref-type="aff" rid="aff2">²</xref>'
        '<xref ref-type="corresp" rid="cor2">*</xref></contrib></contrib-group>'
        '<aff id="aff2"><label>2</label>Institute</aff>'
        '<author-notes><corresp id="cor2">* Correspondence: Ada</corresp>'
        '</author-notes></article-meta></front></article>'
    )
    rendered = render_v2(result.document, head_jats_xml=direct)
    root = etree.fromstring(rendered.xml_bytes)
    assert root.xpath("string(.//article-title)") == "Source title"
    assert root.xpath("string(.//contrib/xref[@ref-type='aff'])") == "²"
    assert root.xpath("string(.//aff[@id='aff2'])") == "2Institute"
    assert root.xpath("string(.//corresp[@id='cor2'])") == texts[3]
    from word2jats.verify.audit import audit_provenance
    from word2jats.validate.validator import Validator
    assert audit_provenance(rendered.xml_bytes, rendered.provenance, source).ok
    assert Validator().validate_bytes(rendered.xml_bytes).dtd_valid


def test_assembly_preserves_general_complex_semantic_containers():
    texts = [
        "A source title", "Ada Able†#, Bob Baker†", "Editor Eve Stone",
        "† These authors contributed equally.", "\ufffc", "Abbreviations",
        "ABC means an arbitrary concept.", "Figure 1 Shared caption",
        "A. First member", "\ufffc", "B. Second member", "\ufffc",
    ]
    nodes = []
    for index, value in enumerate(texts, 1):
        run = RunRef(f"r{index}", "document", f"/p[{index}]")
        spans = [RunSpan(0, len(value), run)] if value else []
        if index == 2:
            spans = [
                RunSpan(0, 8, run),
                RunSpan(8, 10, RunRef("r2s1", "document", "/p[2]", superscript=True)),
                RunSpan(10, 21, run),
                RunSpan(21, 22, RunRef("r2s2", "document", "/p[2]", superscript=True)),
            ]
        objects = []
        if value == "\ufffc":
            occurrence_number = {5: 1, 10: 2, 12: 3}[index]
            objects = [ObjectAnchor(0, f"o{occurrence_number}")]
        nodes.append(SourceNode(
            f"doc/p{index}", "document", "para", None, index - 1, value,
            spans, objects=objects,
        ))
    occurrences = [
        ObjectOccurrence(f"o{index}", "drawing", f"doc/p{node_index}", 0,
                         resource_id=f"res{index}")
        for index, node_index in enumerate((5, 10, 12), 1)
    ]
    resources = [
        BinaryResource(f"res{index}", f"media/{index}.png", "image/png",
                       b"png" + bytes([index]), "png")
        for index in range(1, 4)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))],
        nodes, occurrences, resources,
    )
    front = {
        "article_type": "research-article",
        "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1"}],
        "authors": [
            {"entity_id": "author:1",
             "author_quote": {"quote": "Ada Able†#", "node_hint": "doc/p2"},
             "given_quote": {"quote": "Ada", "node_hint": "doc/p2"},
             "surname_quote": {"quote": "Able", "node_hint": "doc/p2"},
             "author_comment_quotes": [{"quote": "#", "node_hint": "doc/p2"}]},
            {"entity_id": "author:2",
             "author_quote": {"quote": "Bob Baker†", "node_hint": "doc/p2"},
             "given_quote": {"quote": "Bob", "node_hint": "doc/p2"},
             "surname_quote": {"quote": "Baker", "node_hint": "doc/p2"},
             "author_comment_quotes": []},
        ],
        "editors": [{
            "given_quote": {"quote": "Eve", "node_hint": "doc/p3"},
            "surname_quote": {"quote": "Stone", "node_hint": "doc/p3"},
            "node_hint": "doc/p3",
        }],
        "contributor_notes": [{
            "entity_id": "note:1",
            "marker_quote": {"quote": "†", "node_hint": "doc/p4"},
            "paragraph_quotes": [{"quote": texts[3], "node_hint": "doc/p4"}],
            "kind": "equal",
        }],
        "relations": [
            {"kind": "author-note", "source_id": "author:1",
             "target_id": "note:1",
             "marker_quote": {"quote": "†", "node_hint": "doc/p2"}},
            {"kind": "author-note", "source_id": "author:2",
             "target_id": "note:1",
             "marker_quote": {"quote": "†", "node_hint": "doc/p2"}},
        ],
        "abstracts": [{"kind": "graphical", "source_nodes": ["doc/p5"],
                       "sections": [], "graphics": ["o1"]}],
    }
    body = {
        "figure_groups": [{
            "caption_nodes": ["doc/p8"],
            "label_quote": {"quote": "Figure 1", "node_hint": "doc/p8"},
            "caption_title_quote": {"quote": "Shared caption", "node_hint": "doc/p8"},
            "members": [
                {"caption_nodes": ["doc/p9"],
                 "caption_title_quote": {"quote": texts[8], "node_hint": "doc/p9"},
                 "graphics": ["o2"]},
                {"caption_nodes": ["doc/p11"],
                 "caption_title_quote": {"quote": texts[10], "node_hint": "doc/p11"},
                 "graphics": ["o3"]},
            ],
        }],
        "special_blocks": [{
            "role": "glossary", "container": "back",
            "nodes": ["doc/p6", "doc/p7"],
            "title_quote": {"quote": texts[5], "node_hint": "doc/p6"},
            "paragraph_quotes": [{"quote": texts[6], "node_hint": "doc/p7"}],
            "items": [],
        }],
    }
    roles = {
        **{f"doc/p{index}": "front" for index in range(1, 6)},
        "doc/p6": "glossary", "doc/p7": "glossary",
        "doc/p8": "figure-caption", "doc/p9": "figure-caption",
        "doc/p10": "figure-caption", "doc/p11": "figure-caption",
        "doc/p12": "figure-caption",
        "o1": "graphical-abstract", "o2": "figure", "o3": "figure",
    }
    assignment = DocumentAssignment(tuple(
        Assignment("object" if key.startswith("o") else "node", key, role, ())
        for key, role in roles.items()
    ), (), ())
    result = assemble(source, serialize(source), front, body, (), [], assignment)
    document = result.document
    assert [len(group.contributors) for group in document.contributor_groups] == [2, 1]
    assert document.contributor_groups[0].contributors[0].author_comments
    assert len(document.notes) == 1
    assert [ref.ref_type for contributor in document.contributor_groups[0].contributors
            for ref in contributor.references] == ["fn", "fn"]
    assert document.abstracts[0].blocks[0].content.parts[0].display
    assert isinstance(document.body[0], sm.FigureGroup)
    assert len(document.body[0].figures) == 2
    assert document.back_sections[0].kind == "glossary"
    assert not [item for item in result.issues if item.severity in {"high", "review_blocking"}]


def test_affiliation_marker_uses_exact_source_pointer_without_rewriting():
    texts = ["Source title", "Ada Able²", "2 Institute"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    front = {
        "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1"}],
        "authors": [{
            "entity_id": "author:1",
            "author_quote": {"quote": texts[1], "node_hint": "doc/p2"},
            "given_quote": {"quote": "Ada", "node_hint": "doc/p2"},
            "surname_quote": {"quote": "Able", "node_hint": "doc/p2"},
        }],
        "affiliations": [{
            "entity_id": "affiliation:1",
            "label_quote": {"quote": "2", "node_hint": "doc/p3"},
            "content_quotes": [{"quote": "Institute", "node_hint": "doc/p3"}],
        }],
        "relations": [{
            "kind": "author-affiliation", "source_id": "author:1",
            "target_id": "affiliation:1",
            "marker_quote": {"quote": "²", "node_hint": "doc/p2"},
        }],
    }
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "front", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), front, {}, (), [], assignment)
    contributor = result.document.contributor_groups[0].contributors[0]
    marker = contributor.references[0]
    assert marker.target_ids == ("affiliation:1",)
    assert marker.content.plain_text(source) == "²"
    from word2jats.render.v2 import render_v2
    root = etree.fromstring(render_v2(result.document).xml_bytes)
    assert root.xpath("string(.//contrib/xref[@ref-type='aff'])") == "²"
    assert not root.xpath(".//contrib/xref[@ref-type='aff']/sup")


def test_correspondence_relation_requires_a_grounded_target_entity():
    texts = ["Source title", "Ada Able*", "Contact: repeated", "Contact: repeated"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    front = {
        "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1"}],
        "authors": [{
            "entity_id": "author:1",
            "author_quote": {"quote": texts[1], "node_hint": "doc/p2"},
            "given_quote": {"quote": "Ada", "node_hint": "doc/p2"},
            "surname_quote": {"quote": "Able", "node_hint": "doc/p2"},
        }],
        # 两处相同通讯文字且没有节点提示：指针不唯一，因而不得
        # 构建通讯实体，更不得留下指向虚构实体的关系。
        "correspondences": [{
            "entity_id": "correspondence:1",
            "content_quotes": [{"quote": "Contact: repeated"}],
        }],
        "relations": [{
            "kind": "author-correspondence", "source_id": "author:1",
            "target_id": "correspondence:1",
            "marker_quote": {"quote": "*", "node_hint": "doc/p2"},
        }],
    }
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "front", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), front, {}, (), [], assignment)
    document = result.document
    assert document.correspondence == ()
    assert document.contributor_groups[0].contributors[0].references == ()
    document.validate()


def test_front_entity_relations_do_not_depend_on_printed_labels_or_cardinality():
    texts = [
        "An invented systems study",
        "Nora Moss, Ivo Reed",
        "Laboratory of Open Systems",
        "Contact for Nora: nora.one@example.org; nora.two@example.org",
        "continued at Building Q",
        "Building Q, 17 Harbor Road (Postal code: 10001)",
    ]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )
    front = {
        "article_type": "research-article",
        "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1"}],
        "authors": [
            {
                "entity_id": "person-a",
                "author_quote": {"quote": "Nora Moss", "node_hint": "doc/p2"},
                "given_quote": {"quote": "Nora", "node_hint": "doc/p2"},
                "surname_quote": {"quote": "Moss", "node_hint": "doc/p2"},
                "email_quotes": [
                    {"quote": "nora.one@example.org", "node_hint": "doc/p4"},
                    {"quote": "nora.two@example.org", "node_hint": "doc/p4"},
                ],
            },
            {
                "entity_id": "person-b",
                "author_quote": {"quote": "Ivo Reed", "node_hint": "doc/p2"},
                "given_quote": {"quote": "Ivo", "node_hint": "doc/p2"},
                "surname_quote": {"quote": "Reed", "node_hint": "doc/p2"},
            },
        ],
        "affiliations": [{
            "entity_id": "institution-a", "label_quote": None,
            "content_quotes": [{"quote": texts[2], "node_hint": "doc/p3"}],
        }],
        "addresses": [{
            "entity_id": "place-a", "source_nodes": ["doc/p6"],
            "line_quotes": [{"quote": "Building Q, 17 Harbor Road",
                              "node_hint": "doc/p6"}],
            "postal_quote": {"quote": "10001", "node_hint": "doc/p6"},
            "postal_label_quote": {"quote": "Postal code", "node_hint": "doc/p6"},
            "phone_quote": None, "phone_label_quote": None,
        }],
        "correspondences": [{
            "entity_id": "contact-a", "content_quotes": [
                {"quote": texts[3], "node_hint": "doc/p4"},
                {"quote": texts[4], "node_hint": "doc/p5"},
            ],
        }],
        "contributor_notes": [],
        "relations": [
            {"kind": "author-affiliation", "source_id": "person-a",
             "target_id": "institution-a", "marker_quote": None},
            {"kind": "author-affiliation", "source_id": "person-b",
             "target_id": "institution-a", "marker_quote": None},
            {"kind": "author-correspondence", "source_id": "person-a",
             "target_id": "contact-a", "marker_quote": None},
            {"kind": "author-address", "source_id": "person-a",
             "target_id": "place-a", "marker_quote": None},
            {"kind": "affiliation-address", "source_id": "institution-a",
             "target_id": "place-a", "marker_quote": None},
        ],
    }
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "front", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), front, {}, (), [], assignment)
    authors = result.document.contributor_groups[0].contributors
    assert authors[0].affiliation_ids == ("affiliation:1",)
    assert authors[1].affiliation_ids == ("affiliation:1",)
    assert authors[0].address_ids == ("address:1",)
    assert authors[1].address_ids == ()
    assert len(authors[0].emails) == 2
    assert authors[0].corresponding and not authors[1].corresponding
    assert [ref.ref_type for ref in authors[0].references] == ["aff", "corresp"]
    assert all(not ref.content.parts for ref in authors[0].references)
    assert result.document.correspondence[0].content.plain_text(source) == (
        texts[3] + texts[4]
    )

    from word2jats.render.v2 import render_v2
    from word2jats.validate.validator import Validator
    rendered = render_v2(result.document)
    root = etree.fromstring(rendered.xml_bytes)
    assert root.xpath("count(.//contrib[1]/xref[@ref-type='aff'])") == 1.0
    assert root.xpath("count(.//contrib[1]/xref[@ref-type='corresp'])") == 1.0
    assert root.xpath("count(.//contrib[2]/xref[@ref-type='aff'])") == 1.0
    assert root.xpath("count(.//contrib[2]/address)") == 0.0
    validation = Validator().validate_bytes(rendered.xml_bytes)
    assert not [error for error in validation.errors
                if "xref" in error or "corresp" in error or "address" in error]


def test_front_contract_rejects_unknown_or_mistyped_relation_endpoints():
    text = "Invented title"
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))],
        [SourceNode("doc/p1", "document", "para", None, 0, text)],
    )
    base = {
        "article_type": "research-article",
        "title_quotes": [{"quote": text, "node_hint": "doc/p1"}],
        "authors": [], "affiliations": [], "addresses": [],
        "correspondences": [], "contributor_notes": [],
        "relations": [{
            "kind": "author-affiliation", "source_id": "missing-author",
            "target_id": "missing-affiliation", "marker_quote": None,
        }],
    }
    failures = front_response_failures(base, source)
    assert any("unknown entity" in item for item in failures)


def test_orcid_projection_is_standardized_and_source_auditable():
    texts = ["Invented title", "Ari North", "0000-0002-1825-0097"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )
    front = {
        "article_type": "research-article",
        "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1"}],
        "authors": [{
            "entity_id": "person-a",
            "author_quote": {"quote": texts[1], "node_hint": "doc/p2"},
            "given_quote": {"quote": "Ari", "node_hint": "doc/p2"},
            "surname_quote": {"quote": "North", "node_hint": "doc/p2"},
            "orcid_quote": {"quote": texts[2], "node_hint": "doc/p3"},
        }],
        "relations": [],
    }
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "front", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), front, {}, (), [], assignment)
    from word2jats.render.v2 import render_v2
    from word2jats.verify.audit import audit_provenance
    rendered = render_v2(result.document)
    root = etree.fromstring(rendered.xml_bytes)
    assert root.xpath("string(.//contrib-id[@contrib-id-type='orcid'])") == (
        "https://orcid.org/0000-0002-1825-0097"
    )
    transforms = [item for item in rendered.provenance
                  if item.transform == "orcid-uri"]
    assert len(transforms) == 1
    assert transforms[0].source_ranges == (("doc/p3", 0, 19),)
    assert audit_provenance(rendered.xml_bytes, rendered.provenance, source).ok


def test_unhyphenated_orcid_uses_the_same_standard_projection():
    from word2jats.semantic.normalize import canonical_orcid

    assert canonical_orcid("0000000218250097") == (
        "https://orcid.org/0000-0002-1825-0097"
    )
    assert canonical_orcid("0000000218250098") is None


def test_empty_front_window_and_continuation_addresses_follow_window_contract():
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))],
        [SourceNode("doc/p1", "document", "para", None, 0, "Body text")],
    )
    empty = {
        "article_type": None, "category_quote": None, "title_quotes": [],
        "authors": [], "affiliations": [], "addresses": [],
        "correspondences": [], "dates": {"format": "unknown", "items": []},
        "editors": [], "abstracts": [], "keywords": None,
        "contributor_notes": [], "relations": [], "author_note_quotes": [],
        "front_nodes": [], "body_start_node": None, "issues": [],
    }
    assert front_response_failures(empty, source) == []

    front = dict(empty)
    front.update({
        "article_type": "other",
        "title_quotes": [{
            "quote": "Body text", "node_hint": "doc/p1.2",
            "left_context": "", "right_context": "",
        }],
        "front_nodes": ["doc/p1.2"],
    })
    assert front_response_failures(front, source) == []

    front["title_quotes"] = [{
        "quote": "Body\ntext", "node_hint": "doc/p1",
        "left_context": "", "right_context": "",
    }]
    failures = front_response_failures(front, source)
    assert any("one Q per record" in item for item in failures)


def test_abstract_sections_use_distinct_nonoverlapping_source_spans():
    texts = [
        "Invented title", "Summary",
        "Motive: A small premise. Process: A neutral procedure.",
    ]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )

    def q(quote, node, left="", right=""):
        return {"quote": quote, "node_hint": node,
                "left_context": left, "right_context": right}

    front = {
        "article_type": "research-article", "category_quote": None,
        "title_quotes": [q(texts[0], "doc/p1")], "authors": [],
        "affiliations": [], "addresses": [], "correspondences": [],
        "dates": {"format": "unknown", "items": []}, "editors": [],
        "abstracts": [{
            "kind": "main", "source_nodes": ["doc/p2", "doc/p3"],
            "container_title_quote": q("Summary", "doc/p2"),
            "sections": [{
                "title_quote": q("Motive:", "doc/p3"),
                "paragraph_quotes": [q("Motive: A small premise.", "doc/p3")],
                "wrapped": True,
            }], "graphics": [],
        }],
        "keywords": None, "contributor_notes": [], "relations": [],
        "author_note_quotes": [], "front_nodes": ["doc/p1", "doc/p2", "doc/p3"],
        "body_start_node": None, "issues": [],
    }
    failures = front_response_failures(front, source)
    assert any("distinct source span" in item for item in failures)

    front["abstracts"][0]["sections"] = [
        {
            "title_quote": q("Motive:", "doc/p3"),
            "paragraph_quotes": [q("A small premise.", "doc/p3")],
            "wrapped": True,
        },
        {
            "title_quote": q("Process:", "doc/p3"),
            "paragraph_quotes": [q("A neutral procedure.", "doc/p3")],
            "wrapped": True,
        },
    ]
    assert front_response_failures(front, source) == []


def test_date_components_are_scoped_to_their_date_and_status_is_not_a_year():
    texts = [
        "Invented title",
        "Received: 7 March 2042; Accepted: 19 April 2042",
    ]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )

    def q(quote, left="", right=""):
        return {"quote": quote, "node_hint": "doc/p2",
                "left_context": left, "right_context": right}

    front = {
        "article_type": "research-article", "category_quote": None,
        "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1",
                           "left_context": "", "right_context": ""}],
        "authors": [], "affiliations": [], "addresses": [],
        "correspondences": [], "editors": [], "abstracts": [],
        "keywords": None, "contributor_notes": [], "relations": [],
        "author_note_quotes": [], "front_nodes": ["doc/p1", "doc/p2"],
        "body_start_node": None, "issues": [],
        "dates": {"format": "dmy", "items": [
            {
                "kind": "received",
                "whole_quote": q("Received: 7 March 2042", right="; Accepted"),
                "year_quote": q("2042", left="7 March ", right="; Accepted"),
                "month_quote": q("March", left="Received: 7 ", right=" 2042"),
                "day_quote": q("7", left="Received: ", right=" March"),
            },
            {
                "kind": "accepted",
                "whole_quote": q("Accepted: 19 April 2042", left="2042; "),
                "year_quote": q("2042", left="19 April "),
                "month_quote": q("April", left="Accepted: 19 ", right=" 2042"),
                "day_quote": q("19", left="Accepted: ", right=" April"),
            },
        ]},
    }
    assert front_response_failures(front, source) == []

    front["dates"]["items"][1] = {
        "kind": "accepted", "whole_quote": q("Accepted"),
        "year_quote": q("Accepted"), "month_quote": None, "day_quote": None,
    }
    failures = front_response_failures(front, source)
    assert any("not a decimal calendar year" in item for item in failures)


def test_a_relationship_marker_alone_is_not_correspondence_content():
    texts = ["Invented title", "Ora Lume#"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )

    def q(quote, node, left="", right=""):
        return {"quote": quote, "node_hint": node,
                "left_context": left, "right_context": right}

    front = {
        "article_type": "research-article", "category_quote": None,
        "title_quotes": [q(texts[0], "doc/p1")],
        "authors": [{
            "entity_id": "person-a", "author_quote": q(texts[1], "doc/p2"),
            "surname_quote": q("Lume", "doc/p2"),
            "given_quote": q("Ora", "doc/p2"), "suffix_quote": None,
            "degree_quotes": [], "email_quotes": [], "orcid_quote": None,
            "author_comment_quotes": [],
        }],
        "affiliations": [], "addresses": [],
        "correspondences": [{
            "entity_id": "contact-a",
            "content_quotes": [q("#", "doc/p2", "Ora Lume", "")],
        }],
        "dates": {"format": "unknown", "items": []}, "editors": [],
        "abstracts": [], "keywords": None, "contributor_notes": [],
        "relations": [{
            "kind": "author-correspondence", "source_id": "person-a",
            "target_id": "contact-a",
            "marker_quote": q("#", "doc/p2", "Ora Lume", ""),
        }],
        "author_note_quotes": [], "front_nodes": ["doc/p1", "doc/p2"],
        "body_start_node": None, "issues": [],
    }
    failures = front_response_failures(front, source)
    assert any("no substantive correspondence text" in item for item in failures)


def test_front_context_pointers_resolve_repeated_short_fields_without_guessing():
    texts = ["Invented title", "Ora Lume, D.Sc.; Taro Nix, D.Sc.", "2042/3/31"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )

    def q(quote, node, left="", right=""):
        return {
            "quote": quote, "node_hint": node,
            "left_context": left, "right_context": right,
        }

    front = {
        "article_type": "research-article", "category_quote": None,
        "title_quotes": [q(texts[0], "doc/p1")],
        "authors": [
            {
                "entity_id": "person-a", "author_quote": q("Ora Lume", "doc/p2"),
                "surname_quote": q("Lume", "doc/p2"),
                "given_quote": q("Ora", "doc/p2"), "suffix_quote": None,
                "degree_quotes": [q(
                    "D.Sc.", "doc/p2", "Ora Lume, ", "; Taro"
                )],
                "email_quotes": [], "orcid_quote": None,
                "author_comment_quotes": [],
            },
            {
                "entity_id": "person-b", "author_quote": q("Taro Nix", "doc/p2"),
                "surname_quote": q("Nix", "doc/p2"),
                "given_quote": q("Taro", "doc/p2"), "suffix_quote": None,
                "degree_quotes": [q("D.Sc.", "doc/p2", "Taro Nix, ", "")],
                "email_quotes": [], "orcid_quote": None,
                "author_comment_quotes": [],
            },
        ],
        "affiliations": [], "addresses": [], "correspondences": [],
        "dates": {"format": "ymd", "items": [{
            "kind": "received", "whole_quote": q(texts[2], "doc/p3"),
            "year_quote": q("2042", "doc/p3"),
            "month_quote": q("3", "doc/p3", "2042/", "/31"),
            "day_quote": q("31", "doc/p3", "2042/3/", ""),
        }]},
        "editors": [], "abstracts": [], "keywords": None,
        "contributor_notes": [], "relations": [], "author_note_quotes": [],
        "front_nodes": ["doc/p1", "doc/p2", "doc/p3"],
        "body_start_node": None, "issues": [],
    }
    assert front_response_failures(front, source) == []
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "front", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), front, {}, (), [], assignment)
    authors = result.document.contributor_groups[0].contributors
    assert [item.degrees[0].ranges[0][1:] for item in authors] == [(10, 15), (27, 32)]
    date = result.document.dates[0]
    assert date.month.ranges == (("doc/p3", 5, 6),)
    assert date.day.ranges == (("doc/p3", 7, 9),)


def test_relation_marker_context_may_extend_beyond_author_but_marker_may_not():
    texts = ["Invented title", "Ari Vale*, Nia Holt", "Open Methods Institute"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )

    def q(quote, node, left="", right=""):
        return {"quote": quote, "node_hint": node,
                "left_context": left, "right_context": right}

    front = {
        "article_type": "research-article", "category_quote": None,
        "title_quotes": [q(texts[0], "doc/p1")],
        "authors": [{
            "entity_id": "person-a", "author_quote": q("Ari Vale*", "doc/p2"),
            "surname_quote": q("Vale", "doc/p2"),
            "given_quote": q("Ari", "doc/p2"), "suffix_quote": None,
            "degree_quotes": [], "email_quotes": [], "orcid_quote": None,
            "author_comment_quotes": [],
        }],
        "affiliations": [{
            "entity_id": "institution-a", "label_quote": None,
            "content_quotes": [q(texts[2], "doc/p3")],
        }],
        "addresses": [], "correspondences": [],
        "dates": {"format": "unknown", "items": []}, "editors": [],
        "abstracts": [], "keywords": None, "contributor_notes": [],
        "relations": [{
            "kind": "author-affiliation", "source_id": "person-a",
            "target_id": "institution-a",
            "marker_quote": q("*", "doc/p2", "Ari Vale", ", Nia"),
        }],
        "author_note_quotes": [], "front_nodes": ["doc/p1", "doc/p2", "doc/p3"],
        "body_start_node": None, "issues": [],
    }
    assert front_response_failures(front, source) == []
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "front", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), front, {}, (), [], assignment)
    reference = result.document.contributor_groups[0].contributors[0].references[0]
    assert reference.content.plain_text(source) == "*"
    assert reference.source_occurrence == ("doc/p2", 8, 9)

    front["relations"][0]["marker_quote"] = q(",", "doc/p2", "Ari Vale*", " Nia")
    failures = front_response_failures(front, source)
    assert any("relations[0].marker_quote" in item for item in failures)


def test_front_candidate_cannot_bypass_the_global_primary_role():
    texts = ["Source title", "Conflicts of Interest", "The authors declare none."]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    assignment = DocumentAssignment((
        Assignment("node", "doc/p1", "front", ()),
        Assignment("node", "doc/p2", "declaration", ()),
        Assignment("node", "doc/p3", "declaration", ()),
    ), (), ())
    result = assemble(
        source, serialize(source), {
            "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1"}],
            "author_note_quotes": [
                {"quote": texts[2], "node_hint": "doc/p3"}
            ],
        }, {}, (), [], assignment,
    )
    assert result.document.author_note_paragraphs == ()
    assert any(item.code == "FRONT_POINTER_NOT_SELECTED" for item in result.issues)


def test_repeated_table_notes_are_allocated_in_source_order():
    nodes = []
    order = 0
    for table_number in (1, 2):
        table_id = f"doc/tbl{table_number}"
        row_id = f"{table_id}/r1"
        cell_id = f"{row_id}/c1"
        nodes.extend([
            SourceNode(table_id, "document", "table", None, order),
            SourceNode(row_id, "document", "row", table_id, order + 1),
            SourceNode(cell_id, "document", "cell", row_id, order + 2),
            SourceNode(f"{cell_id}/p1", "document", "para", cell_id,
                       order + 3, f"value {table_number}"),
        ])
        order += 4
        nodes.append(SourceNode(
            f"doc/pnote{table_number}", "document", "para", None, order,
            "Repeated note",
        ))
        order += 1
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    assignments = []
    for node in nodes:
        role = "table-footnote" if node.node_id.startswith("doc/pnote") else "table"
        assignments.append(Assignment("node", node.node_id, role, ()))
    body = {"tables": [
        {"table_node": "doc/tbl1", "header_rows": 0,
         "footnote_nodes": ["doc/pnote1"],
         "footnotes": [{"paragraphs": [
             {"content_quotes": ["Repeated note"]},
         ]}]},
        {"table_node": "doc/tbl2", "header_rows": 0,
         "footnote_nodes": ["doc/pnote2"],
         "footnotes": [{"paragraphs": [
             {"content_quotes": ["Repeated note"]},
         ]}]},
    ]}
    result = assemble(
        source, serialize(source), {}, body, (), [],
        DocumentAssignment(tuple(assignments), (), ()),
    )
    tables = [item for item in result.document.body if isinstance(item, sm.TableBlock)]
    assert [table.notes[0].paragraphs[0].parts[0].source.ranges[0][0]
            for table in tables] == ["doc/pnote1", "doc/pnote2"]
    assert not [item for item in result.issues if item.code == "TABLE_NOTES_UNRESOLVED"]


def test_unstructured_abstract_cannot_create_a_titleless_sec():
    texts = ["Article title", "One unstructured abstract paragraph."]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    front = {
        "title_quotes": [{"quote": texts[0], "node_hint": "doc/p1"}],
        "abstracts": [{
            "kind": "main", "source_nodes": ["doc/p2"],
            "sections": [{
                "title_quote": None,
                "paragraph_quotes": [{"quote": texts[1], "node_hint": "doc/p2"}],
                "wrapped": True,
            }],
        }],
    }
    assignment = DocumentAssignment((
        Assignment("node", "doc/p1", "front", ()),
        Assignment("node", "doc/p2", "front", ()),
    ), (), ())
    result = assemble(source, serialize(source), front, {}, (), [], assignment)
    assert result.document.abstracts[0].sections[0].wrapped is False
    assert any(item.code == "ABSTRACT_SECTION_TITLE_UNRESOLVED"
               for item in result.issues)


def test_root_level_glossary_is_placed_in_a_legal_back_container():
    texts = ["Article title", "Abbreviations", "ABC means arbitrary concept."]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    body = {"special_blocks": [{
        "role": "glossary", "container": "body",
        "nodes": ["doc/p2", "doc/p3"],
        "title_quote": {"quote": texts[1], "node_hint": "doc/p2"},
        "paragraph_quotes": [{"quote": texts[2], "node_hint": "doc/p3"}],
        "items": [],
    }]}
    assignment = DocumentAssignment((
        Assignment("node", "doc/p1", "front", ()),
        Assignment("node", "doc/p2", "glossary", ()),
        Assignment("node", "doc/p3", "glossary", ()),
    ), (), ())
    result = assemble(source, serialize(source), {}, body, (), [], assignment)
    assert not result.document.body
    assert result.document.back_sections[0].kind == "glossary"
    assert any(item.code == "TOP_LEVEL_GLOSSARY_MOVED_TO_BACK"
               for item in result.issues)


def test_body_graphical_abstract_judgment_is_not_lost_when_front_omits_it():
    nodes = [
        SourceNode("doc/p1", "document", "para", None, 0, "Article title"),
        SourceNode("doc/p2", "document", "para", None, 1, "\ufffc",
                   objects=[ObjectAnchor(0, "o1")]),
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1", "doc/p2"))], nodes,
        [ObjectOccurrence("o1", "image", "doc/p2", 0, resource_id="res1")],
        [BinaryResource("res1", "word/media/graph.png", "image/png", b"png", "png")],
    )
    assignment = DocumentAssignment((
        Assignment("node", "doc/p1", "front", ()),
        Assignment("node", "doc/p2", "front", ()),
        Assignment("object", "o1", "graphical-abstract", ()),
    ), (), ())
    result = assemble(
        source, serialize(source), {
            "title_quotes": [{"quote": "Article title", "node_hint": "doc/p1"}],
            "abstracts": [],
        }, {}, (), [], assignment,
    )
    assert len(result.document.abstracts) == 1
    assert result.document.abstracts[0].kind == "graphical"
    graphic = result.document.abstracts[0].blocks[0].content.parts[0]
    assert isinstance(graphic, sm.InlineGraphic) and graphic.display


def test_validated_flattened_table_assembles_only_from_source_ranges():
    texts = ["Left heading\tRight heading", "alpha\tbeta"]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1", "doc/p2"))], nodes,
    )
    rows = flattened_rows(serialize(source), ["doc/p1", "doc/p2"])
    response = {
        "resolved": True, "n_rows": 2, "n_cols": 2, "header_rows": 1,
        "issues": [],
        "cells": [
            {"row": row.index + 1, "column": segment.segment_index + 1,
             "rowspan": 1, "colspan": 1, "row_header": False,
             "segment_ids": [segment.segment_id]}
            for row in rows for segment in row.segments
        ],
    }
    layout, failures = validate_flattened_layout(rows, response)
    assert not failures
    body = {"tables": [{
        "flattened_row_nodes": ["doc/p1", "doc/p2"],
        "flattened_layout": layout,
    }]}
    assignment = DocumentAssignment((
        Assignment("node", "doc/p1", "table", ()),
        Assignment("node", "doc/p2", "table", ()),
    ), (), ())
    result = assemble(source, serialize(source), {}, body, (), [], assignment)
    table = next(item for item in result.document.body if isinstance(item, sm.TableBlock))
    assert [cell.content.plain_text(source) for cell in table.header_rows[0].cells] == [
        "Left heading", "Right heading",
    ]
    assert [cell.content.plain_text(source) for cell in table.body_rows[0].cells] == [
        "alpha", "beta",
    ]
    assert not [item for item in result.issues
                if item.code == "FLATTENED_TABLE_UNRESOLVED"]


def test_flattened_grid_supports_joined_fragments_and_source_backed_colspan():
    texts = [
        "Factor\telective\t (%)\turgent (%)",
        "Clinical group",
        "measure\t12\t19",
    ]
    nodes = [
        SourceNode(f"doc/p{index}", "document", "para", None, index - 1, text)
        for index, text in enumerate(texts, 1)
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(node.node_id for node in nodes))], nodes,
    )
    rows = flattened_rows(serialize(source), ["doc/p1", "doc/p2", "doc/p3"])
    response = {
        "resolved": True, "n_rows": 3, "n_cols": 3, "header_rows": 1,
        "cells": [
            {"row": 1, "column": 1, "rowspan": 1, "colspan": 1,
             "row_header": False, "segment_ids": ["r0s0"]},
            {"row": 1, "column": 2, "rowspan": 1, "colspan": 1,
             "row_header": False, "segment_ids": ["r0s1", "r0s2"]},
            {"row": 1, "column": 3, "rowspan": 1, "colspan": 1,
             "row_header": False, "segment_ids": ["r0s3"]},
            {"row": 2, "column": 1, "rowspan": 1, "colspan": 3,
             "row_header": False, "segment_ids": ["r1s0"]},
            *[
                {"row": 3, "column": index + 1, "rowspan": 1, "colspan": 1,
                 "row_header": index == 0, "segment_ids": [f"r2s{index}"]}
                for index in range(3)
            ],
        ],
        "issues": [],
    }
    layout, failures = validate_flattened_layout(rows, response)
    assert failures == []
    body = {"tables": [{
        "flattened_row_nodes": ["doc/p1", "doc/p2", "doc/p3"],
        "flattened_layout": layout,
    }]}
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "table", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), {}, body, (), [], assignment)
    table = next(item for item in result.document.body if isinstance(item, sm.TableBlock))
    assert [cell.content.plain_text(source) for cell in table.header_rows[0].cells] == [
        "Factor", "elective (%)", "urgent (%)",
    ]
    assert len(table.body_rows[0].cells) == 1
    assert table.body_rows[0].cells[0].colspan == 3
    assert table.body_rows[0].cells[0].content.plain_text(source) == "Clinical group"
    assert table.body_rows[1].cells[0].header_kind == "row"


def test_native_table_prefers_ooxml_header_and_uses_explicit_row_header_cell():
    nodes = [
        SourceNode("doc/tbl1", "document", "table", None, 0),
        SourceNode("doc/tbl1/r1", "document", "row", "doc/tbl1", 1,
                   properties={"header": True}),
        SourceNode("doc/tbl1/r1/c1", "document", "cell", "doc/tbl1/r1", 2),
        SourceNode("doc/tbl1/r1/c1/p1", "document", "para",
                   "doc/tbl1/r1/c1", 3, "Heading"),
        SourceNode("doc/tbl1/r2", "document", "row", "doc/tbl1", 4,
                   properties={"header": False}),
        SourceNode("doc/tbl1/r2/c1", "document", "cell", "doc/tbl1/r2", 5),
        SourceNode("doc/tbl1/r2/c1/p1", "document", "para",
                   "doc/tbl1/r2/c1", 6, "Row label"),
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    body = {"tables": [{
        "table_node": "doc/tbl1", "header_rows": 0,
        "row_header_cells": [{"row": 2, "column": 1}],
    }]}
    assignment = DocumentAssignment(tuple(
        Assignment("node", node.node_id, "table", ()) for node in nodes
    ), (), ())
    result = assemble(source, serialize(source), {}, body, (), [], assignment)
    table = next(item for item in result.document.body if isinstance(item, sm.TableBlock))
    assert len(table.header_rows) == 1
    assert table.header_rows[0].cells[0].header_kind == "col"
    assert table.body_rows[0].cells[0].header_kind == "row"
