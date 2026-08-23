# -*- coding: utf-8 -*-
"""头部直出 XML 的 DTD 自修复循环。

框架只负责一件事：让输出合法。改得对不对由模型能力决定，不在这里断言。
所以用例全部围绕循环的机械行为：什么时候停、对话怎么累积、失败怎么记账。
"""

from __future__ import annotations

import copy

import pytest

from word2jats.understand.passes import (
    MAX_DTD_REPAIR, UnderstandConfig, head_jats_pass,
    _dtd_failure_digest, _head_jats_repair_message,
)
from word2jats.understand.serialize import serialize
from word2jats.validate import dtd

# 复用既有夹具：六条记录的最小源文档，以及应答全部路由的假模型。
# 重造一份只会多出一处要同步维护的东西。
from test_understand_v2 import StubLLM, _source


# 合法：title-group 在前，contrib 里只有 name。依据是 DTD 的 article-meta 与
# contrib 内容模型，不取自任何样例。
GOOD = (
    '<article><front><article-meta>'
    "<title-group><article-title>T</article-title></title-group>"
    '<contrib-group><contrib contrib-type="author">'
    "<name><surname>A</surname></name></contrib></contrib-group>"
    "</article-meta></front></article>"
)

# 不合法：contrib 的内容模型要求 contrib-id* 排在姓名之前。
BAD = (
    '<article><front><article-meta>'
    "<title-group><article-title>T</article-title></title-group>"
    '<contrib-group><contrib contrib-type="author">'
    "<name><surname>A</surname></name>"
    '<contrib-id contrib-id-type="orcid">x</contrib-id>'
    "</contrib></contrib-group>"
    "</article-meta></front></article>"
)

MALFORMED = "<article><front><article-meta>"


def test_fixtures_match_their_labels():
    """先钉死两个夹具确实一个合法一个不合法，后面的断言才有意义。"""
    assert dtd.validate_head_fragment(GOOD).ok
    assert not dtd.validate_head_fragment(BAD).ok
    assert not dtd.validate_head_fragment(MALFORMED).well_formed


class FullStub(StubLLM):
    """应答全部路由，头部一路吐同一个固定回答。用于验证失败如何传导到交付门。"""

    def __init__(self, reply):
        super().__init__()
        self._reply = reply

    def request_text(self, system, user, max_tokens=4096, route=None,
                     messages=None):
        del system, max_tokens, messages
        self.requests.append((route, user))
        return self._reply, {"route": route, "cache_hit": False, "ok": True}


class ScriptedHead:
    """按脚本逐轮吐回答的假模型。

    只放行 metadata_range，front_content_range 置空，这样整趟只跑 request_metadata，
    把自修复循环单独隔离出来。
    """

    provider = "stub"
    model = "fixture"

    def __init__(self, replies, metadata_range=True):
        self.replies = list(replies)
        self.text_calls = []          # [(route, 发出去的完整 messages)]
        self._metadata_range = metadata_range

    def request_json(self, system, user, max_tokens=4096, route=None,
                     response_format=None):
        del system, user, max_tokens, response_format
        assert ":head-boundary:" in route
        return ({
            "metadata_range": (
                {"first_node": "doc/p1", "last_node": "doc/p2"}
                if self._metadata_range else None
            ),
            "front_content_range": None,
        }, {"route": route, "ok": True})

    def request_text(self, system, user, max_tokens=4096, route=None,
                     messages=None):
        del system, user, max_tokens
        # 深拷贝：调用方之后还会往同一个列表上接轮次，不拷就断言不到当时的样子。
        self.text_calls.append((route, copy.deepcopy(messages)))
        index = len(self.text_calls) - 1
        reply = self.replies[index] if index < len(self.replies) else self.replies[-1]
        return reply, {"route": route, "ok": reply is not None}


def run(replies, **kwargs):
    llm = ScriptedHead(replies, **kwargs)
    return llm, head_jats_pass(serialize(_source()), llm, UnderstandConfig())


def head_audits(task):
    return [item for item in task.audit if item.get("task") == "head-jats"]


# ------------------------------------------------ 停机条件一：合法即退出

def test_stops_on_first_valid_answer():
    llm, task = run([GOOD])
    assert len(llm.text_calls) == 1
    assert task.xml == GOOD
    assert task.issues == ()
    audits = head_audits(task)
    assert [item["attempt"] for item in audits] == [0]
    assert audits[0]["dtd_ok"] is True
    assert audits[0]["dtd_violations"] == []


def test_stops_as_soon_as_it_becomes_valid():
    llm, task = run([BAD, BAD, GOOD])
    assert len(llm.text_calls) == 3
    assert task.xml == GOOD
    assert task.issues == ()
    audits = head_audits(task)
    assert [item["attempt"] for item in audits] == [0, 1, 2]
    assert [item["dtd_ok"] for item in audits] == [False, False, True]


# ------------------------------------------------ 停机条件二：问满即失败

def test_fails_after_the_limit_without_extra_calls():
    llm, task = run([BAD])
    assert len(llm.text_calls) == MAX_DTD_REPAIR + 1
    audits = head_audits(task)
    assert [item["attempt"] for item in audits] == list(range(MAX_DTD_REPAIR + 1))
    assert all(item["dtd_ok"] is False for item in audits)


def test_failure_keeps_the_last_xml_and_records_one_issue():
    """失败不等于丢弃产物：丢掉 XML 会让整个 front 塌成空 article-meta。"""
    _, task = run([BAD])
    assert task.xml == BAD
    assert len(task.issues) == 1
    assert "经 %d 轮仍不符合 JATS 1.3 DTD" % (MAX_DTD_REPAIR + 1) in task.issues[0]
    assert "DTD_CONTENT_MODEL" in task.issues[0]


def test_failure_blocks_delivery_through_the_existing_gate():
    """"不放行"落在既有机制上，不另造闸门。

    head 的 issue 在 understand 里被记成 review_blocking，进而让 meta["blocking"]
    为真；pipeline 的 gates.understanding 据此判否，产物落 failed/ 不交付。
    """
    from word2jats.understand.understand import understand

    _, meta = understand(_source(), FullStub(BAD))
    assert meta["blocking"] is True
    blocking = [
        item for item in meta["issues"]
        if item["code"] == "HEAD_JATS_UNAVAILABLE"
    ]
    assert len(blocking) == 1
    assert blocking[0]["severity"] == "review_blocking"
    assert "经 %d 轮仍不符合" % (MAX_DTD_REPAIR + 1) in blocking[0]["detail"]


def test_valid_head_does_not_block_delivery():
    """反面：同一条路径下，合法的头部不得留下任何阻断项。"""
    from word2jats.understand.understand import understand

    _, meta = understand(_source(), FullStub(GOOD))
    assert not any(
        item["code"] == "HEAD_JATS_UNAVAILABLE" for item in meta["issues"]
    )


# ------------------------------------------- 对话累积：模型看到的是什么

def test_second_turn_carries_the_previous_answer_and_the_report():
    llm, _ = run([BAD, GOOD])
    first_route, first_messages = llm.text_calls[0]
    second_route, second_messages = llm.text_calls[1]

    assert [m["role"] for m in first_messages] == ["system", "user"]
    assert [m["role"] for m in second_messages] == [
        "system", "user", "assistant", "user",
    ]
    # 前两轮逐字保留，不能被改写。
    assert second_messages[:2] == first_messages
    assert second_messages[2]["content"] == BAD
    correction = second_messages[3]["content"]
    assert "不符合 JATS Publishing 1.3 DTD" in correction
    assert "DTD_CONTENT_MODEL" in correction
    assert "/article/front/article-meta/contrib-group/contrib" in correction
    assert first_route != second_route


def test_every_turn_replays_the_whole_conversation():
    llm, _ = run([BAD, BAD, BAD, GOOD])
    lengths = [len(messages) for _, messages in llm.text_calls]
    assert lengths == [2, 4, 6, 8]
    _, last = llm.text_calls[-1]
    assert [m["role"] for m in last] == [
        "system", "user", "assistant", "user", "assistant", "user",
        "assistant", "user",
    ]


def test_route_differs_per_attempt_so_cache_keys_separate():
    """每轮必须走不同的 route，否则第 2 轮会撞第 1 轮的缓存。"""
    llm, _ = run([BAD])
    routes = [route for route, _ in llm.text_calls]
    assert len(set(routes)) == len(routes)
    for attempt, route in enumerate(routes):
        assert route.endswith(":try%d" % attempt)
        assert ":head-jats:" in route


# ------------------------------------------------- 退化输入：没文本、非良构

def test_empty_answer_does_not_push_an_empty_assistant_turn():
    """没拿到文本就没有可供模型修正的对象，原样再问，不往对话里塞空轮次。"""
    llm, task = run([None, GOOD])
    assert len(llm.text_calls) == 2
    _, second_messages = llm.text_calls[1]
    assert [m["role"] for m in second_messages] == ["system", "user"]
    assert task.xml == GOOD
    assert task.issues == ()


@pytest.mark.parametrize("blank", [None, "", "   \n  "])
def test_blank_answers_are_all_treated_as_no_text(blank):
    llm, task = run([blank])
    assert len(llm.text_calls) == MAX_DTD_REPAIR + 1
    assert task.xml is None
    assert all(len(messages) == 2 for _, messages in llm.text_calls)
    assert any("没有返回可用的 XML 文本" in item for item in task.issues)


def test_malformed_xml_is_reported_back_as_a_syntax_error():
    llm, task = run([MALFORMED, GOOD])
    _, second_messages = llm.text_calls[1]
    assert second_messages[2]["content"] == MALFORMED
    assert "XML 非良构" in second_messages[3]["content"]
    assert task.xml == GOOD


def test_malformed_xml_failure_digest_names_the_syntax_error():
    _, task = run([MALFORMED])
    assert "返回的不是良构 XML" in task.issues[0]


# --------------------------------------------------------- 不该跑的时候不跑

def test_no_metadata_range_means_no_call_at_all():
    llm, task = run([GOOD], metadata_range=False)
    assert llm.text_calls == []
    assert task.xml is None
    assert head_audits(task) == []
    assert task.issues == ()


# --------------------------------------------------------------- 两个辅助

def test_repair_message_demands_a_complete_rewrite():
    report = dtd.validate_head_fragment(BAD)
    text = _head_jats_repair_message(report)
    assert report.render() in text
    assert "重新返回完整的 XML" in text
    assert "不要只返回改动部分" in text
    # 只准改结构，不准动文字——否则自修复会变成一条丢内容的捷径。
    assert "一个都不能增删或改写" in text


def test_failure_digest_handles_a_missing_report():
    assert _dtd_failure_digest(None) == "没有取得校验结果"


def test_failure_digest_truncates_long_lists():
    report = dtd.validate_head_fragment(
        '<article><front><article-meta>'
        "<title-group><article-title>T</article-title></title-group>"
        '<contrib-group>'
        + "".join(
            '<contrib contrib-type="author"><name><surname>A</surname></name>'
            '<contrib-id contrib-id-type="orcid">x</contrib-id></contrib>'
            for _ in range(6)
        )
        + "</contrib-group></article-meta></front></article>"
    )
    assert len(report.violations) == 6
    digest = _dtd_failure_digest(report, limit=3)
    assert digest.count("DTD_CONTENT_MODEL") == 3
    assert "等 6 条" in digest


# ------------------------------------------------------- 循环之外的两个出口

def test_client_without_request_text_is_rejected_loudly():
    """头部任务必须拿原始文本；客户端不支持就直接报错，不能悄悄降级。"""
    from word2jats.understand.passes import _request_text

    class NoText:
        pass

    with pytest.raises(TypeError, match="不支持原始文本响应"):
        _request_text(NoText(), "s", "u", route="r", max_tokens=1)


def test_unusable_boundary_skips_the_loop_entirely():
    """头部范围都没定下来时不进循环——没有范围就没有可送检的片段。"""
    class BadBoundary(ScriptedHead):
        def request_json(self, system, user, max_tokens=4096, route=None,
                         response_format=None):
            del system, user, max_tokens, response_format
            return ({"metadata_range": {"first_node": "nope",
                                        "last_node": "doc/p2"},
                     "front_content_range": None},
                    {"route": route, "ok": True})

    llm = BadBoundary([GOOD])
    task = head_jats_pass(serialize(_source()), llm, UnderstandConfig())
    assert llm.text_calls == []
    assert task.xml is None
    assert len(task.audit) == 1 and task.audit[0]["task"] == "head-boundary"
    assert any("无法确定头部范围" in item for item in task.issues)
