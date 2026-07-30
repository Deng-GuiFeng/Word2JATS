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
        # display 公式:把平衡的 <mo>(</mo>…<mo>)</mo> 折成 <mfenced open close>——
        # 这是 presentation MathML 表示成对括号的标准做法,且与金标准一致(实测金标准
        # display 公式用 mfenced、inline 公式保留 mo 括号,故仅对 display 折叠,不动 inline)。
        if display:
            _fold_fences(mrow)
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


_FENCE = {"(": ")", "[": "]", "{": "}"}


def _is_mo(el, chars) -> bool:
    return etree.QName(el).localname == "mo" and (el.text or "").strip() in chars


def _fold_fences(parent):
    """把 parent 子序列里**同型平衡**的 mo 括号对折成 <mfenced open close>(递归到各层)。"""
    for k in list(parent):
        _fold_fences(k)
    kids = list(parent)
    out, i, n, changed = [], 0, len(kids), False
    while i < n:
        k = kids[i]
        if _is_mo(k, "([{"):
            open_c = (k.text or "").strip()
            close_c = _FENCE[open_c]
            depth, j = 1, i + 1
            while j < n:
                if _is_mo(kids[j], open_c):
                    depth += 1
                elif _is_mo(kids[j], close_c):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j < n and depth == 0:                      # 找到匹配的闭括号
                fenced = etree.Element("{%s}mfenced" % MML)
                fenced.set("open", open_c)
                fenced.set("close", close_c)
                inner = kids[i + 1:j]
                if inner:
                    mrow = etree.SubElement(fenced, "{%s}mrow" % MML)
                    for c in inner:
                        mrow.append(c)
                fenced.tail = kids[j].tail
                out.append(fenced)
                changed = True
                i = j + 1
                continue
        out.append(k)
        i += 1
    if changed:
        for k in list(parent):
            parent.remove(k)
        for k in out:
            parent.append(k)


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
