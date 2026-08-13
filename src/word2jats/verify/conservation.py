"""内容守恒校验：输出的内容词必须来自 docx（+ B 档白名单），杜绝编造。

这是"出口硬校验"，让 LLM 主导变安全的关键——LLM 若把某字段写成非原文子串，会在这里
作为"编造词"暴露。它**不依赖结构参考（gold-free）**，任何新样例都能跑。

**两侧都按块内拼合再切词，且必须对称。**

docx 侧按段落（`<w:p>`）拼合 run：Word 常把一个词拆进多个 run（首字母单独成 run、拼写
检查/修订痕迹等），逐 `<w:t>` 切会把 "Age" 切成 "A"+"ge"、"Keywords" 切成
"Key"+"w"+"ords"，虚报大量编造/丢失。

XML 侧按块级边界拼合（内联元素并入当前块，`<break/>` 当空格）：JATS 同样会把词拆开，
`stat<italic>ins</italic>` 逐元素取就是 "stat"+"ins"。这一侧曾经漏做（2026-08 修），
后果比"少修一处"更坏——**两侧不对称会互相抵消，拿假差掩盖真差**：评测层同一个毛病改对
之后，立刻暴露出一处原本被抵消掉的真改字（`bioprosthesis` 被写成了 `bioprostheses`）。
所以这两半是一个不变量，见 `tests/test_conservation_tokens.py`。

残余的"编造"主要是三类噪声：① 系统按 JATS 规范注入的刊名/ISSN/版权样板词；② docx 里
相邻词之间既无空格也无标点也无 tab 的不可约边界（如表格续页标记 "ContinuedContinuedeGFR"）；
③ 角标与相邻文字之间的空格有无（docx 写 `1 Department`、输出写 `<sup>1</sup>Department`，
渲染一致但取词不同）。故 n_fab 是**粗略指标**、并非精确编造计数；正文"不改写"的硬保证
来自架构（正文按源块 idx 取回、不经 LLM）。

与评测 L1（`scripts/eval_v1/fidelity.py`）现已同源同口径。差别只在职责：本模块是 gold-free
的出口自检，评测 L1 另有一套以冻结参考作仲裁的主口径，并把公式符号单列成桶。
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


# JATS 内联元素：文本属于所在块，拼合时并入当前块（与 docx 的 run 拆分对称）。
_INLINE = {"bold", "italic", "sup", "sub", "underline", "sc", "monospace", "overline", "strike",
           "roman", "sans-serif", "styled-content", "named-content", "xref", "ext-link", "uri",
           "email", "inline-formula", "inline-graphic", "target", "milestone-start",
           "milestone-end", "private-char", "abbrev", "x"}
# `<break/>` 是换行，是**词边界**不是粘合点：当空格处理，否则表格里由它分隔的多行会粘成
# `smokingldl` 这类怪词。
_XML_SEP = {"break"}


def xml_tokens(root):
    """(主内容词多重集, B 档网络子树词多重集)。**按块内拼合，与 docx_tokens 对称。**

    docx 侧按段落拼合了 run，XML 侧却逐元素取词，两侧口径就不对称：JATS 同样会把一个词
    拆开（`stat<italic>ins</italic>`），逐元素取就是 `stat`+`ins`，与 docx 拼出来的整词
    `statins` 对不上，凭空虚报。更坏的是这种不对称会**互相抵消**，拿假差掩盖真差——
    评测层同一个毛病改对之后，立刻暴露出一处原本被掩盖的真改字（`bioprosthesis`
    被写成了 `bioprostheses`）。故两侧必须同时拼合。
    """
    main, bnet = Counter(), Counter()

    def subtree_text(el):
        return "".join(el.itertext())

    def take(el, buf, in_bnet, inline_only):
        """把 el 这一块内的文本收进 buf。inline_only=True 时不再开新块（已在内联层）。"""
        if el.text:
            buf.append(el.text)
        for c in el:
            if not isinstance(c.tag, str):
                if c.tail:
                    buf.append(c.tail)
                continue
            name = etree.QName(c).localname
            if name in _XML_SEP:
                buf.append(" ")
            elif name in _BNET_TAGS:
                bnet.update(tokens(subtree_text(c)))
            elif inline_only or name in _INLINE:
                take(c, buf, in_bnet, True)
            else:
                flush(buf, in_bnet)
                walk(c, in_bnet)
            if c.tail:
                buf.append(c.tail)

    def flush(buf, in_bnet):
        if buf:
            (bnet if in_bnet else main).update(tokens("".join(buf)))
            del buf[:]

    def walk(el, in_bnet):
        buf = []
        take(el, buf, in_bnet, False)
        flush(buf, in_bnet)

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
