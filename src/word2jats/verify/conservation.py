"""内容守恒校验：输出的内容词必须来自 docx（+ B 档白名单），杜绝编造。

口径与评测 L1（scripts/eval/fidelity.py + normalize.py）保持一致，故这里独立复刻其
词元归一化：这是"出口硬校验"，让 LLM 主导变安全的关键——LLM 若把某字段写成非原文子串，
会在这里作为"编造词"暴露，供定点重问 / 剔除。
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from collections import Counter

from lxml import etree

W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
M_T = "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"
_HDRFTR = re.compile(r"word/(header|footer)\d*\.xml$")

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
    """(正文词多重集, 页眉页脚词多重集)。"""
    z = zipfile.ZipFile(docx_path)
    main, aux = Counter(), Counter()

    def feed(name, into):
        try:
            root = etree.fromstring(z.read(name))
        except KeyError:
            return
        for el in root.iter(W_T, M_T):
            into.update(tokens(el.text or ""))

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
