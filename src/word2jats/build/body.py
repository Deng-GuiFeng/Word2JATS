"""构建 JATS ``<body>`` 与 ``<back>``。

正文按 Section 树递归生成 sec/title/p；表格生成 table-wrap/table；
后置区生成声明小节（Acknowledgment→ack，其余→sec）+ ref-list。
图片与公式由各自 builder 通过回调注入（见 pipeline 装配）。
"""

from __future__ import annotations

from lxml import etree

from ..classify import patterns as P
from ..model.blocks import Paragraph, Table
from ..model.structured import Section, StructuredDoc
from .jats import E, append_inline, drop_leading_chars, sub
from .references import build_ref_list


def build_body(sd: StructuredDoc, ctx) -> "etree._Element":
    body = E("body")
    for sec in sd.body:
        # 标题为空的顶层"隐式节"（首个标题前的零散内容）：直接挂到 body 下，
        # 不能包成无 <title> 的 <sec>（违反 DTD）。
        if not sec.title and not sec.subsections:
            _render_blocks(body, sec.blocks, ctx, "S0")
        else:
            _render_section(body, sec, ctx)
    return body


def build_back(sd: StructuredDoc, ctx) -> "etree._Element":
    back = E("back")
    for s in sd.back_sections:
        title = s.title or ""
        low = title.lower()
        # Acknowledgment → <ack>;缩写表 → <glossary>(与金标准一致);其余声明 → <sec>
        if "acknowledg" in low:
            node = sub(back, "ack")
        elif "abbreviation" in low:
            node = sub(back, "glossary")
        else:
            node = sub(back, "sec")
        sub(node, "title", title)
        _render_decl_blocks(node, s, ctx)
    if sd.references:
        rl, num_to_id = build_ref_list(sd.references)
        back.append(rl)
        ctx.ref_num_to_id = num_to_id  # 供 xref 按显示号映射到正确 ref id
    # IMR 固定的 Publisher's Note(出版方声明),始终附在 back 末尾
    fng = sub(back, "fn-group")
    fn = sub(fng, "fn")
    pp = sub(fn, "p")
    b = sub(pp, "bold", "Publisher’s Note: ")
    b.tail = ("IMR Press stays neutral with regard to jurisdictional claims in "
              "published maps and institutional affiliations. ")
    return back


def _render_section(parent, sec: Section, ctx):
    el = E("sec", id=sec.sec_id or None)
    if sec.title:
        sub(el, "title", sec.title)
    _render_blocks(el, sec.blocks, ctx, sec.sec_id or "S")
    for sub_sec in sec.subsections:
        _render_section(el, sub_sec, ctx)
    # 跳过"只有 title、无任何内容且无子节"的空 sec(违反 sec 内容模型/属低质量输出);
    # 其 title 多因同级标题被误判为兄弟节所致,丢弃比产空节更稳妥。
    has_content = any(etree.QName(c).localname != "title" for c in el)
    if has_content:
        parent.append(el)


def _append_table_foot(table_wrap, para, ctx):
    """把表脚注段落收入 table-wrap 的 <table-wrap-foot>(用 <fn><p>)。"""
    foot = table_wrap.find("table-wrap-foot")
    if foot is None:
        foot = sub(table_wrap, "table-wrap-foot")
    fn = sub(foot, "fn")
    p = sub(fn, "p")
    append_inline(p, para.runs, ctx.inline_math)


def _render_blocks(parent, blocks, ctx, sec_id):
    p_counter = 0
    consumed = set()  # 已被图片表消费的图片段下标
    last_tw = None    # 上一张表的 table-wrap,用于收纳紧随其后的表脚注
    n = len(blocks)
    for idx in range(n):
        if idx in consumed:
            continue
        blk = blocks[idx]
        if isinstance(blk, Paragraph):
            # 0) 紧跟表格之后的表脚注/缩写释义 → 收入该表 <table-wrap-foot>
            if last_tw is not None and blk.text.strip() \
                    and P.TABLE_FOOTNOTE.match(blk.text.strip()) \
                    and ctx.figure_caption_number(blk) is None \
                    and ctx.table_caption(blk) is None:
                _append_table_foot(last_tw, blk, ctx)
                continue
            last_tw = None  # 非脚注段落 → 结束当前表的脚注收纳
            # 1) 图片题注 "Fig. N ..." → 生成 <fig>
            fignum = ctx.figure_caption_number(blk)
            if fignum is not None:
                # 去重:同一图号只生成一次(重复出现多为正文再次提及,避免重复 id)
                if fignum in ctx.figures.numbers:
                    p_counter += 1
                    pe = sub(parent, "p", id="%s.p%d" % (sec_id, p_counter))
                    append_inline(pe, blk.runs, ctx.inline_math)
                    continue
                node = ctx.build_figure(fignum, blk)
                if node is not None:
                    parent.append(node)
                continue
            # 2) 表格题注 "Table N ..."
            tcap = ctx.table_caption(blk)
            if tcap is not None:
                # 2a) 若开启视觉且紧邻有图片(整表是图片),看图重建
                img_idx = _find_adjacent_image(blocks, idx, n)
                if ctx.vision_ok and img_idx is not None:
                    img = blocks[img_idx].images[0]
                    node = ctx.build_vision_table(blk, img)
                    if node is None:
                        # 视觉重建失败 → 无损兜底:整张表图作为 <graphic> 包进 table-wrap
                        # (不丢表、表计数不变、不把题注误挂到下一张真实表;DTD 合规)
                        node = ctx.build_image_table_fallback(blk, img)
                    if node is not None:
                        parent.append(node)
                        last_tw = node
                        consumed.add(img_idx)
                        continue
                # 2b) 若开启模型且紧随是成组的制表符行(整表用 Tab 拼),交文本模型结构化
                if ctx.vision_ok:
                    tab_idxs = _find_tab_cluster(blocks, idx, n)
                    if tab_idxs:
                        lines = [blocks[k].text.rstrip() for k in tab_idxs]
                        node = ctx.build_text_table(blk, lines)
                        if node is not None:
                            parent.append(node)
                            last_tw = node
                            consumed.update(tab_idxs)
                            continue
                        # 文本表结构化失败 → 无损兜底:题注与各制表符行就地渲为 <p>
                        # (不丢内容、不把题注误挂到下一张真实表;避免静默丢表 + 串号)
                        last_tw = None
                        p_counter += 1
                        pe = sub(parent, "p", id="%s.p%d" % (sec_id, p_counter))
                        append_inline(pe, blk.runs, ctx.inline_math)
                        for k in tab_idxs:
                            if not blocks[k].text.strip():
                                continue
                            p_counter += 1
                            pe = sub(parent, "p", id="%s.p%d" % (sec_id, p_counter))
                            append_inline(pe, blocks[k].runs, ctx.inline_math)
                        consumed.update(tab_idxs)
                        continue
                # 2c) 否则暂存,附到下一张真实表格(真正"题注先于真实 <w:tbl>"的合法情形)
                ctx.set_pending_table_caption(tcap)
                continue
            # 3) 纯图片段（无题注）→ 跳过（MVP）
            if blk.images and not blk.text.strip() and not blk.maths:
                continue
            # 4) 块级公式段（仅含公式 + 可选编号）
            if ctx.is_display_formula(blk):
                node = ctx.display_formula_for(blk)
                if node is not None:
                    parent.append(node)
                    continue
            if not blk.text.strip() and not blk.maths:
                continue
            p_counter += 1
            pe = sub(parent, "p", id="%s.p%d" % (sec_id, p_counter))
            append_inline(pe, blk.runs, ctx.inline_math)
        elif isinstance(blk, Table):
            node = ctx.table_for(blk)
            if node is not None:
                parent.append(node)
                last_tw = node  # 收纳紧随其后的表脚注


def iter_table_tasks(sections):
    """遍历章节树,产出需要模型处理的表任务:('image', ImageRun) 或 ('text', [行...])。

    供管线预热并发用(先把这些 VLM/文本调用并发打到本地服务,缓存预热),与
    _render_blocks 的检测逻辑保持一致;即便略有出入也只影响缓存命中,不影响正确性。
    """
    from ..classify import patterns as P
    out = []
    for sec in sections:
        blocks = sec.blocks
        n = len(blocks)
        for idx in range(n):
            blk = blocks[idx]
            if not isinstance(blk, Paragraph):
                continue
            if not P.TABLE_CAPTION.match(blk.text.strip()):
                continue
            img_idx = _find_adjacent_image(blocks, idx, n)
            if img_idx is not None:
                out.append(("image", blocks[img_idx].images[0]))
                continue
            tab_idxs = _find_tab_cluster(blocks, idx, n)
            if tab_idxs:
                out.append(("text", [blocks[k].text.rstrip() for k in tab_idxs]))
        out.extend(iter_table_tasks(sec.subsections))
    return out


def _find_adjacent_image(blocks, idx, n):
    """表题注后的小窗口内,找"基本只含图片"的段(即整张表是图片)。返回其下标或 None。"""
    for j in range(idx + 1, min(idx + 4, n)):
        b = blocks[j]
        if isinstance(b, Paragraph) and b.images and len(b.text.strip()) < 8:
            return j
        # 碰到下一个题注/正文段就停,避免误抓远处的图
        if isinstance(b, Paragraph) and len(b.text.strip()) >= 8:
            break
    return None


def _find_tab_cluster(blocks, idx, n):
    """表题注后,收集连续的"制表符行"(整表用 Tab 拼)。返回下标列表(可空)。

    允许夹杂少量无制表符的分组小标题行;碰到下一个题注/明显正文长段就停。
    """
    out = []
    j = idx + 1
    while j < min(idx + 60, n):
        b = blocks[j]
        if not isinstance(b, Paragraph):
            break
        t = b.text
        if "\t" in t:
            out.append(j)
        elif t.strip() == "":
            pass  # 空段跳过
        elif len(t.strip()) <= 30 and out:
            out.append(j)  # 短行(疑似分组小标题),并入
        else:
            break  # 长正文段,表结束
        j += 1
    # 至少要有几行制表符行才算表
    tabbed = sum(1 for k in out if "\t" in blocks[k].text)
    return out if tabbed >= 2 else None


def _render_decl_blocks(parent, sec: Section, ctx):
    """渲染后置声明小节内容。行内标签小节("Author Contributions: …")的首块
    需剥离 ``sec.label_len`` 个前缀字符(标签已作 <title>,内容不再重复标签)。"""
    first_done = False
    for blk in sec.blocks:
        if isinstance(blk, Paragraph) and blk.text.strip():
            runs = blk.runs
            if not first_done and sec.label_len:
                runs = drop_leading_chars(runs, sec.label_len)
            first_done = True
            # 剥前缀后可能为空(标签独占一段),则跳过空 <p>
            if not any(getattr(r, "text", "").strip() for r in runs
                       if r.__class__.__name__ == "TextRun"):
                if not any(r.__class__.__name__ in ("MathRun", "ImageRun") for r in runs):
                    continue
            pe = sub(parent, "p")
            append_inline(pe, runs, ctx.inline_math)
        elif isinstance(blk, Table):
            node = ctx.table_for(blk)
            if node is not None:
                parent.append(node)
