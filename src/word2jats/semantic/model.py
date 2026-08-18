"""SemanticDoc v2：源地址驱动的语义契约。

本模型不保存大模型改写的内容字符串。所有可见内容都是
:class:`~word2jats.model.source.SourceText` 地址；渲染时必须回到源对象图取回。
只有 JATS 骨架、内部关系和显式出版配置不属于源可见文字。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Iterable, Optional, TypeAlias

from ..model.source import SourceDocument, SourceText


# ---------------------------------------------------------------------------
# 混合内容：枚举支持的语义内联类型，不接受任意 XML 标签
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Text:
    source: SourceText


@dataclass(frozen=True)
class Styled:
    style: str  # bold | italic | sub | sup
    content: "RichText"

    def __post_init__(self):
        if self.style not in {"bold", "italic", "sub", "sup"}:
            raise ValueError(f"不支持的内联样式: {self.style}")


@dataclass(frozen=True)
class Break:
    pass


@dataclass(frozen=True)
class ExternalLink:
    link_type: str
    href: str
    content: "RichText"


@dataclass(frozen=True)
class CrossReference:
    ref_type: str
    target_ids: tuple[str, ...]
    content: "RichText"
    source_occurrence: Optional[tuple[str, int, int]] = None


@dataclass(frozen=True)
class EmailInline:
    content: "RichText"


@dataclass(frozen=True)
class CitationFieldInline:
    """mixed-citation 中仍被源稿显式标出的著录字段。"""

    field_kind: str  # 当前金标准仅出现 source
    content: "RichText"

    def __post_init__(self):
        if self.field_kind not in {"source"}:
            raise ValueError(f"不支持的混合著录内联字段: {self.field_kind}")


@dataclass(frozen=True)
class InlineGraphic:
    occurrence_id: str


@dataclass(frozen=True)
class InlineFormula:
    formula_id: str


InlinePart: TypeAlias = (
    Text | Styled | Break | ExternalLink | CrossReference | EmailInline
    | CitationFieldInline
    | InlineGraphic | InlineFormula
)


@dataclass(frozen=True)
class RichText:
    parts: tuple[InlinePart, ...] = ()

    @classmethod
    def from_source(cls, source: SourceText) -> "RichText":
        return cls((Text(source),))

    def plain_text(self, doc: SourceDocument) -> str:
        return "".join(_inline_text(part, doc) for part in self.parts)


def _inline_text(part: InlinePart, doc: SourceDocument) -> str:
    if isinstance(part, Text):
        return part.source.text(doc)
    if isinstance(part, Styled):
        return part.content.plain_text(doc)
    if isinstance(part, Break):
        return "\n"
    if isinstance(part, (ExternalLink, CrossReference, EmailInline, CitationFieldInline)):
        return part.content.plain_text(doc)
    if isinstance(part, (InlineGraphic, InlineFormula)):
        return "\ufffc"
    raise TypeError(type(part))


# ---------------------------------------------------------------------------
# MathML 是封闭标准树，可以用受控节点表达
# ---------------------------------------------------------------------------
_MATH_TAGS = {
    "math", "semantics", "mrow", "mi", "mn", "mo", "mtext", "mfrac",
    "msqrt", "msub", "msup", "msubsup", "mover", "munder", "mfenced",
    "munderover", "annotation", "annotation-xml",
}
_MATH_ATTRS = {
    "alttext", "display", "id", "mathvariant", "stretchy", "accent",
    "open", "close", "separators", "encoding",
}


@dataclass(frozen=True)
class MathNode:
    tag: str
    attributes: tuple[tuple[str, str], ...] = ()
    text: Optional[str] = None
    children: tuple["MathNode", ...] = ()

    def __post_init__(self):
        if self.tag not in _MATH_TAGS:
            raise ValueError(f"不支持的 MathML 标签: {self.tag}")
        unknown = {name for name, _ in self.attributes} - _MATH_ATTRS
        if unknown:
            raise ValueError(f"不支持的 MathML 属性: {sorted(unknown)}")


# ---------------------------------------------------------------------------
# 前置区
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TypedText:
    kind: str
    value: RichText


@dataclass(frozen=True)
class JournalIdentifier:
    kind: str
    value: RichText


@dataclass(frozen=True)
class Issn:
    publication_type: str
    value: RichText


@dataclass(frozen=True)
class JournalMeta:
    identifiers: tuple[JournalIdentifier, ...] = ()
    title: Optional[RichText] = None
    abbreviated_titles: tuple[TypedText, ...] = ()
    issns: tuple[Issn, ...] = ()
    publisher_name: Optional[RichText] = None
    publisher_location: Optional[RichText] = None


@dataclass(frozen=True)
class ArticleIdentifier:
    kind: str
    value: RichText


@dataclass(frozen=True)
class ArticleCategory:
    kind: str
    subject: RichText


@dataclass(frozen=True)
class ContributorIdentifier:
    kind: str
    value: SourceText
    authenticated: Optional[bool] = None


@dataclass(frozen=True)
class PersonName:
    surname: SourceText
    given_names: SourceText
    suffix: Optional[SourceText] = None


@dataclass(frozen=True)
class Address:
    entity_id: str
    lines: tuple[RichText, ...] = ()
    postal_code: Optional[SourceText] = None
    phone: Optional[SourceText] = None


@dataclass(frozen=True)
class Affiliation:
    entity_id: str
    label: Optional[RichText]
    content: RichText
    address_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Contributor:
    entity_id: str
    kind: str
    name: PersonName
    degrees: tuple[SourceText, ...] = ()
    roles: tuple[SourceText, ...] = ()
    identifiers: tuple[ContributorIdentifier, ...] = ()
    affiliation_ids: tuple[str, ...] = ()
    address_ids: tuple[str, ...] = ()
    references: tuple[CrossReference, ...] = ()
    emails: tuple[SourceText, ...] = ()
    author_comments: tuple["Paragraph", ...] = ()
    corresponding: bool = False
    # 不同期刊的 contrib 子字段次序不同；记录类型化槽位次序，
    # 如 ("identifier:0", "name", "reference:0", "email:0")。
    child_order: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContributorGroup:
    kind: Optional[str]
    contributors: tuple[Contributor, ...]


@dataclass(frozen=True)
class Correspondence:
    entity_id: str
    content: RichText


@dataclass(frozen=True)
class DateValue:
    kind: str
    year: SourceText
    month: Optional[SourceText] = None
    day: Optional[SourceText] = None


@dataclass(frozen=True)
class AbstractSection:
    title: Optional[RichText]
    paragraphs: tuple["Paragraph", ...]
    # True 对应 <abstract><sec>...</sec></abstract>；False 对应
    # 摘要下直接出现的 <title>/<p>。二者语义相近，JATS 骨架不同。
    wrapped: bool = True


@dataclass(frozen=True)
class Abstract:
    kind: str  # main | graphical | precis
    sections: tuple[AbstractSection, ...] = ()
    blocks: tuple["Block", ...] = ()


@dataclass(frozen=True)
class KeywordGroup:
    kind: Optional[str]
    title: Optional[RichText]
    keywords: tuple[RichText, ...]


@dataclass(frozen=True)
class License:
    license_type: Optional[str]
    href: Optional[str]
    paragraphs: tuple[RichText, ...]


@dataclass(frozen=True)
class Permissions:
    copyright_statement: Optional[RichText] = None
    copyright_year: Optional[RichText] = None
    license: Optional[License] = None


# ---------------------------------------------------------------------------
# 通用脚注与正文显示对象
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Note:
    entity_id: str
    kind: Optional[str]
    label: Optional[RichText]
    paragraphs: tuple[RichText, ...]
    owner_scope: str  # article | contrib-group | table | body
    target_ids: tuple[str, ...] = ()
    reference_occurrences: tuple[tuple[str, int, int], ...] = ()
    emit_id: bool = True


@dataclass(frozen=True)
class Paragraph:
    entity_id: Optional[str]
    content: RichText


@dataclass(frozen=True)
class Caption:
    title: Optional[RichText] = None
    paragraphs: tuple[Paragraph, ...] = ()


@dataclass(frozen=True)
class Formula:
    entity_id: str
    presentation: str  # mathml | image
    math: Optional[MathNode] = None
    omml_occurrence: Optional[str] = None
    image_occurrence: Optional[str] = None
    label: Optional[RichText] = None
    display: bool = True
    emit_id: bool = True

    def __post_init__(self):
        if self.presentation not in {"mathml", "image"}:
            raise ValueError(f"不支持的公式展示方式: {self.presentation}")
        if self.presentation == "mathml" and self.math is None:
            raise ValueError("mathml 公式缺运算树")
        if self.presentation == "image" and not self.image_occurrence:
            raise ValueError("图形公式缺图片出现记录")


@dataclass(frozen=True)
class Figure:
    entity_id: str
    label: Optional[RichText]
    caption: Optional[Caption]
    graphics: tuple[str, ...]
    position: Optional[str] = None


@dataclass(frozen=True)
class FigureGroup:
    entity_id: str
    label: Optional[RichText]
    caption: Optional[Caption]
    figures: tuple[Figure, ...]


@dataclass(frozen=True)
class TableCellStyle:
    align: Optional[str] = None
    valign: Optional[str] = None
    style: Optional[str] = None
    width: Optional[str] = None


@dataclass(frozen=True)
class TableCell:
    content: RichText
    cell_type: str = "td"  # th | td
    colspan: int = 1
    rowspan: int = 1
    header_kind: Optional[str] = None  # col | row | None
    style: TableCellStyle = field(default_factory=TableCellStyle)

    def __post_init__(self):
        if self.cell_type not in {"th", "td"}:
            raise ValueError(f"不支持的单元格类型: {self.cell_type}")
        if self.header_kind not in {None, "col", "row"}:
            raise ValueError(f"不支持的表头类型: {self.header_kind}")
        if self.colspan < 1 or self.rowspan < 1:
            raise ValueError("单元格跨度必须为正整数")


@dataclass(frozen=True)
class TableRow:
    cells: tuple[TableCell, ...]


@dataclass(frozen=True)
class TableBlock:
    entity_id: str
    label: Optional[RichText]
    caption: Optional[Caption]
    column_widths: tuple[Optional[str], ...]
    header_rows: tuple[TableRow, ...]
    body_rows: tuple[TableRow, ...]
    notes: tuple[Note, ...] = ()
    foot_paragraphs: tuple[RichText, ...] = ()
    foot_order: tuple[str, ...] = ()  # note:0 | paragraph:0
    position: Optional[str] = None
    graphic_occurrence: Optional[str] = None
    # 表行可位于 <tbody> 中，也可直接位于 <table> 下。
    # 这是 JATS 结构事实，不能在往返时默认改写。
    body_container: str = "tbody"  # tbody | direct

    def __post_init__(self):
        if self.body_container not in {"tbody", "direct"}:
            raise ValueError(f"不支持的表体容器: {self.body_container}")


@dataclass(frozen=True)
class DefinitionItem:
    term: RichText
    definitions: tuple[RichText, ...]


@dataclass(frozen=True)
class DefinitionList:
    items: tuple[DefinitionItem, ...]


@dataclass(frozen=True)
class Glossary:
    entity_id: Optional[str]
    title: Optional[RichText]
    blocks: tuple["Block", ...]


@dataclass(frozen=True)
class Section:
    entity_id: Optional[str]
    title: Optional[RichText]
    blocks: tuple["Block", ...]


Block: TypeAlias = (
    Paragraph | Formula | Figure | FigureGroup | TableBlock | DefinitionList
    | Glossary | Section
)


# ---------------------------------------------------------------------------
# 参考文献与后置区
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReferencePersonGroup:
    kind: str
    persons: tuple[PersonName, ...] = ()
    collaborations: tuple[RichText, ...] = ()
    et_al: Optional[RichText] = None
    child_order: tuple[str, ...] = ()  # person:0 | collaboration:0 | et_al


@dataclass(frozen=True)
class ReferenceIdentifier:
    kind: str
    value: RichText
    carrier: Optional[str] = None  # bare | url | hyperlink
    href: Optional[str] = None


@dataclass(frozen=True)
class StructuredCitation:
    publication_type: str
    person_groups: tuple[ReferencePersonGroup, ...] = ()
    article_title: Optional[RichText] = None
    chapter_title: Optional[RichText] = None
    source: Optional[RichText] = None
    year: Optional[RichText] = None
    month: Optional[RichText] = None
    day: Optional[RichText] = None
    volume: Optional[RichText] = None
    issue: Optional[RichText] = None
    fpage: Optional[RichText] = None
    lpage: Optional[RichText] = None
    elocation_id: Optional[RichText] = None
    edition: Optional[RichText] = None
    publisher_name: Optional[RichText] = None
    publisher_location: Optional[RichText] = None
    identifiers: tuple[ReferenceIdentifier, ...] = ()
    comments: tuple[RichText, ...] = ()
    # 著录体例允许字段顺序变化；这是类型化字段的原序，不是 XML 逃生口。
    field_order: tuple[str, ...] = ()


@dataclass(frozen=True)
class MixedCitation:
    publication_type: Optional[str]
    content: RichText


Citation: TypeAlias = StructuredCitation | MixedCitation


@dataclass(frozen=True)
class ReferenceIdentity:
    surnames: tuple[str, ...] = ()
    year: Optional[str] = None
    year_suffix: Optional[str] = None
    title_key: Optional[str] = None


@dataclass(frozen=True)
class Reference:
    entity_id: str
    label: Optional[RichText]
    citation: Citation
    identity: ReferenceIdentity = field(default_factory=ReferenceIdentity)


@dataclass(frozen=True)
class BackSection:
    kind: str  # ack | author-comment | declaration | glossary
    entity_id: Optional[str]
    title: Optional[RichText]
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class ReferenceList:
    title: Optional[RichText]
    references: tuple[Reference, ...]


@dataclass
class SemanticDoc:
    source: SourceDocument
    article_type: str = "research-article"
    language: str = "en"
    dtd_version: str = "1.3"
    journal: JournalMeta = field(default_factory=JournalMeta)
    article_identifiers: tuple[ArticleIdentifier, ...] = ()
    categories: tuple[ArticleCategory, ...] = ()
    title: Optional[RichText] = None
    contributor_groups: tuple[ContributorGroup, ...] = ()
    affiliations: tuple[Affiliation, ...] = ()
    addresses: tuple[Address, ...] = ()
    correspondence: tuple[Correspondence, ...] = ()
    author_note_paragraphs: tuple[RichText, ...] = ()
    notes: tuple[Note, ...] = ()
    dates: tuple[DateValue, ...] = ()
    abstracts: tuple[Abstract, ...] = ()
    keyword_groups: tuple[KeywordGroup, ...] = ()
    permissions: Optional[Permissions] = None
    inline_formulas: tuple[Formula, ...] = ()
    body: tuple[Block, ...] = ()
    back_sections: tuple[BackSection, ...] = ()
    reference_list: Optional[ReferenceList] = None

    def visible_title(self) -> str:
        return self.title.plain_text(self.source).strip() if self.title else ""

    def validate(self) -> None:
        """证明源地址、实体身份、对象和关系均闭合。"""
        self.source.validate()
        entities: dict[str, Any] = {}
        source_texts: list[SourceText] = []
        xrefs: list[CrossReference] = []
        graphics: list[str] = []
        formulas: list[InlineFormula] = []
        note_targets: list[str] = []
        for item in _walk(self):
            if isinstance(item, SourceText):
                source_texts.append(item)
            if hasattr(item, "entity_id"):
                entity_id = getattr(item, "entity_id")
                if entity_id:
                    if entity_id in entities:
                        raise ValueError(f"重复语义身份: {entity_id}")
                    entities[entity_id] = item
            if isinstance(item, CrossReference):
                xrefs.append(item)
            elif isinstance(item, (InlineGraphic,)):
                graphics.append(item.occurrence_id)
            elif isinstance(item, Figure):
                graphics.extend(item.graphics)
            elif isinstance(item, TableBlock) and item.graphic_occurrence:
                graphics.append(item.graphic_occurrence)
            elif isinstance(item, Formula):
                if item.omml_occurrence:
                    graphics.append(item.omml_occurrence)
                if item.image_occurrence:
                    graphics.append(item.image_occurrence)
            elif isinstance(item, InlineFormula):
                formulas.append(item)
            elif isinstance(item, Note):
                note_targets.extend(item.target_ids)
        for source in source_texts:
            for text_range in source.ranges:
                self.source.slice_text(text_range)
        for xref in xrefs:
            unknown = set(xref.target_ids) - set(entities)
            if unknown:
                raise ValueError(f"交叉引用指向未知实体: {sorted(unknown)}")
        for occurrence_id in graphics:
            if occurrence_id not in {item.occ_id for item in self.source.occurrences}:
                raise ValueError(f"显示对象指向未知出现: {occurrence_id}")
        for inline in formulas:
            target = entities.get(inline.formula_id)
            if not isinstance(target, Formula):
                raise ValueError(f"行内公式指向未知公式: {inline.formula_id}")
        unknown_notes = set(note_targets) - set(entities)
        if unknown_notes:
            raise ValueError(f"脚注指向未知实体: {sorted(unknown_notes)}")


def _walk(value: Any) -> Iterable[Any]:
    """遍历类型化契约；SourceDocument 是独立图，不向内展开。"""
    yield value
    if isinstance(value, SourceDocument):
        return
    if is_dataclass(value):
        for item in fields(value):
            yield from _walk(getattr(value, item.name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
