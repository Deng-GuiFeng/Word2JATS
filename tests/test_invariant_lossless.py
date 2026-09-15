"""阶段 3：解析层无损不变量。

本文件内的 `_IndependentDocument` 直接读取 zip/XML，不导入生产解析
辅助函数。它与生产实现分别计算文字、节点树、链接和对象位置，
防止“用同一个错误函数给自己作证”。
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
import posixpath
import re
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree
import pytest

from word2jats.model.source import BinaryResource, ChartResource, SmartArtResource
from word2jats.parse.docx_reader import read_source_docx


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "样例数据"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "m": M, "a": A, "r": R, "v": V, "mc": MC, "c": C, "dgm": DGM}
Q = lambda namespace, local: f"{{{namespace}}}{local}"
OBJECT = "\ufffc"


def _local(element):
    return etree.QName(element).localname


class _IndependentDocument:
    """只为不变量测试实现的独立 OOXML 阅读器。"""

    HYPERLINK = re.compile(r"\bHYPERLINK\s+(?:\"([^\"]+)\"|([^\s]+))", re.I)
    LOCAL = re.compile(r"\\l\s+\"([^\"]+)\"", re.I)

    def __init__(self, path: Path):
        self.archive = ZipFile(path)
        self.nodes = {}
        self.textbox_no = 0
        self.rels = self._relationships("word/document.xml")

    def _relationships(self, part):
        source = PurePosixPath(part)
        name = str(source.parent / "_rels" / (source.name + ".rels"))
        if name not in self.archive.namelist():
            return {}
        root = etree.fromstring(self.archive.read(name))
        result = {}
        for item in root.findall(Q(REL, "Relationship")):
            target = item.get("Target") or ""
            external = item.get("TargetMode") == "External"
            if not external:
                target = posixpath.normpath(posixpath.join(str(source.parent), target))
            result[item.get("Id")] = (target, external)
        return result

    @staticmethod
    def _field_link(instruction):
        match = _IndependentDocument.HYPERLINK.search(instruction)
        if match:
            return match.group(1) or match.group(2)
        match = _IndependentDocument.LOCAL.search(instruction)
        return "#" + match.group(1) if match else None

    def _object_count(self, element):
        name = _local(element)
        if name == "object":
            return 1
        if name == "AlternateContent":
            return len(element.xpath(
                "./mc:Choice//a:blip | ./mc:Choice//v:imagedata", namespaces=NS
            ))
        if element.xpath(".//c:chart | .//dgm:relIds", namespaces=NS):
            return 1
        refs = element.xpath(".//a:blip | .//v:imagedata", namespaces=NS)
        return len([
            item for item in refs
            if not any(_local(parent) == "object" for parent in item.iterancestors())
        ])

    def _paragraph(self, paragraph):
        text = []
        links = []
        objects = []
        fields = []
        textboxes = []
        offset = 0

        def link_for(explicit):
            if explicit:
                return explicit
            for field in reversed(fields):
                if field["phase"] == "result" and field.get("link"):
                    return field["link"]
            return None

        def emit(value, explicit=None):
            nonlocal offset
            if not value or any(field["phase"] == "instruction" for field in fields):
                return
            start = offset
            text.append(value)
            offset += len(value)
            target = link_for(explicit)
            if target:
                links.append((start, offset, target))

        def object_mark(count):
            nonlocal offset
            for _ in range(count):
                objects.append(offset)
                text.append(OBJECT)
                offset += 1

        def run(element, explicit=None):
            for child in element:
                name = _local(child)
                if name == "rPr":
                    continue
                if name == "fldChar":
                    kind = child.get(Q(W, "fldCharType"))
                    if kind == "begin":
                        fields.append({"phase": "instruction", "instruction": ""})
                    elif kind == "separate" and fields:
                        fields[-1]["phase"] = "result"
                        fields[-1]["link"] = self._field_link(fields[-1]["instruction"])
                    elif kind == "end" and fields:
                        fields.pop()
                elif name == "instrText":
                    if fields:
                        fields[-1]["instruction"] += child.text or ""
                elif name == "t":
                    emit(child.text or "", explicit)
                elif name in {"tab", "ptab"}:
                    emit("\t", explicit)
                elif name in {"br", "cr"}:
                    emit("\n", explicit)
                elif name == "noBreakHyphen":
                    emit("\u2011", explicit)
                elif name == "softHyphen":
                    emit("\u00ad", explicit)
                elif name == "sym":
                    try:
                        emit(chr(int(child.get(Q(W, "char")) or "", 16)), explicit)
                    except ValueError:
                        pass
                elif name in {"footnoteReference", "endnoteReference"}:
                    object_mark(1)
                elif name in {"drawing", "pict", "object", "AlternateContent"}:
                    object_mark(self._object_count(child))
                    textboxes.extend(child.xpath(".//w:txbxContent", namespaces=NS))
                elif name == "oMath":
                    object_mark(1)

        def walk(container, explicit=None):
            for child in container:
                name = _local(child)
                if name == "r":
                    run(child, explicit)
                elif name == "hyperlink":
                    rel_id = child.get(Q(R, "id"))
                    anchor = child.get(Q(W, "anchor"))
                    relation = self.rels.get(rel_id)
                    target = relation[0] if relation and relation[1] else (
                        "#" + anchor if anchor else None
                    )
                    walk(child, target)
                elif name == "fldSimple":
                    walk(child, self._field_link(child.get(Q(W, "instr")) or "") or explicit)
                elif name == "oMath":
                    object_mark(1)
                elif name == "oMathPara":
                    object_mark(len(child.findall(Q(M, "oMath"))))
                elif name in {"ins", "moveTo", "smartTag", "customXml"}:
                    walk(child, explicit)
                elif name == "sdt":
                    content = child.find(Q(W, "sdtContent"))
                    if content is not None:
                        walk(content, explicit)
                elif name in {"del", "moveFrom"}:
                    continue
                elif name in {"drawing", "pict", "object", "AlternateContent"}:
                    object_mark(self._object_count(child))
                    textboxes.extend(child.xpath(".//w:txbxContent", namespaces=NS))

        walk(paragraph)
        merged_links = []
        for item in links:
            if merged_links and merged_links[-1][1] == item[0] and merged_links[-1][2] == item[2]:
                merged_links[-1] = (merged_links[-1][0], item[1], item[2])
            else:
                merged_links.append(item)
        return "".join(text), tuple(objects), tuple(merged_links), textboxes

    def _add_paragraph(self, paragraph, node_id, parent):
        text, objects, links, textboxes = self._paragraph(paragraph)
        self.nodes[node_id] = ("para", parent, text, objects, links)
        for textbox in textboxes:
            self.textbox_no += 1
            # 独立按 OOXML 的 Requires 判定可用分支，不把替代表示当成两份正文。
            supported = {*NS.values(),
                'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
                'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
                'http://schemas.openxmlformats.org/drawingml/2006/picture',
                'urn:schemas-microsoft-com:office:office',
                'http://schemas.microsoft.com/office/word/2010/wordml'}
            active, child = True, textbox
            for ancestor in textbox.iterancestors():
                if ancestor.tag == Q(MC, 'AlternateContent'):
                    selected = ancestor.find(Q(MC, 'Fallback'))
                    for choice in ancestor.findall(Q(MC, 'Choice')):
                        requirements = choice.get('Requires', '').split()
                        if requirements and {choice.nsmap.get(p) for p in requirements} <= supported:
                            selected = choice
                            break
                    if child is not selected:
                        active = False
                child = ancestor
            if not active:
                continue
            box_id = f"doc/txbx{self.textbox_no}"
            self.nodes[box_id] = ("textbox", node_id, "", (), ())
            self._blocks(textbox, box_id, box_id, 0, 0)

    def _table(self, table, node_id, parent):
        self.nodes[node_id] = ("table", parent, "", (), ())
        for ri, row in enumerate(table.findall(Q(W, "tr"))):
            row_id = f"{node_id}/r{ri}"
            self.nodes[row_id] = ("row", node_id, "", (), ())
            for ci, cell in enumerate(row.findall(Q(W, "tc"))):
                cell_id = f"{row_id}/c{ci}"
                self.nodes[cell_id] = ("cell", row_id, "", (), ())
                self._blocks(cell, cell_id, cell_id, 0, 0)

    def _blocks(self, container, base, parent, p_no, t_no):
        for child in container:
            name = _local(child)
            if name == "p":
                self._add_paragraph(child, f"{base}/p{p_no}", parent)
                p_no += 1
            elif name == "tbl":
                self._table(child, f"{base}/tbl{t_no}", parent)
                t_no += 1
            elif name in {"ins", "moveTo", "customXml"}:
                p_no, t_no = self._blocks(child, base, parent, p_no, t_no)
            elif name == "sdt":
                content = child.find(Q(W, "sdtContent"))
                if content is not None:
                    p_no, t_no = self._blocks(content, base, parent, p_no, t_no)
        return p_no, t_no

    def read(self):
        root = etree.fromstring(self.archive.read("word/document.xml"))
        body = root.find(Q(W, "body"))
        self._blocks(body, "doc", None, 1, 1)
        return self.nodes


def _samples():
    return [path.parent.name for path in sorted(SAMPLES.glob("*/初始文件.docx"))]


@pytest.mark.parametrize("key", _samples())
def test_text_node_tree_links_and_object_positions_match_independent_reader(key):
    path = SAMPLES / key / "初始文件.docx"
    reference_reader = _IndependentDocument(path)
    try:
        expected = reference_reader.read()
    finally:
        reference_reader.archive.close()
    actual_document = read_source_docx(str(path))
    actual = {}
    for node in actual_document.nodes:
        if node.part != "document":
            continue
        actual[node.node_id] = (
            node.kind, node.parent, node.text,
            tuple(anchor.pos for anchor in node.objects),
            tuple((link.start, link.end, link.target) for link in node.links),
        )
    assert actual == expected


@pytest.mark.parametrize("key", _samples())
def test_every_registered_binary_and_xml_resource_matches_package_bytes(key):
    path = SAMPLES / key / "初始文件.docx"
    document = read_source_docx(str(path))
    with ZipFile(path) as archive:
        for resource in document.resources:
            if isinstance(resource, BinaryResource):
                assert resource.rel_target in archive.namelist()
                assert resource.blob == archive.read(resource.rel_target)
                assert resource.digest == sha256(archive.read(resource.rel_target)).hexdigest()
            elif isinstance(resource, (ChartResource, SmartArtResource)):
                assert resource.node_path in archive.namelist()
                assert resource.xml.encode("utf-8") == archive.read(resource.node_path)


def _write_style_fixture(path: Path):
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
    styles = f"""<w:styles xmlns:w="{W}">
  <w:docDefaults><w:rPrDefault><w:rPr><w:smallCaps/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:styleId="Base" w:default="1"><w:name w:val="Base"/><w:rPr><w:i/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Child"><w:name w:val="Child"/><w:basedOn w:val="Base"/><w:rPr><w:b/></w:rPr></w:style>
  <w:style w:type="character" w:styleId="Emphasis"><w:name w:val="Emphasis"/><w:rPr><w:u w:val="single"/></w:rPr></w:style>
  <w:style w:type="table" w:styleId="Grid"><w:name w:val="Grid"/><w:tblStylePr w:type="firstRow"><w:rPr><w:strike/></w:rPr></w:tblStylePr></w:style>
</w:styles>"""
    document = f"""<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body><w:p><w:pPr><w:pStyle w:val="Child"/></w:pPr>
<w:r><w:t>A</w:t></w:r><w:r><w:rPr><w:rStyle w:val="Emphasis"/><w:b w:val="0"/></w:rPr><w:t>B</w:t></w:r>
</w:p><w:tbl><w:tblPr><w:tblStyle w:val="Grid"/><w:tblLook w:firstRow="1"/></w:tblPr><w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>X</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr/></w:body></w:document>"""
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)


def _write_parts_fixture(path: Path):
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
  <Override PartName="/word/endnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>
  <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
  <Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
</Types>"""
    document = f"""<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body><w:p>
<w:r><w:t>A</w:t></w:r><w:ins><w:r><w:t>B</w:t></w:r></w:ins>
<w:del><w:r><w:delText>OLD</w:delText></w:r></w:del><w:moveFrom><w:r><w:t>PAST</w:t></w:r></w:moveFrom><w:moveTo><w:r><w:t>C</w:t></w:r></w:moveTo>
<w:r><w:footnoteReference w:id="2"/></w:r><w:r><w:endnoteReference w:id="3"/></w:r>
<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> ADDIN SECRET </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>[R]</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>
</w:p><w:foo><w:p><w:r><w:t>Unsupported visible block</w:t></w:r></w:p></w:foo><w:sectPr/></w:body></w:document>"""
    relationships = f"""<Relationships xmlns="{REL}">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes" Target="endnotes.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
</Relationships>"""
    footnotes = f"""<w:footnotes xmlns:w="{W}"><w:footnote w:id="-1" w:type="separator"><w:p/></w:footnote><w:footnote w:id="2"><w:p><w:r><w:footnoteRef/><w:t>Foot text</w:t></w:r></w:p></w:footnote></w:footnotes>"""
    endnotes = f"""<w:endnotes xmlns:w="{W}"><w:endnote w:id="3"><w:p><w:r><w:endnoteRef/><w:t>End text</w:t></w:r></w:p></w:endnote></w:endnotes>"""
    header = f"<w:hdr xmlns:w=\"{W}\"><w:p><w:r><w:t>Header text</w:t></w:r></w:p></w:hdr>"
    footer = f"<w:ftr xmlns:w=\"{W}\"><w:p><w:r><w:t>Footer text</w:t></w:r></w:p></w:ftr>"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        archive.writestr("word/footnotes.xml", footnotes)
        archive.writestr("word/endnotes.xml", endnotes)
        archive.writestr("word/header1.xml", header)
        archive.writestr("word/footer1.xml", footer)


def test_effective_format_follows_defaults_paragraph_character_and_direct_order(tmp_path):
    path = tmp_path / "styles.docx"
    _write_style_fixture(path)
    document = read_source_docx(str(path))
    node = document.node("doc/p1")
    assert node.text == "AB"
    first, second = node.run_spans
    assert (first.run.bold, first.run.italic, first.run.small_caps) == (True, True, True)
    assert (second.run.bold, second.run.italic, second.run.underline,
            second.run.small_caps) == (False, True, True, True)
    table_run = document.node("doc/tbl1/r0/c0/p0").run_spans[0].run
    assert table_run.strike is True


def test_notes_headers_revisions_fields_and_unsupported_structures_are_explicit(tmp_path):
    path = tmp_path / "parts.docx"
    _write_parts_fixture(path)
    document = read_source_docx(str(path))
    paragraph = document.node("doc/p1")
    assert paragraph.text == "ABC" + OBJECT * 2 + "[R]"
    refs = [document.occurrence(anchor.occ_id) for anchor in paragraph.objects]
    assert [(item.kind, item.relations[0].target) for item in refs] == [
        ("footnote-reference", "fn2"), ("endnote-reference", "en3")
    ]
    assert document.node("fn2/p0").text == "Foot text"
    assert document.node("en3/p0").text == "End text"
    assert document.node("header1/p0").text == "Header text"
    assert document.node("footer1/p0").text == "Footer text"
    assert "SECRET" not in paragraph.text
    assert any(item.kind == "block:foo" and item.visible for item in document.unsupported)


def test_object_graph_field_results_and_textboxes_hit_real_acceptance_targets():
    x02 = read_source_docx(str(SAMPLES / "X02" / "初始文件.docx"))
    media = [
        resource for resource in x02.resources
        if isinstance(resource, BinaryResource)
        and resource.rel_target.startswith("word/media/")
    ]
    payloads = [
        resource for resource in x02.resources
        if isinstance(resource, BinaryResource)
        and resource.rel_target.startswith("word/embeddings/")
    ]
    assert len(x02.occurrences) == 41
    assert len(media) == 39
    assert len(payloads) == 26
    reused = {}
    for occurrence in x02.occurrences:
        reused[occurrence.resource_id] = reused.get(occurrence.resource_id, 0) + 1
    assert sorted(count for count in reused.values() if count > 1) == [2, 2]

    sample01 = read_source_docx(str(SAMPLES / "01" / "初始文件.docx"))
    images = [item for item in sample01.occurrences if item.kind == "image"]
    assert len(images) == 7
    assert sorted(
        len([item for item in images if item.composition_id == group])
        for group in {item.composition_id for item in images if item.composition_id}
    ) == [5]
    # 两个逻辑文本框，每个都有 Choice/Fallback 两种表示；保留原定位编号。
    assert {node.node_id for node in sample01.nodes if node.kind == "textbox"} == {'doc/txbx1', 'doc/txbx3'}

    sample03 = read_source_docx(str(SAMPLES / "03" / "初始文件.docx"))
    visible = "\n".join(node.text for node in sample03.nodes)
    assert "ADDIN ZOTERO_ITEM" not in visible
