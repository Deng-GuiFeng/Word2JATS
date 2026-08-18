from dataclasses import dataclass

from word2jats.model.source import (
    OBJECT_REPLACEMENT, ObjectAnchor, ObjectOccurrence,
    SourceDocument, SourceNode, SourcePart,
)
from word2jats.understand.merge import (
    build_reference_spans, grounded_heads, merge_assignments,
    reconcile_boundaries,
)
from word2jats.understand.passes import (
    ReferenceInput, UnderstandConfig, body_contract_failures,
    flattened_rows, flattened_tables_pass,
    make_windows, reference_fields_pass, reference_contract_failures,
    run_windowed, validate_flattened_layout,
)
from word2jats.understand.serialize import serialize


def _source(texts):
    nodes = [SourceNode(f"doc/p{i + 1}", "document", "para", None, i, value)
             for i, value in enumerate(texts)]
    return SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )


def test_reference_heads_can_share_one_source_paragraph():
    source = _source(["Body", "References", "Smith 2020. Title. Jones 2021. Other."])
    view = serialize(source)
    payload = {
        "reference_title_node": "doc/p2",
        "entries": [
            {"head_quote": "Smith 2020", "node_hint": "doc/p3"},
            {"head_quote": "Jones 2021", "node_hint": "doc/p3"},
        ],
        "first_non_reference_after": None,
    }
    assert grounded_heads(payload, source) == [
        ("doc/p3", 0, 10), ("doc/p3", 19, 29),
    ]
    spans = build_reference_spans(view, payload)
    assert [item.text(source) for item in spans] == [
        "Smith 2020. Title. ", "Jones 2021. Other.",
    ]


def test_windows_have_disjoint_centers_and_overlapping_context():
    source = _source([f"paragraph {i} " + "x" * 40 for i in range(20)])
    view = serialize(source)
    windows = make_windows(view, UnderstandConfig(
        input_token_budget=120, boundary_token_budget=20
    ))
    centers = [index for window in windows for index in window.center_indices]
    assert centers == list(range(len(view.records)))
    assert len(windows) > 1
    assert any(set(left.context_indices) & set(right.context_indices)
               for left, right in zip(windows, windows[1:]))


def test_empty_json_response_is_reasked_once():
    class EmptyThenValid:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            return ({} if self.calls == 1 else {"blocks": [{"nodes": ["doc/p1"],
                                                              "role": "body-paragraph"}]}), {}

    llm = EmptyThenValid()
    result = run_windowed(
        serialize(_source(["Body"])), llm, task="body-test",
        prompt_version="fixture", system="return json", config=UnderstandConfig(),
    )
    assert llm.calls == 2
    assert result.combined()["blocks"][0]["role"] == "body-paragraph"


def test_body_contract_reasks_when_native_table_is_omitted():
    nodes = [
        SourceNode("doc/p1", "document", "para", None, 0, "Results"),
        SourceNode("doc/tbl1", "document", "table", None, 1),
        SourceNode("doc/tbl1/r1", "document", "row", "doc/tbl1", 2),
        SourceNode("doc/tbl1/r1/c1", "document", "cell", "doc/tbl1/r1", 3),
        SourceNode("doc/tbl1/r1/c1/p1", "document", "para",
                   "doc/tbl1/r1/c1", 4, "Value"),
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )

    class MissingThenComplete:
        def __init__(self):
            self.users = []

        def request_json(self, system, user, max_tokens, route):
            del system, max_tokens, route
            self.users.append(user)
            if len(self.users) == 1:
                return {"blocks": [{"nodes": ["doc/p1"],
                                     "role": "section-title"}],
                        "objects": [], "tables": []}, {}
            return {
                "blocks": [
                    {"nodes": ["doc/p1"], "role": "section-title"},
                    {"nodes": ["doc/tbl1"], "role": "table"},
                ],
                "objects": [],
                "tables": [{"table_node": "doc/tbl1", "header_rows": 1}],
            }, {}

    llm = MissingThenComplete()
    result = run_windowed(
        serialize(source), llm, task="body", prompt_version="fixture",
        system="return json", config=UnderstandConfig(),
    )
    assert len(llm.users) == 2
    assert "doc/tbl1" in llm.users[1]
    assert result.combined()["tables"][0]["table_node"] == "doc/tbl1"


def test_body_contract_reasks_when_table_spec_conflicts_with_paragraph_block():
    nodes = [
        SourceNode("doc/p1", "document", "para", None, 0, "Results"),
        SourceNode("doc/tbl1", "document", "table", None, 1),
        SourceNode("doc/tbl1/r1", "document", "row", "doc/tbl1", 2),
        SourceNode("doc/tbl1/r1/c1", "document", "cell", "doc/tbl1/r1", 3),
        SourceNode("doc/tbl1/r1/c1/p1", "document", "para",
                   "doc/tbl1/r1/c1", 4, "Value"),
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )

    class ContradictionThenCorrection:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            role = "body-paragraph" if self.calls == 1 else "table"
            return {
                "blocks": [
                    {"nodes": ["doc/p1"], "role": "section-title"},
                    {"nodes": ["doc/tbl1|表"], "role": role},
                ],
                "objects": [],
                "tables": [{"table_node": "doc/tbl1|表", "header_rows": 1}],
            }, {}

    llm = ContradictionThenCorrection()
    result = run_windowed(
        serialize(source), llm, task="body", prompt_version="fixture",
        system="return json", config=UnderstandConfig(),
    )
    assert llm.calls == 2
    assert next(block for block in result.combined()["blocks"]
                if "doc/tbl1|表" in block["nodes"])["role"] == "table"


def test_body_contract_requires_complex_roles_to_have_assembly_specs():
    text = "Figure caption" + OBJECT_REPLACEMENT
    node = SourceNode(
        "doc/p1", "document", "para", None, 0, text,
        objects=[ObjectAnchor(len(text) - 1, "o1")],
    )
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=("doc/p1",))], [node],
        [ObjectOccurrence("o1", "image", "doc/p1", len(text) - 1)],
    )

    class MissingThenSpecified:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            result = {
                "blocks": [{"nodes": ["doc/p1"], "role": "figure-caption"}],
                "objects": [{"occurrence_id": "o1", "role": "figure"}],
                "tables": [], "figures": [],
            }
            if self.calls == 2:
                result["figures"] = [{
                    "caption_nodes": ["doc/p1"], "graphics": ["o1"],
                }]
            return result, {}

    llm = MissingThenSpecified()
    result = run_windowed(
        serialize(source), llm, task="body", prompt_version="fixture",
        system="return json", config=UnderstandConfig(),
    )
    assert llm.calls == 2
    assert result.combined()["figures"][0]["graphics"] == ["o1"]


def test_body_contract_rejects_declaration_without_disjoint_title_and_content():
    source = _source(["Funding", "The study had no external funding."])
    view = serialize(source)
    window = make_windows(view, UnderstandConfig())[0]
    response = {
        "blocks": [{
            "nodes": ["doc/p1", "doc/p2"], "role": "declaration",
            "title_quote": {"quote": "Funding", "node_hint": "doc/p1"},
            "content_nodes": ["doc/p1"],
        }],
        "objects": [], "tables": [],
    }
    failures = body_contract_failures(view, window, response)
    assert "declaration title node is repeated in content_nodes" in failures


def test_body_retry_does_not_erase_regions_that_were_valid_in_first_answer():
    nodes = [
        SourceNode("doc/p1", "document", "para", None, 0, "Before"),
        SourceNode("doc/p2", "document", "para", None, 1, "After"),
        SourceNode("doc/tbl1", "document", "table", None, 2),
        SourceNode("doc/tbl1/r1", "document", "row", "doc/tbl1", 3),
        SourceNode("doc/tbl1/r1/c1", "document", "cell", "doc/tbl1/r1", 4),
        SourceNode("doc/tbl1/r1/c1/p1", "document", "para",
                   "doc/tbl1/r1/c1", 5, "Value"),
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )

    class ComplementaryAnswers:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            if self.calls == 1:
                return {
                    "blocks": [
                        {"nodes": ["doc/p1"], "role": "body-paragraph"},
                        {"nodes": ["doc/tbl1|表"], "role": "table"},
                    ],
                    "objects": [],
                    "tables": [{"table_node": "doc/tbl1|表", "header_rows": 1}],
                }, {}
            # 重问补了 p2，却遗忘复述首答中的表。
            return {
                "blocks": [
                    {"nodes": ["doc/p1", "doc/p2"], "role": "body-paragraph"},
                ],
                "objects": [], "tables": [],
            }, {}

    result = run_windowed(
        serialize(source), ComplementaryAnswers(), task="body",
        prompt_version="fixture", system="return json", config=UnderstandConfig(),
    )
    body = result.combined()
    assert {raw for block in body["blocks"] for raw in block["nodes"]} == {
        "doc/p1", "doc/p2", "doc/tbl1|表",
    }
    assert body["tables"][0]["table_node"] == "doc/tbl1|表"


def test_body_retry_replaces_same_flattened_table_when_boundary_is_corrected():
    source = _source([
        "Table 7: outcome", "heading\tvalue", "group\t3", "legend text",
    ])
    view = serialize(source)
    previous = {
        "blocks": [
            {"nodes": ["doc/p1"], "role": "table-caption"},
            {"nodes": ["doc/p2", "doc/p3", "doc/p4"], "role": "table"},
        ],
        "tables": [{
            "caption_nodes": ["doc/p1"],
            "flattened_row_nodes": ["doc/p2", "doc/p3", "doc/p4"],
        }],
    }
    corrected = {
        "blocks": [
            {"nodes": ["doc/p1"], "role": "table-caption"},
            {"nodes": ["doc/p2", "doc/p3"], "role": "table"},
            {"nodes": ["doc/p4"], "role": "table-footnote"},
        ],
        "tables": [{
            "caption_nodes": ["doc/p1"],
            "flattened_row_nodes": ["doc/p2", "doc/p3"],
            "footnote_nodes": ["doc/p4"],
        }],
    }
    from word2jats.understand.passes import merge_body_retry
    merged = merge_body_retry(view, previous, corrected)
    assert len(merged["tables"]) == 1
    assert merged["tables"][0]["flattened_row_nodes"] == ["doc/p2", "doc/p3"]
    assert merged["tables"][0]["footnote_nodes"] == ["doc/p4"]


def test_flattened_layout_notes_do_not_invalidate_complete_resolved_grid():
    source = _source(["Heading A\tHeading B", "row\tvalue"])
    rows = flattened_rows(serialize(source), ["doc/p1", "doc/p2"])
    response = {
        "resolved": True,
        "n_rows": 2, "n_cols": 2, "header_rows": 1,
        "cells": [
            {"row": row.index + 1, "column": segment.segment_index + 1,
             "rowspan": 1, "colspan": 1,
             "row_header": row.index == 1 and segment.segment_index == 0,
             "segment_ids": [segment.segment_id]}
            for row in rows for segment in row.segments
        ],
        "issues": ["The second logical column is sparse."],
    }
    layout, failures = validate_flattened_layout(rows, response)
    assert failures == []
    assert layout["valid"] is True


def test_flattened_layout_explicitly_unresolved_is_not_actionable():
    source = _source(["ambiguous\tcontent"])
    rows = flattened_rows(serialize(source), ["doc/p1"])
    response = {
        "resolved": False,
        "n_rows": 1, "n_cols": 2, "header_rows": 0,
        "cells": [
            {"row": 1, "column": segment.segment_index + 1,
             "rowspan": 1, "colspan": 1, "row_header": False,
             "segment_ids": [segment.segment_id]}
            for row in rows for segment in row.segments
        ],
        "issues": ["Two grids remain possible."],
    }
    layout, failures = validate_flattened_layout(rows, response)
    assert "resolved must be true for an actionable table layout" in failures
    assert layout["valid"] is False


def test_failed_body_reask_cannot_erase_a_better_first_answer():
    class PartialThenUnavailable:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            if self.calls == 1:
                return {
                    "blocks": [{"nodes": ["doc/p1"], "role": "body-paragraph"}],
                    "objects": [], "tables": [],
                }, {}
            return {}, {}

    source = _source(["First", "Second"])
    result = run_windowed(
        serialize(source), PartialThenUnavailable(), task="body",
        prompt_version="fixture", system="return json", config=UnderstandConfig(),
    )
    assert result.combined()["blocks"] == [
        {"nodes": ["doc/p1"], "role": "body-paragraph"},
    ]


def test_reference_reask_failure_cannot_erase_structured_first_answer():
    class PartialThenUnavailable:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            if self.calls == 1:
                return {
                    "structured": True,
                    "person_groups": [],
                    "fields": {"article_title": "Changed title"},
                }, {}
            return {}, {}

    result = reference_fields_pass((ReferenceInput(
        1, "[doc/p1] Exact title. Journal. 2024."
    ),), PartialThenUnavailable())
    assert result[0][1]["fields"]["article_title"] == "Changed title"


def test_reference_boundary_agreement_is_defined_by_start_not_quote_length():
    source = _source(["References", "Smith 2020. A title."])
    view = serialize(source)

    class MustNotBeCalled:
        def request_json(self, *args, **kwargs):
            raise AssertionError("same boundary starts must not be adjudicated")

    left = {"entries": [
        {"head_quote": "Smith 2020", "node_hint": "doc/p2"},
    ]}
    right = {"entries": [
        {"head_quote": "Smith 2020. A title", "node_hint": "doc/p2"},
    ]}
    chosen, issues, audit = reconcile_boundaries(
        view, MustNotBeCalled(), left, right,
    )
    assert chosen["entries"] == left["entries"]
    assert not issues
    assert not audit


def test_ungrounded_boundary_claim_cannot_overrule_grounded_evidence():
    source = _source(["References", "Smith 2020. A title."])
    view = serialize(source)

    class MustNotBeCalled:
        def request_json(self, *args, **kwargs):
            raise AssertionError("an invalid quote is not contrary boundary evidence")

    left = {"entries": [
        {"head_quote": "Smith 2020", "node_hint": "doc/p2"},
    ]}
    right = {"entries": [
        {"head_quote": "Smyth 2020", "node_hint": "doc/p2"},
    ]}
    chosen, issues, audit = reconcile_boundaries(
        view, MustNotBeCalled(), left, right,
    )
    assert chosen["entries"] == left["entries"]
    assert not issues
    assert not audit


def test_serialized_native_table_address_claims_all_source_descendants():
    nodes = [
        SourceNode("doc/tbl7", "document", "table", None, 0),
        SourceNode("doc/tbl7/r1", "document", "row", "doc/tbl7", 1),
        SourceNode("doc/tbl7/r1/c1", "document", "cell", "doc/tbl7/r1", 2),
        SourceNode("doc/tbl7/r1/c1/p1", "document", "para",
                   "doc/tbl7/r1/c1", 3, "arbitrary cell text"),
    ]
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )
    assignment = merge_assignments(
        serialize(source), {},
        {"blocks": [{"nodes": ["doc/tbl7|表"], "role": "table"}]},
        {}, (), None,
    )
    assert not assignment.issues
    assert assignment.role("doc/tbl7") == "table"
    assert assignment.role("doc/tbl7/r1/c1/p1") == "table"


def test_display_spec_role_conflict_reaches_global_adjudication():
    source = _source(["A caption paragraph"])

    class Judge:
        def __init__(self):
            self.conflicts = None

        def request_json(self, system, user, max_tokens, route):
            del system, max_tokens, route
            import json
            payload = user.split("CONFLICTS:\n", 1)[1].split(
                "\n\nReturn strict JSON now.", 1
            )[0]
            self.conflicts = json.loads(payload)
            return {"decisions": [
                {"source_id": "doc/p1", "role": "figure-caption"},
            ], "unresolved": []}, {}

    judge = Judge()
    assignment = merge_assignments(
        serialize(source), {}, {
            "blocks": [{"nodes": ["doc/p1"], "role": "body-paragraph"}],
            "figures": [{"caption_nodes": ["doc/p1"], "graphics": []}],
        }, {}, (), judge,
    )
    assert judge.conflicts[0]["roles"] == ["body-paragraph", "figure-caption"]
    assert assignment.role("doc/p1") == "figure-caption"
    assert not assignment.issues


def test_nonempty_decorative_discard_requires_an_independent_approval():
    source = _source(["A visible publisher ornament"])

    class Reviewer:
        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens
            assert route == "v2:discard-review:discard-v1.0"
            return {
                "approved": [{"source_id": "doc/p1", "reason": "ornament"}],
                "unresolved": [], "issues": [],
            }, {}

    assignment = merge_assignments(
        serialize(source), {}, {
            "blocks": [{"nodes": ["doc/p1"], "role": "decorative"}],
        }, {}, (), Reviewer(),
    )
    item = next(value for value in assignment.assignments
                if value.source_id == "doc/p1")
    assert "discard-review:approved" in item.evidence
    assert not assignment.issues


def test_nonempty_decorative_discard_stays_blocking_without_approval():
    source = _source(["Possibly meaningful text"])

    class Reviewer:
        def request_json(self, *args, **kwargs):
            return {"approved": [], "unresolved": ["doc/p1"]}, {}

    assignment = merge_assignments(
        serialize(source), {}, {
            "blocks": [{"nodes": ["doc/p1"], "role": "decorative"}],
        }, {}, (), Reviewer(),
    )
    assert any(item.code == "NONEMPTY_DISCARD_UNRESOLVED"
               for item in assignment.issues)


def test_reference_field_nonverbatim_quote_is_reasked():
    class CorrectsTypo:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            title = "Changed spelling" if self.calls == 1 else "Exact spelling"
            return {
                "structured": True, "publication_type": "journal",
                "person_groups": [],
                "fields": {
                    "article_title": {"quote": title, "node_hint": "doc/p9"},
                    "source": {"quote": "Journal", "node_hint": "doc/p9"},
                    "year": {"quote": "2024", "node_hint": "doc/p9"},
                },
            }, {}

    llm = CorrectsTypo()
    results = reference_fields_pass((ReferenceInput(
        1, "[doc/p9] Exact spelling. Journal. 2024."
    ),), llm)
    assert llm.calls == 2
    assert results[0][1]["fields"]["article_title"]["quote"] == "Exact spelling"
    assert results[0][2][0]["contract_failures"]
    assert results[0][2][1]["contract_failures"] == []


def test_bounded_reference_accepts_verbatim_bare_quote_without_node_hint():
    response = {
        "structured": True, "publication_type": "journal", "person_groups": [],
        "fields": {"article_title": "Exact title"},
    }
    assert reference_contract_failures(
        "[doc/p4] Exact title. Journal. 2024.", response
    ) == []


def test_flattened_table_segments_have_stable_source_ranges_across_soft_lines():
    source = _source(["Head A\tHead B\nrow a\trow b"])
    rows = flattened_rows(serialize(source), ["doc/p1", "doc/p1.2"])
    assert [(row.node_id, row.start, row.end) for row in rows] == [
        ("doc/p1", 0, 13), ("doc/p1", 14, 25),
    ]
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
    assert not failures and layout["valid"]
    assert layout["rows"][1]["cells"] == [
        {"column": 1, "rowspan": 1, "colspan": 1, "row_header": False,
         "ranges": [["doc/p1", 14, 19]]},
        {"column": 2, "rowspan": 1, "colspan": 1, "row_header": False,
         "ranges": [["doc/p1", 20, 25]]},
    ]


def test_flattened_table_assignment_reasks_non_monotone_or_incomplete_mapping():
    source = _source(["left\tright", "a\tb"])
    body = {"tables": [{
        "table_node": None, "graphic": None,
        "flattened_row_nodes": ["doc/p1", "doc/p2"],
    }]}

    class CorrectsMapping:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            if self.calls == 1:
                return {
                    "resolved": True, "n_rows": 2, "n_cols": 2,
                    "header_rows": 1,
                    "cells": [
                        {"row": 1, "column": 2, "rowspan": 1, "colspan": 1,
                         "row_header": False, "segment_ids": ["r0s0"]},
                        {"row": 1, "column": 1, "rowspan": 1, "colspan": 1,
                         "row_header": False, "segment_ids": ["r0s1"]},
                    ], "issues": [],
                }, {}
            return {
                "resolved": True, "n_rows": 2, "n_cols": 2,
                "header_rows": 1,
                "cells": [
                    {"row": row + 1, "column": segment + 1,
                     "rowspan": 1, "colspan": 1, "row_header": False,
                     "segment_ids": [f"r{row}s{segment}"]}
                    for row in range(2) for segment in range(2)
                ], "issues": [],
            }, {}

    llm = CorrectsMapping()
    result = flattened_tables_pass(serialize(source), body, llm)
    assert llm.calls == 2
    assert result[0][1]["valid"]
    assert result[0][2][0]["contract_failures"]
    assert result[0][2][1]["contract_failures"] == []


def test_flattened_table_assignment_uses_configured_output_budget():
    source = _source(["heading", "value"])
    body = {"tables": [{
        "table_node": None, "graphic": None,
        "flattened_row_nodes": ["doc/p1", "doc/p2"],
    }]}

    class CapturesBudget:
        def __init__(self):
            self.calls = []

        def request_json(self, system, user, max_tokens, route):
            del system, user
            self.calls.append((max_tokens, route))
            return {
                "resolved": True, "n_rows": 2, "n_cols": 1,
                "header_rows": 1,
                "cells": [
                    {"row": row + 1, "column": 1, "rowspan": 1,
                     "colspan": 1, "row_header": False,
                     "segment_ids": [f"r{row}s0"]}
                    for row in range(2)
                ],
                "issues": [],
            }, {}

    llm = CapturesBudget()
    result = flattened_tables_pass(
        serialize(source), body, llm,
        UnderstandConfig(output_token_budget=12_345),
    )
    assert result[0][1]["valid"]
    assert llm.calls == [(12_345, "v2:flattened-table:flat-v3.1:t0:try0")]
