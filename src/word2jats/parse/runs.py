"""段落内联内容（run）抽取。

把一个 ``<w:p>`` 元素按**文档顺序**拆解为 IR 的 run 列表
（:class:`TextRun` / :class:`MathRun` / :class:`ImageRun` / :class:`BreakRun`），
完整保留文本、公式、图片三类内容的先后位置。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..model.blocks import BreakRun, ImageRun, MathRun, TextRun
from .ooxml import NS, is_on, local_name, qn, w_val


def _run_format(r_el):
    """从 ``w:r`` 的 ``w:rPr`` 读取 bold/italic/上下标。"""
    rpr = r_el.find(qn("w:rPr"))
    bold = italic = sup = sub = False
    if rpr is not None:
        bold = is_on(rpr.find(qn("w:b")))
        italic = is_on(rpr.find(qn("w:i")))
        va = rpr.find(qn("w:vertAlign"))
        if va is not None:
            v = w_val(va)
            sup = v == "superscript"
            sub = v == "subscript"
    return bold, italic, sup, sub


def _emit_run_children(r_el, fmt, hyperlink, resolve_image, out):
    """遍历 ``w:r`` 的子节点，按序产出文本 / 换行 / 图片 run。"""
    bold, italic, sup, sub = fmt
    for child in r_el:
        ln = local_name(child)
        if ln == "t":
            txt = child.text or ""
            out.append(
                TextRun(text=txt, bold=bold, italic=italic,
                        superscript=sup, subscript=sub, hyperlink=hyperlink)
            )
        elif ln == "tab":
            out.append(TextRun(text="\t", bold=bold, italic=italic,
                               superscript=sup, subscript=sub, hyperlink=hyperlink))
        elif ln in ("br", "cr"):
            out.append(BreakRun())
        elif ln == "drawing":
            # DrawingML：a:blip/@r:embed
            blips = child.findall(".//" + qn("a:blip"))
            rid = None
            for b in blips:
                rid = b.get(qn("r:embed")) or b.get(qn("r:link"))
                if rid:
                    break
            img = resolve_image(rid) if rid else None
            if img is not None:
                out.append(img)
        elif ln == "pict" or ln == "object":
            # VML：v:imagedata/@r:id（常见于 MathType/OLE 的位图回退）
            rid = None
            for idata in child.findall(".//" + qn("v:imagedata")):
                rid = idata.get(qn("r:id"))
                if rid:
                    break
            img = resolve_image(rid) if rid else None
            if img is not None:
                img.is_fallback = True  # VML 图通常是公式/对象的截图回退
                out.append(img)


def extract_runs(p_el, resolve_image: Callable, resolve_hyperlink: Callable) -> list:
    """把 ``<w:p>`` 拆为有序 run 列表。

    :param resolve_image: ``rel_id -> ImageRun | None``
    :param resolve_hyperlink: ``rel_id -> url | None``
    """
    out: list = []
    for node in p_el:
        ln = local_name(node)
        if ln == "pPr":
            continue
        elif ln == "r":
            fmt = _run_format(node)
            _emit_run_children(node, fmt, None, resolve_image, out)
        elif ln == "hyperlink":
            rid = node.get(qn("r:id"))
            anchor = w_val(node, "anchor")
            target = (resolve_hyperlink(rid) if rid else None) or (
                "#" + anchor if anchor else None
            )
            for r_el in node.findall(qn("w:r")):
                fmt = _run_format(r_el)
                _emit_run_children(r_el, fmt, target, resolve_image, out)
        elif ln == "oMath":
            out.append(MathRun(omml=node, display=False))
        elif ln == "oMathPara":
            for om in node.findall(qn("m:oMath")):
                out.append(MathRun(omml=om, display=True))
        elif ln == "smartTag" or ln == "ins" or ln == "sdt":
            # 包裹性元素：递归取其中的 run（修订插入 / 智能标记 / 内容控件）
            inner = node.find(qn("w:sdtContent")) if ln == "sdt" else node
            if inner is not None:
                for r_el in inner.findall(".//" + qn("w:r")):
                    fmt = _run_format(r_el)
                    _emit_run_children(r_el, fmt, None, resolve_image, out)
    return out
