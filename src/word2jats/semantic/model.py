"""语义模型：LLM 理解层与确定性渲染层之间的**类型化契约**。

设计立场（本次重构的核心）：
- 文档理解全部由 LLM 承担，产出的是"结构判定"——哪一段是标题、哪一段是段落、
  哪张表是图片、这条参考的各字段边界在哪。
- 但**文本内容不由 LLM 重写**。正文类内容以"源块索引"引用解析层（parse/）的原始
  ``runs``，渲染时按索引取回，斜体/上下标/加粗等内联格式零损失、内容守恒结构性成立；
  只有必须把一个源块切成多个语义单元处（作者行→逐位作者、参考行→各字段、通讯段），
  LLM 才给出**子串**，再由出口校验确认其为 docx 原文子串。
- 因此本模块的对象携带的是"已从 IR 解析回来的内容"（runs 或校验过的子串），
  渲染层只消费本模型，不再回看 parse/ 或 classify/，职责单一。

字段命名与取值口径对齐评测 L2（scripts/eval/structure.py）：作者按姓名对齐、
图表按 caption 对齐、参考按 label 对齐、章节按 (归一标题, 深度) 对齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
# 前置区（front）
# --------------------------------------------------------------------------- #
@dataclass
class Affiliation:
    aff_id: str            # 'aff1'
    label: str             # 上标编号 '1'（可空）
    text: str              # 机构全文（docx 原文子串）


@dataclass
class Author:
    surname: str = ""
    given_names: str = ""
    orcid: Optional[str] = None          # 纯 16 位带连字符
    orcid_authenticated: bool = False
    aff_labels: list = field(default_factory=list)   # 关联单位上标 ['1','2']
    is_corresponding: bool = False
    email: Optional[str] = None          # 非通讯作者的普通邮箱
    equal_contrib: bool = False
    raw: str = ""                        # 原始姓名串（调试）


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
    title: Optional[str]                 # 'Background:'；非结构化摘要为 None
    paragraphs: list = field(default_factory=list)   # list[list[run]]，每段是原始 runs


# --------------------------------------------------------------------------- #
# 正文块（body blocks）—— 携带解析回来的原始 runs，保内联格式
# --------------------------------------------------------------------------- #
@dataclass
class Para:
    """普通段落：直接携带源块 runs。"""
    runs: list = field(default_factory=list)


@dataclass
class Figure:
    number: int
    label: Optional[str] = None          # docx 原样前缀 'Fig. 1'/'Figure 1'
    caption_runs: list = field(default_factory=list)   # 剥掉 label 前缀后的题注 runs
    image_ph: Optional[int] = None       # 关联的图片占位符 index（LLM 按位置关联）
    _image_blob: Optional[bytes] = None  # 该图占位符对应的 docx 原始图片字节（assemble 回填）


@dataclass
class TableBlock:
    number: int
    label: Optional[str] = None          # 'Table 1'
    caption_runs: list = field(default_factory=list)
    kind: str = "grid"                   # grid=有单元格结构；image=整表是图片→graphic
    # kind=grid：单元格网格（每格是 runs 列表）；来自原生 w:tbl 或制表符行拆分
    header_rows: list = field(default_factory=list)  # list[list[cell_runs]]
    body_rows: list = field(default_factory=list)    # list[list[cell_runs]]
    # kind=image：整表图片占位符
    image_ph: Optional[int] = None
    foot_runs: Optional[list] = None     # 表脚注 runs（缩写释义等）
    table_id: Optional[str] = None       # 覆盖默认 'T%03d'（如 05 的无题注 RT 表）
    native: bool = False                 # 源自原生 w:tbl（真列头，表头 th 用 scope="col"）；
                                         # 制表符重建表为 False（表头裸 th，与结构参考一致）


@dataclass
class Formula:
    display: bool = True
    number: Optional[int] = None


@dataclass
class Section:
    title_runs: list = field(default_factory=list)   # 标题 runs（可空=隐式首节）
    sec_id: str = ""
    blocks: list = field(default_factory=list)       # list[Para/Figure/TableBlock/Formula]
    subsections: list = field(default_factory=list)  # list[Section]


# --------------------------------------------------------------------------- #
# 后置区（back）
# --------------------------------------------------------------------------- #
@dataclass
class Declaration:
    """声明类小节：Funding / Conflict of Interest / Author Contributions / Acknowledgments…"""
    title: str = ""
    blocks: list = field(default_factory=list)       # list[Para/TableBlock]
    label_len: int = 0                               # 行内标签("Funding: …")需剥离的前缀长度


@dataclass
class Reference:
    label: str = ""                      # '[1]'
    raw_text: str = ""                   # 整条原文（mixed-citation 用）
    # 结构化字段（LLM 切分成功时填；都是原文子串）
    authors: list = field(default_factory=list)   # list[(surname, given/initials)]
    editors: list = field(default_factory=list)   # 书籍编者 list[(surname, given/initials)]
    collab: list = field(default_factory=list)    # 机构/团体作者
    etal: bool = False
    article_title: Optional[str] = None
    source: Optional[str] = None
    publisher_name: Optional[str] = None          # 书籍出版社
    publisher_loc: Optional[str] = None           # 出版地
    edition: Optional[str] = None                 # 版次（'2 ed.'）
    year: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    fpage: Optional[str] = None
    lpage: Optional[str] = None
    doi: Optional[str] = None
    comment: Optional[str] = None        # '(In Chinese)' 等
    pub_type: str = "journal"
    structured: bool = False             # True → element-citation


# --------------------------------------------------------------------------- #
# 文档容器
# --------------------------------------------------------------------------- #
@dataclass
class SemanticDoc:
    article_type: str = "research-article"
    article_category: Optional[str] = None
    title_runs: list = field(default_factory=list)
    authors: list = field(default_factory=list)
    affiliations: list = field(default_factory=list)
    editors: list = field(default_factory=list)
    corresp_text: Optional[str] = None   # 通讯段原文（忠实保留，不模板化）
    corresp_emails: list = field(default_factory=list)   # 兜底：文中未出现但已知的邮箱
    equal_contrib_note: Optional[str] = None
    dates: DateInfo = field(default_factory=DateInfo)
    abstract: list = field(default_factory=list)        # list[AbstractSection]
    precis: Optional[str] = None
    keywords: list = field(default_factory=list)
    keywords_title: str = "Keywords"                    # 'Keywords'/'Key words'（docx 原样）
    body: list = field(default_factory=list)            # list[Section]
    declarations: list = field(default_factory=list)    # list[Declaration]
    references: list = field(default_factory=list)      # list[Reference]

    @property
    def title(self) -> str:
        from ..model.blocks import TextRun
        return "".join(r.text for r in self.title_runs if isinstance(r, TextRun)).strip()
