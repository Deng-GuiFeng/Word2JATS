"""内容忠实自检（给网页看的诚实口径）。

本项目核心主张是"只加结构、正文一字不改"。这条主张的**构造性保证**在管线里：正文文本
按源块位置整段取回、不经过大模型改写（见 docs/04）。本模块是给用户看的**出口自检**：
把输出与原稿做词表比对，给出覆盖率 + 可逐词核对的差异清单。

口径说明（为什么不用管线里 verify 的原始 n_fab 直接喊"编造 N 词"）：
verify 的守恒检查在 docx 侧**逐个 <w:t> 分别切词**，而 Word 常把一个词拆进多个 run
（首字母单独成 run 等排版原因），于是"paradigm"被切成"p"+"aradigm"，与输出的整词
"paradigm"对不上——这是**切词假象**，不是真编造。这里在 docx 侧**按段落先拼合再切词**，
消掉这类假象；复用项目自带的 tokenizer（conservation.tokens）保持口径一致。剩余的"多出词"
主要是系统按 JATS 规范注入的刊名/ISSN/版权声明等元数据，逐词列出供人核对。
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter

from lxml import etree

from word2jats.verify import conservation as C

_W_P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"


def _docx_para_tokens(docx_path: str):
    """(正文词多重集, 页眉页脚词多重集)。正文按段落先拼合 run 再切词，消除切词假象。"""
    z = zipfile.ZipFile(docx_path)
    main, aux = Counter(), Counter()
    for nm in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
        try:
            root = etree.fromstring(z.read(nm))
        except KeyError:
            continue
        for p in root.iter(_W_P):
            txt = "".join(t.text or "" for t in p.iter(C.W_T, C.M_T))
            main.update(C.tokens(txt))
    for nm in z.namelist():
        if C._HDRFTR.match(nm):
            try:
                root = etree.fromstring(z.read(nm))
            except KeyError:
                continue
            for p in root.iter(_W_P):
                txt = "".join(t.text or "" for t in p.iter(C.W_T, C.M_T))
                aux.update(C.tokens(txt))
    return main, aux


def summary(docx_path: str, xml_bytes: bytes, sample_words: int = 40) -> dict:
    """返回给网页展示的忠实自检结果。"""
    text = re.sub(r"<!DOCTYPE.*?>", "", xml_bytes.decode("utf-8"), count=1, flags=re.DOTALL)
    root = etree.fromstring(text.encode("utf-8"))

    D, D_aux = _docx_para_tokens(docx_path)
    X, _bnet = C.xml_tokens(root)
    Dc = Counter({t: n for t, n in (D + D_aux).items() if C._is_content(t)})
    Xc = Counter({t: n for t, n in X.items() if C._is_content(t)})

    extra = Counter({t: n for t, n in (Xc - Dc).items()})       # 输出有·原稿无
    missing = Counter({t: n for t, n in (Dc - Xc).items()})     # 原稿有·输出无

    out_total = sum(Xc.values())
    src_total = sum(Dc.values())
    extra_occ = sum(extra.values())
    missing_occ = sum(missing.values())

    # 覆盖率：输出中来自原稿的占比(precision) / 原稿被保留的占比(recall)
    from_source = round(100 * (1 - extra_occ / out_total), 1) if out_total else 100.0
    kept = round(100 * (1 - missing_occ / src_total), 1) if src_total else 100.0

    return {
        "from_source_pct": from_source,   # 输出正文 X% 的词来自原稿
        "kept_pct": kept,                 # 原稿 Y% 的词保留在输出
        "out_word_types": len(Xc),
        "src_word_types": len(Dc),
        "n_extra": len(extra),
        "n_missing": len(missing),
        "extra_words": [w for w, _ in extra.most_common(sample_words)],
        "missing_words": [w for w, _ in missing.most_common(sample_words)],
    }
