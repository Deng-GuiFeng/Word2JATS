"""中间表示（IR）数据模型。

本模块定义与 Word 和 JATS 都解耦的中间数据结构。解析层（parse/）把 docx
转成由这些对象组成的 :class:`Document`；理解层（understand/）序列化它给 LLM 判定
语义结构；渲染层（render/）据语义模型生成 JATS XML。

设计原则：
- 解析层 IR 尽量贴近 docx 的"物理"结构（有序的段落 / 表格 / 内联 run），
  不做任何语义判断；语义判断全部由理解层的 LLM 承担，保证职责单一。
- 内联内容用不同的 run 子类表示（文本 / 公式 / 图片 / 换行），统一放在
  ``Paragraph.runs`` 列表里，从而保留它们在原文中的先后顺序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# 内联 run（段落内的最小内容单元）
# --------------------------------------------------------------------------- #
@dataclass
class TextRun:
    """一段带格式的文本。"""

    text: str = ""
    bold: bool = False
    italic: bool = False
    superscript: bool = False
    subscript: bool = False
    #: 若该 run 位于超链接内，记录目标（外链 URL 或内部锚点）
    hyperlink: Optional[str] = None


@dataclass
class MathRun:
    """一个 OMML 公式（行内或块级）。

    ``omml`` 保存原始的 ``m:oMath`` lxml 元素，供后续 OMML→MathML 转换；
    解析层不在此做转换，避免把 XSLT 依赖耦合进 IR。
    """

    omml: Any  # lxml.etree._Element (m:oMath)
    display: bool = False  # True 表示来自 m:oMathPara（块级独立段落）


@dataclass
class ImageRun:
    """段落内的一张内嵌图片引用。"""

    #: docx 包内的部件名，如 ``word/media/image1.tif``
    part_name: str
    #: 关系 id（r:embed / r:id）
    rel_id: Optional[str] = None
    #: 原始二进制（解析时即读出，便于后续转换 / 外部化）
    blob: Optional[bytes] = None
    #: 图片格式（由内容嗅探，如 'TIFF'/'PNG'/'JPEG'/'WMF'）
    fmt: Optional[str] = None
    #: 像素尺寸 (w, h)，无法识别时为 None
    size: Optional[tuple] = None
    #: 是否为 VML/OLE 回退位图（如 MathType 公式的 wmf 截图），构建时应忽略
    is_fallback: bool = False


@dataclass
class BreakRun:
    """换行（``w:br``）。在 caption / 地址等多行文本里需要保留。"""

    pass


# --------------------------------------------------------------------------- #
# 块级元素
# --------------------------------------------------------------------------- #
@dataclass
class Paragraph:
    """一个段落（``w:p``）。"""

    runs: list = field(default_factory=list)
    style_id: Optional[str] = None
    style_name: Optional[str] = None
    #: 编号信息 (num_id, ilvl)；非列表段落为 None
    numbering: Optional[tuple] = None
    #: 对齐方式（left/center/right/both），用于辅助识别标题 / 题注
    alignment: Optional[str] = None

    @property
    def text(self) -> str:
        """纯文本（拼接所有 TextRun，公式 / 图片占位为空）。"""
        out = []
        for r in self.runs:
            if isinstance(r, TextRun):
                out.append(r.text)
            elif isinstance(r, BreakRun):
                out.append("\n")
        return "".join(out)

    @property
    def is_bold(self) -> bool:
        """整段是否基本为粗体（用于启发式识别标题）。忽略空白 run。"""
        runs = [r for r in self.runs if isinstance(r, TextRun) and r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)

    @property
    def is_italic(self) -> bool:
        runs = [r for r in self.runs if isinstance(r, TextRun) and r.text.strip()]
        return bool(runs) and all(r.italic for r in runs)

    @property
    def images(self) -> list:
        return [r for r in self.runs if isinstance(r, ImageRun)]


@dataclass
class TableCell:
    #: 单元格内的块（通常是若干 Paragraph）
    blocks: list = field(default_factory=list)
    grid_span: int = 1  # 跨列数（w:gridSpan）
    v_merge: Optional[str] = None  # 'restart' / 'continue'（w:vMerge）

    @property
    def text(self) -> str:
        return "\n".join(
            b.text for b in self.blocks if isinstance(b, Paragraph)
        ).strip()


@dataclass
class TableRow:
    cells: list = field(default_factory=list)
    header: bool = False   # w:trPr/w:tblHeader（跨页重复表头行）——多行表头的原生信号


@dataclass
class Table:
    rows: list = field(default_factory=list)
    #: 列宽（来自 w:tblGrid 的 w:gridCol，单位 twips），可为空
    grid_cols: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 文档容器
# --------------------------------------------------------------------------- #
@dataclass
class Document:
    """解析后的 docx 中间表示。"""

    #: 有序的块序列（Paragraph 与 Table 交错，保留原文顺序）
    blocks: list = field(default_factory=list)
    #: styleId -> 样式名（来自 styles.xml）
    styles: dict = field(default_factory=dict)

    @property
    def paragraphs(self) -> list:
        return [b for b in self.blocks if isinstance(b, Paragraph)]
