"""锁住 table-wrap 内容判定:table 网格与 graphic 图片表都算有内容,只有两者皆无才报空表。

赛题样例里有的表格在源稿中直接以图片形式给出(<table-wrap> 内是 <graphic> 而非 <table>),
这是 JATS 合法且常见的做法(样例 02 金标准的 8 张表全是图片表)。检查层只认 <table> 会把
图片表误报成 table_no_content,和成品预览里明明渲染出来的表格自相矛盾。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from word2jats.validate.checks import run_checks

_HEAD = b'<article xmlns:xlink="http://www.w3.org/1999/xlink" dtd-version="1.3"><body><sec>'
_TAIL = b'</sec></body></article>'


def _codes(xml):
    return [i.code for i in run_checks(xml)]


def test_grid_table_ok():
    xml = _HEAD + b'<table-wrap id="T1"><label>Table 1</label>' \
        b'<table><tbody><tr><td>a</td></tr></tbody></table></table-wrap>' + _TAIL
    assert "table_no_content" not in _codes(xml)


def test_image_table_ok():
    # 图片表:table-wrap 内是 graphic,不该被误报为无内容
    xml = _HEAD + b'<table-wrap id="T1"><label>Table 1</label>' \
        b'<caption><p>c</p></caption>' \
        b'<graphic xlink:href="art/table-01.jpg"/></table-wrap>' + _TAIL
    assert "table_no_content" not in _codes(xml)


def test_media_table_ok():
    xml = _HEAD + b'<table-wrap id="T1"><media xlink:href="art/t.pdf"/></table-wrap>' + _TAIL
    assert "table_no_content" not in _codes(xml)


def test_truly_empty_table_flagged():
    # 只有 label/caption、既无 table 也无 graphic/media → 真空表,必须报
    xml = _HEAD + b'<table-wrap id="T1"><label>Table 1</label>' \
        b'<caption><p>c</p></caption></table-wrap>' + _TAIL
    assert "table_no_content" in _codes(xml)
