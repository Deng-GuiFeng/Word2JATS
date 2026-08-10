"""把三个 LLM pass 的 JSON 组装成 SemanticDoc。

核心职责：**索引解析**——LLM 的产出以 idx 指回源块，这里取回原始 ``runs``（正文类）
或按占位符取回图/式；只有必须切分处（作者/单位/参考字段/关键词）才消费 LLM 给的子串。
组装时对子串做**软守恒检查**（是否 docx 归一子串），异常记入 warnings 供出口校验用。
"""

from __future__ import annotations

from ..model.blocks import BreakRun, MathRun, Paragraph, Table, TextRun
from ..semantic.model import (Affiliation, AbstractSection, Author, DateInfo,
                              Declaration, Editor, Figure, Formula, Para,
                              Reference, Section, SemanticDoc, TableBlock)
from ..build.jats import drop_leading_chars
from .patterns import (CANON_DECL_TITLE, normalize_orcid, strip_fig_label,
                       strip_table_label, strip_title_prefix_len)


# --------------------------------------------------------------------------- #
# 取内容原语
# --------------------------------------------------------------------------- #
def _runs(stream, idx):
    blk = stream.block(idx)
    if isinstance(blk, Paragraph):
        return list(blk.runs)
    return []


def _plain(stream, idx):
    blk = stream.block(idx)
    if isinstance(blk, Paragraph):
        return blk.text
    if isinstance(blk, Table):
        return "\n".join(c.text for r in blk.rows for c in r.cells)
    return ""


def _slice_runs(runs, start, end):
    """按字符偏移 [start, end) 切 runs（只按 TextRun 计字符；math/break 若落区间内保留）。"""
    out, pos = [], 0
    for r in runs:
        if isinstance(r, TextRun):
            rs, re_ = pos, pos + len(r.text)
            a, b = max(start, rs), min(end, re_)
            if a < b:
                out.append(TextRun(text=r.text[a - rs:b - rs], bold=r.bold, italic=r.italic,
                                   superscript=r.superscript, subscript=r.subscript,
                                   hyperlink=r.hyperlink))
            pos = re_
        else:
            if start <= pos < end:
                out.append(r)
    return out


def _split_abstract_runs(runs, subheads):
    """结构化摘要：在各子标题处把整段 runs 切成多节。返回 [(title, seg_runs)]；切不动返回 []。"""
    text = "".join(r.text for r in runs if isinstance(r, TextRun))
    low = text.casefold()
    positions = []
    for sh in subheads:
        if not sh:
            continue
        i = low.find(sh.casefold())
        if i >= 0:
            positions.append((i, sh))
    positions.sort()
    if len(positions) < 1:
        return []
    out = []
    for k, (pos, sh) in enumerate(positions):
        start = pos + len(sh)
        end = positions[k + 1][0] if k + 1 < len(positions) else len(text)
        seg = _slice_runs(runs, start, end)
        out.append((sh, seg))
    return out


def _split_runs_by_tab(runs):
    """把一段 runs 按 \\t 切成若干单元格（每格是 run 列表），保留内联格式。"""
    cells, cur = [], []
    for r in runs:
        if isinstance(r, TextRun) and "\t" in r.text:
            parts = r.text.split("\t")
            for i, part in enumerate(parts):
                if i > 0:
                    cells.append(cur)
                    cur = []
                if part:
                    cur.append(TextRun(text=part, bold=r.bold, italic=r.italic,
                                       superscript=r.superscript, subscript=r.subscript,
                                       hyperlink=r.hyperlink))
        else:
            cur.append(r)
    cells.append(cur)
    return cells


def _cell_nonempty(cell_runs):
    for r in cell_runs:
        if isinstance(r, TextRun) and r.text.strip():
            return True
        if not isinstance(r, (TextRun, BreakRun)):
            return True
    return False


def _tab_row_cells(runs):
    """制表符行 → 内容单元格：切分后丢掉空白单元格（视觉对齐产生的多余 tab）。"""
    return [c for c in _split_runs_by_tab(runs) if _cell_nonempty(c)]


def _normalize_grid(rows, nhead):
    """把制表符表归一成矩形：列数取表头内容单元数；短行补空格、长行溢出并入末格。
    这是"视觉对齐制表符表 → 逻辑矩形表"的正确重建（表本就该各行同列数），
    实测与结构参考逐格一致（如 'Age' 行 → [Age,<0.001,22.3,'','','']）。"""
    if not rows:
        return [], []
    head = rows[:nhead] or rows[:1]
    ncol = max((len(r) for r in head), default=0)
    if ncol == 0:
        ncol = max((len(r) for r in rows), default=0)
    norm = []
    for r in rows:
        if len(r) > ncol and ncol > 0:
            merged = list(r[:ncol - 1])
            tail = []
            for c in r[ncol - 1:]:
                if tail:
                    tail.append(TextRun(text=" "))
                tail.extend(c)
            merged.append(tail)
            norm.append(merged)
        else:
            norm.append(list(r) + [[] for _ in range(ncol - len(r))])
    return norm[:nhead], norm[nhead:]


def _cell_runs_from_paragraphs(paragraphs):
    """原生表单元格：把单元格内多段落 runs 拼平，段间插 BreakRun（保多行）。"""
    out = []
    for i, p in enumerate(paragraphs):
        if not isinstance(p, Paragraph):
            continue
        if i > 0 and out:
            out.append(BreakRun())
        out.extend(p.runs)
    return out


# ---- 原生表：gridSpan→colspan / vMerge→rowspan（跳过 continue 续行单元格）---- #
def _col_positions(row):
    pos, out = 0, []
    for c in row.cells:
        out.append(pos)
        pos += (c.grid_span or 1)
    return out


def _compute_rowspans(rows):
    rowspans, positions = {}, [_col_positions(r) for r in rows]
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row.cells):
            if cell.v_merge != "restart":
                continue
            col, span = positions[ri][ci], 1
            for rj in range(ri + 1, len(rows)):
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


def _native_header_rows(rows):
    """表头行数：优先采用 Word 原生 w:tblHeader 标记的前导连续行数（跨页重复表头，最稳健，
    支持任意多行复杂表头）；无任何行标记时才回退启发式（首行跨列分组 + 次行续行单元格 → 2，否则 1）。"""
    if not rows:
        return 0
    marked = 0
    for r in rows:
        if getattr(r, "header", False):
            marked += 1
        else:
            break
    if marked:
        return marked
    first_has_group = any((c.grid_span or 1) > 1 for c in rows[0].cells)
    if first_has_group and len(rows) > 1 and \
            any(c.v_merge == "continue" for c in rows[1].cells):
        return 2
    return 1


def _native_grid(table):
    """原生 w:tbl → (header_rows, body_rows)，每格 = (runs, colspan, rowspan)；
    跳过 vMerge=continue 续行单元格（由上方单元格 rowspan 覆盖）。"""
    rows = table.rows
    if not rows:
        return [], []
    rowspans = _compute_rowspans(rows)
    nhead = _native_header_rows(rows)
    grid = []
    for ri, row in enumerate(rows):
        if row.cells and all(c.v_merge == "continue" for c in row.cells):
            continue   # 整行都是续行 → 不产出（空 tr 违反 DTD）
        cells = []
        for ci, cell in enumerate(row.cells):
            if cell.v_merge == "continue":
                continue
            cs = cell.grid_span if (cell.grid_span and cell.grid_span > 1) else 1
            rs = rowspans.get((ri, ci), 1)
            cells.append((_cell_runs_from_paragraphs(cell.blocks), cs, rs))
        grid.append(cells)
    return grid[:nhead], grid[nhead:]


# --------------------------------------------------------------------------- #
# front
# --------------------------------------------------------------------------- #
def _assemble_front(sd, stream, fj, body_start=0):
    sd.article_type = fj.get("article_type") or "research-article"
    sd.article_category = fj.get("article_category")

    title_idxs = fj.get("title_idxs") or ([fj["title_idx"]] if fj.get("title_idx") is not None else [])
    tr = []
    for i in title_idxs:
        if tr:
            tr.append(TextRun(text=" "))
        tr.extend(_runs(stream, i))
    sd.title_runs = tr

    for a in fj.get("authors", []):
        au = Author(
            surname=(a.get("surname") or "").strip(),
            given_names=(a.get("given") or a.get("given_names") or "").strip(),
            aff_labels=[str(x) for x in (a.get("aff_labels") or [])],
            is_corresponding=bool(a.get("corresp")),
            equal_contrib=bool(a.get("equal")),
            orcid=normalize_orcid(a.get("orcid")) if a.get("orcid") else None,
            email=(a.get("email") or None),
        )
        au.orcid_authenticated = bool(au.orcid)   # 有 ORCID 即标 authenticated（JATS4R）
        if au.surname:
            sd.authors.append(au)

    for af in fj.get("affiliations", []):
        label = str(af.get("label") or "").strip()
        if af.get("text") is not None:
            text = af["text"].strip()
        elif af.get("text_idx") is not None:
            text = _plain(stream, af["text_idx"]).strip()
        else:
            text = ""
        # 剥去可能的前导角标数字（"1 Faculty…" → "Faculty…"），角标由渲染器加 <sup>
        import re as _re
        text = _re.sub(r"^\s*%s\s*[\.\)]?\s*" % _re.escape(label) if label else r"^\s*", "", text)
        sd.affiliations.append(Affiliation(aff_id="aff" + label if label else "aff%d" % (len(sd.affiliations) + 1),
                                           label=label, text=text))

    # 通讯段：多块拼接（忠实原文，邮箱由渲染器包 <email>）
    cor_idxs = fj.get("corresp_idxs") or []
    if cor_idxs:
        parts = [_plain(stream, i).strip() for i in cor_idxs]
        parts = [p for p in parts if p]
        sd.corresp_text = ", ".join(parts) if parts else None
    sd.corresp_emails = [e for e in (fj.get("corresp_emails") or []) if e]

    if fj.get("equal_contrib_note_idx") is not None:
        sd.equal_contrib_note = _plain(stream, fj["equal_contrib_note_idx"]).strip()
    elif fj.get("equal_contrib_note"):
        sd.equal_contrib_note = fj["equal_contrib_note"].strip()

    d = fj.get("dates") or {}
    def _dt(v):
        if not v or len(v) != 3:
            return None
        return (str(v[0]), str(v[1]), str(v[2]))
    sd.dates = DateInfo(received=_dt(d.get("received")), revised=_dt(d.get("revised")),
                        accepted=_dt(d.get("accepted")))

    for ed in fj.get("editors", []):
        sd.editors.append(Editor(surname=(ed.get("surname") or "").strip(),
                                 given_names=(ed.get("given") or ed.get("given_names") or "").strip(),
                                 role=ed.get("role") or "Academic Editor"))

    ab = fj.get("abstract") or {}
    sections = ab.get("sections", [])
    structured = bool(ab.get("structured")) or any(s.get("title") for s in sections)
    subheads = [s.get("title") for s in sections if s.get("title")]
    # 摘要涉及的全部块（去重保序）
    all_idxs = []
    for s in sections:
        for pi in (s.get("para_idxs") or []):
            if pi not in all_idxs:
                all_idxs.append(pi)
    split = []
    if structured and subheads and all_idxs:
        # 拼接全部摘要块，在各子标题处切分（正确处理"整篇结构化摘要在同一个 docx 块"）
        runs = []
        for pi in all_idxs:
            if runs:
                runs.append(TextRun(text=" "))
            runs.extend(_runs(stream, pi))
        split = _split_abstract_runs(runs, subheads)
    if split:
        for title, seg in split:
            sd.abstract.append(AbstractSection(title=title, paragraphs=[seg]))
    else:
        # 非结构化，或子标题切不动 → 各块各自成段（同一块只用一次，避免重复）
        for pi in all_idxs:
            sd.abstract.append(AbstractSection(title=None, paragraphs=[_runs(stream, pi)]))

    if fj.get("precis_idx") is not None:
        sd.precis = _plain(stream, fj["precis_idx"]).strip()
    elif fj.get("precis"):
        sd.precis = fj["precis"].strip()

    sd.keywords = [k.strip().rstrip(".;,").strip() for k in (fj.get("keywords") or [])
                   if k and k.strip().rstrip(".;,").strip()]
    # 关键词标题只在确为 docx 原文时才保留（内容守恒安全网）：防 LLM 在关键词行本无
    # "Keywords" 标签时凭空补一个。（注：S02/S03 的 "keywords" 编造是另一回事——docx 里
    # "Keywords" 被 Word 拆成 "Key"/"w"/"ords" 三个 run，评测逐 w:t 分词看不到该词，而我们
    # 忠实拼接后输出，属度量伪差、非真编造，此门控不处理它。）
    sd.keywords_title = _keywords_title_if_present(stream, fj.get("keywords_title"))

    # 前置声明（部分期刊把 Funding/Conflict/Author Contributions 等置于摘要之前）
    fdecls = [d for d in (fj.get("declarations") or []) if d.get("idx") is not None]
    if fdecls:
        # 内容上界：摘要块 / precis / 正文起点等前置结构标记（防止吞并其后内容）
        markers = list(all_idxs)
        if fj.get("precis_idx") is not None:
            markers.append(fj["precis_idx"])
        if body_start:
            markers.append(body_start)
        fdecls = [d for d in fdecls if not body_start or d["idx"] < body_start]
        fdecls.sort(key=lambda d: d["idx"])
        idxs = [d["idx"] for d in fdecls]
        for i, d in enumerate(fdecls):
            lo = d["idx"]
            nxt = idxs[i + 1] if i + 1 < len(idxs) else None
            ups = [m for m in markers if m > lo]
            hi = min([x for x in (nxt, min(ups) if ups else None) if x is not None], default=lo + 3)
            tail = [Para(runs=_runs(stream, k)) for k in range(lo + 1, hi)
                    if isinstance(stream.block(k), Paragraph) and stream.block(k).text.strip()]
            _append_declaration(sd, stream, {"dkind": d.get("kind"), "title": d.get("title"),
                                             "idx": lo}, tail)


# --------------------------------------------------------------------------- #
# body
# --------------------------------------------------------------------------- #
def _assemble_table(stream, spec):
    number = spec.get("number")
    kind = spec.get("kind") or "grid"
    nhead = int(spec.get("nhead") or 1)
    cap_idx = spec.get("cap_idx")
    label = None
    cap_runs = []
    if cap_idx is not None:
        text = _plain(stream, cap_idx)
        label, plen = strip_table_label(text)
        cap_runs = drop_leading_chars(_runs(stream, cap_idx), plen)
    tid = spec.get("table_id")
    tb = TableBlock(number=number if number is not None else 0,
                    label=label, caption_runs=cap_runs, kind=kind, table_id=tid)

    if kind == "image":
        ph = stream.placeholder(spec.get("image_ph")) if spec.get("image_ph") is not None else None
        if ph is not None and getattr(ph.obj, "blob", None):
            tb._image_blob = ph.obj.blob
        else:
            tb._image_blob = None
    else:
        if spec.get("native_idx") is not None:
            # 原生表：按 gridSpan/vMerge 合并单元格（colspan/rowspan），跳过续行单元格
            blk = stream.block(spec["native_idx"])
            if isinstance(blk, Table):
                tb.header_rows, tb.body_rows = _native_grid(blk)
                tb.native = True   # 真列头 → 表头 th 用 scope="col"（与结构参考一致）
        elif spec.get("row_idxs"):
            # 制表符表：切内容单元 + 归一成矩形（多 tab 是视觉对齐，非空单元）
            a, b = _row_span(spec["row_idxs"])
            skip = {spec.get("cap_idx"), spec.get("foot_idx")}
            raw = []
            for i in range(a, b + 1):
                if i in skip:                       # 题注/脚注行不作表体行
                    continue
                if not isinstance(stream.block(i), Paragraph):
                    continue
                if not stream.block(i).text.strip():
                    continue
                raw.append(_tab_row_cells(_runs(stream, i)))
            tb.header_rows, tb.body_rows = _normalize_grid(raw, nhead)

    if spec.get("foot_idx") is not None:
        tb.foot_runs = _runs(stream, spec["foot_idx"])
    return tb


def _assemble_block(stream, spec):
    t = spec.get("t")
    if t == "para":
        return Para(runs=_runs(stream, spec["idx"]))
    if t == "figure":
        cap_idx = spec.get("cap_idx")
        label, cap_runs = None, []
        if cap_idx is not None:
            text = _plain(stream, cap_idx)
            label, plen = strip_fig_label(text)
            cap_runs = drop_leading_chars(_runs(stream, cap_idx), plen)
        fig = Figure(number=spec.get("number") or 0, label=label, caption_runs=cap_runs)
        # 复用图片表已验证的通道：LLM 报的 image_ph → 精确取 docx 里那张图的字节
        ph_n = spec.get("image_ph")
        ph = stream.placeholder(ph_n) if ph_n is not None else None
        if ph is not None and getattr(ph.obj, "blob", None):
            fig.image_ph = ph_n
            fig._image_blob = ph.obj.blob
        return fig
    if t == "table":
        return _assemble_table(stream, spec)
    if t == "formula":
        import re as _re
        idx = spec.get("idx")
        runs = _runs(stream, idx)
        mathrun = next((r for r in runs if isinstance(r, MathRun)), None)
        text = "".join(r.text for r in runs if isinstance(r, TextRun)).strip()
        # 安全网：只有"整块基本就是一条公式（文本为空或仅编号）"才作 disp-formula；
        # 含实质正文的段落即使带内联公式也当普通段落（内联公式随文渲染），绝不丢文本。
        if mathrun is None or (text and not _re.fullmatch(r"\(?\s*\d+\s*\)?", text)):
            has = any((isinstance(r, TextRun) and r.text.strip()) or isinstance(r, MathRun)
                      for r in runs)
            return Para(runs=runs) if has else None
        f = Formula(display=True, number=spec.get("number"))
        f._mathrun = mathrun
        f._label = "(%s)" % spec["number"] if spec.get("number") else None
        return f
    return None


def _row_span(row_idxs):
    """把 LLM 给的 row_idxs 归一成 (a, b) 闭区间。

    契约是 [first_line_idx, last_line_idx],但 LLM 偶发只给 1 个下标(单行表)或反序;
    这里一律兜住,绝不让越界索引把管线搞崩(宁可少收几行成段落,也不崩、不编造)。
    """
    a = row_idxs[0]
    b = row_idxs[1] if len(row_idxs) > 1 else row_idxs[0]
    return (a, b) if a <= b else (b, a)


# LLM 返回的 JSON 里所有"整数索引 / 计数"字段。高温或弱模型偶发把它们给成 list/str/嵌套,
# 下游 int()/range()/set.add()/stream.block() 会直接崩。统一在进 assemble 前归一:
# well-formed 输入(低温)是无操作、零行为改变;仅畸形输入被安全降级(与"宁漏不造、绝不崩"一致)。
_IDX_INT_KEYS = {"idx", "cap_idx", "foot_idx", "native_idx", "image_idx", "body_start_idx",
                 "equal_contrib_note_idx", "precis_idx", "title_idx", "text_idx", "nhead", "level"}
_IDX_LIST_KEYS = {"row_idxs", "block_idxs", "corresp_idxs", "para_idxs", "title_idxs"}


def _coerce_int(x):
    """标量 → int,畸形 → None。LLM 偶发把标量包成单元素 list,取首个可解析的。"""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    if isinstance(x, str):
        s = x.strip()
        return int(s) if s.lstrip("-").isdigit() else None
    if isinstance(x, (list, tuple)):
        for e in x:
            v = _coerce_int(e)
            if v is not None:
                return v
    return None


def _sanitize_llm_indices(obj):
    """递归把 LLM JSON 里的索引/计数字段归一成 int / list[int];畸形整数键删除(下游有兜底)。原地改。"""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in _IDX_LIST_KEYS:
                seq = v if isinstance(v, (list, tuple)) else [v]
                obj[k] = [i for i in (_coerce_int(e) for e in seq) if i is not None]
            elif k in _IDX_INT_KEYS:
                iv = _coerce_int(v)
                if iv is None:
                    del obj[k]
                else:
                    obj[k] = iv
            else:
                _sanitize_llm_indices(v)
    elif isinstance(obj, list):
        for e in obj:
            _sanitize_llm_indices(e)
    return obj


def _item_span(stream, it):
    """返回 (anchor_idx, consumed_set)：该图/表/式覆盖的全部源块下标。"""
    span = set()
    t = it.get("t")
    if t == "formula":
        idx = it.get("idx")
        if idx is not None:
            span.add(idx)
    elif t == "figure":
        for k in ("cap_idx", "image_idx"):
            if it.get(k) is not None:
                span.add(it[k])
    elif t == "table":
        for k in ("cap_idx", "native_idx", "foot_idx"):
            if it.get(k) is not None:
                span.add(it[k])
        if it.get("row_idxs"):
            a, b = _row_span(it["row_idxs"])
            span.update(range(a, b + 1))
        if it.get("kind") == "image" and it.get("image_ph") is not None:
            ph = stream.placeholder(it["image_ph"])
            if ph is not None and ph.block_idx >= 0:
                span.add(ph.block_idx)
    anchor = min(span) if span else (it.get("cap_idx") or it.get("idx") or 0)
    return anchor, span


def _collect_blocks(stream, lo, hi, item_by_anchor, consumed):
    """收集 (lo, hi) 区间的有序内容块：命中 item 锚点则插入该 item，其余非空段落成 Para。"""
    out = []
    for idx in range(lo, hi):
        if idx in item_by_anchor:
            blk = _assemble_block(stream, item_by_anchor[idx])
            if blk is not None:
                out.append(blk)
            continue
        if idx in consumed:
            continue
        b = stream.block(idx)
        if isinstance(b, Table):
            out.append(_assemble_table(stream, {"kind": "grid", "native_idx": idx}))
        elif isinstance(b, Paragraph) and b.text.strip():
            out.append(Para(runs=list(b.runs)))
    return out


def _append_declaration(sd, stream, h, tail_blocks):
    """构造一个声明小节：标题优先用 docx 原文标题，无标题的裸声明补 IMR 规范标题；
    头块自身（剥掉标签后）的内容并入，避免丢失内联标签后的正文。"""
    head_text = _plain(stream, h["idx"])
    title = (h.get("title") or "").strip()
    if not title:
        title = CANON_DECL_TITLE.get((h.get("dkind") or "").strip().lower()) or head_text.strip()
    blocks = []
    strip_len = strip_title_prefix_len(head_text, h.get("title"))   # 仅当标题确为头块前缀才剥
    first_runs = drop_leading_chars(_runs(stream, h["idx"]), strip_len)
    if any(isinstance(r, TextRun) and r.text.strip() for r in first_runs):
        blocks.append(Para(runs=first_runs))
    blocks.extend(tail_blocks)
    sd.declarations.append(Declaration(title=title, blocks=blocks))


def _assemble_body(sd, stream, bj, body_start, body_end):
    sections = bj.get("sections", [])       # [{idx, level}]
    decls = bj.get("declarations", [])      # [{idx, title, label_len}]
    items = bj.get("items", [])             # [图/表/式 spec]

    item_by_anchor, consumed = {}, set()
    for it in items:
        anchor, span = _item_span(stream, it)
        item_by_anchor[anchor] = it
        consumed |= span

    # 合并所有标题（正文节 + 声明），按 idx 排序，各标题拥有到下一标题为止的内容。
    # body_end 之后的块（参考标题块 + 参考条目区）一律不进 body。
    heads = []
    for s in sections:
        if s["idx"] < body_end:
            heads.append({"idx": s["idx"], "level": int(s.get("level") or 1), "kind": "sec"})
    for d in decls:
        if d["idx"] < body_end:
            heads.append({"idx": d["idx"], "level": 1, "kind": "decl",
                          "dkind": d.get("kind"), "title": d.get("title")})
    heads.sort(key=lambda h: h["idx"])
    bounds = [h["idx"] for h in heads] + [body_end]

    # body_start 到首个标题之间的零散内容 → 隐式首节
    first_head = heads[0]["idx"] if heads else body_end
    if first_head > body_start:
        lead_blocks = _collect_blocks(stream, body_start, first_head, item_by_anchor, consumed)
        if lead_blocks:
            sd.body.append(Section(title_runs=[], blocks=lead_blocks))

    # 逐标题构造；正文节按 level 建树，声明单列
    stack = []   # [(level, Section)]
    for i, h in enumerate(heads):
        lo, hi = h["idx"] + 1, bounds[i + 1]
        content = _collect_blocks(stream, lo, hi, item_by_anchor, consumed)
        if h["kind"] == "decl":
            _append_declaration(sd, stream, h, content)
            continue
        sec = Section(title_runs=_runs(stream, h["idx"]), blocks=content)
        while stack and stack[-1][0] >= h["level"]:
            stack.pop()
        if stack:
            stack[-1][1].subsections.append(sec)
        else:
            sd.body.append(sec)
        stack.append((h["level"], sec))


# --------------------------------------------------------------------------- #
# references
# --------------------------------------------------------------------------- #
def _keywords_title_if_present(stream, kt):
    """关键词标题门控：LLM 给的 keywords_title 只有确为 docx 原文（压空白+小写子串）时才保留，
    否则返回 None（渲染层据此不 emit <title>）。防"docx 无关键词标签却凭空补 Keywords"的编造。"""
    import re as _re
    kt = (kt or "").strip()
    if not kt:
        return None
    core = _re.sub(r"\s+", " ", kt.rstrip(":;,. ").casefold()).strip()
    if not core:
        return None
    full = _re.sub(r"\s+", " ", " ".join(ln.text for ln in stream.lines).casefold())
    return kt if core in full else None


def _norm_sub(s):
    """守恒子串判定用的归一化：小写 + 压空白 + 去连字符/点，便于宽松包含判断。"""
    import re as _re
    return _re.sub(r"[\s\-.,;:()]", "", (s or "").casefold())


def _wordset(s):
    import re as _re3
    return {t for t in _re3.findall(r"[^\W_]+", (s or "").casefold())
            if len(t) >= 2 and not t.isdigit()}


def _sanitize_ref(ref):
    """出口守恒：字段须来自该条参考原文，防 LLM 编造。长字段(标题/刊名)用词重叠判
    (容忍跨块 raw 偶缺尾词)，短字段(卷/期/页)用精确子串。作者姓须在原文出现。"""
    raw = _norm_sub(ref.raw_text)
    raw_words = _wordset(ref.raw_text)
    if not raw:
        return
    # 长文本字段：词重叠 < 40% 视为编造，置空
    for attr in ("article_title", "source", "comment"):
        v = getattr(ref, attr)
        if v:
            fw = _wordset(v)
            if fw and len(fw & raw_words) / len(fw) < 0.4:
                setattr(ref, attr, None)
    # 短字段：须为原文归一子串
    for attr in ("volume", "issue", "fpage", "lpage"):
        v = getattr(ref, attr)
        if v and _norm_sub(v) not in raw:
            setattr(ref, attr, None)
    # 单 locator（文章号/单页，如 ytaf353 / 146）常被 LLM 同时填进 fpage 与 lpage，
    # 而结构参考只用 fpage、lpage 留空。判据：原文里是否有 "X–X" 相邻范围写法——有才是
    # 真页码区间（如 "e2019801–e2019801"，结构参考确保留 lpage），否则是重复填充，去掉
    # lpage（仍保 fpage）。注意不能用 raw.count()：locator 常也出现在 DOI 里（如
    # doi.org/10.1177/2633105520979841），会被误当第二次出现而漏修。
    if ref.fpage and ref.lpage and ref.fpage == ref.lpage:
        import re as _rerange
        rng = _rerange.search(_rerange.escape(ref.fpage) + r"\s*[-–—]\s*" + _rerange.escape(ref.lpage),
                              ref.raw_text)
        if not rng:
            ref.lpage = None
    if ref.year and ref.year not in ref.raw_text:
        ref.year = None
    ref.authors = [(sn, gn) for (sn, gn) in ref.authors if _norm_sub(sn) in raw]
    ref.editors = [(sn, gn) for (sn, gn) in ref.editors if _norm_sub(sn) in raw]
    ref.collab = [c for c in ref.collab if _norm_sub(c) in raw]
    for attr in ("publisher_name", "publisher_loc", "edition"):
        v = getattr(ref, attr)
        if v and _norm_sub(v) not in raw:
            setattr(ref, attr, None)
    # collab 与 article_title 重叠 → 不是团体作者，是把标题尾部的"…From the XXX Society"
    # 误当团体作者（会与标题重复输出该串，L1 编造，实测 S04 ref[35] 三个学会）。删之。
    # 用**整词集合包含**（collab 的全部内容词都在标题词集里）而非字符子串，避免 'WHO'⊂'knowhow'
    # 之类跨词巧合误删真实缩写团体作者。
    if ref.article_title:
        title_words = _wordset(ref.article_title)
        ref.collab = [c for c in ref.collab
                      if not (_wordset(c) and _wordset(c) <= title_words)]
    ref.structured = bool(ref.source and (ref.article_title or ref.authors or ref.collab))


def _assemble_refs(sd, stream, rj):
    for pos, r in enumerate(rj.get("references", []), 1):
        block_idxs = r.get("block_idxs") or []
        if block_idxs:
            raw = " ".join(_plain(stream, i).strip() for i in block_idxs).strip()
        else:
            raw = (r.get("raw_text") or "").strip()
        authors = [(a[0], a[1] if len(a) > 1 else "") for a in (r.get("authors") or []) if a]
        editors = [(a[0], a[1] if len(a) > 1 else "") for a in (r.get("editors") or []) if a]
        # 机构/团体作者常被 LLM 放进 authors（当作 surname）：判据="无 initials 且 surname 含空格
        # （多词机构名）"——真人多词姓（von Bardeleben / Della Villa）都带 initials，不会误伤。
        # 移入 collab（结构参考把机构作者作 collab，个人名 surname 集才对得上，实测 S02 三条）。
        _inst = [sn.strip() for (sn, gn) in authors if not (gn or "").strip() and " " in sn.strip()]
        if _inst:
            authors = [(sn, gn) for (sn, gn) in authors if not (not (gn or "").strip() and " " in sn.strip())]
        collab_from_llm = [c for c in (r.get("collab") or []) if c]
        # 无显式 [N] 编号的条目（部分文献前几条直接作者名开头）按位置补号。
        # 注意：序列化给 LLM 的每块带 "[块索引]" 前缀，LLM 可能把块索引误当参考标签；
        # 若 label 号恰是本条的某个块索引，视为误抓 → 按位置补号（实测 S03 前 6 条）。
        import re as _rel
        label = (r.get("label") or "").strip()
        _m = _rel.search(r"\d+", label)
        _lnum = int(_m.group()) if _m else None
        if not label or (_lnum is not None and _lnum in block_idxs):
            label = "[%d]" % pos
        ref = Reference(
            label=label,
            raw_text=raw,
            authors=authors,
            editors=editors,
            collab=collab_from_llm + _inst,
            etal=bool(r.get("etal")),
            article_title=(r.get("article_title") or None),
            source=(r.get("source") or None),
            publisher_name=(r.get("publisher_name") or None),
            publisher_loc=(r.get("publisher_loc") or None),
            edition=(r.get("edition") or None),
            year=(str(r["year"]) if r.get("year") else None),
            volume=(str(r["volume"]) if r.get("volume") else None),
            issue=(str(r["issue"]) if r.get("issue") else None),
            fpage=(str(r["fpage"]) if r.get("fpage") else None),
            lpage=(str(r["lpage"]) if r.get("lpage") else None),
            doi=(r.get("doi") or None),
            comment=(r.get("comment") or None),
            pub_type=(r.get("pub_type") or "journal"),
        )
        # 有 source(期刊名/书名) + 作者即可结构化为 element-citation；
        # 书籍类无 article_title 但有书名 source，同样应结构化（否则永远落 mixed）。
        ref.structured = bool(ref.source and (ref.article_title or ref.authors or ref.collab))
        _sanitize_ref(ref)   # 出口守恒：非原文子串的字段置空，防编造
        sd.references.append(ref)


def assemble(stream, front_json, body_json, refs_json,
             body_start=0, refs_start=None, body_end=None) -> SemanticDoc:
    sd = SemanticDoc()
    if refs_start is None:
        refs_start = len(stream.lines)
    # 正文内容上界：默认到参考区起点；调用方传入 body_end（=References 标题块 idx）时，
    # 把参考标题块排除在 body 之外，避免它作为尾随段落漏进最后一个声明块。
    if body_end is None:
        body_end = refs_start
    if front_json:
        _assemble_front(sd, stream, front_json, body_start)
    if body_json:
        _assemble_body(sd, stream, body_json, body_start, body_end)
    if refs_json:
        _assemble_refs(sd, stream, refs_json)
    return sd
