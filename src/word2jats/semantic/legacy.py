"""旧生产管线的过渡语义模型。

阶段 2–3 中旧管线仍用该独立契约保持可运行；阶段 4 整体切换后删除。
新旧字段不混在同一类中，避免半新半旧状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Affiliation:
    aff_id: str
    label: str
    text: str


@dataclass
class Author:
    surname: str = ""
    given_names: str = ""
    orcid: Optional[str] = None
    orcid_authenticated: bool = False
    aff_labels: list = field(default_factory=list)
    is_corresponding: bool = False
    email: Optional[str] = None
    equal_contrib: bool = False
    raw: str = ""


@dataclass
class Editor:
    surname: str = ""
    given_names: str = ""
    role: str = "Academic Editor"


@dataclass
class DateInfo:
    received: Optional[tuple] = None
    revised: Optional[tuple] = None
    accepted: Optional[tuple] = None


@dataclass
class AbstractSection:
    title: Optional[str]
    paragraphs: list = field(default_factory=list)


@dataclass
class Para:
    runs: list = field(default_factory=list)


@dataclass
class Figure:
    number: int
    label: Optional[str] = None
    caption_runs: list = field(default_factory=list)
    image_ph: Optional[int] = None
    _image_blob: Optional[bytes] = None


@dataclass
class TableBlock:
    number: int
    label: Optional[str] = None
    caption_runs: list = field(default_factory=list)
    kind: str = "grid"
    header_rows: list = field(default_factory=list)
    body_rows: list = field(default_factory=list)
    image_ph: Optional[int] = None
    foot_runs: Optional[list] = None
    table_id: Optional[str] = None
    native: bool = False


@dataclass
class Formula:
    display: bool = True
    number: Optional[int] = None


@dataclass
class Section:
    title_runs: list = field(default_factory=list)
    sec_id: str = ""
    blocks: list = field(default_factory=list)
    subsections: list = field(default_factory=list)


@dataclass
class Declaration:
    title: str = ""
    blocks: list = field(default_factory=list)
    label_len: int = 0


@dataclass
class Reference:
    label: str = ""
    raw_text: str = ""
    authors: list = field(default_factory=list)
    editors: list = field(default_factory=list)
    collab: list = field(default_factory=list)
    etal: bool = False
    article_title: Optional[str] = None
    source: Optional[str] = None
    publisher_name: Optional[str] = None
    publisher_loc: Optional[str] = None
    edition: Optional[str] = None
    year: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    fpage: Optional[str] = None
    lpage: Optional[str] = None
    doi: Optional[str] = None
    comment: Optional[str] = None
    pub_type: str = "journal"
    structured: bool = False


@dataclass
class SemanticDoc:
    article_type: str = "research-article"
    article_category: Optional[str] = None
    title_runs: list = field(default_factory=list)
    authors: list = field(default_factory=list)
    affiliations: list = field(default_factory=list)
    editors: list = field(default_factory=list)
    corresp_text: Optional[str] = None
    corresp_emails: list = field(default_factory=list)
    equal_contrib_note: Optional[str] = None
    dates: DateInfo = field(default_factory=DateInfo)
    abstract: list = field(default_factory=list)
    precis: Optional[str] = None
    keywords: list = field(default_factory=list)
    keywords_title: str = "Keywords"
    body: list = field(default_factory=list)
    declarations: list = field(default_factory=list)
    references: list = field(default_factory=list)

    @property
    def title(self) -> str:
        from ..model.blocks import TextRun
        return "".join(
            run.text for run in self.title_runs if isinstance(run, TextRun)
        ).strip()
