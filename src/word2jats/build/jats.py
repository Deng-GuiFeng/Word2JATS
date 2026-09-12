"""JATS XML 构建底层工具。

集中管理命名空间、DOCTYPE、元素工厂、内联格式转换与序列化，供各 build 模块复用。
所有正文用 lxml 构建 DOM，保证良构与可校验（不采用 baseline 的"拼字符串"做法）。
"""

from __future__ import annotations

from typing import Optional

from lxml import etree

from ..model.blocks import BreakRun, ImageRun, MathRun, TextRun

# 命名空间
MML = "http://www.w3.org/1998/Math/MathML"
XLINK = "http://www.w3.org/1999/xlink"
XML = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"mml": MML, "xlink": XLINK}

# JATS Journal Publishing DTD v1.3（与全部 10 例结构参考逐字一致）
DOCTYPE = (
    '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) '
    'Journal Publishing DTD v1.3 20210610//EN" '
    '"https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd">'
)
XML_DECL = '<?xml version="1.0" encoding="utf-8"?>'

# JATS Publishing 1.3 的 person-group-type 是封闭枚举
# （resources/dtd/JATS-Publishing-1-3-MathML3-DTD/JATS-journalpubcustom-models1-3.ent
#   %person-group-types;）。枚举之外的语义角色按 JATS 标准走
# person-group-type="custom" + custom-type 出路，不得写成非法属性值。
PERSON_GROUP_TYPES = frozenset({
    "allauthors", "assignee", "author", "compiler", "curator", "director",
    "editor", "guest-editor", "illustrator", "inventor", "research-assistant",
    "translator", "transed", "custom",
})


def E(tag: str, text: Optional[str] = None, **attrs) -> etree._Element:
    """创建元素。属性名中的 ``xlink_href`` / ``xml_lang`` 自动转命名空间属性。"""
    el = etree.Element(tag, nsmap=None)
    if text is not None:
        el.text = text
    for k, v in attrs.items():
        if v is None:
            continue
        if k.startswith("xlink_"):
            el.set("{%s}%s" % (XLINK, k[6:]), str(v))
        elif k.startswith("xml_"):
            el.set("{%s}%s" % (XML, k[4:]), str(v))
        else:
            el.set(k.replace("__", ":").replace("_", "-"), str(v))
    return el


def sub(parent, tag, text=None, **attrs):
    el = E(tag, text, **attrs)
    parent.append(el)
    return el


def make_article(article_type: str = "research-article", lang: str = "en"):
    """创建带命名空间声明的根 ``<article>``。"""
    root = etree.Element("article", nsmap=NSMAP)
    root.set("dtd-version", "1.3")
    root.set("{%s}lang" % XML, lang)
    root.set("article-type", article_type)
    return root


# --------------------------------------------------------------------------- #
# 内联格式转换：IR run 列表 → JATS 内联内容（追加到 parent）
# --------------------------------------------------------------------------- #
def _append_text(parent, text: str):
    """把纯文本追加到 parent（正确处理 text/tail）。"""
    if not text:
        return
    if len(parent) == 0:
        parent.text = (parent.text or "") + text
    else:
        last = parent[-1]
        last.tail = (last.tail or "") + text


def _wrap_text_run(run: TextRun) -> etree._Element | str:
    """把一个带格式的 TextRun 转为内联元素或纯文本。

    嵌套顺序（由内到外）：text → italic → bold → sup/sub。
    """
    text = run.text
    node = None
    if run.italic:
        node = E("italic")
        node.text = text
    if run.bold:
        outer = E("bold")
        if node is not None:
            outer.append(node)
        else:
            outer.text = text
        node = outer
    if run.superscript or run.subscript:
        outer = E("sup" if run.superscript else "sub")
        if node is not None:
            outer.append(node)
        else:
            outer.text = text
        node = outer
    return node if node is not None else text


def _merge_adjacent_runs(runs):
    """合并相邻且格式完全相同的 TextRun → 单个 run。

    既消除碎片化的 <bold>/<italic>(Word 因拼写检查/修订把连续文本拆成多 run),
    也让"标签+编号"分属相邻同格式 run 的交叉引用(如 'Fig.'+' 1')能拼成 'Fig. 1'
    被交叉引用识别(实测样例5 Fig.1-3)。
    """
    out = []
    for r in runs:
        if (isinstance(r, TextRun) and out and isinstance(out[-1], TextRun)
                and out[-1].bold == r.bold and out[-1].italic == r.italic
                and out[-1].superscript == r.superscript
                and out[-1].subscript == r.subscript
                and out[-1].hyperlink == r.hyperlink):
            out[-1] = TextRun(text=out[-1].text + r.text, bold=r.bold, italic=r.italic,
                              superscript=r.superscript, subscript=r.subscript,
                              hyperlink=r.hyperlink)
        else:
            out.append(r)
    return out


def append_inline(parent, runs, math_builder=None, allow_break=False):
    """把 IR run 列表渲染为 parent 下的内联内容。

    :param math_builder: 可选回调 ``MathRun -> etree._Element``（inline-formula）；
        未提供时公式以 alt 文本占位，保证不丢内容。
    :param allow_break: 父元素是否允许 ``<break/>``。``<p>`` 不允许(渲为空格保 DTD),
        但表格单元格 ``<td>/<th>`` 允许——单元格内多行须保留为 ``<break/>``(与结构参考一致),
        否则相邻行文本粘连(如 'Current smokingLDL-C')。
    """
    for r in _merge_adjacent_runs(runs):
        if isinstance(r, TextRun):
            wrapped = _wrap_text_run(r)
            if isinstance(wrapped, str):
                _append_text(parent, wrapped)
            else:
                parent.append(wrapped)
        elif isinstance(r, BreakRun):
            if allow_break:
                parent.append(E("break"))
            else:
                _append_text(parent, " ")   # <p> 内不允许 <break>,渲为空格保 DTD
        elif isinstance(r, MathRun):
            if math_builder is not None:
                node = math_builder(r)
                if node is not None:
                    parent.append(node)
        elif isinstance(r, ImageRun):
            # 内联图片一般在图片处理阶段单独成 <fig>，此处忽略
            continue


def append_title_inline(parent, runs, math_builder=None):
    """标题元素已表达标题语义；去掉 Word 的整段粗体，保留斜体、上下标等内容格式。"""
    normalized = []
    for run in runs:
        if isinstance(run, TextRun) and run.bold:
            run = TextRun(
                text=run.text, bold=False, italic=run.italic,
                superscript=run.superscript, subscript=run.subscript,
                hyperlink=run.hyperlink,
            )
        normalized.append(run)
    append_inline(parent, normalized, math_builder)


def drop_leading_chars(runs, n: int) -> list:
    """返回去掉前 ``n`` 个字符后的 run 列表（用于剥离题注前缀 "Fig. 1." 等）。

    只对 :class:`TextRun` 计数与裁剪，保留其余 run 的格式。
    """
    out = []
    remaining = n
    started = False
    for r in runs:
        if started:
            out.append(r)
            continue
        if isinstance(r, TextRun):
            if remaining >= len(r.text):
                remaining -= len(r.text)
                continue
            # 在该 run 内部切断
            new_r = TextRun(text=r.text[remaining:], bold=r.bold, italic=r.italic,
                            superscript=r.superscript, subscript=r.subscript,
                            hyperlink=r.hyperlink)
            out.append(new_r)
            remaining = 0
            started = True
        else:
            # 前置非文本 run（内联图片/公式/换行）透传，但**不终止**前缀剥离——否则题注/声明段
            # 若以内联图片或公式开头，其后 TextRun 的 "Fig. N"/"Table N"/标签前缀就剥不掉了
            # （实测 S04 fig3 题注段首 run 是内联图片，"Figure. 3." 前缀漏剥）。
            out.append(r)
    return out


def serialize(root, with_doctype: bool = True) -> bytes:
    """序列化为带 XML 声明与 DOCTYPE 的 UTF-8 字节串（缩进美化）。"""
    etree.indent(root, space="  ")
    body = etree.tostring(root, encoding="unicode", pretty_print=True)
    parts = [XML_DECL]
    if with_doctype:
        parts.append(DOCTYPE)
    parts.append(body.rstrip("\n"))
    return ("\n".join(parts) + "\n").encode("utf-8")
