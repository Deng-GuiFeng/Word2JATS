"""理解层各任务的提示词、响应模式与用户消息。

提示词正文放在同目录的 .md 文件里，本模块只负责装载它们，以及定义响应模式和
用户消息。正文与代码分开，改提示词时不必碰 Python，改代码时也不会误动正文。
"""

from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str) -> str:
    """读入一段提示词正文，逐字节照原样返回。"""
    with open(os.path.join(_HERE, name), encoding="utf-8") as handle:
        return handle.read()


import json


FRONT_SYSTEM = _load('front.md')


HEAD_BOUNDARY_SYSTEM = _load('head_boundary.md')


FRONT_CONTENT_SYSTEM = _load('front_content.md')


HEAD_JATS_SYSTEM = _load('head_jats.md')


def _strict_object(**properties):
    """生成供模型服务端和本地共用的封闭对象模式。"""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items):
    return {"type": "array", "items": items}


def _nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


HEAD_BOUNDARY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "manuscript_front_ranges",
        "strict": True,
        "schema": _strict_object(
            metadata_range=_nullable(_strict_object(
                first_node={
                    "type": "string", "minLength": 1,
                    "pattern": "^[^\\r\\n]+$",
                },
                last_node={
                    "type": "string", "minLength": 1,
                    "pattern": "^[^\\r\\n]+$",
                },
            )),
            front_content_range=_nullable(_strict_object(
                first_node={
                    "type": "string", "minLength": 1,
                    "pattern": "^[^\\r\\n]+$",
                },
                last_node={
                    "type": "string", "minLength": 1,
                    "pattern": "^[^\\r\\n]+$",
                },
            )),
        ),
    },
}


_FRONT_Q = _strict_object(
    quote={
        "type": "string",
        "minLength": 1,
        "pattern": "^[^\\r\\n]+$",
        "description": (
            "Exact contiguous source characters from one displayed record; never join "
            "several records with a newline"
        ),
    },
    node_hint={
        "type": "string",
        "minLength": 1,
        "pattern": "^[^\\r\\n]+$",
        "description": "Exact displayed address of the one source node containing quote",
    },
    left_context={
        "type": "string",
        "pattern": "^[^\\r\\n]*$",
        "description": (
            "Exact immediately adjacent characters in the same source node, long enough "
            "with quote and right_context to identify one occurrence"
        ),
    },
    right_context={
        "type": "string",
        "pattern": "^[^\\r\\n]*$",
        "description": (
            "Exact immediately adjacent characters in the same source node, long enough "
            "with left_context and quote to identify one occurrence"
        ),
    },
)


_FRONT_AUTHOR = _strict_object(
    entity_id={"type": "string"},
    author_quote=_FRONT_Q,
    surname_quote=_FRONT_Q,
    given_quote=_FRONT_Q,
    suffix_quote=_nullable(_FRONT_Q),
    degree_quotes=_array(_FRONT_Q),
    email_quotes=_array(_FRONT_Q),
    orcid_quote=_nullable(_FRONT_Q),
    author_comment_quotes=_array(_FRONT_Q),
)

_FRONT_AFFILIATION = _strict_object(
    entity_id={"type": "string"},
    label_quote=_nullable(_FRONT_Q),
    content_quotes=_array(_FRONT_Q),
)

_FRONT_ADDRESS = _strict_object(
    entity_id={"type": "string"},
    source_nodes=_array({"type": "string"}),
    line_quotes=_array(_FRONT_Q),
    postal_label_quote=_nullable(_FRONT_Q),
    postal_quote=_nullable(_FRONT_Q),
    phone_label_quote=_nullable(_FRONT_Q),
    phone_quote=_nullable(_FRONT_Q),
)

_FRONT_CORRESPONDENCE = _strict_object(
    entity_id={"type": "string"},
    content_quotes=_array(_FRONT_Q),
)

_FRONT_DATE_ITEM = _strict_object(
    kind={"type": "string", "enum": ["received", "revised", "accepted"]},
    whole_quote=_FRONT_Q,
    year_quote=_FRONT_Q,
    month_quote=_nullable(_FRONT_Q),
    day_quote=_nullable(_FRONT_Q),
)

_FRONT_EDITOR = _strict_object(
    surname_quote=_FRONT_Q,
    given_quote=_FRONT_Q,
    role_quote=_nullable(_FRONT_Q),
)

_FRONT_ABSTRACT_SECTION = _strict_object(
    title_quote=_nullable(_FRONT_Q),
    paragraph_quotes=_array(_FRONT_Q),
    wrapped={"type": "boolean"},
)

_FRONT_ABSTRACT = _strict_object(
    kind={"type": "string", "enum": ["main", "graphical", "precis"]},
    source_nodes=_array({"type": "string"}),
    container_title_quote=_nullable(_FRONT_Q),
    sections=_array(_FRONT_ABSTRACT_SECTION),
    graphics=_array({"type": "string"}),
)

_FRONT_KEYWORDS = _strict_object(
    source_nodes=_array({"type": "string"}),
    title_quote=_nullable(_FRONT_Q),
    keyword_quotes=_array(_FRONT_Q),
)

_FRONT_CONTRIBUTOR_NOTE = _strict_object(
    entity_id={"type": "string"},
    marker_quote=_FRONT_Q,
    paragraph_quotes=_array(_FRONT_Q),
    kind={"type": "string", "enum": ["equal", "other"]},
)

_FRONT_RELATION = _strict_object(
    kind={
        "type": "string",
        "enum": [
            "author-affiliation", "author-correspondence", "author-address",
            "affiliation-address", "author-note",
        ],
    },
    source_id={"type": "string"},
    target_id={"type": "string"},
    marker_quote=_nullable(_FRONT_Q),
)

FRONT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "manuscript_front_matter",
        "strict": True,
        "schema": _strict_object(
            article_type=_nullable({
                "type": "string", "enum": [
                    "research-article", "review-article", "case-report",
                    "editorial", "other",
                ],
            }),
            category_quote=_nullable(_FRONT_Q),
            title_quotes=_array(_FRONT_Q),
            authors=_array(_FRONT_AUTHOR),
            affiliations=_array(_FRONT_AFFILIATION),
            addresses=_array(_FRONT_ADDRESS),
            correspondences=_array(_FRONT_CORRESPONDENCE),
            dates=_strict_object(
                format={"type": "string", "enum": ["dmy", "mdy", "ymd", "unknown"]},
                items=_array(_FRONT_DATE_ITEM),
            ),
            editors=_array(_FRONT_EDITOR),
            abstracts=_array(_FRONT_ABSTRACT),
            keywords=_nullable(_FRONT_KEYWORDS),
            contributor_notes=_array(_FRONT_CONTRIBUTOR_NOTE),
            relations=_array(_FRONT_RELATION),
            author_note_quotes=_array(_FRONT_Q),
            front_nodes=_array({"type": "string"}),
            body_start_node=_nullable({"type": "string"}),
            issues=_array({"type": "string"}),
        ),
    },
}


BODY_SYSTEM = _load('body.md')


_CITATION_QUOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "quote": {
            "type": "string",
            "description": "Exact visible citation characters copied from the manuscript",
        },
        "record_key": {
            "type": "string",
            "description": (
                "Exact address printed before the source record that contains the citation"
            ),
        },
        "left_context": {
            "type": "string",
            "pattern": "^[^\\r\\n]*$",
            "description": (
                "Exact visible characters immediately before quote in the same source record"
            ),
        },
        "right_context": {
            "type": "string",
            "pattern": "^[^\\r\\n]*$",
            "description": (
                "Exact visible characters immediately after quote in the same source record"
            ),
        },
    },
    "required": ["quote", "record_key", "left_context", "right_context"],
    "additionalProperties": False,
}


CITATION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "bibliographic_citation_links",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "compact_range_citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_quote": _CITATION_QUOTE_SCHEMA,
                            "target_reference_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Ordered stable entity IDs represented by one compact "
                                    "range, including targets without separate visible text"
                                ),
                            },
                        },
                        "required": ["citation_quote", "target_reference_ids"],
                        "additionalProperties": False,
                    },
                },
                "single_target_citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_quote": _CITATION_QUOTE_SCHEMA,
                            "target_reference_id": {
                                "type": "string",
                                "description": (
                                    "从「参考文献身份」中原样复制的一个稳定实体编号"
                                ),
                            },
                        },
                        "required": ["citation_quote", "target_reference_id"],
                        "additionalProperties": False,
                    },
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "compact_range_citations", "single_target_citations", "issues",
            ],
            "additionalProperties": False,
        },
    },
}


CITATION_SYSTEM = _load('citation.md')


FLATTENED_TABLE_SYSTEM = _load('flattened_table.md')


REF_BOUNDARY_A_SYSTEM = _load('ref_boundary_a.md')


REF_BOUNDARY_B_SYSTEM = _load('ref_boundary_b.md')


REF_BOUNDARY_JUDGE_SYSTEM = _load('ref_boundary_judge.md')


REFERENCE_FIELDS_SYSTEM = _load('reference_fields.md')


MERGE_JUDGE_SYSTEM = _load('merge_judge.md')


DISCARD_REVIEW_SYSTEM = _load('discard_review.md')


def user_message(view: str, *, instruction: str = "") -> str:
    """参考文献各任务的英文用户消息；这几份提示词尚未改写为中文。"""
    suffix = f"\n\nAdditional task context:\n{instruction}" if instruction else ""
    return f"SOURCE VIEW:\n{view}{suffix}\n\nReturn strict JSON now."


def _zh_user_message(lead: str):
    """按同一体例生成中文用户消息，只有开头交代输入的那句不同。"""
    def build(view: str, *, instruction: str = "") -> str:
        suffix = f"\n\n补充要求：\n{instruction}" if instruction else ""
        return f"{lead}\n{view}{suffix}\n\n请返回 JSON。"
    return build


# 正文各任务的输入形态不同，开头一句照实交代，不套同一段话。
body_user_message = _zh_user_message("以下是 Word 稿件的完整记录清单：")
citation_user_message = _zh_user_message("以下是 Word 稿件的记录清单：")
flattened_table_user_message = _zh_user_message("以下是同一张表格的全部物理行：")
merge_judge_user_message = _zh_user_message("以下是 Word 稿件的完整记录清单：")
discard_review_user_message = _zh_user_message("以下是 Word 稿件的完整记录清单：")


def head_boundary_user_message(view: str) -> str:
    """头部边界任务使用独立中文消息，避免改变其他 JSON 任务。"""
    return f"以下是从 Word 主文档开头连续提取的记录：\n{view}\n\n请返回 JSON。"


def xml_user_message(view: str) -> str:
    """XML 任务不得复用带 JSON 结尾的用户消息。"""
    return f"以下是已经确认的 Word 文首信息区：\n{view}\n\n请返回 XML。"


def front_content_user_message(view: str, *, instruction: str = "") -> str:
    """摘要与关键词任务使用正文式清单，但保持中文任务消息。"""
    suffix = f"\n\n补充要求：\n{instruction}" if instruction else ""
    return f"以下是已经定位好的 Word 文首内容范围：\n{view}{suffix}\n\n请返回 JSON。"


def judge_message(view: str, left: dict, right: dict) -> str:
    evidence = json.dumps({"candidate_A": left, "candidate_B": right}, ensure_ascii=False)
    return user_message(view, instruction="INDEPENDENT CANDIDATES:\n" + evidence)
