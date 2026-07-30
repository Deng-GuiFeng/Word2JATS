"""把解析层 IR（Document）序列化成"给 LLM 看的文档表示"。

这是理解层的输入。每个源块占一行，带稳定的 ``[idx]``；LLM 的全部产出都以 ``idx``
指回源块，据此在渲染时取回原始 ``runs``（内容守恒结构性成立、内联格式零损失）。

关键设计：
- **上/下标编码为** ``^{...}`` / ``_{...}``：作者姓名后的单位角标、通讯符 ``*``、
  共同贡献符 ``†/#`` 全靠上标呈现，编码后 LLM 一眼可辨，仍是纯文本。
- **图片/公式/图片表只给占位符** ``⟦IMG#n⟧`` / ``⟦MATH#n⟧``：不喂字节、不喂渲染图，
  从架构上杜绝"看图 OCR 造字"。
- **行级标注** 加粗/居中/样式名/列表：给 LLM 判标题、题注、层级的排版信号（有时可靠、
  有时全无——如全 Normal 的样例，此时 LLM 靠语义判定）。
- VML 回退位图（``is_fallback``，多为公式截图）不出现在流里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..model.blocks import (BreakRun, ImageRun, MathRun, Paragraph, Table,
                            TextRun)


@dataclass
class Placeholder:
    """占位符：图片 / 公式 / 图片表，按 index 解析回 IR 对象。"""
    n: int
    kind: str            # 'img' | 'math'
    obj: object          # ImageRun | MathRun
    display: bool = False  # math: 是否块级
    block_idx: int = -1    # 所在块 idx


@dataclass
class Line:
    """内容流的一行 = 一个源块。"""
    idx: int
    kind: str            # 'para' | 'table'
    text: str            # 序列化文本（含占位符 / 上下标编码）
    block: object        # IR Paragraph | Table（渲染时取回 runs）
    flags: list = field(default_factory=list)   # 'bold'/'italic'/'center'/'list'/'style:X'
    placeholders: list = field(default_factory=list)  # 本行涉及的 Placeholder n


class ContentStream:
    def __init__(self):
        self.lines: list = []
        self.placeholders: list = []           # 全局占位符表
        self._by_idx: dict = {}
        self._ph_by_n: dict = {}

    def block(self, idx: int):
        return self._by_idx.get(idx)

    def placeholder(self, n: int) -> Optional[Placeholder]:
        return self._ph_by_n.get(n)

    def text_of(self, idx: int) -> str:
        ln = None
        for l in self.lines:
            if l.idx == idx:
                ln = l
                break
        return ln.text if ln else ""

    def render(self, lo: int = 0, hi: Optional[int] = None) -> str:
        """把 [lo, hi) 区间的行渲染成给 LLM 的多行文本。"""
        hi = len(self.lines) if hi is None else hi
        out = []
        for ln in self.lines:
            if ln.idx < lo or ln.idx >= hi:
                continue
            tag = ""
            if ln.flags:
                tag = " «%s»" % ",".join(ln.flags)
            out.append("[%d]%s %s" % (ln.idx, tag, ln.text))
        return "\n".join(out)


def _valign(run) -> str:
    if getattr(run, "superscript", False):
        return "sup"
    if getattr(run, "subscript", False):
        return "sub"
    return "normal"


def _encode_runs(runs, stream: ContentStream, block_idx: int, ph_list: list) -> str:
    """把一段 runs 序列化：文本 + 上下标编码 + 图/式占位符。"""
    # 先把相邻同 vertAlign 的 TextRun 合并，避免 ^{1}^{,} 碎片
    segs = []  # (text, valign) 或 ('__img__'/'__math__', obj)
    for r in runs:
        if isinstance(r, TextRun):
            va = _valign(r)
            if segs and segs[-1][1] == va and isinstance(segs[-1][0], str):
                segs[-1] = (segs[-1][0] + r.text, va)
            else:
                segs.append((r.text, va))
        elif isinstance(r, BreakRun):
            segs.append(("↵", "normal"))   # ↵ 行内换行标记
        elif isinstance(r, MathRun):
            segs.append(("__math__", r))
        elif isinstance(r, ImageRun):
            if not getattr(r, "is_fallback", False) and r.blob:
                segs.append(("__img__", r))
    out = []
    for text, meta in segs:
        if text == "__img__":
            n = len(stream.placeholders)
            ph = Placeholder(n=n, kind="img", obj=meta, block_idx=block_idx)
            stream.placeholders.append(ph)
            stream._ph_by_n[n] = ph
            ph_list.append(n)
            out.append("⟦IMG#%d⟧" % n)   # ⟦IMG#n⟧
        elif text == "__math__":
            n = len(stream.placeholders)
            ph = Placeholder(n=n, kind="math", obj=meta,
                             display=getattr(meta, "display", False), block_idx=block_idx)
            stream.placeholders.append(ph)
            stream._ph_by_n[n] = ph
            ph_list.append(n)
            out.append("⟦MATH#%d⟧" % n)   # ⟦MATH#n⟧
        else:
            va = meta
            if not text:
                continue
            if va == "sup":
                out.append("^{%s}" % text)
            elif va == "sub":
                out.append("_{%s}" % text)
            else:
                out.append(text)
    return "".join(out)


def _para_flags(p: Paragraph) -> list:
    flags = []
    if p.is_bold:
        flags.append("bold")
    if p.is_italic:
        flags.append("italic")
    if p.alignment == "center":
        flags.append("center")
    if p.numbering is not None:
        flags.append("list")
    # 有意义的样式名（标题类）——给层级判定信号；正文/Normal 不标注避免噪声
    sn = (p.style_name or "").strip()
    if sn and sn.lower() not in ("normal", "body text", "default paragraph font", ""):
        flags.append("style:%s" % sn)
    return flags


def _table_preview(t: Table, max_rows: int = 4, max_cell: int = 24) -> str:
    nrow = len(t.rows)
    ncol = max((len(r.cells) for r in t.rows), default=0)
    rows_txt = []
    for r in t.rows[:max_rows]:
        cells = []
        for c in r.cells:
            ct = c.text.replace("\n", " ").strip()
            if len(ct) > max_cell:
                ct = ct[:max_cell] + "…"
            cells.append(ct)
        rows_txt.append(" | ".join(cells))
    more = "" if nrow <= max_rows else " …(+%d行)" % (nrow - max_rows)
    return "«TABLE %d×%d» " % (nrow, ncol) + " ⏎ ".join(rows_txt) + more


def serialize(doc) -> ContentStream:
    """Document → ContentStream（每个 Paragraph / Table 一行）。"""
    stream = ContentStream()
    for idx, blk in enumerate(doc.blocks):
        if isinstance(blk, Paragraph):
            ph_list: list = []
            text = _encode_runs(blk.runs, stream, idx, ph_list)
            ln = Line(idx=idx, kind="para", text=text, block=blk,
                      flags=_para_flags(blk), placeholders=ph_list)
        elif isinstance(blk, Table):
            ln = Line(idx=idx, kind="table", text=_table_preview(blk), block=blk,
                      flags=["grid-table"], placeholders=[])
        else:
            continue
        stream.lines.append(ln)
        stream._by_idx[idx] = blk
    return stream
