"""渲染 ``<body>``：消费 SemanticDoc.body（Section 树）。

纯机械：LLM 已把每个源块判成 Para/Figure/TableBlock/Formula 并组织成章节树，
渲染器按类型逐一构造 JATS 元素，不再做任何"这段是什么"的判定。
"""

from __future__ import annotations

from lxml import etree

from ..build.jats import E, append_inline, sub
from ..semantic.model import Figure, Formula, Para, Section, TableBlock
from .tables import render_table


def render_body(sd, ctx):
    body = E("body")
    for i, sec in enumerate(sd.body, 1):
        if not sec.title_runs and not sec.subsections:
            # 隐式首节（首个标题前的零散段落）：直接挂 body 下，不能包成无 title 的 sec
            _render_blocks(body, sec.blocks, ctx, "S0")
        else:
            _render_section(body, sec, ctx, "S%d" % i)
    return body


def _render_section(parent, sec: Section, ctx, sec_id: str):
    el = E("sec", id=sec_id or None)
    if sec.title_runs:
        t = sub(el, "title")
        append_inline(t, sec.title_runs, ctx.inline_math)
    _render_blocks(el, sec.blocks, ctx, sec_id)
    for j, sub_sec in enumerate(sec.subsections, 1):
        _render_section(el, sub_sec, ctx, "%s.%d" % (sec_id, j))
    # 跳过"只有 title、无内容且无子节"的空 sec（多因同级标题被误判为兄弟节；丢弃比产空节稳妥）
    has_content = any(etree.QName(c).localname != "title" for c in el)
    if has_content:
        parent.append(el)


def _render_blocks(parent, blocks, ctx, sec_id):
    p_counter = 0
    for blk in blocks:
        if isinstance(blk, Para):
            if not _has_content(blk.runs):
                continue
            p_counter += 1
            pe = sub(parent, "p", id="%s.p%d" % (sec_id, p_counter))
            append_inline(pe, blk.runs, ctx.inline_math)
        elif isinstance(blk, Figure):
            node = ctx.figures.build_fig(blk.number, blk.caption_runs,
                                         ctx.inline_math, label=blk.label)
            if node is not None:
                parent.append(node)
        elif isinstance(blk, TableBlock):
            node = render_table(blk, ctx)
            if node is not None:
                parent.append(node)
        elif isinstance(blk, Formula):
            node = ctx.disp_math(blk._mathrun, blk._label) if getattr(blk, "_mathrun", None) else None
            if node is not None:
                parent.append(node)


def _has_content(runs) -> bool:
    # 仅 TextRun(有字) 与 MathRun 计为内容；纯图片块跳过（独立图片走 Figure，内联图片被丢弃），
    # 否则会产出空 <p>。
    from ..model.blocks import MathRun, TextRun
    for r in runs:
        if isinstance(r, TextRun) and r.text.strip():
            return True
        if isinstance(r, MathRun):
            return True
    return False
