"""docx → :class:`Document` IR 的主解析器。

用 python-docx 打开包、读取样式与图片部件，用 lxml 直接遍历 body 元素以保证
**顺序**与对 OMML / 图片的精确控制（python-docx 不暴露 OMML）。
"""

from __future__ import annotations

import io
from typing import Optional

from docx import Document as _DocxDocument
from PIL import Image

from ..model.blocks import (Document, ImageRun, Paragraph, Table, TableCell,
                            TableRow)
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
