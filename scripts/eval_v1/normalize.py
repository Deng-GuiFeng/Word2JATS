"""归一化(设计 §3.5 / 附录D):只吸收"表示层等价",绝不吸收结构差异。

实现口径:比对在"取值层"进行——逐字段取出文本值再比,属性顺序/命名空间前缀/缩进等
字节层差异天然不进入比较,W3C C14N 关心的问题在此被上位覆盖;本模块只负责文本值的
等价折叠(上下标 Unicode≡<sup> 标记、破折号/引号/空白、大小写、章节编号前缀)。
"""
import re
import unicodedata

# 上/下标 Unicode → ASCII(数字-上标等价:χ² ≡ χ<sup>2</sup>,Ca²⁺ ≡ Ca<sup>2+</sup>)
_SUPSUB = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    " ": " ", " ": " ", " ": " ", " ": " ",  # 各式空格
})
_DASH = re.compile(r"[‐‑‒–—―−]")   # 连字符/破折号族 → '-'
_SQUOTE = re.compile(r"[‘’‚‛]")                    # 弯单引号 → '
_DQUOTE = re.compile(r"[“”„‟]")                    # 弯双引号 → "
_WS = re.compile(r"\s+")
_NUMBERING = re.compile(r"^\s*\d+(\.\d+)*\.?\s+")                      # 章节编号前缀 "2.1 " / "1. "
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)                            # 词元:字母/数字连续段(任意文种)


def norm_text(s):
    """基础归一化:NFC、上下标折叠、破折号/引号/空白统一。保留大小写。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", s).translate(_SUPSUB)
    s = _DASH.sub("-", s)
    s = _SQUOTE.sub("'", s)
    s = _DQUOTE.sub('"', s)
    return _WS.sub(" ", s).strip()


def norm_value(s):
    """取值比较用:基础归一化 + casefold(标题/声明标题大小写归一,附录D)。"""
    return norm_text(s).casefold()


def norm_key(s):
    """对齐键:norm_value 再剥章节编号前缀与尾部标点。只用于对齐,不用于取值比较。"""
    v = _NUMBERING.sub("", norm_text(s)).casefold()
    return v.rstrip(" .:;,")


def tokens(s):
    """词多重集词元:归一化后按字母/数字段切分并 casefold。
    句点/连字符不入词元,从根上消除 "interv." vs "interv"、"3290-5" 类分词假差。"""
    return [t.casefold() for t in _TOKEN.findall(norm_text(s))]


def itertext(el):
    """元素子孙全文,归一化。el 为 None 时返回空串。"""
    if el is None:
        return ""
    return norm_text("".join(el.itertext()))
