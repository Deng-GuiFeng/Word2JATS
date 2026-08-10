"""内容守恒校验：输出的内容词必须来自 docx（+ B 档白名单），杜绝编造。

这是"出口硬校验"，让 LLM 主导变安全的关键——LLM 若把某字段写成非原文子串，会在这里
作为"编造词"暴露。它**不依赖结构参考（gold-free）**，任何新样例都能跑。

**docx 取词按段落（<w:p>）先拼合 run 再切词**，不逐个 <w:t> 切。原因：Word 常把一个词
拆进多个 run（首字母单独成 run、拼写检查/修订痕迹等），逐 <w:t> 切会把 "Age" 切成
"A"+"ge"、"Keywords" 切成 "Key"+"w"+"ords"，与输出的整词对不上，虚报大量编造/丢失。
段内拼合消掉这类**切词假象**（一个词的多 run 必在同一段落内）。

注意与评测 L1 的口径差异：评测有冻结结构参考做仲裁，能把切词假象自动抵消，故它保持逐
<w:t>；本自检 gold-free、无仲裁，只能靠段内拼合直接降噪。残余的"编造"主要是两类良性
噪声——① 系统按 JATS 规范注入的刊名/ISSN/版权样板词；② docx 里相邻词之间既无空格也无
标点也无 tab 的不可约边界（如表格续页标记 "ContinuedContinuedeGFR"）。故 n_fab 是**粗
略指标**、并非精确编造计数；正文"不改写"的硬保证来自架构（正文按源块 idx 取回、不经 LLM）。
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from collections import Counter

from lxml import etree

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W_T = _W_NS + "t"
W_P = _W_NS + "p"
M_T = "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"
_HDRFTR = re.compile(r"word/(header|footer)\d*\.xml$")
# 段内这些元素代表词边界（制表符/换行/软回车）：拼合时当空格，否则 tab 分隔的词会粘连。
_SEP_TAGS = {_W_NS + "tab", _W_NS + "br", _W_NS + "cr"}


def _para_text(p):
    """一个段落内按文档顺序拼合 run 文本；制表符/换行按空格处理，保住词边界。"""
    parts = []
    for el in p.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        if tag == W_T or tag == M_T:
            parts.append(el.text or "")
        elif tag in _SEP_TAGS:
            parts.append(" ")
    return "".join(parts)

# B 档网络/标识子树（DOI 链接、ORCID URL 等），允许补全，不计入编造
_BNET_TAGS = {"ext-link", "pub-id", "contrib-id", "uri"}

_SUPSUB = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6",
    "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6",
    "₇": "7", "₈": "8", "₉": "9",
})
_DASH = re.compile(r"[‐‑‒–—―−]")
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).translate(_SUPSUB)
    s = _DASH.sub("-", s)
    return _WS.sub(" ", s).strip()


def tokens(s):
    return [t.casefold() for t in _TOKEN.findall(_norm(s))]


def _is_content(tok):
    """内容词：长度≥2 且非纯数字（角标/年份/页码是结构化天然产物，噪声大，不计）。"""
    return len(tok) >= 2 and not tok.isdigit()


def docx_tokens(docx_path):
    """(正文词多重集, 页眉页脚词多重集)。按段落拼合 run 再切词，消除切词假象（见模块头）。"""
    z = zipfile.ZipFile(docx_path)
    main, aux = Counter(), Counter()

    def feed(name, into):
        try:
            root = etree.fromstring(z.read(name))
        except KeyError:
            return
        for p in root.iter(W_P):
            into.update(tokens(_para_text(p)))

    feed("word/document.xml", main)
    feed("word/footnotes.xml", main)
    feed("word/endnotes.xml", main)
    for nm in z.namelist():
        if _HDRFTR.match(nm):
            feed(nm, aux)
    return main, aux


def xml_tokens(root):
    """(主内容词多重集, B 档网络子树词多重集)。"""
    main, bnet = Counter(), Counter()

    def walk(el, in_bnet):
        if not isinstance(el.tag, str):
            return
        b = in_bnet or etree.QName(el).localname in _BNET_TAGS
        if el.text:
            (bnet if b else main).update(tokens(el.text))
        for c in el:
            walk(c, b)
            if c.tail:
                (bnet if in_bnet else main).update(tokens(c.tail))

    walk(root, False)
    return main, bnet


def check(docx_path, root):
    """返回 {fabricated, lost, n_fab, n_lost}。
    fabricated = 输出有·docx 无（排除 B 档网络子树）的内容词 —— 编造，硬违规。
    lost = docx 有·输出无 的内容词 —— 可能漏内容（软信号；含合法剥离的标签/角标）。
    """
    D, D_aux = docx_tokens(docx_path)
    X, _bnet = xml_tokens(root)
    fab = Counter({t: n for t, n in (X - (D + D_aux)).items() if _is_content(t)})
    lost = Counter({t: n for t, n in (D - X).items() if _is_content(t)})
    return {
        "fabricated": dict(fab),
        "lost": dict(lost),
        "n_fab": len(fab),
        "n_lost": len(lost),
    }
