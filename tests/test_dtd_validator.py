# -*- coding: utf-8 -*-
"""DTD 校验器的覆盖测试。

用例一律**从 XML 1.0 的有效性约束与 JATS DTD 的声明构造**，不取自任何样例或
历史缺陷——按样本造用例只会覆盖到已经暴露过的问题。文件末尾另有一组以真实
金标准做端到端验证的用例，那里样本只承担验证角色。
"""

from __future__ import annotations

import io

import pytest
from lxml import etree

from word2jats.validate import dtd as D


DOCTYPE = '<!DOCTYPE article PUBLIC "%s" "%s">' % (D.JATS_PUBLIC_ID, D.JATS_SYSTEM_ID)

# 最小合法骨架。必需性来自 DTD：article→front；front→journal-meta+article-meta；
# journal-meta→journal-id+ 与 issn+；article-meta→title-group；title-group→article-title。
MINIMAL = (
    '<?xml version="1.0" encoding="utf-8"?>\n' + DOCTYPE + '\n'
    '<article xmlns:mml="http://www.w3.org/1998/Math/MathML"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink"'
    ' dtd-version="1.3" article-type="research-article">'
    '<front>'
    '<journal-meta><journal-id journal-id-type="publisher-id">J</journal-id>'
    '<issn>1234-5678</issn></journal-meta>'
    '<article-meta><title-group><article-title>T</article-title></title-group>'
    '%s</article-meta>'
    '</front>%s</article>'
)


def doc(article_meta_extra: str = "", after_front: str = "") -> bytes:
    return (MINIMAL % (article_meta_extra, after_front)).encode("utf-8")


def check(xml: bytes) -> D.Report:
    return D.validate_bytes(xml)


def codes(report: D.Report) -> set:
    return {v.code for v in report.violations}


# ---------------------------------------------------------------- 基线

def test_minimal_skeleton_is_valid():
    r = check(doc())
    assert r.ok, r.render()
    assert r.violations == []


def test_report_renders_empty_when_valid():
    assert check(doc()).render() == ""


# ------------------------------------------------- A 组：文档—DTD 绑定

def test_root_element_name_must_match_doctype():
    """XML 1.0 VC: Root Element Type。etree.DTD().validate() 不查这条。"""
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n' + DOCTYPE
           + '\n<sec id="s"><title>T</title><p>x</p></sec>').encode()
    r = check(xml)
    assert not r.ok
    assert "DTD_ROOT_NAME" in codes(r)


def test_non_wellformed_is_reported_separately_from_invalid():
    """解析失败与校验失败必须分成两类结果，不能混报。"""
    r = check(b"<article><front></article>")
    assert not r.well_formed
    assert not r.ok
    assert r.parse_error
    assert "非良构" in r.render()


# --------------------------------------------------- B 组：元素内容

def test_undeclared_element_is_rejected():
    r = check(doc(after_front="<body><zzz/></body>"))
    assert not r.ok
    assert codes(r) & {"DTD_UNKNOWN_ELEM", "DTD_CONTENT_MODEL"}


def test_empty_element_rejects_child():
    """break 声明为 EMPTY。"""
    r = check(doc(after_front="<body><p><break><x/></break></p></body>"))
    assert not r.ok


def test_empty_element_rejects_text():
    r = check(doc(after_front="<body><p><break>text</break></p></body>"))
    assert not r.ok
    assert "DTD_NOT_EMPTY" in codes(r)


def test_empty_element_rejects_comment():
    """规范明确：EMPTY 元素内不能有任何内容，注释也不行。"""
    r = check(doc(after_front="<body><p><break><!--c--></break></p></body>"))
    assert not r.ok
    assert "DTD_NOT_EMPTY" in codes(r)


def test_mixed_content_rejects_child_outside_its_list():
    """corresp 是混合内容，break 不在它的名单里。"""
    r = check(doc(article_meta_extra="<author-notes><corresp>a<break/>b</corresp></author-notes>"))
    assert not r.ok
    assert "DTD_INVALID_CHILD" in codes(r)


def test_mixed_content_ignores_order_and_count():
    """混合内容不约束顺序与次数，这是与 element-only 的根本差别。"""
    r = check(doc(article_meta_extra=(
        "<author-notes><corresp><sup>1</sup>a<italic>b</italic>"
        "<sup>2</sup>c</corresp></author-notes>")))
    assert r.ok, r.render()


def test_element_only_rejects_character_data():
    """element-only 内容里出现非空白文字即非法。"""
    r = check(doc(article_meta_extra="<author-notes>裸文本<corresp>a</corresp></author-notes>"))
    assert not r.ok


def test_element_only_allows_whitespace_and_comments():
    r = check(doc(article_meta_extra=(
        "<author-notes>\n  <!--c--><?pi x?>\n  <corresp>a</corresp>\n</author-notes>")))
    assert r.ok, r.render()


def test_missing_required_child_is_rejected():
    """title-group 的内容模型里 article-title 不带量词，必需。"""
    xml = MINIMAL % ("", "")
    xml = xml.replace("<article-title>T</article-title>", "")
    r = check(xml.encode())
    assert not r.ok
    assert "DTD_CONTENT_MODEL" in codes(r)


def test_child_order_violation_is_rejected():
    """contrib 要求 contrib-id* 在姓名之前。"""
    r = check(doc(article_meta_extra=(
        '<contrib-group><contrib contrib-type="author">'
        '<name><surname>A</surname></name>'
        '<contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-1825-0097</contrib-id>'
        "</contrib></contrib-group>")))
    assert not r.ok
    assert "DTD_CONTENT_MODEL" in codes(r)


def test_group_position_violation_is_rejected():
    """article-meta 要求 contrib-group 全部排在 author-notes 之前。"""
    r = check(doc(article_meta_extra=(
        "<author-notes><corresp>c</corresp></author-notes>"
        '<contrib-group><contrib contrib-type="editor">'
        "<name><surname>E</surname></name></contrib></contrib-group>")))
    assert not r.ok
    assert "DTD_CONTENT_MODEL" in codes(r)


def test_prefix_mismatch_is_rejected():
    """DTD 按字面 qname 匹配：同一命名空间换个前缀就不认。"""
    r = check(doc(after_front=(
        '<body><p><inline-formula>'
        '<m:math xmlns:m="http://www.w3.org/1998/Math/MathML"><m:mi>x</m:mi></m:math>'
        "</inline-formula></p></body>")))
    assert not r.ok


def test_declared_prefix_is_accepted():
    r = check(doc(after_front=(
        "<body><p><inline-formula>"
        "<mml:math><mml:mi>x</mml:mi></mml:math>"
        "</inline-formula></p></body>")))
    assert r.ok, r.render()


# ------------------------------------------------------ C 组：属性

def test_undeclared_attribute_is_rejected():
    r = check(doc(after_front='<body><p bogus="1">x</p></body>'))
    assert not r.ok
    assert "DTD_UNKNOWN_ATTRIBUTE" in codes(r)


def test_enumeration_value_out_of_range_is_rejected():
    xml = MINIMAL % ("", "")
    r = check(xml.replace('dtd-version="1.3"', 'dtd-version="9.9"').encode())
    assert not r.ok
    assert "DTD_ATTRIBUTE_VALUE" in codes(r)


def test_enumeration_hint_lists_allowed_values():
    xml = MINIMAL % ("", "")
    r = check(xml.replace('dtd-version="1.3"', 'dtd-version="9.9"').encode())
    hints = " ".join(v.hint for v in r.violations)
    assert "1.3" in hints and "允许" in hints


def test_fixed_attribute_value_mismatch_is_rejected():
    """xmlns:xlink 在 article 上是 #FIXED。"""
    xml = MINIMAL % ("", "")
    r = check(xml.replace('xmlns:xlink="http://www.w3.org/1999/xlink"',
                          'xmlns:xlink="http://example.com/WRONG"').encode())
    assert not r.ok


def test_required_attribute_missing_is_rejected():
    """graphic/@xlink:href 是 #REQUIRED。"""
    r = check(doc(after_front="<body><p><graphic/></p></body>"))
    assert not r.ok
    assert "DTD_MISSING_ATTRIBUTE" in codes(r)


def test_duplicate_id_is_rejected():
    r = check(doc(after_front=(
        '<body><sec id="dup"><title>A</title></sec>'
        '<sec id="dup"><title>B</title></sec></body>')))
    assert not r.ok


def test_dangling_idref_is_rejected():
    r = check(doc(after_front=(
        '<body><p><xref ref-type="bibr" rid="nope">1</xref></p></body>')))
    assert not r.ok


# ------------------------------------------------ 判定不设例外

def test_id_and_idref_violations_are_never_excused():
    """ID 唯一性与 IDREF 解析都是 XML 1.0 的有效性约束，不给任何豁免。

    这两类曾被"片段校验时目标可能还没生成"的理由放过。头部片段的 id 与 rid
    是模型在同一段输出里写的，两头都在，那个理由不成立。
    """
    dangling = doc(after_front=(
        '<body><p><xref ref-type="bibr" rid="nope">1</xref></p></body>'))
    assert "DTD_UNKNOWN_ID" in codes(check(dangling))

    duplicate = doc(after_front=(
        '<body><sec id="dup"><title>A</title></sec>'
        '<sec id="dup"><title>B</title></sec></body>'))
    assert "DTD_ID_REDEFINED" in codes(check(duplicate))


# --------------------------------------------------- 报错字段与提示

def test_violation_carries_path_expected_actual_hint():
    r = check(doc(article_meta_extra=(
        '<contrib-group><contrib contrib-type="author">'
        "<name><surname>A</surname></name>"
        '<contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-1825-0097</contrib-id>'
        "</contrib></contrib-group>")))
    v = next(x for x in r.violations if x.code == "DTD_CONTENT_MODEL")
    assert v.path.startswith("/article/")
    assert "contrib" in v.path
    assert v.qname == "contrib"
    assert "contrib-id" in v.expected
    assert "name" in v.actual
    assert "contrib-id" in v.hint and "name" in v.hint


def test_path_distinguishes_sibling_occurrences():
    """同名兄弟必须能分辨到第几个，否则模型无法定位。"""
    bad = ('<contrib contrib-type="author"><name><surname>%s</surname></name>'
           '<contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-1825-0097'
           "</contrib-id></contrib>")
    r = check(doc(article_meta_extra="<contrib-group>%s%s</contrib-group>"
                  % (bad % "A", bad % "B")))
    paths = {v.path for v in r.violations if v.code == "DTD_CONTENT_MODEL"}
    assert len(paths) == 2
    assert any("contrib[2]" in p for p in paths)


def test_expected_model_is_not_truncated():
    """libxml2 的消息约 5000 字节截断，重建出来的不能截断。"""
    decls = D._load_decls()
    rendered = D.render_model(decls["article-meta"].content)
    assert rendered.startswith("(")
    assert rendered.endswith(")")
    assert "custom-meta-group" in rendered      # 模型末尾的元素


# --------------------------------------------- 内部函数的直接覆盖

def test_decl_index_is_keyed_by_qname_not_local_name():
    """sec / mml:sec 局部名相同，按局部名建索引会让后者覆盖前者。"""
    decls = D._load_decls()
    assert decls["sec"].type == "element"
    assert decls["mml:sec"].type == "empty"
    assert len(decls) == D._EXPECTED_ELEMENTS


def test_render_model_reproduces_quantifiers():
    n = D._Node("element", "mult", "a", None, None)
    assert D.render_model(n) == "a*"
    assert D.render_model(D._Node("element", "opt", "b", None, None)) == "b?"
    assert D.render_model(D._Node("element", "plus", "c", None, None)) == "c+"
    assert D.render_model(D._Node("element", "once", "d", None, None)) == "d"
    assert D.render_model(None) == ""


def test_render_model_flattens_right_leaning_tree():
    leaf = lambda n: D._Node("element", "once", n, None, None)
    inner = D._Node("seq", "once", None, leaf("b"), leaf("c"))
    root = D._Node("seq", "once", None, leaf("a"), inner)
    assert D.render_model(root) == "(a, b, c)"


def test_analyze_locates_the_failing_position():
    decls = D._load_decls()
    model = decls["contrib"].content
    assert D.analyze(model, ["contrib-id", "name"]) is None
    bad = D.analyze(model, ["name", "contrib-id"])
    assert bad.position == 1 and bad.child == "contrib-id"
    assert D.analyze(model, []) is None


def test_analyze_uses_longest_viable_prefix_not_full_match():
    """(label?, citation+) 遇到 [label]：label 位置合法，真因是末尾缺内容。
    按"能否完整匹配"判会一律怪罪第 1 个子元素。"""
    decls = D._load_decls()
    miss = D.analyze(decls["ref"].content, ["label"])
    assert miss is not None
    assert miss.kind == "missing-tail"
    assert miss.position == 1


def test_analyze_separates_not_allowed_from_wrong_place():
    decls = D._load_decls()
    # degrees 根本不在 name 的允许集合里
    assert D.analyze(decls["name"].content, ["surname", "degrees"]).kind == "not-allowed"
    # thead 是 table 的合法子元素，只是位置不对
    assert D.analyze(decls["table"].content, ["tr", "thead"]).kind == "wrong-place"


def test_analyze_on_empty_model():
    assert D.analyze(None, []) is None
    assert D.analyze(None, ["a"]).kind == "not-allowed"


def test_allowed_children_collects_leaf_names():
    decls = D._load_decls()
    allowed = D.allowed_children(decls["corresp"].content)
    assert "email" in allowed
    assert "break" not in allowed


def test_attr_qname_restores_prefix():
    el = etree.fromstring(
        b'<r xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="u" xml:lang="en" plain="p"/>')
    keys = {D._attr_qname(k, el) for k in el.attrib}
    assert keys == {"xlink:href", "xml:lang", "plain"}


def test_dtd_integrity_is_asserted_on_load():
    """DTD 由 64 个文件拼成，缺模块时 lxml 只给 WARNING。规模必须被断言。"""
    decls = D._load_decls()
    assert len(decls) == 498
    assert decls["article"].type == "element"


# ------------------------------------- 端到端：样本只在这里承担验证角色

@pytest.mark.parametrize("name", ["01", "02", "03", "S01", "X01"])
def test_reference_structures_validate_clean(name):
    import os
    path = os.path.join("样例数据", name, "结构参考.xml")
    if not os.path.exists(path):
        pytest.skip("样例缺失: %s" % path)
    with open(path, "rb") as fh:
        r = check(fh.read())
    assert r.ok, r.render()[:2000]


def test_agrees_with_libxml2_on_reference_structures():
    """判定必须与 libxml2 一致——不能比它更松或更严。"""
    import glob
    dtd = etree.DTD(D.DTD_PATH)
    parser = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
    for path in sorted(glob.glob("样例数据/*/结构参考.xml")):
        with open(path, "rb") as fh:
            raw = fh.read()
        theirs = bool(dtd.validate(etree.fromstring(raw, parser)))
        ours = check(raw).ok
        assert ours == theirs, path


# ------------------------------------------------- 渲染与边界路径

def test_violation_render_keeps_both_hint_and_raw_message():
    """提示可能算错，原文是唯一能纠正它的东西，两者都要留。"""
    v = D.Violation(code="X", path="/a/b", line=3, message="raw",
                    hint="人话", actual="a → b", expected="(a, b)")
    out = v.render()
    assert "[X] /a/b" in out and "第 3 行" in out
    assert "人话" in out and "raw" in out
    assert "a → b" in out and "(a, b)" in out


def test_violation_render_caps_overlong_model_and_message():
    """内容模型与原文都可能有几千字符，整份贴出只会淹没结论。"""
    v = D.Violation(code="X", path="/a", line=1, message="M" * 900,
                    hint="h", expected=" | ".join("e%d" % i for i in range(200)))
    out = v.render()
    assert "内容模型过长" in out
    assert "原文过长已截断" in out
    assert len(out) < 900


def test_violation_render_falls_back_to_message():
    v = D.Violation(code="X", path="", line=0, message="raw only")
    out = v.render()
    assert "raw only" in out
    assert out.startswith("[X] /")          # path 为空时退回 "/"


def test_report_render_lists_every_violation():
    r = D.Report(well_formed=True, valid=False, violations=[
        D.Violation(code="A", path="/p", line=1, message="m1"),
        D.Violation(code="B", path="/q", line=2, message="m2"),
    ])
    out = r.render()
    assert "m1" in out and "m2" in out
    assert not r.ok


def test_child_qnames_handles_namespaces_and_comments():
    el = etree.fromstring(
        b'<r xmlns:mml="http://www.w3.org/1998/Math/MathML">'
        b'<mml:mi>x</mml:mi><plain/><!--c--><?pi y?></r>')
    assert D._child_qnames(el) == ["mml:mi", "plain"]


def test_element_qname_variants():
    ns = etree.fromstring(
        b'<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"/>')
    assert D._element_qname(ns) == "mml:math"
    assert D._element_qname(etree.fromstring(b"<plain/>")) == "plain"
    default_ns = etree.fromstring(b'<t xmlns="urn:x"/>')
    assert D._element_qname(default_ns) == "t"     # 默认命名空间没有前缀
    comment = etree.Comment("c")
    assert D._element_qname(comment) == ""


def test_attr_qname_without_matching_prefix_falls_back_to_local():
    el = etree.fromstring(b'<r xmlns="urn:x"/>')
    assert D._attr_qname("{urn:unknown}k", el) == "k"


def test_attr_hint_reports_fixed_value_mismatch():
    decls = D._load_decls()
    el = etree.fromstring(
        b'<article xmlns:xlink="http://example.com/WRONG" dtd-version="1.3"/>')
    hint = D._attr_hint(el, decls["article"])
    assert "固定值" in hint


def test_attr_hint_reports_missing_required():
    decls = D._load_decls()
    el = etree.fromstring(b"<graphic/>")
    assert "缺少必需属性" in D._attr_hint(el, decls["graphic"])


def test_attr_hint_reports_undeclared_attribute():
    decls = D._load_decls()
    el = etree.fromstring(b'<p nope="1"/>')
    assert "未声明" in D._attr_hint(el, decls["p"])


def test_enrich_returns_untouched_when_path_missing():
    v = D.Violation(code="DTD_CONTENT_MODEL", path="", line=0, message="m")
    assert D._enrich(v, None, D._load_decls()) is v


def test_enrich_returns_untouched_on_unresolvable_path():
    decls = D._load_decls()
    tree = etree.fromstring(b"<article/>").getroottree()
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/nope/nope", line=0, message="m")
    assert D._enrich(v, tree, decls).hint == ""


def test_enrich_returns_untouched_for_unknown_element():
    decls = D._load_decls()
    tree = etree.fromstring(b"<zzz/>").getroottree()
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/zzz", line=0, message="m")
    assert D._enrich(v, tree, decls).expected == ""


def test_enrich_marks_empty_element_violation():
    decls = D._load_decls()
    tree = etree.fromstring(b"<break>text</break>").getroottree()
    v = D.Violation(code="DTD_NOT_EMPTY", path="/break", line=0, message="m")
    assert "EMPTY" in D._enrich(v, tree, decls).hint


def test_enrich_marks_pcdata_only_violation():
    decls = D._load_decls()
    tree = etree.fromstring(b"<year><x/></year>").getroottree()
    out = D._enrich(D.Violation(code="DTD_NOT_PCDATA", path="/year",
                                line=0, message="m"), tree, decls)
    assert out.hint


def test_render_model_handles_pcdata_and_unknown_node():
    assert D.render_model(D._Node("pcdata", "once", None, None, None)) == "#PCDATA"
    assert D.render_model(D._Node("weird", "once", None, None, None)) == "?"


def test_regex_helpers_normalise():
    leaf = D._Node("element", "once", "a", None, None)
    assert D._to_regex(None) == D._EPS
    assert D._to_regex(D._Node("pcdata", "once", None, None, None)) == D._EPS
    assert D._to_regex(D._Node("weird", "once", None, None, None)) == D._NIL
    assert D._cat(D._EPS, ("el", "a")) == ("el", "a")
    assert D._cat(("el", "a"), D._NIL) == D._NIL
    assert D._alt(D._NIL, ("el", "a")) == ("el", "a")
    assert D._alt(("el", "a"), ("el", "a")) == ("el", "a")
    assert D._star(D._EPS) == D._EPS
    assert D._to_regex(D._Node("element", "plus", "a", None, None)) == (
        "seq", ("el", "a"), ("star", ("el", "a")))


def test_nullable_and_derive():
    assert D._nullable(D._EPS) and not D._nullable(D._NIL)
    assert not D._nullable(("el", "a"))
    assert D._nullable(("star", ("el", "a")))
    assert D._nullable(("or", D._EPS, ("el", "a")))
    assert not D._nullable(("seq", ("el", "a"), D._EPS))
    memo = {}
    assert D._derive(("el", "a"), "a", memo) == D._EPS
    assert D._derive(("el", "a"), "b", memo) == D._NIL
    assert D._derive(D._NIL, "a", memo) == D._NIL
    assert D._derive(("star", ("el", "a")), "a", memo) == ("star", ("el", "a"))
    assert D._derive(("el", "a"), "a", memo) == D._EPS       # 记忆化命中


def test_flatten_skips_missing_side():
    leaf = D._Node("element", "once", "a", None, None)
    node = D._Node("seq", "once", None, leaf, None)
    assert D.render_model(node) == "(a)"


def test_dtd_unavailable_when_path_is_broken(monkeypatch, tmp_path):
    bad = tmp_path / "broken.dtd"
    bad.write_text("<!ELEMENT a (", encoding="utf-8")
    monkeypatch.setattr(D, "DTD_PATH", str(bad))
    monkeypatch.setattr(D, "_DECL_CACHE", {})
    with pytest.raises(D.DtdUnavailable):
        D._load_decls()


def test_dtd_unavailable_when_declaration_count_is_short(monkeypatch, tmp_path):
    """缺模块时 lxml 只给 WARNING，规模断言必须把残缺 DTD 挡在判定之外。"""
    small = tmp_path / "small.dtd"
    small.write_text("<!ELEMENT a EMPTY>", encoding="utf-8")
    monkeypatch.setattr(D, "DTD_PATH", str(small))
    monkeypatch.setattr(D, "_DECL_CACHE", {})
    with pytest.raises(D.DtdUnavailable) as exc:
        D._load_decls()
    assert "不完整" in str(exc.value)


def test_fixed_non_namespace_attribute_mismatch_is_hinted():
    """xml:space 在 code 上是 #FIXED "preserve"，是非命名空间的固定值属性。"""
    r = check(doc(after_front=(
        '<body><sec><title>T</title>'
        '<p><code xml:space="default">x</code></p></sec></body>')))
    assert not r.ok
    assert any("固定值" in v.hint for v in r.violations), r.render()


def test_undeclared_namespace_binding_is_hinted():
    r = check(doc(after_front=(
        '<body><sec xmlns:zz="urn:zz"><title>T</title><p>x</p></sec></body>')))
    assert not r.ok
    assert any("命名空间绑定" in v.hint for v in r.violations), r.render()


def test_pcdata_only_element_rejects_children():
    """year 的内容模型是 (#PCDATA)。"""
    r = check(doc(after_front=(
        "<body><sec><title>T</title><p><year><x/></year></p></sec></body>")))
    assert not r.ok
    assert "DTD_NOT_PCDATA" in codes(r)
    assert any("不允许子元素" in v.hint for v in r.violations), r.render()


def test_element_only_illegal_child_reports_content_model():
    """element-only 元素出现名单外子元素时报的是内容模型不符，不是 INVALID_CHILD。"""
    r = check(doc(after_front=(
        "<body><sec><title>T</title><issn>1</issn></sec></body>")))
    assert "DTD_CONTENT_MODEL" in codes(r)
    assert "DTD_INVALID_CHILD" not in codes(r)
    v = next(x for x in r.violations if x.code == "DTD_CONTENT_MODEL")
    assert "issn" in v.hint


# --------------------------------------------- _enrich 的各条早退分支

def test_enrich_bails_out_on_root_only_path():
    """部分错误的 path 是 "/"，切出来的标签名为空，无从定位。"""
    decls = D._load_decls()
    tree = etree.fromstring(b"<article/>").getroottree()
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/", line=0, message="m")
    assert D._enrich(v, tree, decls).hint == ""


def test_enrich_bails_out_on_malformed_xpath():
    decls = D._load_decls()
    tree = etree.fromstring(b"<article/>").getroottree()
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/[[[bad", line=0, message="m")
    assert D._enrich(v, tree, decls).hint == ""


def test_enrich_bails_out_when_tag_name_is_ambiguous():
    """相对路径退路只在唯一命中时采用；同名元素有多个就不猜。"""
    decls = D._load_decls()
    tree = etree.fromstring(
        b"<article><body><sec/><sec/></body></article>").getroottree()
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/sec", line=0, message="m")
    out = D._enrich(v, tree, decls)
    assert out.hint == "" and out.path == "/sec"


def test_enrich_returns_early_for_mixed_element_without_bad_child():
    """mixed 元素上的内容模型错误没有名单外子元素时不给提示。"""
    decls = D._load_decls()
    tree = etree.fromstring(b"<p><italic>x</italic></p>").getroottree()
    v = D.Violation(code="DTD_INVALID_CHILD", path="/p", line=0, message="m")
    assert D._enrich(v, tree, decls).hint == ""


def test_enrich_returns_early_when_model_actually_matches():
    """libxml2 判错但子元素序列本身可匹配时，不硬造失配点。"""
    decls = D._load_decls()
    tree = etree.fromstring(
        b"<title-group><article-title>T</article-title></title-group>").getroottree()
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/title-group",
                    line=0, message="m")
    assert D._enrich(v, tree, decls).hint == ""


# ------------------------------------------- validate_bytes 的边界分支

def test_validation_errors_alone_keep_document_wellformed():
    """只有校验错误、没有语法错误时，文档仍是良构的。"""
    r = check(doc(after_front="<body><p><graphic/></p></body>"))
    assert r.well_formed
    assert not r.valid
    assert r.parse_error == ""


def test_duplicate_entries_are_collapsed():
    """同一处违反被 libxml2 报多条时，同 code+path+message 只留一条。

    corresp 是混合内容且允许名单里没有 break（DTD 声明如此），break 又是 EMPTY，
    所以重复三个 break 只会触发同一条消息三次，不掺杂别的违反。
    """
    xml = doc(article_meta_extra=(
        "<author-notes><corresp><break/><break/><break/></corresp></author-notes>"))
    parser = D._validating_parser()
    try:
        etree.fromstring(xml, parser)
    except etree.XMLSyntaxError:
        pass
    raw = [(e.type_name, e.path, e.message) for e in parser.error_log
           if e.level_name != "WARNING"]
    assert len(raw) == 3 and len(set(raw)) == 1   # 先钉死 libxml2 确实报了三条

    r = check(xml)
    assert len(r.violations) == 1
    keys = [(v.code, v.path, v.message) for v in r.violations]
    assert len(keys) == len(set(keys))


def test_enrich_bails_out_when_path_points_to_non_element():
    """path 指向属性或文本节点时拿不到 tag，不给提示。"""
    decls = D._load_decls()
    tree = etree.fromstring(b'<article id="a1"/>').getroottree()
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/article/@id",
                    line=0, message="m")
    assert D._enrich(v, tree, decls).hint == ""


# ------------------------------------------- validate_head_fragment：片段送检

# 头部任务直出的形状：只有 article/front/article-meta，没有 DOCTYPE，也没有
# journal-meta。必需性来自 DTD 的 front-model "(journal-meta, article-meta, notes?)"。
HEAD = (
    '<article xmlns:mml="http://www.w3.org/1998/Math/MathML"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink" article-type="research-article">'
    '<front><article-meta>'
    '<title-group><article-title>T</article-title></title-group>'
    "%s</article-meta></front></article>"
)


def head(extra: str = "") -> str:
    return HEAD % extra


def test_head_fragment_alone_is_unvalidatable_without_host():
    """先钉死前提：不套宿主，这个形状永远判不合法，与模型写得对不对无关。"""
    r = check(head().encode("utf-8"))
    assert not r.ok
    assert "DTD_NO_DTD" in codes(r)
    assert "DOCTYPE_PUBLIC_INVALID" in codes(r)


def test_head_fragment_host_makes_correct_output_valid():
    """宿主本身必须合法：正确的片段套上宿主后不得报任何违反。

    这条是宿主的锁。宿主一旦写错（占位 journal-meta 不合法、DOCTYPE 写岔），
    每个片段都会凭空多出违反，而那些违反不是模型造成的。
    """
    r = D.validate_head_fragment(head())
    assert r.ok, r.render()
    assert r.violations == []


def test_head_fragment_accepts_bytes_and_bytearray():
    assert D.validate_head_fragment(head().encode("utf-8")).ok
    assert D.validate_head_fragment(bytearray(head().encode("utf-8"))).ok


@pytest.mark.parametrize("value", ["", "   \n\t ", b"", b"  "])
def test_head_fragment_rejects_empty_text(value):
    r = D.validate_head_fragment(value)
    assert not r.ok and not r.well_formed
    assert r.parse_error == "头部模型没有返回文本"


@pytest.mark.parametrize("value", [None, 42, {"xml": "x"}, ["<article/>"]])
def test_head_fragment_rejects_non_text(value):
    r = D.validate_head_fragment(value)
    assert not r.ok and not r.well_formed
    assert "不是文本" in r.parse_error


def test_head_fragment_reports_syntax_error_verbatim():
    r = D.validate_head_fragment("<article><front>")
    assert not r.well_formed
    assert "Premature end of data" in r.parse_error
    assert r.render().startswith("XML 非良构")


def test_head_fragment_reports_markdown_fence_as_syntax_error():
    """提示词禁止 Markdown 代码块；真出现了要当语法错报回去，不能静默吃掉。"""
    r = D.validate_head_fragment("```xml\n<article/>\n```")
    assert not r.well_formed and r.parse_error


def test_head_fragment_flags_wrong_root():
    r = D.validate_head_fragment("<front><article-meta/></front>")
    assert "ROOT_ELEMENT_INVALID" in codes(r)


def test_head_fragment_does_not_inject_second_journal_meta():
    """模型自己写了 journal-meta 就用它的，补第二个会造出不存在的违反。"""
    own = (
        '<article><front>'
        '<journal-meta><journal-id journal-id-type="publisher-id">J</journal-id>'
        "<issn>1234-5678</issn></journal-meta>"
        "<article-meta><title-group><article-title>T</article-title></title-group>"
        "</article-meta></front></article>"
    )
    r = D.validate_head_fragment(own)
    assert r.ok, r.render()


def test_head_fragment_host_placeholder_never_appears_in_report():
    r = D.validate_head_fragment(head(
        '<contrib-group><contrib contrib-type="author">'
        "<name><surname>A</surname></name>"
        '<contrib-id contrib-id-type="orcid">x</contrib-id>'
        "</contrib></contrib-group>"))
    assert not r.ok
    assert all("journal-meta" not in v.path for v in r.violations)
    assert "HOST-PLACEHOLDER" not in r.render()


def test_head_fragment_flags_out_of_order_children():
    """contrib-id 必须排在姓名之前——DTD 的 contrib 内容模型如此规定。"""
    r = D.validate_head_fragment(head(
        '<contrib-group><contrib contrib-type="author">'
        "<name><surname>A</surname></name>"
        '<contrib-id contrib-id-type="orcid">x</contrib-id>'
        "</contrib></contrib-group>"))
    assert "DTD_CONTENT_MODEL" in codes(r)
    hint = " ".join(v.hint for v in r.violations)
    assert "contrib-id" in hint and "位置不对" in hint


def test_head_fragment_flags_missing_front():
    r = D.validate_head_fragment("<article><body><p>x</p></body></article>")
    assert "DTD_CONTENT_MODEL" in codes(r)
    assert any(v.path == "/article" for v in r.violations)


def test_head_fragment_zeroes_line_numbers():
    """检的是宿主包裹后的文档，它的行号不指向模型写出来的文本，一律清零。"""
    r = D.validate_head_fragment(
        head('<contrib-group><contrib contrib-type="author">'
             "<name><surname>A</surname></name>"
             '<contrib-id contrib-id-type="orcid">x</contrib-id>'
             "</contrib></contrib-group>").replace("><", ">\n<"))
    assert r.violations
    assert all(v.line == 0 for v in r.violations)
    import re as _re
    assert not _re.search(r"第 \d+ 行", r.render())


def test_head_fragment_catches_dangling_reference():
    """头部片段里指不到的 rid 就是模型写错了，必须报出来让它自己改。"""
    r = D.validate_head_fragment(head(
        '<contrib-group><contrib contrib-type="author">'
        "<name><surname>A</surname></name>"
        '<xref ref-type="aff" rid="aff-does-not-exist"/>'
        "</contrib></contrib-group>"))
    assert "DTD_UNKNOWN_ID" in codes(r)


def test_head_fragment_catches_duplicate_id():
    r = D.validate_head_fragment(head(
        '<aff id="aff-1">X</aff><aff id="aff-1">Y</aff>'))
    assert "DTD_ID_REDEFINED" in codes(r)


def test_head_fragment_accepts_a_resolved_reference():
    """反面：指得到目标就不该报。"""
    r = D.validate_head_fragment(head(
        '<contrib-group><contrib contrib-type="author">'
        "<name><surname>A</surname></name>"
        '<xref ref-type="aff" rid="aff-1"/>'
        "</contrib></contrib-group>"
        '<aff id="aff-1">X</aff>'))
    assert r.ok, r.render()


def test_head_fragment_empty_container_hint_reads_correctly():
    r = D.validate_head_fragment(
        "<article><front><article-meta/></front></article>")
    hint = " ".join(v.hint for v in r.violations)
    assert "<article-meta> 是空的" in hint


# ------------------------------------------- 覆盖补齐：剩余分支

def test_doctype_name_other_than_article_is_flagged():
    """DOCTYPE 里写的名字必须是 article。

    libxml2 只核对"根元素 == DOCTYPE 里写的名字"，那个名字由待检文档自己提供，
    所以 DOCTYPE 写 sec、根也写 sec 时它判通过。这条约束只能自己查。
    """
    xml = ('<!DOCTYPE sec PUBLIC "%s" "%s">\n<article/>'
           % (D.JATS_PUBLIC_ID, D.JATS_SYSTEM_ID)).encode("utf-8")
    r = check(xml)
    assert "DOCTYPE_NAME_INVALID" in codes(r)
    hint = " ".join(v.hint for v in r.violations)
    assert "DOCTYPE 声明的元素名必须是 article" in hint


def test_internal_subset_is_rejected():
    """内部子集能覆盖外部 DTD 的定义，校验结果就不再代表符合 JATS 1.3。"""
    xml = ('<!DOCTYPE article PUBLIC "%s" "%s" [<!ENTITY %% body-model "ANY">]>\n'
           "<article/>" % (D.JATS_PUBLIC_ID, D.JATS_SYSTEM_ID)).encode("utf-8")
    r = check(xml)
    assert "INTERNAL_SUBSET_FORBIDDEN" in codes(r)


def test_str_input_with_encoding_declaration_is_rejected():
    """lxml 不接受带 encoding 声明的 str，要报成输入问题而不是崩出去。"""
    r = D.validate_bytes('<?xml version="1.0" encoding="utf-8"?><article/>')
    assert not r.well_formed
    assert "不是可解析的 XML 字节串" in r.parse_error


def test_missing_tail_hint_names_the_last_child():
    """末尾缺内容时要指出"到哪个子元素为止"，而不是怪罪第一个合法子元素。

    ref 的内容模型是 (label?, (citation-alternatives | element-citation | …)+)，
    只写 label 时 label 本身位置合法，真因是后面缺引文元素。
    """
    r = check(doc(after_front=(
        "<back><ref-list><ref><label>1</label></ref></ref-list></back>")))
    hint = " ".join(v.hint for v in r.violations)
    assert "到 <label> 为止" in hint
    assert "还要求后面有内容" in hint


def test_warning_level_entries_are_not_violations():
    """WARNING 不是有效性违反，不能进违反清单。"""
    xml = ('<?xml version="1.1" encoding="utf-8"?>\n'
           + DOCTYPE + "\n<article/>").encode("utf-8")
    parser = D._validating_parser()
    try:
        etree.fromstring(xml, parser)
    except etree.XMLSyntaxError:
        pass
    assert any(e.level_name == "WARNING" for e in parser.error_log)   # 先钉死确有 WARNING

    r = check(xml)
    assert "WAR_UNKNOWN_VERSION" not in codes(r)


def test_cat_absorbs_epsilon_on_the_right():
    left = ("lit", "x")
    assert D._cat(left, D._EPS) is left


def test_resolve_falls_back_when_name_is_undeclared():
    """内容模型引用了未声明的元素时，原样保留局部名，不能凭空造前缀。"""
    src = "<!ELEMENT lone (nowhere)>\n"
    decls = D._build_decls(etree.DTD(io.StringIO(src)))
    assert D.render_model(decls["lone"].content) == "nowhere"


def test_resolve_falls_back_when_prefix_is_ambiguous():
    """局部名有多个前缀版本、又都跟所属元素的前缀不同时，退回局部名。"""
    src = ("<!ELEMENT p:x (#PCDATA)>\n<!ELEMENT q:x (#PCDATA)>\n"
           "<!ELEMENT r:owner (x)>\n")
    decls = D._build_decls(etree.DTD(io.StringIO(src)))
    assert D.render_model(decls["r:owner"].content) == "x"


# ------------------------------------ 报错里的"允许 N 种子元素"必须是真数出来的

def test_child_count_is_not_the_number_of_bars():
    """竖线个数不是子元素种数，两个方向都会错。

    article-meta 用逗号把 46 种子元素连起来、只含 10 个竖线（严重低估）；
    p、corresp 的模型里同一个名字出现多次（高估）。这个数是超长内容模型被
    省略后模型唯一拿得到的规模信息，算错就是在给模型喂假话。
    """
    decls = D._load_decls()
    for name, bars, real in [("article-meta", 11, 46), ("contrib", 16, 19),
                             ("p", 73, 72), ("corresp", 29, 28)]:
        model = D.render_model(decls[name].content)
        assert model.count("|") + 1 == bars, name
        assert len(D.allowed_children(decls[name].content)) == real, name


def test_long_model_reports_the_real_child_count():
    r = check(doc(article_meta_extra=(
        "<author-notes><corresp>c</corresp></author-notes>"
        '<contrib-group><contrib contrib-type="editor">'
        "<name><surname>E</surname></name></contrib></contrib-group>")))
    target = next(v for v in r.violations
                  if v.path == "/article/front/article-meta")
    assert len(target.expected) > D._MAX_MODEL_CHARS      # 确实走了省略分支
    assert target.expected_count == 46
    assert "允许 46 种子元素" in target.render()
    assert "允许约 11" not in target.render()


def test_long_model_omits_the_number_when_it_is_unknown():
    """数不出来时不说数字，不许退回竖线估算。"""
    v = D.Violation(code="DTD_CONTENT_MODEL", path="/x", line=0, message="m",
                    expected="a | " * D._MAX_MODEL_CHARS)
    text = v.render()
    assert "内容模型过长,请按上面的问题定位" in text
    assert "种子元素" not in text


# ------------------------------- 宿主注入的 journal-meta 不得污染 front 层报错

def test_front_level_violation_drops_the_injected_position():
    """违反落在 front 自己身上时，序号会把补入的 journal-meta 算进去。

    模型写的 notes 是它自己的第 3 个子元素，加上补入的 journal-meta 就成了第 4 个。
    指偏的位置比不给位置更有害——模型会照着去改一个合法的元素。
    """
    r = D.validate_head_fragment(
        "<article><front><article-meta>"
        "<title-group><article-title>T</article-title></title-group>"
        "</article-meta><notes><p>a</p></notes><notes><p>b</p></notes>"
        "</front></article>")
    target = next(v for v in r.violations if v.path == "/article/front")
    assert target.actual == ""
    assert "不给子元素序号" in target.hint
    assert "第 4 个" not in target.render()
    # 期望模型与校验器原文照留：提示可能算错，原文是唯一能纠正它的。
    assert "(journal-meta, article-meta, notes?)" in target.render()


def test_front_level_violation_keeps_position_when_nothing_was_injected():
    """模型自己写了 journal-meta 就没有补入，序号是真的，不许扣。"""
    r = D.validate_head_fragment(
        "<article><front>"
        '<journal-meta><journal-id journal-id-type="p">J</journal-id>'
        "<issn>1234-5678</issn></journal-meta>"
        "<article-meta><title-group><article-title>T</article-title></title-group>"
        "</article-meta><notes><p>a</p></notes><notes><p>b</p></notes>"
        "</front></article>")
    target = next(v for v in r.violations if v.path == "/article/front")
    assert target.actual == "journal-meta → article-meta → notes → notes"
    assert "第 4 个子元素" in target.hint


def test_article_meta_violations_are_untouched_by_the_injection():
    """扣除只针对 front 这一层，article-meta 内的序号必须原样保留。"""
    r = D.validate_head_fragment(
        "<article><front><article-meta>"
        "<title-group><article-title>T</article-title></title-group>"
        "<author-notes><corresp>c</corresp></author-notes>"
        '<contrib-group><contrib contrib-type="editor">'
        "<name><surname>E</surname></name></contrib></contrib-group>"
        "</article-meta></front></article>")
    target = next(v for v in r.violations
                  if v.path == "/article/front/article-meta")
    assert target.actual == "title-group → author-notes → contrib-group"
    assert "第 3 个子元素" in target.hint
