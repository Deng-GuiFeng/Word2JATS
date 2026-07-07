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
        """整表图片外部化为 {article_id}/table-NN.jpg（直写原始字节，不重编码）。"""
        rel = "%s/table-%02d.jpg" % (self.article_id, number)
        dest = os.path.join(self.out_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob)
        return rel
