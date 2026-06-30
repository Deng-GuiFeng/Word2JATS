"""核心工具函数与算法的单元测试。"""

import os

from word2jats.build.jats import (E, append_inline, drop_leading_chars,
                                   make_article, serialize)
from word2jats.build.xref import XrefResolver
from word2jats.classify.frontmatter import (_flip_name, _is_degree,
                                             parse_author_line)
from word2jats.classify.patterns import normalize_orcid
from word2jats.model.blocks import Paragraph, TextRun


def test_normalize_orcid():
    assert normalize_orcid("ORCID: 0000-0002-4278-4814") == "0000-0002-4278-4814"
    assert normalize_orcid("https://orcid.org/0009-0006-1026-1505") == "0009-0006-1026-1505"
    assert normalize_orcid("0000000242784814") == "0000-0002-4278-4814"
    assert normalize_orcid("not an orcid") is None


def test_is_degree_does_not_eat_short_names():
    # 高危回归：常见短姓名绝不能被当作学位丢弃
    for name in ["Lee", "Wang", "Kim", "Liu", "Yang", "Chen", "Xu", "Ali"]:
        assert _is_degree(name) is False, name
    # 真正的学位仍要识别
    for deg in ["M.D.", "Ph.D.", "MSc", "MD", "PhD", "B.Sc."]:
        assert _is_degree(deg) is True, deg


def test_flip_name():
    assert _flip_name("Yinze Ji") == ("Ji", "Yinze")
    assert _flip_name("Salvatore De Rosa") == ("Rosa", "Salvatore De")
    assert _flip_name("Madonna") == ("Madonna", "")


def test_parse_author_line_with_superscripts():
    runs = [
        TextRun("Yinze Ji"), TextRun("1,2", superscript=True),
        TextRun(", Aimin Dang"), TextRun("1,*", superscript=True),
    ]
    authors = parse_author_line(Paragraph(runs=runs))
    assert authors[0].surname == "Ji" and authors[0].aff_labels == ["1", "2"]
    assert authors[1].surname == "Dang" and authors[1].is_corresponding


def test_drop_leading_chars_preserves_format():
    runs = [TextRun("Fig. 1. "), TextRun("Bold caption", bold=True)]
    out = drop_leading_chars(runs, len("Fig. 1. "))
    assert out[0].bold is True and out[0].text == "Bold caption"


def test_inline_formatting_serializes_wellformed():
    art = make_article()
    p = E("p")
    append_inline(p, [TextRun("normal "), TextRun("bold", bold=True),
                      TextRun(" sup", superscript=True)])
    art.append(E("front"))  # 占位，保证可序列化
    assert b"<bold>bold</bold>" in serialize(p, with_doctype=False)
    assert b"<sup>" in serialize(p, with_doctype=False)


def test_xref_expands_ranges_and_guards():
    r = XrefResolver(max_ref=20, fig_nums={1}, table_nums=set())
    toks = r._linkify("See [8-10] and Fig. 1 and Table 5.")
    # [8-10] 展开为 b8/b10 的 xref；Fig.1 链接 F001；Table 5 无对应表 → 保留文本
    xrefs = [t for t in toks if not isinstance(t, str)]
    rids = {x.get("rid") for x in xrefs}
    assert "b8" in rids and "b10" in rids and "F001" in rids
    assert "T005" not in rids
    assert any("Table 5" in t for t in toks if isinstance(t, str))
