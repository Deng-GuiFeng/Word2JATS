"""守恒度量的 docx 切词回归测试。

锁定修复：docx 侧按段落拼合 run 再切词，消除 Word 分 run 存词造成的切词假象
（"Age"→"A"+"ge" 要拼回整词），同时把制表符/换行当词边界（防粘连）。
"""

import io
import os
import tempfile
import zipfile

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
