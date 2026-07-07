"""渲染总装：SemanticDoc → JATS article 树 → 序列化字节。

流程：front/body/back 三部分机械构造 → 正文交叉引用解析（Fig./Table/[n] → <xref>，
机械 regex，非内容判定）→ 悬空引用/空表行兜底修复 → 序列化。
"""

from __future__ import annotations

from ..build.jats import make_article, serialize
from ..build.xref import XrefResolver
from ..validate.repair import repair
from .back import render_back
from .body import render_body
from .context import RenderContext
from .front import render_front


def render_document(sd, registry, doi, journal_id, figure_source,
                    article_id, out_dir, default_year=None):
    """返回 (xml_bytes, ctx)。ctx 携带图/表/公式计数，供上层统计。"""
    ctx = RenderContext(figure_source, article_id, out_dir)

    article = make_article(sd.article_type, "en")
    article.append(render_front(sd, registry, doi, journal_id, ctx, default_year))
    article.append(render_body(sd, ctx))
    back = render_back(sd, ctx)
    if back is not None:
        article.append(back)

    # 交叉引用解析（正文文本 "Fig. N"/"Table N"/"[n]" → <xref>）
    ref_nums = set(ctx.ref_num_to_id.keys()) or set(range(1, len(sd.references) + 1))
    xr = XrefResolver(
        ref_nums=ref_nums,
        fig_nums=ctx.figures.numbers,
        table_nums=ctx.table_numbers,
        eqn_nums=list(range(1, ctx.formula.stats["disp"] + 1)),
    )
    n_xref = xr.process(article)

    repair(article)   # 悬空 xref / 空表行的确定性兜底
    xml_bytes = serialize(article)
    ctx.n_xref = n_xref
    return xml_bytes, ctx
