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
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_quote": _CITATION_QUOTE_SCHEMA,
                            "target_reference_ids": {
                                "type": "array",
                                "items": {"type": "string", "pattern": "^[0-9]+$"},
                                "description": (
                                    "这处引用代表的全部参考文献编号，照正文印出的"
                                    "样子填，含紧凑范围中间没有印出来的那些"
                                ),
                            },
                        },
                        # 正文没印编号的引用（作者姓氏加年份那种）照常给出
                        # citation_quote，只是不带 target_reference_ids。
                        "required": ["citation_quote"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["citations"],
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
