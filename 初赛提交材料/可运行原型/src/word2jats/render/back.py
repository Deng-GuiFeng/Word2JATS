"""渲染 ``<back>``：声明类小节 + ref-list + IMR 固定 Publisher's Note。"""

from __future__ import annotations

from ..build.jats import E, append_inline, drop_leading_chars, sub
from ..semantic.model import Para, TableBlock
from .references import build_ref_list
from .tables import render_table


def render_back(sd, ctx):
    back = E("back")
    for decl in sd.declarations:
        title = decl.title or ""
        low = title.lower()
        if "acknowledg" in low:
            node = sub(back, "ack")
        elif "abbreviation" in low:
            node = sub(back, "glossary")
        else:
            node = sub(back, "sec")
        if title:
            sub(node, "title", title)
        _render_decl_blocks(node, decl, ctx)

    if sd.references:
        rl, num_to_id = build_ref_list(sd.references)
        back.append(rl)
        ctx.ref_num_to_id = num_to_id

    # IMR 固定 Publisher's Note，始终附在 back 末尾
    fng = sub(back, "fn-group")
    fn = sub(fng, "fn")
    pp = sub(fn, "p")
    b = sub(pp, "bold", "Publisher’s Note: ")
    b.tail = ("IMR Press stays neutral with regard to jurisdictional claims in "
              "published maps and institutional affiliations. ")
    return back


def _render_decl_blocks(parent, decl, ctx):
    first_done = False
    for blk in decl.blocks:
        if isinstance(blk, Para):
            runs = blk.runs
            if not first_done and decl.label_len:
                runs = drop_leading_chars(runs, decl.label_len)
            first_done = True
            from ..model.blocks import ImageRun, MathRun, TextRun
            has_text = any(isinstance(r, TextRun) and r.text.strip() for r in runs)
            has_other = any(isinstance(r, (MathRun, ImageRun)) for r in runs)
            if not has_text and not has_other:
                continue
            pe = sub(parent, "p")
            append_inline(pe, runs, ctx.inline_math)
        elif isinstance(blk, TableBlock):
            node = render_table(blk, ctx)
            if node is not None:
                parent.append(node)
