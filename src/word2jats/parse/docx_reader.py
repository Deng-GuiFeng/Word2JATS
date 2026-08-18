"""docx → :class:`Document` IR 的主解析器。

用 python-docx 打开包、读取样式与图片部件，用 lxml 直接遍历 body 元素以保证
**顺序**与对 OMML / 图片的精确控制（python-docx 不暴露 OMML）。
"""

from __future__ import annotations

import io
import hashlib
from pathlib import Path
import re
from typing import Optional
from zipfile import ZipFile

from docx import Document as _DocxDocument
from lxml import etree
from PIL import Image

from ..model.blocks import (BreakRun, Document, ImageRun, MathRun, Paragraph,
                            Table, TableCell, TableRow, TextRun)
from ..model.source import (
    OBJECT_REPLACEMENT,
    BinaryResource,
    LinkSpan,
    ObjectAnchor,
    ObjectOccurrence,
    ObjectRelation,
    OmmlResource,
    RunRef,
    RunSpan,
    SourceDocument,
    SourceNode,
    SourcePart,
    UnsupportedSource,
)
from .ooxml import is_on, local_name, qn, w_val
from .runs import extract_runs
from .media import MediaRegistry, ObjectSpec, stable_xml_path
from .styles import StyleResolver
from .table_style import cell_properties, row_properties, table_properties


def _sniff_image(blob: bytes):
    """返回 (fmt, size)；无法识别（如 WMF/EMF）时 fmt 由扩展名兜底、size=None。"""
    try:
        im = Image.open(io.BytesIO(blob))
        return im.format, im.size
    except Exception:
        # PIL 不支持 WMF/EMF，按文件头粗判
        if blob[:4] == b"\xd7\xcd\xc6\x9a" or blob[:4] == b"\x01\x00\x00\x00":
            return "WMF", None
        if blob[:4] == b" EMF" or blob[40:44] == b" EMF":
            return "EMF", None
        return None, None


class DocxReader:
    """打开并解析一个 docx 文件。"""

    def __init__(self, path: str):
        self.path = path
        self._doc = _DocxDocument(path)
        self._part = self._doc.part

    # ---- 资源解析器 ---------------------------------------------------- #
    def _resolve_image(self, rel_id: Optional[str]):
        if not rel_id:
            return None
        try:
            rel = self._part.rels[rel_id]
        except KeyError:
            return None
        if rel.is_external:
            return ImageRun(part_name=rel.target_ref, rel_id=rel_id)
        img_part = rel.target_part
        blob = img_part.blob
        fmt, size = _sniff_image(blob)
        return ImageRun(
            part_name=str(img_part.partname),
            rel_id=rel_id,
            blob=blob,
            fmt=fmt,
            size=size,
        )

    def _resolve_hyperlink(self, rel_id: Optional[str]):
        if not rel_id:
            return None
        try:
            rel = self._part.rels[rel_id]
        except KeyError:
            return None
        return rel.target_ref if rel.is_external else None

    # ---- 块解析 -------------------------------------------------------- #
    def _parse_paragraph(self, p_el) -> Paragraph:
        runs = extract_runs(p_el, self._resolve_image, self._resolve_hyperlink)
        style_id = None
        numbering = None
        alignment = None
        ppr = p_el.find(qn("w:pPr"))
        if ppr is not None:
            pstyle = ppr.find(qn("w:pStyle"))
            if pstyle is not None:
                style_id = w_val(pstyle)
            numpr = ppr.find(qn("w:numPr"))
            if numpr is not None:
                numid = numpr.find(qn("w:numId"))
                ilvl = numpr.find(qn("w:ilvl"))
                numbering = (
                    w_val(numid) if numid is not None else None,
                    w_val(ilvl) if ilvl is not None else "0",
                )
            jc = ppr.find(qn("w:jc"))
            if jc is not None:
                alignment = w_val(jc)
        style_name = self._styles.get(style_id) if style_id else None
        return Paragraph(
            runs=runs,
            style_id=style_id,
            style_name=style_name,
            numbering=numbering,
            alignment=alignment,
        )

    def _parse_table(self, tbl_el) -> Table:
        rows = []
        for tr in tbl_el.findall(qn("w:tr")):
            # w:trPr/w:tblHeader = Word 标记的"跨页重复表头行"（可 w:val=false 关闭）——
            # 多行复杂表头的原生信号，供表头行数判定优先采用（比启发式稳健）。
            trpr = tr.find(qn("w:trPr"))
            is_header = trpr is not None and is_on(trpr.find(qn("w:tblHeader")))
            cells = []
            for tc in tr.findall(qn("w:tc")):
                grid_span = 1
                v_merge = None
                tcpr = tc.find(qn("w:tcPr"))
                if tcpr is not None:
                    gs = tcpr.find(qn("w:gridSpan"))
                    if gs is not None:
                        try:
                            grid_span = int(w_val(gs) or "1")
                        except ValueError:
                            grid_span = 1
                    vm = tcpr.find(qn("w:vMerge"))
                    if vm is not None:
                        v_merge = w_val(vm) or "continue"
                # 单元格内的块（可能含嵌套段落 / 表格，这里取段落）
                blocks = [
                    self._parse_paragraph(p) for p in tc.findall(qn("w:p"))
                ]
                cells.append(
                    TableCell(blocks=blocks, grid_span=grid_span, v_merge=v_merge)
                )
            rows.append(TableRow(cells=cells, header=is_header))
        grid_cols = []
        grid = tbl_el.find(qn("w:tblGrid"))
        if grid is not None:
            for gc in grid.findall(qn("w:gridCol")):
                try:
                    grid_cols.append(int(w_val(gc, "w") or "0"))
                except (ValueError, TypeError):
                    grid_cols.append(0)
        return Table(rows=rows, grid_cols=grid_cols)

    # ---- 入口 ---------------------------------------------------------- #
    def read(self) -> Document:
        # 样式表：styleId -> name
        self._styles = {}
        for s in self._doc.styles:
            try:
                if s.style_id:
                    self._styles[s.style_id] = s.name
            except Exception:
                continue

        blocks = []
        body = self._doc.element.body
        for node in body:
            ln = local_name(node)
            if ln == "p":
                blocks.append(self._parse_paragraph(node))
            elif ln == "tbl":
                blocks.append(self._parse_table(node))
            # 忽略 sectPr 等

        return Document(
            blocks=blocks,
            styles=self._styles,
        )


def read_docx(path: str) -> Document:
    """便捷函数：解析 docx 为 IR Document。"""
    return DocxReader(path).read()


class _LegacySourceAdapter:
    """阶段 1 过渡适配器：把旧物理 IR 无损投影为新源对象图。

    阶段 3 会让 OOXML 解析器直接产出同一套契约，并补全脚注、域、
    文本框、修订和完整媒体关系。本适配器不进入现有生产管线。
    """

    _MIME = {
        "jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png",
        "tiff": "image/tiff", "tif": "image/tiff", "gif": "image/gif",
        "bmp": "image/bmp", "wmf": "image/x-wmf", "emf": "image/x-emf",
        "svg": "image/svg+xml",
    }

    def __init__(self, legacy: Document, source_path: str):
        self.legacy = legacy
        self.source_path = source_path
        self.nodes: list[SourceNode] = []
        self.occurrences: list[ObjectOccurrence] = []
        self.resources = []
        self.unsupported: list[UnsupportedSource] = []
        self._order = 0
        self._run_number = 0
        self._occ_number = 0
        self._res_number = 0
        self._resource_by_physical_key: dict[tuple[str, str], str] = {}

    def build(self) -> SourceDocument:
        paragraph_number = 0
        table_number = 0
        for block in self.legacy.blocks:
            if isinstance(block, Paragraph):
                paragraph_number += 1
                self._paragraph(block, f"doc/p{paragraph_number}", None)
            elif isinstance(block, Table):
                table_number += 1
                self._table(block, f"doc/tbl{table_number}")
        part = SourcePart(
            part_id="document", name="document", uri="/word/document.xml",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document.main+xml"
            ),
            node_ids=tuple(node.node_id for node in self.nodes),
        )
        path = Path(self.source_path)
        doc = SourceDocument(
            parts=[part], nodes=self.nodes, occurrences=self.occurrences,
            resources=self.resources, unsupported=self.unsupported,
            metadata={
                "source_name": path.name,
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "adapter": "legacy-ir-v1",
                "styles": dict(sorted(self.legacy.styles.items())),
            },
        )
        doc.validate()
        return doc

    def _next_order(self) -> int:
        value = self._order
        self._order += 1
        return value

    def _table(self, table: Table, node_id: str):
        self.nodes.append(SourceNode(
            node_id=node_id, part="document", kind="table", parent=None,
            order=self._next_order(), properties={"grid_cols": list(table.grid_cols)},
        ))
        for ri, row in enumerate(table.rows):
            row_id = f"{node_id}/r{ri}"
            self.nodes.append(SourceNode(
                node_id=row_id, part="document", kind="row", parent=node_id,
                order=self._next_order(), properties={"header": row.header},
            ))
            for ci, cell in enumerate(row.cells):
                cell_id = f"{row_id}/c{ci}"
                self.nodes.append(SourceNode(
                    node_id=cell_id, part="document", kind="cell", parent=row_id,
                    order=self._next_order(),
                    properties={"grid_span": cell.grid_span, "v_merge": cell.v_merge},
                ))
                for pi, block in enumerate(cell.blocks):
                    if isinstance(block, Paragraph):
                        self._paragraph(block, f"{cell_id}/p{pi}", cell_id)

    def _paragraph(self, paragraph: Paragraph, node_id: str, parent: Optional[str]):
        text_parts: list[str] = []
        run_spans: list[RunSpan] = []
        links: list[LinkSpan] = []
        anchors: list[ObjectAnchor] = []
        offset = 0
        for physical_run in paragraph.runs:
            if isinstance(physical_run, TextRun):
                value = physical_run.text
                if not value:
                    continue
                self._run_number += 1
                run = RunRef(
                    run_id=f"r{self._run_number}", part="document",
                    node_path=node_id, style_id=paragraph.style_id,
                    bold=physical_run.bold, italic=physical_run.italic,
                    superscript=physical_run.superscript,
                    subscript=physical_run.subscript,
                )
                end = offset + len(value)
                run_spans.append(RunSpan(offset, end, run))
                if physical_run.hyperlink:
                    links.append(LinkSpan(offset, end, physical_run.hyperlink))
                text_parts.append(value)
                offset = end
            elif isinstance(physical_run, BreakRun):
                text_parts.append("\n")
                offset += 1
            elif isinstance(physical_run, ImageRun):
                occ_id = self._object(
                    kind="image", node_id=node_id, char_pos=offset,
                    resource_id=self._image_resource(physical_run),
                    properties={"rel_id": physical_run.rel_id,
                                "legacy_fallback": physical_run.is_fallback},
                )
                anchors.append(ObjectAnchor(offset, occ_id))
                text_parts.append(OBJECT_REPLACEMENT)
                offset += 1
            elif isinstance(physical_run, MathRun):
                self._res_number += 1
                res_id = f"res{self._res_number}"
                xml = etree.tostring(physical_run.omml, encoding="unicode")
                self.resources.append(OmmlResource(
                    res_id=res_id, part="document", node_path=node_id, omml_xml=xml,
                ))
                occ_id = self._object(
                    kind="omml", node_id=node_id, char_pos=offset,
                    resource_id=res_id, properties={"display": physical_run.display},
                )
                anchors.append(ObjectAnchor(offset, occ_id))
                text_parts.append(OBJECT_REPLACEMENT)
                offset += 1
        self.nodes.append(SourceNode(
            node_id=node_id, part="document", kind="para", parent=parent,
            order=self._next_order(), text="".join(text_parts),
            run_spans=run_spans, links=links, objects=anchors,
            properties={
                "style_id": paragraph.style_id,
                "style_name": paragraph.style_name,
                "numbering": list(paragraph.numbering) if paragraph.numbering else None,
                "alignment": paragraph.alignment,
            },
        ))

    def _object(self, *, kind: str, node_id: str, char_pos: int,
                resource_id: Optional[str], properties: dict) -> str:
        self._occ_number += 1
        occ_id = f"o{self._occ_number}"
        self.occurrences.append(ObjectOccurrence(
            occ_id=occ_id, kind=kind, node_id=node_id, char_pos=char_pos,
            resource_id=resource_id, properties=properties,
        ))
        return occ_id

    def _image_resource(self, image: ImageRun) -> Optional[str]:
        if image.blob is None:
            self.unsupported.append(UnsupportedSource(
                part="document", node_path=image.part_name, kind="external-image",
                detail="外部图片关系没有内嵌字节", visible=True,
            ))
            return None
        digest = hashlib.sha256(image.blob).hexdigest()
        key = (image.part_name, digest)
        if key in self._resource_by_physical_key:
            return self._resource_by_physical_key[key]
        self._res_number += 1
        res_id = f"res{self._res_number}"
        fmt = (image.fmt or "unknown").lower()
        self.resources.append(BinaryResource(
            res_id=res_id, rel_target=image.part_name,
            content_type=self._MIME.get(fmt, "application/octet-stream"),
            blob=image.blob, fmt=fmt, part="document",
        ))
        self._resource_by_physical_key[key] = res_id
        return res_id


class _Field:
    """复合域状态：指令阶段不输出，只保留 separate 后的域结果。"""

    def __init__(self):
        self.phase = "instruction"
        self.instruction = ""
        self.hyperlink: Optional[str] = None


class _ParagraphState:
    def __init__(self, reader, node_id: str, part_id: str, part_uri: str,
                 paragraph_style: Optional[str], note_target: Optional[str],
                 table_context: Optional[dict]):
        self.reader = reader
        self.node_id = node_id
        self.part_id = part_id
        self.part_uri = part_uri
        self.paragraph_style = paragraph_style
        self.note_target = note_target
        self.table_context = table_context
        self.text: list[str] = []
        self.run_spans: list[RunSpan] = []
        self.links: list[LinkSpan] = []
        self.objects: list[ObjectAnchor] = []
        self.fields: list[_Field] = []
        self.textboxes = []
        self.offset = 0

    def active_link(self, explicit: Optional[str]) -> Optional[str]:
        if explicit:
            return explicit
        for field in reversed(self.fields):
            if field.phase == "result" and field.hyperlink:
                return field.hyperlink
        return None

    @property
    def suppress_text(self) -> bool:
        return any(field.phase == "instruction" for field in self.fields)

    def append_text(self, value: str, run: Optional[RunRef],
                    hyperlink: Optional[str] = None):
        if not value or self.suppress_text:
            return
        start = self.offset
        self.text.append(value)
        self.offset += len(value)
        if run is not None:
            self.run_spans.append(RunSpan(start, self.offset, run))
        target = self.active_link(hyperlink)
        if target:
            self.links.append(LinkSpan(start, self.offset, target))

    def append_object(self, spec: ObjectSpec):
        occurrence = self.reader._new_occurrence(
            spec, self.node_id, self.offset, self.part_id
        )
        self.objects.append(ObjectAnchor(self.offset, occurrence.occ_id))
        self.text.append(OBJECT_REPLACEMENT)
        self.offset += 1
        if not occurrence.resource_id and (spec.properties or {}).get("external_target"):
            self.reader.unsupported.append(UnsupportedSource(
                part=self.part_id,
                node_path=(spec.properties or {}).get("xml_path", self.node_id),
                kind="external-image",
                detail=f"外部媒体: {(spec.properties or {}).get('external_target')}",
                visible=True,
            ))


class SourceDocxReader:
    """WordprocessingML Transitional -> 无损、可寻址源对象图。"""

    _HYPERLINK = re.compile(
        r"\bHYPERLINK\s+(?:\"([^\"]+)\"|([^\s]+))", re.IGNORECASE
    )
    _LOCAL_LINK = re.compile(r"\\l\s+\"([^\"]+)\"", re.IGNORECASE)

    def __init__(self, path: str):
        self.path = Path(path)
        self.archive = ZipFile(self.path)
        self.media = MediaRegistry(self.archive)
        styles = self.archive.read("word/styles.xml") if "word/styles.xml" in self.archive.namelist() else None
        self.styles = StyleResolver(styles)
        self.nodes: list[SourceNode] = []
        self.occurrences: list[ObjectOccurrence] = []
        self.unsupported: list[UnsupportedSource] = []
        self.parts: list[SourcePart] = []
        self._order = 0
        self._run_number = 0
        self._occ_number = 0
        self._textbox_number = 0
        self._parsed_textboxes: set[tuple[str, str]] = set()

    def _next_order(self) -> int:
        value = self._order
        self._order += 1
        return value

    def _new_occurrence(self, spec: ObjectSpec, node_id: str, char_pos: int,
                        part_id: str) -> ObjectOccurrence:
        self._occ_number += 1
        value = ObjectOccurrence(
            occ_id=f"o{self._occ_number}", kind=spec.kind,
            node_id=node_id, char_pos=char_pos,
            representation_group_id=spec.representation_group_id,
            representation_role=spec.representation_role,
            composition_id=spec.composition_id,
            composition_index=spec.composition_index,
            resource_id=spec.resource_id,
            relations=list(spec.relations),
            properties={"source_part": part_id, **(spec.properties or {})},
        )
        self.occurrences.append(value)
        return value

    @staticmethod
    def _paragraph_style(element) -> Optional[str]:
        properties = element.find(qn("w:pPr"))
        style = properties.find(qn("w:pStyle")) if properties is not None else None
        return w_val(style) if style is not None else None

    @staticmethod
    def _paragraph_properties(element, style_id, style_name) -> dict:
        properties = element.find(qn("w:pPr"))
        numbering = None
        alignment = None
        if properties is not None:
            numpr = properties.find(qn("w:numPr"))
            if numpr is not None:
                numid = numpr.find(qn("w:numId"))
                level = numpr.find(qn("w:ilvl"))
                numbering = [
                    w_val(numid) if numid is not None else None,
                    w_val(level) if level is not None else "0",
                ]
            jc = properties.find(qn("w:jc"))
            alignment = w_val(jc) if jc is not None else None
        return {
            "style_id": style_id,
            "style_name": style_name,
            "numbering": numbering,
            "alignment": alignment,
            "xml_path": stable_xml_path(element),
        }

    def _instruction_link(self, instruction: str) -> Optional[str]:
        match = self._HYPERLINK.search(instruction)
        if match:
            return match.group(1) or match.group(2)
        match = self._LOCAL_LINK.search(instruction)
        return "#" + match.group(1) if match else None

    def _relationship_link(self, part_uri: str, element) -> Optional[str]:
        rel_id = element.get(qn("r:id"))
        anchor = w_val(element, "anchor")
        relationship = self.media.relationships.get(part_uri, rel_id)
        if relationship and relationship.external:
            return relationship.target
        return "#" + anchor if anchor else None

    def _run(self, element, state: _ParagraphState, hyperlink=None):
        self._run_number += 1
        run = self.styles.effective(
            element, state.paragraph_style, run_id=f"r{self._run_number}",
            part=state.part_id,
            node_path=f"{state.part_uri}:{stable_xml_path(element)}",
            table_context=state.table_context,
        )
        for child in element:
            name = local_name(child)
            if name == "rPr":
                continue
            if name == "fldChar":
                kind = w_val(child, "fldCharType")
                if kind == "begin":
                    state.fields.append(_Field())
                elif kind == "separate" and state.fields:
                    field = state.fields[-1]
                    field.phase = "result"
                    field.hyperlink = self._instruction_link(field.instruction)
                elif kind == "end" and state.fields:
                    state.fields.pop()
                continue
            if name == "instrText":
                if state.fields:
                    state.fields[-1].instruction += child.text or ""
                continue
            if name == "t":
                state.append_text(child.text or "", run, hyperlink)
            elif name == "tab" or name == "ptab":
                state.append_text("\t", run, hyperlink)
            elif name in {"br", "cr"}:
                state.append_text("\n", run, hyperlink)
            elif name == "noBreakHyphen":
                state.append_text("\u2011", run, hyperlink)
            elif name == "softHyphen":
                state.append_text("\u00ad", run, hyperlink)
            elif name == "sym":
                raw = w_val(child, "char") or ""
                try:
                    state.append_text(chr(int(raw, 16)), run, hyperlink)
                except ValueError:
                    self._unsupported(state, child, "invalid-symbol", True)
            elif name in {"delText", "lastRenderedPageBreak", "footnoteRef",
                          "endnoteRef", "separator", "continuationSeparator"}:
                # 删除文字按“接受修订”口径不输出；注释本身的自动序号
                # 是 Word 生成内容，关系由引用出现单独承载。
                continue
            elif name in {"footnoteReference", "endnoteReference"}:
                note_id = w_val(child, "id") or ""
                prefix = "fn" if name == "footnoteReference" else "en"
                target = f"{prefix}{note_id}"
                state.append_object(ObjectSpec(
                    kind="footnote-reference" if prefix == "fn" else "endnote-reference",
                    resource_id=None,
                    relations=(ObjectRelation("references", target),),
                    properties={"note_id": note_id, "xml_path": stable_xml_path(child)},
                ))
            elif name in {"drawing", "pict", "object", "AlternateContent"}:
                for spec in self.media.scan(child, state.part_uri):
                    state.append_object(spec)
                state.textboxes.extend(child.xpath(".//w:txbxContent", namespaces={"w": qn("w:p").split("}")[0][1:]}))
            elif name == "oMath":
                self._math(child, state, display=False)
            elif name in {"bookmarkStart", "bookmarkEnd", "commentReference",
                          "annotationRef"}:
                continue
            else:
                visible = self._has_visible(child)
                if visible:
                    self._unsupported(state, child, f"run-child:{name}", True)

    def _math(self, element, state: _ParagraphState, *, display: bool):
        resource_id = self.media.add_omml(
            state.part_id, f"{state.part_uri}:{stable_xml_path(element)}", element
        )
        state.append_object(ObjectSpec(
            kind="omml", resource_id=resource_id,
            properties={"display": display, "xml_path": stable_xml_path(element)},
        ))

    def _inline_children(self, container, state: _ParagraphState,
                         hyperlink: Optional[str] = None):
        for child in container:
            name = local_name(child)
            if name in {"pPr", "proofErr", "bookmarkStart", "bookmarkEnd",
                        "commentRangeStart", "commentRangeEnd", "permStart", "permEnd"}:
                continue
            if name == "r":
                self._run(child, state, hyperlink)
            elif name == "hyperlink":
                self._inline_children(
                    child, state, self._relationship_link(state.part_uri, child)
                )
            elif name == "fldSimple":
                instruction = w_val(child, "instr") or ""
                self._inline_children(
                    child, state, self._instruction_link(instruction) or hyperlink
                )
            elif name == "oMath":
                self._math(child, state, display=False)
            elif name == "oMathPara":
                for math in child.findall(qn("m:oMath")):
                    self._math(math, state, display=True)
            elif name in {"ins", "moveTo", "smartTag", "customXml"}:
                self._inline_children(child, state, hyperlink)
            elif name == "sdt":
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    self._inline_children(content, state, hyperlink)
            elif name in {"del", "moveFrom"}:
                continue
            elif name in {"drawing", "pict", "object", "AlternateContent"}:
                for spec in self.media.scan(child, state.part_uri):
                    state.append_object(spec)
                state.textboxes.extend(child.xpath(".//w:txbxContent", namespaces={"w": qn("w:p").split("}")[0][1:]}))
            else:
                visible = self._has_visible(child)
                if visible:
                    self._unsupported(state, child, f"paragraph-child:{name}", True)
                    # 先保留能识别的内层 run，同时以 unsupported 显性拦截交付。
                    self._inline_children(child, state, hyperlink)

    @staticmethod
    def _has_visible(element) -> bool:
        return bool(element.xpath(
            ".//w:t | .//w:tab | .//w:br | .//w:cr | .//w:drawing | "
            ".//w:pict | .//w:object | .//m:oMath",
            namespaces={
                "w": qn("w:p").split("}")[0][1:],
                "m": qn("m:oMath").split("}")[0][1:],
            },
        ))

    def _unsupported(self, state: _ParagraphState, element, kind: str, visible: bool):
        self.unsupported.append(UnsupportedSource(
            part=state.part_id,
            node_path=f"{state.part_uri}:{stable_xml_path(element)}",
            kind=kind, detail="受支持 OOXML 配置外的可见结构",
            visible=visible,
        ))

    @staticmethod
    def _merge_spans(spans: list[RunSpan]) -> list[RunSpan]:
        result = []
        for span in spans:
            if result and result[-1].end == span.start and result[-1].run == span.run:
                old = result[-1]
                result[-1] = RunSpan(old.start, span.end, old.run)
            else:
                result.append(span)
        return result

    @staticmethod
    def _merge_links(links: list[LinkSpan]) -> list[LinkSpan]:
        result = []
        for link in links:
            if result and result[-1].end == link.start and result[-1].target == link.target:
                old = result[-1]
                result[-1] = LinkSpan(old.start, link.end, old.target, old.source)
            else:
                result.append(link)
        return result

    def _paragraph(self, element, node_id: str, parent: Optional[str],
                   part_id: str, part_uri: str,
                   note_target: Optional[str] = None,
                   table_context: Optional[dict] = None):
        style_id = self._paragraph_style(element)
        state = _ParagraphState(
            self, node_id, part_id, part_uri, style_id, note_target,
            table_context,
        )
        self._inline_children(element, state)
        node = SourceNode(
            node_id=node_id, part=part_id, kind="para", parent=parent,
            order=self._next_order(), text="".join(state.text),
            run_spans=self._merge_spans(state.run_spans),
            links=self._merge_links(state.links), objects=state.objects,
            properties=self._paragraph_properties(
                element, style_id, self.styles.style_name(style_id)
            ),
        )
        self.nodes.append(node)
        for textbox in state.textboxes:
            self._textbox(textbox, node_id, part_id, part_uri)

    def _textbox(self, element, anchor_node: str, part_id: str, part_uri: str):
        key = (part_uri, stable_xml_path(element))
        if key in self._parsed_textboxes:
            return
        self._parsed_textboxes.add(key)
        self._textbox_number += 1
        prefix = "doc" if part_id == "document" else part_id
        base = f"{prefix}/txbx{self._textbox_number}"
        root = SourceNode(
            node_id=base, part=part_id, kind="textbox", parent=anchor_node,
            order=self._next_order(),
            properties={"anchored_to": anchor_node, "xml_path": key[1]},
        )
        self.nodes.append(root)
        self._blocks(
            element, base, base, part_id, part_uri,
            paragraph_start=0, table_start=0,
        )

    def _table(self, element, node_id: str, parent: Optional[str],
               part_id: str, part_uri: str):
        physical = table_properties(element)
        table_node = SourceNode(
            node_id=node_id, part=part_id, kind="table", parent=parent,
            order=self._next_order(), properties={
                **physical, "xml_path": stable_xml_path(element)
            },
        )
        self.nodes.append(table_node)
        rows = element.findall(qn("w:tr"))
        total_columns = len(physical.get("grid_cols_twips") or [])
        if not total_columns:
            total_columns = max((len(row.findall(qn("w:tc"))) for row in rows), default=1)
        for row_index, row in enumerate(rows):
            row_id = f"{node_id}/r{row_index}"
            self.nodes.append(SourceNode(
                node_id=row_id, part=part_id, kind="row", parent=node_id,
                order=self._next_order(), properties={
                    **row_properties(row), "xml_path": stable_xml_path(row)
                },
            ))
            column_position = 0
            for cell_index, cell in enumerate(row.findall(qn("w:tc"))):
                cell_id = f"{row_id}/c{cell_index}"
                cell_physical = cell_properties(cell)
                self.nodes.append(SourceNode(
                    node_id=cell_id, part=part_id, kind="cell", parent=row_id,
                    order=self._next_order(), properties={
                        **cell_physical, "xml_path": stable_xml_path(cell),
                        "column_position": column_position,
                    },
                ))
                self._blocks(
                    cell, cell_id, cell_id, part_id, part_uri,
                    paragraph_start=0, table_start=0,
                    table_context={
                        "style_id": physical.get("style_id"),
                        "look": physical.get("look"),
                        "row": row_index, "col": column_position,
                        "colspan": int(cell_physical.get("grid_span") or 1),
                        "rows": len(rows), "cols": total_columns,
                    },
                )
                column_position += int(cell_physical.get("grid_span") or 1)

    def _blocks(self, container, base: str, parent: Optional[str], part_id: str,
                part_uri: str, *, paragraph_start: int, table_start: int,
                note_target: Optional[str] = None,
                table_context: Optional[dict] = None):
        paragraph_number = paragraph_start
        table_number = table_start
        for child in container:
            name = local_name(child)
            if name == "p":
                node_id = f"{base}/p{paragraph_number}"
                paragraph_number += 1
                self._paragraph(
                    child, node_id, parent, part_id, part_uri, note_target,
                    table_context,
                )
            elif name == "tbl":
                node_id = f"{base}/tbl{table_number}"
                table_number += 1
                self._table(child, node_id, parent, part_id, part_uri)
            elif name == "sdt":
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    paragraph_number, table_number = self._blocks(
                        content, base, parent, part_id, part_uri,
                        paragraph_start=paragraph_number, table_start=table_number,
                        note_target=note_target,
                        table_context=table_context,
                    )
            elif name in {"ins", "moveTo", "customXml"}:
                paragraph_number, table_number = self._blocks(
                    child, base, parent, part_id, part_uri,
                    paragraph_start=paragraph_number, table_start=table_number,
                    note_target=note_target,
                    table_context=table_context,
                )
            elif name in {"del", "moveFrom", "sectPr", "bookmarkStart", "bookmarkEnd"}:
                continue
            elif self._has_visible(child):
                self.unsupported.append(UnsupportedSource(
                    part_id, f"{part_uri}:{stable_xml_path(child)}",
                    f"block:{name}", "受支持 OOXML 配置外的可见块", True,
                ))
        return paragraph_number, table_number

    def _part(self, part_id: str, name: str, uri: str, root, prefix: str):
        before = len(self.nodes)
        self._blocks(
            root, prefix, None, part_id, uri,
            paragraph_start=1 if part_id == "document" else 0,
            table_start=1 if part_id == "document" else 0,
        )
        node_ids = tuple(node.node_id for node in self.nodes[before:])
        self.parts.append(SourcePart(
            part_id, name, "/" + uri,
            self.media._content_type(uri), node_ids,
        ))

    def _notes_part(self, part_id: str, name: str, uri: str, root, item_tag: str,
                    prefix: str):
        before = len(self.nodes)
        for item in root.findall(qn(f"w:{item_tag}")):
            raw_id = w_val(item, "id") or ""
            try:
                numeric = int(raw_id)
            except ValueError:
                numeric = -1
            note_type = w_val(item, "type")
            if numeric < 0 or note_type in {"separator", "continuationSeparator"}:
                continue
            root_id = f"{prefix}{raw_id}"
            self.nodes.append(SourceNode(
                root_id, part_id, item_tag, None, self._next_order(),
                properties={"note_id": raw_id, "note_type": note_type,
                            "xml_path": stable_xml_path(item)},
            ))
            self._blocks(
                item, root_id, root_id, part_id, uri,
                paragraph_start=0, table_start=0, note_target=root_id,
            )
        node_ids = tuple(node.node_id for node in self.nodes[before:])
        self.parts.append(SourcePart(
            part_id, name, "/" + uri,
            self.media._content_type(uri), node_ids,
        ))

    def read(self) -> SourceDocument:
        document_uri = "word/document.xml"
        document_root = etree.fromstring(self.archive.read(document_uri))
        body = document_root.find(qn("w:body"))
        if body is None:
            raise ValueError("document.xml 缺 w:body")
        self._part("document", "document", document_uri, body, "doc")

        relationships = self.media.relationships.for_part(document_uri)
        related = []
        for relationship in relationships.values():
            if relationship.external or relationship.target not in self.archive.namelist():
                continue
            tail = relationship.rel_type.rsplit("/", 1)[-1]
            if tail in {"footnotes", "endnotes", "header", "footer"}:
                related.append((tail, relationship.target))
        seen = set()
        counters = {"header": 0, "footer": 0}
        for kind, uri in related:
            if uri in seen:
                continue
            seen.add(uri)
            root = etree.fromstring(self.archive.read(uri))
            if kind == "footnotes":
                self._notes_part("footnotes", "footnotes", uri, root, "footnote", "fn")
            elif kind == "endnotes":
                self._notes_part("endnotes", "endnotes", uri, root, "endnote", "en")
            else:
                counters[kind] += 1
                part_id = f"{kind}{counters[kind]}"
                self._part(part_id, kind, uri, root, part_id)

        result = SourceDocument(
            parts=self.parts, nodes=self.nodes, occurrences=self.occurrences,
            resources=self.media.resources, unsupported=self.unsupported,
            metadata={
                "source_name": self.path.name,
                "source_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                "parser": "ooxml-source-v2",
                "revision_policy": "accepted",
                "header_footer_policy": "template-content",
                "supported_profile": "WordprocessingML Transitional (Word 2007+)",
            },
        )
        result.validate()
        return result


def read_source_docx(path: str) -> SourceDocument:
    """直接从 OOXML 包构建新源对象图，不经旧 IR 压平。"""
    reader = SourceDocxReader(path)
    try:
        return reader.read()
    finally:
        reader.archive.close()
