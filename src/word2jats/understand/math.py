"""OMML 出现记录到受控 MathML 语义树的机械变换。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

from ..model.source import OmmlResource, SourceDocument
from ..semantic.model import MathNode


MML = "http://www.w3.org/1998/Math/MathML"


@lru_cache(maxsize=1)
def _transform():
    path = Path(__file__).resolve().parents[1] / "resources" / "OMML2MML.XSL"
    return etree.XSLT(etree.parse(str(path)))


def _node(element) -> MathNode:
    tag = etree.QName(element).localname
    attributes = []
    for raw, value in element.attrib.items():
        name = etree.QName(raw).localname
        # 命名空间声明不是 MathML 内容属性；其他属性由封闭模型审核。
        attributes.append((name, value))
    return MathNode(
        tag=tag, attributes=tuple(attributes), text=element.text,
        children=tuple(_node(child) for child in element
                       if isinstance(child.tag, str)),
    )


def occurrence_math(source: SourceDocument, occurrence_id: str,
                    *, display: bool) -> MathNode:
    occurrence = source.occurrence(occurrence_id)
    resource = source.resource(occurrence.resource_id) if occurrence.resource_id else None
    if not isinstance(resource, OmmlResource):
        raise ValueError(f"{occurrence_id}: 不是 OMML 资源")
    omml = etree.fromstring(resource.omml_xml.encode("utf-8"))
    result = _transform()(omml)
    roots = result.xpath("//*[local-name()='math']")
    if roots:
        math = roots[0]
    else:
        math = etree.Element(f"{{{MML}}}math")
        roots = result.xpath("/*")
        if not roots:
            raise ValueError(f"{occurrence_id}: OMML 转换结果为空")
        # XSLT 可返回多个顶层节点；getroot() 只返回第一个，会截断等式。
        row = etree.SubElement(math, f"{{{MML}}}mrow")
        for root in roots:
            row.append(root)
    math.set("display", "block" if display else "inline")
    return _node(math)
