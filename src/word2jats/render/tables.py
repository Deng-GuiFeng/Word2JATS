"""渲染表格：消费 SemanticDoc 里的 TableBlock。

两类（由 LLM 判定，渲染器只按判定执行）：
- kind=grid：有单元格结构（原生表 / 制表符表拆分而来）→ <table><thead><tbody>。
  表头用裸 <th>（不加 scope，与金标准一致）；单元格文本逐字来自 docx 的 runs。
- kind=image：整表是一张图片 → <table-wrap><graphic>，绝不 OCR 读图造字。
"""

from __future__ import annotations

from ..build.jats import E, append_inline, sub


def _emit_cell(tr, tag, cell, ctx):
    """单元格可为纯 runs 列表，或 (runs, colspan, rowspan) 元组（原生表合并单元格）。
    表头用裸 th（不加 scope）、表用裸 table（不加 frame/rules），与金标准一致。"""
    if isinstance(cell, tuple):
        runs, cs, rs = cell
    else:
        runs, cs, rs = cell, 1, 1
    attrs = {}
    if cs and cs > 1:
        attrs["colspan"] = str(cs)
    if rs and rs > 1:
        attrs["rowspan"] = str(rs)
    el = sub(tr, tag, **attrs)
    append_inline(el, runs, ctx.inline_math, allow_break=True)


def render_table(tb, ctx):
    """TableBlock → <table-wrap>。失败返回 None。"""
    number = tb.number
    tid = tb.table_id or ("T%03d" % number)
    wrap = E("table-wrap", id=tid)
    if tb.label:
        sub(wrap, "label", tb.label)
    if tb.caption_runs:
        cap = sub(wrap, "caption")
        p = sub(cap, "p")
        append_inline(p, tb.caption_runs, ctx.inline_math)

    if tb.kind == "image":
        ph_blob = getattr(tb, "_image_blob", None)
        if not ph_blob:
            return None
        rel = ctx.export_table_image(ph_blob, number)
        g = sub(wrap, "graphic", **{"xlink_href": rel})
        g.set("id", "%s.g1" % tid)
    else:
        table = sub(wrap, "table")
        if tb.header_rows:
            thead = sub(table, "thead")
            for row in tb.header_rows:
                tr = sub(thead, "tr")
                for cell in row:
                    _emit_cell(tr, "th", cell, ctx)
        if tb.body_rows:
            tbody = sub(table, "tbody")
            for row in tb.body_rows:
                tr = sub(tbody, "tr")
                for cell in row:
                    _emit_cell(tr, "td", cell, ctx)
        # DTD：table 至少要有内容；纯空表退化为无（调用方决定）
        if not tb.header_rows and not tb.body_rows:
            return None

    if tb.foot_runs:
        foot = sub(wrap, "table-wrap-foot")
        fn = sub(foot, "fn")
        p = sub(fn, "p")
        append_inline(p, tb.foot_runs, ctx.inline_math)

    ctx.table_numbers.append(number)
    return wrap
