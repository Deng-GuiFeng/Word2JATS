from dataclasses import dataclass

from lxml import etree

from word2jats.model.source import (
    OBJECT_REPLACEMENT, ObjectAnchor, ObjectOccurrence,
    RunRef, RunSpan, SourceDocument, SourceNode, SourcePart,
)
from word2jats.parse.styles import StyleResolver
from word2jats.understand.merge import (
    Assignment, DocumentAssignment, build_reference_spans, grounded_heads,
    merge_assignments, project_body_to_assignment, reconcile_boundaries,
)
from word2jats.understand.passes import (
    ReferenceInput, UnderstandConfig, body_pass,
    citation_contract_failures, citation_pass,
    flattened_rows, flattened_tables_pass,
    make_windows, reference_fields_pass, reference_contract_failures,
    run_windowed, validate_flattened_layout,
)
from word2jats.understand.prompts import CITATION_RESPONSE_FORMAT, CITATION_SYSTEM
from word2jats.understand.serialize import serialize


def _source(texts):
    nodes = [SourceNode(f"doc/p{i + 1}", "document", "para", None, i, value)
             for i, value in enumerate(texts)]
    return SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )


def _requires_every_record(view, window, response):
    """测试固件：要求本窗每条记录都出现在某个块里，表行随所在表格一并算。

    用来制造一次可控的重问，检验的是 run_windowed 的择优与合并行为本身，
    与任何一项具体任务的契约内容无关。
    """
    covered = {raw[:-2] if raw.endswith("|表") else raw
               for block in response.get("blocks") or []
               for raw in block.get("nodes") or []}
    return [f"缺少记录 {view.records[index].key}"
            for index in window.center_indices
            if view.records[index].kind != "table-row"
            and view.records[index].key not in covered]


def _requires_footnote_block_role(view, window, response):
    """测试固件：被列为表注的记录，其所在块的角色必须是 table-footnote。"""
    del view, window
    roles = {raw: block.get("role")
             for block in response.get("blocks") or []
             for raw in block.get("nodes") or []}
    return [f"{raw} 所在块不是表注"
            for spec in response.get("tables") or []
            for raw in spec.get("footnote_nodes") or []
            if roles.get(raw) != "table-footnote"]


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


def test_body_pass_sees_word_structure_facts_without_changing_source_view():
    run = RunRef(
        "r1", "document", "/document/body/p[1]",
        style_id="Emphasis", bold=True,
    )
    node = SourceNode(
        "doc/p1", "document", "para", None, 0, "Short block",
        run_spans=[RunSpan(0, 11, run)],
        properties={
            "style_id": "Custom7", "style_name": "Arbitrary publisher style",
            "outline_level": "2", "numbering": ["8", "1"],
            "alignment": "center",
        },
    )
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=(node.node_id,))], [node],
    )

    class Capture:
        def __init__(self):
            self.system = self.user = None

        def request_json(self, system, user, max_tokens, route):
            del max_tokens, route
            self.system, self.user = system, user
            return {"blocks": [{"nodes": ["doc/p1"],
                                "role": "body-paragraph"}]}, {}

    view = serialize(source)
    llm = Capture()
    result = body_pass(view, llm)

    assert "WORD_FACTS" not in view.render()
    assert "WORD_FACTS(doc/p1)" in llm.user
    # 已有提纲级别时，不再重复输出模板自定义样式名。
    assert '"s":["Custom7"' not in llm.user
    assert '"o":"2"' in llm.user
    assert '"n":["8","1"]' in llm.user
    assert '"f":[[0,11,"bold"]]' in llm.user
    # 提示词已改写为中文，这条钉的仍是同一件事：WORD_FACTS 行不是稿件文字。
    assert "它本身不构成稿件文字，不能摘抄" in llm.system
    assert result.prompt_version == "body-v2.14"


def _table_source():
    nodes = [
        SourceNode("doc/p1", "document", "para", None, 0, "Results"),
        SourceNode("doc/tbl1", "document", "table", None, 1),
        SourceNode("doc/tbl1/r1", "document", "row", "doc/tbl1", 2),
        SourceNode("doc/tbl1/r1/c1", "document", "cell", "doc/tbl1/r1", 3),
        SourceNode("doc/tbl1/r1/c1/p1", "document", "para",
                   "doc/tbl1/r1/c1", 4, "Value"),
    ]
    return SourceDocument(
        [SourcePart("document", "document", "/word/document.xml",
                    node_ids=tuple(item.node_id for item in nodes))], nodes,
    )


def test_body_pass_takes_the_first_answer_without_a_self_consistency_reask():
    """正文通道不再复核响应自洽，也就不再为此重问。

    下面这份响应整张原生表既没有块也没有表规格，正是过去会被拦下重问的形态。
    重问的实测收效为零，而遗漏最终会由源覆盖账与输出来源账拦住，
    所以此处只取首答。
    """
    class Incomplete:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            return {"blocks": [{"nodes": ["doc/p1"], "role": "section-title"}],
                    "objects": [], "tables": []}, {}

    llm = Incomplete()
    result = body_pass(serialize(_table_source()), llm)
    assert llm.calls == 1
    assert result.issues == ()
    assert result.combined()["blocks"] == [
        {"nodes": ["doc/p1"], "role": "section-title"},
    ]


def test_body_pass_still_reasks_once_when_no_json_comes_back():
    """拿不到 JSON 与响应自洽是两回事，前者的重问保留。"""
    class EmptyThenAnswer:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            if self.calls == 1:
                return {}, {}
            return {"blocks": [{"nodes": ["doc/p1"], "role": "section-title"}],
                    "objects": [], "tables": []}, {}

    llm = EmptyThenAnswer()
    result = body_pass(serialize(_table_source()), llm)
    assert llm.calls == 2
    assert result.combined()["blocks"][0]["role"] == "section-title"


def test_paragraph_structure_facts_follow_ooxml_style_inheritance():
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    styles = etree.fromstring(f'''<w:styles xmlns:w="{namespace}">
      <w:style w:type="paragraph" w:styleId="Base">
        <w:name w:val="Base style"/>
        <w:pPr>
          <w:outlineLvl w:val="1"/>
          <w:numPr><w:ilvl w:val="1"/><w:numId w:val="8"/></w:numPr>
          <w:jc w:val="center"/>
        </w:pPr>
      </w:style>
      <w:style w:type="paragraph" w:styleId="Child">
        <w:name w:val="Renamed by template author"/>
        <w:basedOn w:val="Base"/>
        <w:pPr><w:numPr><w:ilvl w:val="2"/></w:numPr></w:pPr>
      </w:style>
    </w:styles>'''.encode())
    paragraph = etree.fromstring(f'''<w:p xmlns:w="{namespace}">
      <w:pPr><w:pStyle w:val="Child"/><w:outlineLvl w:val="2"/></w:pPr>
    </w:p>'''.encode())

    facts = StyleResolver(etree.tostring(styles)).effective_paragraph(
        paragraph, "Child"
    )

    assert facts == {
        "outline_level": "2",
        "numbering": ["8", "2"],
        "alignment": "center",
    }


def test_citation_prompt_shows_complete_quote_objects_in_few_shot_examples():
    assert '"citation_quote":Q' not in CITATION_SYSTEM
    assert (
        '"citation_quote":{"quote":"2","record_key":"doc/p12",'
        '"left_context":"conclusion [","right_context":",5]"}'
        in CITATION_SYSTEM
    )
    assert (
        '"citation_quote":{"quote":"Rivera and Chen, 2021",'
        '"record_key":"doc/p27"' in CITATION_SYSTEM
    )
    # 讲评文字已随提示词改写为中文；示例的 Word 记录原文与 JSON 仍是英文原样，
    # 因此下面对示例材料的断言不变，只有对讲评的断言改用对应的中文原句。
    assert '方括号与逗号都是普通原文。' in CITATION_SYSTEM
    assert '共用的圆括号与分号都是普通原文。' in CITATION_SYSTEM
    assert '"quote":"1-3","record_key":"doc/p55"' in CITATION_SYSTEM
    assert '"record_key":"doc/tbl2.r3"' in CITATION_SYSTEM
    assert '`right_context` 抄写至 doc/p70 结束即止。' in CITATION_SYSTEM
    assert '“正文里的引用”不等于“只看叙述性段落”。' in CITATION_SYSTEM
    assert 'Skipped because the citations occur in a non-narrative table row.' in CITATION_SYSTEM
    assert '"record_key":"doc/p83"' in CITATION_SYSTEM
    assert '下面两种写法都是错误的：' in CITATION_SYSTEM
    assert '隐去了地址' in CITATION_SYSTEM
    assert '两个数组在原文字符层面互斥' in CITATION_SYSTEM
    assert '核对有无遗漏' in CITATION_SYSTEM
    assert '不能写成单个字符串' in CITATION_SYSTEM
    citation_schema = CITATION_RESPONSE_FORMAT["json_schema"]["schema"]
    quote_schema = (
        citation_schema["properties"]["single_target_citations"]["items"]
        ["properties"]["citation_quote"]
    )
    assert quote_schema["required"] == [
        "quote", "record_key", "left_context", "right_context",
    ]
    assert quote_schema["properties"]["left_context"]["pattern"] \
        == "^[^\\r\\n]*$"
    assert "uniqueItems" not in (
        citation_schema["properties"]["compact_range_citations"]["items"]
        ["properties"]["target_reference_ids"]
    )


def test_citation_pass_sends_strict_schema_and_preserves_source_address():
    source = _source([
        "Earlier work reached the same conclusion [1].",
        "References",
        "[1] Example A. A deliberately invented title. 2024.",
    ])
    view = serialize(source)
    references = build_reference_spans(view, {
        "reference_title_node": "doc/p2",
        "entries": [{"head_quote": "[1] Example", "node_hint": "doc/p3"}],
    })
    fields = ({
        "label_quote": {"quote": "[1]", "node_hint": "doc/p3"},
        "person_groups": [],
        "fields": {
            "year": {"quote": "2024", "node_hint": "doc/p3"},
            "article_title": {
                "quote": "A deliberately invented title", "node_hint": "doc/p3",
            },
        },
    },)

    class RespondsWithGroundedCitation:
        def __init__(self):
            self.calls = []

        def request_json(self, system, user, max_tokens, route, response_format):
            self.calls.append({
                "system": system, "user": user, "max_tokens": max_tokens,
                "route": route, "response_format": response_format,
            })
            return {
                "single_target_citations": [{
                    "citation_quote": {
                        "quote": "1", "record_key": "doc/p1",
                        "left_context": "conclusion [", "right_context": "].",
                    },
                    "target_reference_id": "reference:1",
                }],
                "compact_range_citations": [],
                "issues": [],
            }, {"response_format": "json_schema"}

    llm = RespondsWithGroundedCitation()
    result = citation_pass(view, references, fields, llm)

    assert not result.issues
    assert len(llm.calls) == 1
    assert llm.calls[0]["route"] == "v2:citations:citations-v2.7:w0-2:try0"
    assert llm.calls[0]["max_tokens"] == 128_000
    assert llm.calls[0]["response_format"] == CITATION_RESPONSE_FORMAT
    assert result.combined()["single_target_citations"][0]["citation_quote"] == {
        "quote": "1", "record_key": "doc/p1",
        "left_context": "conclusion [", "right_context": "].",
    }


def test_citation_contract_names_bare_string_error_directly():
    view = serialize(_source(["Earlier work [1]."]))
    failures = citation_contract_failures(
        view, make_windows(view, UnderstandConfig())[0],
        {"single_target_citations": [{
            "citation_quote": "[1]",
            "target_reference_id": "reference:1",
        }], "compact_range_citations": []},
        {"reference:1"},
    )
    assert failures == [
        "single_target_citations[0].citation_quote must be an object with "
        "string fields quote, record_key, left_context, and right_context; "
        "a bare string is invalid"
    ]


def test_citation_contract_distinguishes_repeated_text_by_adjacent_context():
    view = serialize(_source([
        "Reed (2022) reported one result; Reed (2022) later revised it."
    ]))
    citations = [
        {
            "citation_quote": {
                "quote": "Reed (2022)", "record_key": "doc/p1",
                "left_context": left, "right_context": right,
            },
            "target_reference_id": "reference:1",
        }
        for left, right in (("", " reported"), ("result; ", " later"))
    ]
    failures = citation_contract_failures(
        view, make_windows(view, UnderstandConfig())[0],
        {"single_target_citations": citations, "compact_range_citations": []},
        {"reference:1"},
    )
    assert failures == []

    citations[1]["citation_quote"]["left_context"] = ""
    citations[1]["citation_quote"]["right_context"] = ""
    failures = citation_contract_failures(
        view, make_windows(view, UnderstandConfig())[0],
        {"single_target_citations": citations, "compact_range_citations": []},
        {"reference:1"},
    )
    assert failures == [
        "single_target_citations[1].citation_quote does not identify one exact "
        "source span in its record_key"
    ]


def test_citation_contract_maps_soft_line_and_table_row_record_keys():
    soft_view = serialize(_source(["First [1]\nSecond [1]"]))
    soft = {
        "single_target_citations": [{
            "citation_quote": {
                "quote": "1", "record_key": "doc/p1.2",
                "left_context": "Second [", "right_context": "]",
            },
            "target_reference_id": "reference:1",
        }],
        "compact_range_citations": [],
    }
    assert citation_contract_failures(
        soft_view, make_windows(soft_view, UnderstandConfig())[0],
        soft, {"reference:1"},
    ) == []

    nodes = [
        SourceNode("doc/tbl1", "document", "table", None, 0),
        SourceNode("doc/tbl1/r1", "document", "row", "doc/tbl1", 1),
        SourceNode("doc/tbl1/r1/c1", "document", "cell", "doc/tbl1/r1", 2),
        SourceNode("doc/tbl1/r1/c1/p1", "document", "para",
                   "doc/tbl1/r1/c1", 3, "Group A [1]"),
        SourceNode("doc/tbl1/r1/c2", "document", "cell", "doc/tbl1/r1", 4),
        SourceNode("doc/tbl1/r1/c2/p1", "document", "para",
                   "doc/tbl1/r1/c2", 5, "Group B [1]"),
    ]
    table_source = SourceDocument([
        SourcePart("document", "document", "/word/document.xml",
                   node_ids=tuple(item.node_id for item in nodes)),
    ], nodes)
    table_view = serialize(table_source)
    table = {
        "single_target_citations": [{
            "citation_quote": {
                "quote": "1", "record_key": "doc/tbl1.r1",
                "left_context": "Group B [", "right_context": "]",
            },
            "target_reference_id": "reference:1",
        }],
        "compact_range_citations": [],
    }
    assert citation_contract_failures(
        table_view, make_windows(table_view, UnderstandConfig())[0],
        table, {"reference:1"},
    ) == []






def test_retry_never_splices_two_incomplete_documents_into_one_answer():
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

    llm = ComplementaryAnswers()
    result = run_windowed(
        serialize(source), llm, task="body-test",
        prompt_version="fixture", system="return json", config=UnderstandConfig(),
        contract_validator=_requires_every_record,
    )
    body = result.combined()
    assert "doc/p2" not in {
        raw for block in body["blocks"] for raw in block["nodes"]
    }
    assert body["tables"][0]["table_node"] == "doc/tbl1|表"
    assert llm.calls == 2
    assert result.issues


def test_corrected_complete_answer_replaces_stale_flattened_table_as_a_whole():
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
            "footnote_nodes": ["doc/p4"],
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
    class TwoCompleteCandidates:
        def __init__(self):
            self.calls = 0

        def request_json(self, system, user, max_tokens, route):
            del system, user, max_tokens, route
            self.calls += 1
            return (previous if self.calls == 1 else corrected), {}

    result = run_windowed(
        view, TwoCompleteCandidates(), task="body-test", prompt_version="fixture",
        system="return json", config=UnderstandConfig(),
        contract_validator=_requires_footnote_block_role,
    )
    body = result.combined()
    assert len(body["tables"]) == 1
    assert body["tables"][0]["flattened_row_nodes"] == ["doc/p2", "doc/p3"]
    assert body["tables"][0]["footnote_nodes"] == ["doc/p4"]


def test_final_assignment_prevents_one_node_from_being_table_data_and_note():
    view = serialize(_source(["Table title", "A\tB", "Explanatory note"]))
    body = {
        "tables": [{
            "caption_nodes": ["doc/p1"],
            "flattened_row_nodes": ["doc/p2", "doc/p3"],
            "footnote_nodes": ["doc/p3"],
        }],
    }
    assignment = DocumentAssignment((
        Assignment("node", "doc/p1", "table-caption", ()),
        Assignment("node", "doc/p2", "table", ()),
        Assignment("node", "doc/p3", "table-footnote", ()),
    ), (), ())
    projected = project_body_to_assignment(view, body, assignment)
    table = projected["tables"][0]
    assert table["flattened_row_nodes"] == ["doc/p2"]
    assert table["footnote_nodes"] == ["doc/p3"]


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


def test_failed_reask_cannot_erase_a_better_first_answer():
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
        serialize(source), PartialThenUnavailable(), task="body-test",
        prompt_version="fixture", system="return json", config=UnderstandConfig(),
        contract_validator=_requires_every_record,
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
            payload = user.split("角色冲突清单：\n", 1)[1].split(
                "\n\n请返回 JSON。", 1
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


def test_understanding_uses_provider_documented_output_limit_by_default():
    assert UnderstandConfig().output_token_budget == 128_000
