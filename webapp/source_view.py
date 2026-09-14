"""从真实 Word 事实层生成可定位的阅读视图，不使用转换结果充当原稿。"""
from functools import lru_cache
from html import escape
from pathlib import Path
import hashlib

from lxml import etree

from word2jats.parse.docx_reader import read_source_docx
from word2jats.model.source import OmmlResource
from word2jats.understand.math import occurrence_math


@lru_cache(maxsize=8)
def _read(path, mtime):
    return read_source_docx(path)


def source(path):
    return _read(str(path), Path(path).stat().st_mtime_ns)


def anchor(node_id):
    return "s-" + hashlib.sha256(node_id.encode()).hexdigest()[:16]


def _math(node):
    el = etree.Element(node.tag, dict(node.attributes))
    el.text = node.text
    for child in node.children:
        el.append(_math(child))
    return el


def document(path, task_id):
    doc = source(path)
    children = {}
    for node in doc.nodes:
        children.setdefault(node.parent, []).append(node)

    def object_html(occ_id):
        occ = doc.occurrence(occ_id)
        resource = doc.resource(occ.resource_id) if occ.resource_id else None
        href = f"/api/source-media/{task_id}/{occ_id}"
        if isinstance(resource, OmmlResource):
            try:
                element = _math(occurrence_math(doc, occ_id, display=False))
                element.set("xmlns", "http://www.w3.org/1998/Math/MathML")
                return etree.tostring(element, encoding="unicode")
            except (ValueError, etree.LxmlError):
                return '<span class="object-note">原稿中的公式暂时无法显示，请下载 Word 查看。</span>'
        if resource is not None and hasattr(resource, "blob"):
            if resource.fmt.lower() in {"emf", "wmf", "svg"}:
                return f'<a class="object-note" href="{href}" download>查看原稿中的 {escape(resource.fmt.upper())} 图像</a>'
            return f'<img src="{href}" alt="Word 原稿中的图片" loading="lazy">'
        return '<span class="object-note">原稿中的嵌入对象，请下载 Word 查看。</span>'

    def paragraph(node):
        objects = {o.pos: o.occ_id for o in node.objects}
        boundaries = {0, len(node.text), *objects, *(p+1 for p in objects)}
        for span in node.run_spans:
            boundaries.update((span.start, span.end))
        bounds = sorted(p for p in boundaries if 0 <= p <= len(node.text))
        pieces = []
        for a, b in zip(bounds, bounds[1:]):
            if a in objects:
                pieces.append(object_html(objects[a])); continue
            value = escape(node.text[a:b]).replace("\n", "<br>").replace("\t", "&emsp;")
            span = next((s for s in node.run_spans if s.start <= a < s.end), None)
            if span:
                for attr, tag in (("bold", "strong"), ("italic", "em"), ("superscript", "sup"), ("subscript", "sub"), ("underline", "u")):
                    if getattr(span.run, attr):
                        value = f"<{tag}>{value}</{tag}>"
            pieces.append(value)
        return "".join(pieces)

    def render(node):
        ident = anchor(node.node_id)
        content = children.get(node.node_id, [])
        if node.kind == "table":
            rows = [n for n in content if n.kind == "row"]
            body = []
            for r, row in enumerate(rows):
                cells = []
                for cell in children.get(row.node_id, []):
                    props = cell.properties
                    if props.get("v_merge") == "continue":
                        continue
                    span = max(1, int(props.get("grid_span") or 1))
                    rowspan = 1
                    if props.get("v_merge") == "restart":
                        for later in rows[r+1:]:
                            match = next((c for c in children.get(later.node_id, []) if c.properties.get("column_position") == props.get("column_position")), None)
                            if match is None or match.properties.get("v_merge") != "continue":
                                break
                            rowspan += 1
                    cells.append(f'<td id="{anchor(cell.node_id)}" colspan="{span}" rowspan="{rowspan}">' + ''.join(render(c) for c in children.get(cell.node_id, [])) + '</td>')
                body.append('<tr>' + ''.join(cells) + '</tr>')
            return f'<div class="table-scroll" id="{ident}"><table>' + ''.join(body) + '</table></div>'
        if node.text or node.objects:
            return f'<p id="{ident}">' + paragraph(node) + '</p>'
        return ''.join(render(c) for c in content)

    roots = [n for n in doc.nodes if n.parent not in doc._nodes]
    content = ''.join(render(n) for n in roots)
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Word 原稿</title><link rel="stylesheet" href="/static/reader.css"><body><main class="source-document">' + content + '</main></body></html>'
