"""验证公式内容本身，防止只有 MathML 外壳却缺少等式右端。"""
from word2jats.model.source import (
    OmmlResource, ObjectOccurrence, SourceDocument, SourceNode, SourcePart,
)
from word2jats.understand.math import occurrence_math


def test_omml_keeps_all_top_level_terms():
    xml = ('<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
           '<m:r><m:t>y</m:t></m:r><m:r><m:t>=</m:t></m:r>'
           '<m:r><m:t>x</m:t></m:r></m:oMath>')
    source = SourceDocument(
        [SourcePart("document", "document", "/word/document.xml")],
        [SourceNode("doc/p1", "document", "para", None, 0, "\ufffc")],
        [ObjectOccurrence("o1", "omml", "doc/p1", 0, resource_id="m1")],
        [OmmlResource("m1", "document", "/oMath", xml)],
    )
    math = occurrence_math(source, "o1", display=False)

    def text(node):
        return (node.text or "") + "".join(text(child) for child in node.children)

    assert text(math) == "y=x"
    assert math.tag == "math"
    assert dict(math.attributes)["display"] == "inline"
