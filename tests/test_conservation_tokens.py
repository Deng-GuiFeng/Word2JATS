"""守恒度量的切词回归测试。

锁定的修复有两条，**必须成对存在**：
1. docx 侧按段落拼合 run 再切词，消除 Word 分 run 存词造成的切词假象
   （"Age"→"A"+"ge" 要拼回整词），同时把制表符/换行当词边界（防粘连）。
2. XML 侧同样按块内拼合（内联元素并入当前块，`<break/>` 当空格）。

第 2 条是 2026-08 补的。只拼 docx 一侧会让两侧口径不对称：JATS 同样会把词拆开
（`stat<italic>ins</italic>`），逐元素取就是 `stat`+`ins`，与 docx 拼出来的 `statins`
对不上。更坏的是这种不对称会互相抵消，拿假差掩盖真差——评测层同一个毛病改对之后，
立刻暴露出一处原本被掩盖的真改字。所以两侧的拼合是一个不变量的两半，谁也不能单独退回去。
"""

import io
import os
import tempfile
import zipfile

from lxml import etree

from word2jats.verify import conservation as C

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(paragraphs):
    """paragraphs: [[run_or_sep, ...], ...]；str=文本 run，'TAB'=制表符。"""
    def run(x):
        if x == "TAB":
            return "<w:r><w:tab/></w:r>"
        return "<w:r><w:t xml:space='preserve'>%s</w:t></w:r>" % x
    ps = "".join("<w:p>%s</w:p>" % "".join(run(x) for x in runs) for runs in paragraphs)
    doc = ('<?xml version="1.0"?><w:document xmlns:w="%s"><w:body>%s</w:body></w:document>'
           % (_W, ps))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.write(fd, buf.getvalue())
    os.close(fd)
    return path


def test_split_word_is_rejoined():
    """Word 把 "Age" 拆成 "A"+"ge"、"Keywords" 拆成 "Key"+"w"+"ords"——须拼回整词。"""
    path = _docx([["A", "ge"], ["Key", "w", "ords"]])
    try:
        main, _aux = C.docx_tokens(path)
        assert "age" in main and "keywords" in main
        assert "ge" not in main and "ords" not in main
    finally:
        os.remove(path)


def test_tab_separated_words_stay_split():
    """制表符分隔的词不能粘连：只有 tab 分隔时，两词各自成词元。"""
    path = _docx([["alpha", "TAB", "beta"]])
    try:
        main, _aux = C.docx_tokens(path)
        assert "alpha" in main and "beta" in main
        assert "alphabeta" not in main
    finally:
        os.remove(path)


def _xml(body):
    return etree.fromstring("<article><body><sec>%s</sec></body></article>" % body)


def test_xml_inline_split_is_rejoined():
    """内联元素把词拆开时须拼回整词：`stat<italic>ins</italic>` 是 statins，不是 stat+ins。
    这一条与 test_split_word_is_rejoined 是同一个不变量的两侧，缺一侧就会造出假差。"""
    main, _bnet = C.xml_tokens(_xml("<p>use of stat<italic>ins</italic> daily</p>"))
    assert "statins" in main
    assert "stat" not in main and "ins" not in main


def test_xml_superscript_joins_with_neighbour():
    """上下标是内联的：`Ca<sup>2+</sup>` 与 docx 里的 `Ca²⁺` 取词一致。"""
    main, _bnet = C.xml_tokens(_xml("<p>Ca<sup>2+</sup> influx</p>"))
    assert "ca2" in main
    assert "ca" not in main


def test_xml_break_is_a_word_boundary():
    """`<break/>` 是换行、是词边界，不是粘合点——否则表格里由它分隔的多行会粘成怪词。"""
    main, _bnet = C.xml_tokens(_xml("<p>Current smoking<break/>LDL-C</p>"))
    assert "smoking" in main and "ldl" in main
    assert "smokingldl" not in main


def test_xml_block_boundary_does_not_glue():
    """相邻块级元素之间不拼合：两个 <p>、两个单元格的内容不能粘成一个词。"""
    main, _bnet = C.xml_tokens(_xml("<p>connectivity</p><p>network</p>"))
    assert "connectivity" in main and "network" in main
    assert "connectivitynetwork" not in main


def test_xml_bnet_subtree_stays_out_of_main():
    """B 档网络子树（DOI 链接、ORCID URL 等）不进主内容，拼合改动不得破坏这条分流。"""
    main, bnet = C.xml_tokens(_xml(
        '<p>see <ext-link xlink:href="x" xmlns:xlink="http://www.w3.org/1999/xlink">'
        'doiexample</ext-link> here</p>'))
    assert "doiexample" in bnet and "doiexample" not in main
    assert "see" in main and "here" in main
