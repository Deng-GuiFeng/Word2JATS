"""新架构确定性单元测试：制表符表归一 / 原生表合并 / 摘要切分 / xref 拼写数字 / 声明标题。
均不依赖 LLM，锁住渲染层与组装层的机械正确性。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from word2jats.model.blocks import TextRun
from word2jats.understand.assemble import (_normalize_grid, _row_span,
                                           _split_abstract_runs, _tab_row_cells)
from word2jats.understand.patterns import (CANON_DECL_TITLE, strip_table_label,
                                           strip_title_prefix_len)


def test_row_span_tolerates_malformed():
    # 契约是 [first, last];但 LLM 偶发只给 1 个下标或反序,绝不能让越界索引把管线搞崩
    assert _row_span([3, 7]) == (3, 7)          # 正常
    assert _row_span([5]) == (5, 5)             # 只给 1 个 → 当单行表(曾致 IndexError 崩溃)
    assert _row_span([9, 4]) == (4, 9)          # 反序 → 归正


def _t(s, **kw):
    return TextRun(text=s, **kw)


def _txt(cell):
    return "".join(r.text for r in cell if isinstance(r, TextRun))


def test_tab_split_drops_empty_alignment_cells():
    cells = _tab_row_cells([_t("Age\t\t\t\t<0.001\t22.3")])
    assert [_txt(c) for c in cells] == ["Age", "<0.001", "22.3"]


def test_normalize_grid_pads_to_header_width():
    rows = [[[_t("H1")], [_t("H2")], [_t("H3")]],
            [[_t("Age")], [_t("<0.001")]]]
    head, body = _normalize_grid(rows, 1)
    assert len(head[0]) == 3 and len(body[0]) == 3
    assert _txt(body[0][2]) == ""


def test_normalize_grid_merges_overflow():
    rows = [[[_t("A")], [_t("B")]],
            [[_t("x")], [_t("y")], [_t("z")]]]
    head, body = _normalize_grid(rows, 1)
    assert len(body[0]) == 2
    assert _txt(body[0][1]) == "y z"


def test_split_abstract_by_subheads():
    runs = [_t("Background: aaa bbb Methods: ccc Results: ddd Conclusions: eee")]
    segs = _split_abstract_runs(runs, ["Background:", "Methods:", "Results:", "Conclusions:"])
    assert [s[0] for s in segs] == ["Background:", "Methods:", "Results:", "Conclusions:"]
    assert _txt(segs[0][1]).strip() == "aaa bbb"
    assert _txt(segs[2][1]).strip() == "ddd"


def test_split_abstract_preserves_italic():
    runs = [_t("Background: "), _t("gene", italic=True), _t(" up Methods: down")]
    segs = _split_abstract_runs(runs, ["Background:", "Methods:"])
    assert any(isinstance(r, TextRun) and r.italic and r.text == "gene" for r in segs[0][1])


def test_strip_title_prefix():
    assert strip_title_prefix_len("Author Contributions: WM did X", "Author Contributions") == \
        len("Author Contributions: ")
    assert strip_title_prefix_len("There was no funding", "Funding") == 0


def test_canon_decl_titles():
    assert CANON_DECL_TITLE["funding"] == "Funding"
    assert CANON_DECL_TITLE["conflict"] == "Conflicts of Interest"
    assert "Ethics" in CANON_DECL_TITLE["ethics"]


def test_strip_table_label():
    label, plen = strip_table_label("Table 1. Baseline characteristics")
    assert label == "Table 1." and plen == len("Table 1. ")


def test_xref_spelled_out_numbers():
    from word2jats.build.jats import E
    from word2jats.build.xref import XrefResolver
    xr = XrefResolver(table_nums={1, 2})
    p = E("p")
    p.text = "As Table one shows, and Table two too."
    xr._process_element(p)
    xrefs = p.findall("xref")
    assert len(xrefs) == 2
    assert xrefs[0].get("rid") == "T001" and xrefs[1].get("rid") == "T002"
