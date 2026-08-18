"""docx → :class:`Document` IR 的主解析器。

用 python-docx 打开包、读取样式与图片部件，用 lxml 直接遍历 body 元素以保证
**顺序**与对 OMML / 图片的精确控制（python-docx 不暴露 OMML）。
"""

from __future__ import annotations

import io
import hashlib
from pathlib import Path
from typing import Optional

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


def read_source_docx(path: str) -> SourceDocument:
    """阶段 1 入口：为快照与离线重放构建源对象图。"""
    legacy = read_docx(path)
    return _LegacySourceAdapter(legacy, path).build()
