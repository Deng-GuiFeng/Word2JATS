"""渲染上下文：把公式 / 图片 builder 与输出路径打包，供各 render 模块共用。

只承载机械设施（OMML→MathML、图字节外部化、编号计数），不含任何内容判定。
"""

from __future__ import annotations

import os

from ..build.figures import FigureBuilder
from ..build.formulas import FormulaBuilder


class RenderContext:
    def __init__(self, figure_source, article_id: str, out_dir: str):
        self.formula = FormulaBuilder()
        self.figures = FigureBuilder(figure_source, article_id, out_dir)
        self.article_id = article_id or "article"
        self.out_dir = out_dir
        self.table_numbers: list = []
        self.ref_num_to_id: dict = {}

    # 公式：MathRun → inline-formula / disp-formula
    def inline_math(self, mathrun):
        return self.formula.inline_formula(mathrun)

    def disp_math(self, mathrun, label=None):
        return self.formula.disp_formula(mathrun, label)

    def export_table_image(self, blob: bytes, number: int) -> str:
        """整表图片外部化为 {article_id}/table-NN.<原格式>（字节忠实、不重编码、保留原扩展名）。"""
        from ..build.figures import write_image_blob
        return write_image_blob(self.out_dir, "%s/table-%02d" % (self.article_id, number), blob)
