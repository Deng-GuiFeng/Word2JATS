"""LLM 主导修复器的安全机制单测(GPU-free,用桩 LLM)。

这些修复器"由 LLM 决定怎么改",但必须有不可逾越的安全护栏:内容守恒(不丢段落)、
覆盖校验(提议必须覆盖全部现有节/段)、姓名集合不变(作者不增删)。本测试守住这些护栏,
保证"即便 LLM 给出离谱提议,也只会不生效或回滚,绝不把对的改坏"。
"""
from word2jats.agent import declarations as D
from word2jats.agent import structure as S
from word2jats.build.jats import E, sub


class StubLLM:
    """桩 LLM:enabled=True,extract_json 返回预置 dict。"""
    enabled = True

    def __init__(self, resp):
        self._resp = resp

    def extract_json(self, system, user, max_tokens=2048):
        return self._resp


# --------------------------- 结构修正 --------------------------- #
def _sample1_like_body():
    """构造样例1式的错误 body:Internal/External 被误升为顶级节。"""
    body = E("body")
    mm = sub(body, "sec"); sub(mm, "title", "Materials and Methods")
    sa = sub(mm, "sec"); sub(sa, "title", "Statistical analysis")
    uni = sub(sa, "sec"); sub(uni, "title", "Univariable analyses"); sub(uni, "p", "uni text")
    iv = sub(body, "sec"); sub(iv, "title", "Internal validation"); sub(iv, "p", "bootstrap internal")
    ev = sub(body, "sec"); sub(ev, "title", "External validation"); sub(ev, "p", "temporal external")
    cm = sub(ev, "sec"); sub(cm, "title", "Comparison of models"); sub(cm, "p", "comparison text")
    res = sub(body, "sec"); sub(res, "title", "Results"); sub(res, "p", "results text")
    return body


_CORRECT_OUTLINE = [
    {"title": "Materials and Methods", "level": 1, "role": "section", "status": "existing"},
    {"title": "Statistical analysis", "level": 2, "role": "section", "status": "existing"},
    {"title": "Univariable analyses", "level": 3, "role": "section", "status": "existing"},
    {"title": "Model validation", "level": 3, "role": "section", "status": "new"},
    {"title": "Internal validation", "level": 4, "role": "runin", "status": "existing"},
    {"title": "External validation", "level": 4, "role": "runin", "status": "existing"},
    {"title": "Comparison of models", "level": 3, "role": "section", "status": "existing"},
    {"title": "Results", "level": 1, "role": "section", "status": "existing"},
]


def _titles_at_depth(body, depth):
    out, stack = [], [(body, -1)]
    def walk(parent, d):
        for sec in parent.findall("sec"):
            if d == depth:
                out.append(sec.findtext("title"))
            walk(sec, d + 1)
    walk(body, 0)
    return out


def test_structure_rebuild_fixes_hierarchy_and_conserves_content():
    body = _sample1_like_body()
    new_body, msg = S.rebuild_body(body, _CORRECT_OUTLINE)
    assert new_body is not None, msg
    # 顶级只剩 Materials and Methods + Results(Internal/External 不再是顶级)
    assert _titles_at_depth(new_body, 0) == ["Materials and Methods", "Results"]
    # Model validation 被恢复,且 Comparison 与之同级(都在 Statistical analysis 下)
    titles = [s.findtext("title") for s in new_body.iter("sec")]
    assert "Model validation" in titles
    # Internal/External 降为 run-in(成为 <p><bold>),不再是 <sec>
    sec_titles = set(titles)
    assert "Internal validation" not in sec_titles
    assert "External validation" not in sec_titles
    # 内容守恒:各段正文都还在
    alltext = " ".join(new_body.itertext())
    for t in ("uni text", "bootstrap internal", "temporal external", "comparison text", "results text"):
        assert t in alltext


def test_structure_rebuild_aborts_if_proposal_drops_a_section():
    body = _sample1_like_body()
    # 提议漏掉了 "Results"(未覆盖)→ 必须放弃(防丢内容),返回 None
    bad = [p for p in _CORRECT_OUTLINE if p["title"] != "Results"]
    new_body, msg = S.rebuild_body(body, bad)
    assert new_body is None
    assert "未覆盖" in msg or "丢" in msg


# --------------------------- 声明拆分 --------------------------- #
def _article_with_crammed_back():
    art = E("article")
    back = sub(art, "back")
    sec = sub(back, "sec"); sub(sec, "title", "Author Contributions")
    sub(sec, "p", "Conceptualization, W.M.; methodology, I.D.")
    sub(sec, "p", "There was no funding to perform this study.")
    sub(sec, "p", "There are no conflicts of interest to declare.")
    rl = sub(back, "ref-list"); sub(sub(rl, "ref", id="b1"), "label", "[1]")
    return art


def test_declarations_split_and_preserve_reflist():
    art = _article_with_crammed_back()
    stub = StubLLM({"sections": [
        {"title": "Author Contributions", "paras": [0]},
        {"title": "Funding", "paras": [1]},
        {"title": "Conflicts of Interest", "paras": [2]},
    ]})
    rec = D.split_declarations(stub, art, validator=None)
    assert rec["applied"], rec.get("reason")
    back = art.find("back")
    decl_titles = [s.findtext("title") for s in back if s.tag == "sec"]
    assert decl_titles == ["Author Contributions", "Funding", "Conflicts of Interest"]
    # ref-list 原样保留,且在声明节之后
    assert back.find("ref-list") is not None
    # 内容守恒
    alltext = " ".join(art.itertext())
    assert "no funding" in alltext and "conflicts of interest" in alltext


def test_declarations_aborts_if_segmentation_incomplete():
    art = _article_with_crammed_back()
    # 切分只覆盖前两段,漏掉第 3 段 → 必须放弃
    stub = StubLLM({"sections": [
        {"title": "Author Contributions", "paras": [0]},
        {"title": "Funding", "paras": [1]},
    ]})
    rec = D.split_declarations(stub, art, validator=None)
    assert not rec["applied"]
    assert "覆盖" in rec["reason"]
