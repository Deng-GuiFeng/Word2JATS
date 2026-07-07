"""渲染 + 出口校验 + 定点修复循环。

当前（S1 阶段）：渲染一次 + 校验一次。S6 会在此加入"定点重问 LLM / 机械修复 → 再校验"
的收敛循环——注意这是"同一方法内的出错兜底"，不是另一种可选方法。
"""

from __future__ import annotations

from ..render.render import render_document


def render_verify_repair(sd, meta, llm, registry, doi, journal_id, fig_src,
                         article_id, out_dir, default_year=None,
                         docx_path=None, max_rounds=2, do_validate=True):
    xml_bytes, ctx = render_document(
        sd, registry, doi, journal_id, fig_src, article_id, out_dir,
        default_year=default_year)

    vreport = None
    if do_validate and docx_path:
        from .verify import verify
        vreport = verify(xml_bytes, docx_path)
    return xml_bytes, ctx, vreport
