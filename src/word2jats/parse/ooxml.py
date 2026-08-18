"""OOXML 命名空间常量与底层 XML 工具函数。

集中管理 WordprocessingML / DrawingML / Math 等命名空间，避免散落各处。
"""

from __future__ import annotations

# OOXML 相关命名空间
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "o": "urn:schemas-microsoft-com:office:office",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
}


def qn(tag: str) -> str:
    """把 ``w:val`` 形式的前缀名转成 ``{namespace}local`` 的 Clark 记法。"""
    prefix, local = tag.split(":", 1)
    return "{%s}%s" % (NS[prefix], local)


def local_name(el) -> str:
    """返回元素的本地名（去掉命名空间）。"""
    tag = el.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def w_val(el, attr: str = "val", default=None):
    """读取元素的 ``w:attr`` 属性值。"""
    return el.get(qn("w:" + attr), default)


def is_on(el) -> bool:
    """判断一个布尔型属性元素（如 ``w:b``）是否为"开"。

    OOXML 约定：元素存在且 ``w:val`` 缺省或为 true/1/on 视为开；
    显式 false/0/off 视为关。
    """
    if el is None:
        return False
    v = w_val(el)
    if v is None:
        return True
    return str(v).lower() not in ("false", "0", "off", "none")
