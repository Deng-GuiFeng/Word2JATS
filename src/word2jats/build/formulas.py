"""公式构建：OMML → MathML3 → JATS disp-formula / inline-formula。

用微软官方 OMML2MML.XSL 的开源移植（libxslt 可作 1.0 处理）把 docx 的 OMML
转为 presentation MathML，再**重建为 mml: 前缀**元素——因为 JATS 的 MathML3 DTD
按字面前缀声明元素（DTD 校验非命名空间感知），不加前缀会校验失败。
"""

from __future__ import annotations

import os
import re
from typing import Optional

from lxml import etree

from .jats import E, sub

MML = "http://www.w3.org/1998/Math/MathML"
_XSL = os.path.join(os.path.dirname(__file__), "..", "resources", "OMML2MML.XSL")


class FormulaBuilder:
    def __init__(self, xsl_path: Optional[str] = None):
        self._transform = None
        path = os.path.abspath(xsl_path or _XSL)
        try:
            self._transform = etree.XSLT(etree.parse(path))
        except Exception:
            self._transform = None  # 转换不可用时降级为文本占位
        self._eq = 0
        self._inline = 0

    # ---- OMML → mml:math ---------------------------------------------- #
    def _omml_to_math(self, omml, display: bool) -> Optional["etree._Element"]:
        if self._transform is None:
            return None
        try:
            result = self._transform(omml)
        except Exception:
            return None
        s = str(result).strip()
        # 去掉 XSLT 输出的 XML 声明，否则包进 <root> 会非法
        s = re.sub(r"^<\?xml[^>]*\?>\s*", "", s)
        if not s:
            return None
        # 结果是若干并列 MathML 顶层节点，包一层再重建为 mml: 前缀
        try:
            frag = etree.fromstring('<root xmlns="%s">%s</root>' % (MML, s))
        except Exception:
            return None
        math = etree.Element("{%s}math" % MML, nsmap={"mml": MML})
        math.set("display", "block" if display else "inline")
        mrow = etree.SubElement(math, "{%s}mrow" % MML)
        for child in frag:
            mrow.append(_reprefix(child))
        # 若 mrow 只有一个子且本身是 mrow，可摊平（可选，略）
        return math

    def inline_formula(self, mathrun) -> Optional["etree._Element"]:
        math = self._omml_to_math(mathrun.omml, display=False)
        if math is None:
            return None
        self._inline += 1
        inf = E("inline-formula")
        inf.append(math)
        return inf

    def disp_formula(self, mathrun, label: Optional[str] = None) -> Optional["etree._Element"]:
        math = self._omml_to_math(mathrun.omml, display=True)
        if math is None:
            return None
        self._eq += 1
        df = E("disp-formula", id="E%03d" % self._eq)
        if label:
            sub(df, "label", label)
        df.append(math)
        return df

    @property
    def stats(self):
        return {"disp": self._eq, "inline": self._inline}


def _reprefix(src) -> "etree._Element":
    """递归把（默认命名空间的）MathML 元素重建为 mml: 前缀元素。"""
    tag = etree.QName(src).localname
    el = etree.Element("{%s}%s" % (MML, tag))
    el.text = src.text
    el.tail = src.tail
    for k, v in src.attrib.items():
        # 去掉可能的命名空间属性键，保留普通属性
        if "}" in k:
            el.set(etree.QName(k).localname, v)
        else:
            el.set(k, v)
    for ch in src:
        el.append(_reprefix(ch))
    return el
