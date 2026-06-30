"""表格构建：IR Table（真 w:tbl）→ JATS table-wrap/table（XHTML 表模型）。

处理 gridSpan→colspan、vMerge→rowspan；首行作为表头（thead）。图片表 / 制表符模拟
表不在此处理（best-effort，见文档已知限制）。
"""

from __future__ import annotations

from ..model.blocks import Table
from .jats import E, append_inline, sub


class TableBuilder:
    def __init__(self):
        self._n = 0
        self.numbers = []  # 已生成表格的编号

    def build(self, tbl: Table, caption_label=None, caption_runs=None,
              inline_math=None, number=None) -> "etree._Element":
        self._n += 1
        num = number or self._n
        self.numbers.append(num)
        tid = "T%03d" % num
        wrap = E("table-wrap", id=tid)
        if caption_label:
            sub(wrap, "label", caption_label)
        if caption_runs:
            cap = sub(wrap, "caption")
            p = sub(cap, "p")
            append_inline(p, caption_runs, inline_math)

        table = sub(wrap, "table", frame="hsides", rules="groups")
        ncols = self._col_count(tbl)
        if ncols:
            cg = sub(table, "colgroup")
            widths = self._col_widths(tbl, ncols)
            for w in widths:
                sub(cg, "col", **({"width": w} if w else {}))

        rows = tbl.rows
        if not rows:
            return wrap
        # 计算每个 restart 单元格的 rowspan(纵向合并)
        rowspans = self._compute_rowspans(rows)
        n_head = self._header_rows(rows)
        thead = sub(table, "thead")
        for ri in range(n_head):
            self._row(thead, rows[ri], ri, rowspans, header=True, inline_math=inline_math)
        if len(rows) > n_head:
            tbody = sub(table, "tbody")
            for ri in range(n_head, len(rows)):
                self._row(tbody, rows[ri], ri, rowspans, header=False, inline_math=inline_math)
        return wrap

    @staticmethod
    def _col_positions(row):
        """返回每个单元格的起始列号(按 grid_span 累加)。"""
        pos, out = 0, []
        for c in row.cells:
            out.append(pos)
            pos += (c.grid_span or 1)
        return out

    def _compute_rowspans(self, rows):
        """rowspans[(ri, 单元格在该行的下标)] = 纵向跨行数。

        对 v_merge=='restart' 的单元格,统计其下方同一起始列、连续 v_merge=='continue'
        的行数 +1。continue 单元格本身不输出(被覆盖)。
        """
        rowspans = {}
        # 预算每行各单元格的起始列
        positions = [self._col_positions(r) for r in rows]
        for ri, row in enumerate(rows):
            for ci, cell in enumerate(row.cells):
                if cell.v_merge != "restart":
                    continue
                col = positions[ri][ci]
                span = 1
                for rj in range(ri + 1, len(rows)):
                    # 找 rj 行在同一起始列、且为 continue 的单元格
                    hit = None
                    for cj, c2 in enumerate(rows[rj].cells):
                        if positions[rj][cj] == col:
                            hit = c2
                            break
                    if hit is not None and hit.v_merge == "continue":
                        span += 1
                    else:
                        break
                if span > 1:
                    rowspans[(ri, ci)] = span
        return rowspans

    def _header_rows(self, rows):
        """判定表头行数。

        默认 1。真正的两行分组表头的判据:首行有跨列分组(colspan>1),**且**次行
        含纵向合并续行单元格(continue)——即首行的行标题向下贯穿、其分组列在次行被
        细分为子表头。仅有 colspan 而次行无 continue 的(如"Predictors"跨2列但次行是
        数据)仍按单行表头,避免把数据行误并入表头(对照金标准校准)。
        """
        if not rows:
            return 0
        first_has_group = any((c.grid_span or 1) > 1 for c in rows[0].cells)
        if first_has_group and len(rows) > 1 and \
                any(c.v_merge == "continue" for c in rows[1].cells):
            return 2
        return 1

    def _row(self, parent, row, ri, rowspans, header, inline_math):
        # 若整行单元格都是上方 rowspan 的延续，则不产出 <tr>（空 tr 违反 DTD）
        if all(c.v_merge == "continue" for c in row.cells) and row.cells:
            return
        from ..model.blocks import Paragraph
        tr = sub(parent, "tr")
        for ci, cell in enumerate(row.cells):
            if cell.v_merge == "continue":
                continue  # 被上方单元格 rowspan 覆盖
            tag = "th" if header else "td"
            attrs = {}
            if cell.grid_span and cell.grid_span > 1:
                attrs["colspan"] = str(cell.grid_span)
            rs = rowspans.get((ri, ci))
            if rs and rs > 1:
                attrs["rowspan"] = str(rs)
            if header:
                attrs["scope"] = "col"
            td = sub(tr, tag, **attrs)
            # 单元格内多段=多行:段间插 <break/>,避免相邻行文本粘连(与金标准一致)
            first_para = True
            for blk in cell.blocks:
                if isinstance(blk, Paragraph):
                    if not first_para and blk.text.strip():
                        td.append(E("break"))
                    append_inline(td, blk.runs, inline_math, allow_break=True)
                    if blk.text.strip():
                        first_para = False

    def _col_count(self, tbl: Table) -> int:
        if tbl.grid_cols:
            return len(tbl.grid_cols)
        return max((sum(c.grid_span for c in r.cells) for r in tbl.rows), default=0)

    def _col_widths(self, tbl: Table, ncols: int):
        if tbl.grid_cols and sum(tbl.grid_cols) > 0:
            total = sum(tbl.grid_cols)
            return ["%.1f%%" % (c * 100.0 / total) for c in tbl.grid_cols]
        return [None] * ncols
