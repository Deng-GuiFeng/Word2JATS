"""BuildContext：装配各 builder，向 body 构建提供统一的内容判定/生成原语。"""

from __future__ import annotations

import re
from typing import Optional

from ..classify import patterns as P
from ..model.blocks import Paragraph
from .figures import FigureBuilder
from .formulas import FormulaBuilder
from .jats import drop_leading_chars
from .tables import TableBuilder

_NUM_LABEL = re.compile(r"^\s*\(?\s*(\d+)\s*\)?\s*$")


class BuildContext:
    def __init__(self, formula: FormulaBuilder, figures: FigureBuilder,
                 tables: TableBuilder, llm=None, out_dir="output", article_id="article"):
        self.formula = formula
        self.figures = figures
        self.tables = tables
        self._pending_table_caption = None
        self.ref_num_to_id = {}             # 文献显示号→ref id(处理跳号)
        self.llm = llm                      # 可选:本地多模态模型(看图读表)
        self.out_dir = out_dir
        self.article_id = article_id
        self._tmp_idx = 0

    @property
    def vision_ok(self) -> bool:
        return self.llm is not None and getattr(self.llm, "enabled", False)

    def build_vision_table(self, caption_block, image_run):
        """图片表:把相邻图片喂给 VLM 重建为 table-wrap;失败返回 None。"""
        if not self.vision_ok or not getattr(image_run, "blob", None):
            return None
        import os
        from .vision_tables import build_table_from_image
        cap = self.table_caption(caption_block)
        label = cap[0] if cap else None
        runs = cap[1] if cap else None
        number = None
        if label:
            m = re.search(r"(\d+)", label)
            number = int(m.group(1)) if m else None
        if number is None:
            number = self.tables._n + 1
        # 落临时图片文件
        self._tmp_idx += 1
        tmpdir = os.path.join(self.out_dir, ".tbl_img")
        os.makedirs(tmpdir, exist_ok=True)
        ext = {"JPEG": ".jpg", "PNG": ".png"}.get(image_run.fmt or "", ".jpg")
        tmp = os.path.join(tmpdir, "t%d%s" % (self._tmp_idx, ext))
        with open(tmp, "wb") as f:
            f.write(image_run.blob)
        node = build_table_from_image(self.llm, tmp, number, caption_label=label,
                                      caption_runs=runs, inline_math=self.inline_math)
        if node is not None:
            self.tables.numbers.append(number)
            self.tables._n += 1
        return node

    def build_text_table(self, caption_block, lines):
        """制表符表:把成组的制表符行文本交给文本模型结构化为 table-wrap。"""
        if not self.vision_ok or not lines:
            return None
        from .vision_tables import build_table_from_text
        cap = self.table_caption(caption_block)
        label = cap[0] if cap else None
        runs = cap[1] if cap else None
        number = None
        if label:
            m = re.search(r"(\d+)", label)
            number = int(m.group(1)) if m else None
        if number is None:
            number = self.tables._n + 1
        node = build_table_from_text(self.llm, lines, number, caption_label=label,
                                     caption_runs=runs, inline_math=self.inline_math)
        if node is not None:
            self.tables.numbers.append(number)
            self.tables._n += 1
        return node

    def build_image_table_fallback(self, caption_block, image_run):
        """图片表的 VLM 重建失败时的**无损兜底**:把整张表图外部化为 ``<graphic>`` 并包进
        ``<table-wrap>``。保证不丢表、表计数不变、不把题注误挂到下一张真实表;``graphic`` 是
        ``table-wrap`` 的合法子元素(inside-table-wrap → simple-intable-display),DTD 合规。
        失败返回 None。"""
        if not getattr(image_run, "blob", None):
            return None
        import io
        import logging
        import os
        from PIL import Image
        from .jats import E, append_inline, sub
        cap = self.table_caption(caption_block)
        label = cap[0] if cap else None
        runs = cap[1] if cap else None
        number = None
        if label:
            m = re.search(r"(\d+)", label)
            number = int(m.group(1)) if m else None
        if number is None:
            number = self.tables._n + 1
        rel = "%s/table-%02d.jpg" % (self.article_id, number)
        dest = os.path.join(self.out_dir, rel)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                im = Image.open(io.BytesIO(image_run.blob))
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.save(dest, "JPEG", quality=90)
            except Exception:
                with open(dest, "wb") as f:  # 转码失败则原样写出
                    f.write(image_run.blob)
        except Exception:
            return None
        wrap = E("table-wrap", id="T%03d" % number)
        if label:
            sub(wrap, "label", label)
        if runs:
            capel = sub(wrap, "caption")
            p = sub(capel, "p")
            append_inline(p, runs, self.inline_math)
        g = sub(wrap, "graphic", **{"xlink_href": rel})
        g.set("id", "T%03d.g1" % number)
        self.tables.numbers.append(number)
        self.tables._n += 1
        logging.getLogger(__name__).warning(
            "表 %s 视觉重建失败,已无损兜底为表图 <graphic>(不丢表)", label or number)
        return wrap

    # ---- 公式 ---------------------------------------------------------- #
    def inline_math(self, mathrun):
        return self.formula.inline_formula(mathrun)

    def is_display_formula(self, para: Paragraph) -> bool:
        if len(para.maths) != 1:
            return False
        txt = para.text.strip()
        return txt == "" or bool(_NUM_LABEL.match(txt))

    def display_formula_for(self, para: Paragraph):
        label = None
        m = _NUM_LABEL.match(para.text.strip())
        if m:
            label = "(%s)" % m.group(1)
        return self.formula.disp_formula(para.maths[0], label)

    # ---- 图片 ---------------------------------------------------------- #
    def figure_caption_number(self, para: Paragraph) -> Optional[int]:
        m = P.FIG_CAPTION.match(para.text.strip())
        if m:
            try:
                return int(m.group(2))
            except ValueError:
                return None
        return None

    def figure_for(self, para: Paragraph):
        """纯图片段：MVP 跳过（图在题注处生成）。"""
        return None

    def build_figure(self, number: int, para: Paragraph):
        # 剥离 "Fig. N." 标签前缀(只去标签,不吃标题首字),保留题注其余内联格式
        m = P.FIG_LABEL_STRIP.match(para.text)
        prefix_len = m.end() if m else 0
        caption_runs = drop_leading_chars(para.runs, prefix_len)
        return self.figures.build_fig(number, caption_runs, self.inline_math)

    # ---- 表格 ---------------------------------------------------------- #
    def table_caption(self, para: Paragraph):
        m = P.TABLE_CAPTION.match(para.text.strip())
        if m:
            sm = P.TABLE_LABEL_STRIP.match(para.text)
            runs = drop_leading_chars(para.runs, sm.end() if sm else 0)
            return ("Table %s." % m.group(2), runs)
        return None

    def set_pending_table_caption(self, cap):
        self._pending_table_caption = cap

    def table_for(self, table_block):
        cap = self._pending_table_caption
        self._pending_table_caption = None
        label = cap[0] if cap else None
        runs = cap[1] if cap else None
        number = None
        if label:
            m = re.search(r"(\d+)", label)
            if m:
                number = int(m.group(1))
        return self.tables.build(table_block, caption_label=label,
                                 caption_runs=runs, inline_math=self.inline_math,
                                 number=number)
