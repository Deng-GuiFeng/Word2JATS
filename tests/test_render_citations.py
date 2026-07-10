"""锁住预览渲染层对 element-citation 的分隔注入。

NLM 预览样式表把 element-citation 各子元素拍平成裸文本相邻输出,年/卷/页会糊成一串
数字。渲染前按温哥华式注入分隔标点(不动交付 XML),这里锁住:
  1. 相邻子元素之间有分隔,年/卷/页不再粘连;
  2. 空的 <etal/> 等"渲染为空"的子元素不会在句点前留下多余空格;
  3. 源文本自带的省略号 "..." 原样保留,子元素自带句点也不会叠成 ".."。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from webapp.render import _cite_sep, render_html

_JATS = """<?xml version="1.0" encoding="utf-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" dtd-version="1.3" xml:lang="en">
<front><article-meta><title-group><article-title>T</article-title></title-group></article-meta></front>
<back><ref-list><title>References</title>
<ref id="b1"><label>[1]</label><element-citation publication-type="journal">
<person-group person-group-type="author">
<name><surname>Nishio</surname><given-names>H</given-names></name>
<name><surname>Awano</surname><given-names>H</given-names></name>
</person-group>
<article-title>Spinal Muscular Atrophy: The Future</article-title>
<source>International Journal of Molecular Sciences</source>
<year>2023</year><volume>24</volume><fpage>1</fpage><lpage>12</lpage>
</element-citation></ref>
<ref id="b2"><label>[2]</label><element-citation publication-type="journal">
<person-group person-group-type="author">
<name><surname>Freedman</surname><given-names>BI</given-names></name>
<etal/>
</person-group>
<article-title>Authors joined by ellipsis, S.,... and last</article-title>
<source>DIABETES CARE</source>
<year>2013</year><volume>36</volume><fpage>972</fpage><lpage>977</lpage>
</element-citation></ref>
<ref id="b3"><label>[3]</label><element-citation publication-type="book">
<person-group person-group-type="author"><name><surname>Steyerberg</surname><given-names>EW</given-names></name></person-group>
<source>Clinical Prediction Models</source>
<edition>2 ed.</edition><publisher-name>Springer</publisher-name><year>2019</year>
</element-citation></ref>
</ref-list></back></article>"""


def _citations(xml):
    html = render_html(xml.encode("utf-8"), "demo")
    out = []
    for c in re.findall(r'<p class="citation">(.*?)</p>', html, re.DOTALL):
        t = re.sub(r"<!--.*?-->", "", c)
        t = re.sub(r"<[^>]+>", "", t)
        out.append(re.sub(r"\s+", " ", t).strip())
    return out


def test_element_citation_gets_separators():
    c = _citations(_JATS)
    assert len(c) == 3
    # 年/卷/页不再粘连,读得出 "2023; 24: 1–12"
    assert "2023; 24: 1–12" in c[0]
    # 作者与篇名之间断开,篇名与刊名之间断开
    assert "H Awano. Spinal Muscular Atrophy" in c[0]
    assert "The Future. International Journal of Molecular Sciences. 2023" in c[0]
    # 关键:全篇不得再出现年卷页粘连的裸数字串
    assert "20232411" not in c[0].replace(" ", "")


def test_empty_etal_leaves_no_space_before_period():
    c = _citations(_JATS)
    # 空 <etal/> 渲染为空,不能留下 "Freedman . " 这种空格+句点
    assert " . " not in c[1] and "Freedman ." not in c[1]
    assert "Freedman. " in c[1] or "Freedman." in c[1]


def test_source_ellipsis_preserved_and_no_double_dot():
    c = _citations(_JATS)
    # 源文本里的省略号必须原样保留
    assert "S.,... and last" in c[1]
    # <edition>"2 ed." 自带句点,叠注入的 ". " 不得成 "2 ed.."
    assert "2 ed.. " not in c[2]
    assert "2 ed. Springer" in c[2]
    # 除省略号外,不得有裸双句点
    for cit in c:
        assert not re.search(r"(?<!\.)\.\.(?!\.)", cit)


_JATS_COMMENT = """<?xml version="1.0" encoding="utf-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" dtd-version="1.3" xml:lang="en">
<front><article-meta><title-group><article-title>T</article-title></title-group></article-meta></front>
<back><ref-list><title>References</title>
<ref id="b1"><label>[1]</label><element-citation publication-type="journal">
<person-group person-group-type="author"><name><surname>Mistiaen</surname><given-names>W</given-names></name></person-group>
<article-title>Risk factors after aortic valve replacement</article-title>
<source>J Heart Valve Dis</source>
<year>2004</year><volume>13</volume><fpage>538</fpage><lpage>44</lpage>
<comment>PMID: 15311858</comment>
</element-citation></ref>
<ref id="b2"><label>[2]</label><element-citation publication-type="journal">
<person-group person-group-type="author"><name><surname>Xiong</surname><given-names>H</given-names></name></person-group>
<article-title>Guidelines</article-title>
<source>Chinese Journal of Evidence-Based Pediatrics</source>
<year>2023</year><volume>18</volume><fpage>1</fpage><lpage>12</lpage>
<comment>(In Chinese)</comment>
</element-citation></ref>
</ref-list></back></article>"""


def test_comment_tail_note_separated():
    # 管线把 PMID / (In Chinese) 存进 <comment>;它前一元素是页码,纯空格分隔会被样式表
    # strip-space 吞掉致 "44PMID" 粘连,必须有可见分隔。
    c = _citations(_JATS_COMMENT)
    assert len(c) == 2
    assert "538–44. PMID: 15311858" in c[0]
    # 页码与尾注之间有可见分隔,不再 "44PMID" / "12(In Chinese)" 粘连
    assert "44PMID" not in c[0]
    assert "1–12. (In Chinese)" in c[1]
    assert "12(In" not in c[1]


def test_cite_sep_rules():
    # 年→卷 分号、卷→页 冒号、起止页 连接号
    assert _cite_sep("year", "volume") == "; "
    assert _cite_sep("volume", "fpage") == ": "
    assert _cite_sep("fpage", "lpage") == "–"
    # 卷→期 开括号,期→页 补闭括号
    assert _cite_sep("volume", "issue") == "("
    assert _cite_sep("issue", "fpage") == "): "
    # 篇名/刊名/年 前置句点
    assert _cite_sep("person-group", "article-title") == ". "
    assert _cite_sep("article-title", "source") == ". "
