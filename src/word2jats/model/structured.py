"""语义模型（StructuredDoc）。

语义层（classify/）把解析 IR 转成本模块的结构化对象；构建层（build/）据此生成 JATS。
这一层表达"文章由哪些语义部件组成"，与 docx 物理结构和 JATS 标签都解耦。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Affiliation:
    aff_id: str            # 如 'aff1'
    label: str             # 上标编号，如 '1'
    text: str              # 机构全文


@dataclass
class Author:
    surname: str = ""
    given_names: str = ""
    orcid: Optional[str] = None          # 纯 16 位带连字符，如 0000-0002-...
    orcid_authenticated: bool = False
    aff_labels: list = field(default_factory=list)   # 关联的单位上标，如 ['1','2']
    is_corresponding: bool = False
    email: Optional[str] = None
    equal_contrib: bool = False          # 是否标注共同贡献（†/#）
    raw: str = ""                        # 原始姓名串（调试用）


@dataclass
class Editor:
    surname: str = ""
    given_names: str = ""
    role: str = "Academic Editor"


@dataclass
class DateInfo:
    received: Optional[tuple] = None     # (year, month, day) 均为 str
    revised: Optional[tuple] = None
    accepted: Optional[tuple] = None


@dataclass
class AbstractSection:
    title: Optional[str]                 # 如 'Background:'；非结构化摘要为 None
    paragraphs: list = field(default_factory=list)   # list[Paragraph]
    lead: Optional[str] = None           # 子标题后的正文文本（结构化摘要常用）


@dataclass
class Reference:
    label: str = ""                      # '[1]'
    raw_text: str = ""                   # 整条引用原文
    # 结构化字段（解析成功时填充，决定 element-citation；否则走 mixed-citation）
    authors: list = field(default_factory=list)   # list[(surname, given/initials)]
    collab: list = field(default_factory=list)    # 机构/团体作者（<collab>），如指南工作组
    etal: bool = False
    article_title: Optional[str] = None
    source: Optional[str] = None
    year: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    fpage: Optional[str] = None
    lpage: Optional[str] = None
    doi: Optional[str] = None
    pub_type: str = "journal"            # journal/book/web...
    structured: bool = False             # True → element-citation


@dataclass
class FigureItem:
    fig_id: str                          # 'F001'
    number: int                          # 1
    label: str                           # 'Fig. 1.'
    caption_runs: list = field(default_factory=list)  # 题注内联 runs
    # 图片来源：外部化后的相对路径，如 'JIN49347/fig-01.jpg'
    href: Optional[str] = None
    src_blob: Optional[bytes] = None     # 原始图片字节
    src_fmt: Optional[str] = None


@dataclass
class Section:
    """正文 / 后置区的章节（可嵌套）。"""

    title: str = ""
    sec_id: str = ""
    number: Optional[str] = None         # '2.1' 等（生成的节号）
    blocks: list = field(default_factory=list)   # 段内块（Paragraph/Table/Figure 占位）
    subsections: list = field(default_factory=list)
    sec_type: Optional[str] = None       # JATS sec-type，可选
    label_len: int = 0                   # 行内声明小节首块需剥离的前缀字符数
                                         # （"Author Contributions: …" 标签长度，
                                         #  渲染时去掉，避免与 <title> 重复）


@dataclass
class StructuredDoc:
    article_type: str = "research-article"
    article_category: Optional[str] = None   # 'Original Research' / 'Review' ...
    title: str = ""
    authors: list = field(default_factory=list)
    affiliations: list = field(default_factory=list)
    editors: list = field(default_factory=list)
    corresp_email_map: list = field(default_factory=list)  # [(email, name)]
    equal_contrib_note: Optional[str] = None
    dates: DateInfo = field(default_factory=DateInfo)
    abstract: list = field(default_factory=list)        # list[AbstractSection]
    precis: Optional[str] = None                        # "Capsule:" 一句话摘要 → abstract-type="precis"
    keywords: list = field(default_factory=list)
    body: list = field(default_factory=list)            # list[Section]
    back_sections: list = field(default_factory=list)   # 声明类小节
    acknowledgment: Optional[str] = None
    references: list = field(default_factory=list)
    figures: list = field(default_factory=list)
