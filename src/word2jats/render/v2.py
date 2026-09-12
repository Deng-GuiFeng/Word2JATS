"""SemanticDoc v2 的确定性 JATS 渲染器。

本模块只消费类型化语义对象和源地址：可见文字从 SourceDocument
机械取回，媒体从 BinaryResource 机械取回。遇到不支持的语义
类型立即失败，不保留原始 XML 作为逃生口。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Optional

from lxml import etree

from ..build.ids import DocIdAllocator
from ..build.jats import (
    DOCTYPE, MML, NSMAP, PERSON_GROUP_TYPES, XLINK, XML, XML_DECL,
)
from ..model.source import BinaryResource, OBJECT_REPLACEMENT, SourceText
from ..semantic import model as sm
from ..verify.provenance import ProvenanceBuilder, ProvenanceEntry


@dataclass(frozen=True)
class V2RenderResult:
    xml_bytes: bytes
    media: dict[str, bytes]
    provenance: tuple[ProvenanceEntry, ...]


class V2RenderError(ValueError):
    pass


def _element(tag: str, **attributes) -> etree._Element:
    value = etree.Element(tag)
    for name, item in attributes.items():
        if item is None:
            continue
        if name == "xlink_href":
            value.set(f"{{{XLINK}}}href", str(item))
        elif name == "xml_lang":
            value.set(f"{{{XML}}}lang", str(item))
        else:
            value.set(name.replace("_", "-"), str(item))
    return value


def _sub(parent: etree._Element, tag: str, **attributes) -> etree._Element:
    child = _element(tag, **attributes)
    parent.append(child)
    return child


def _iter_values(value: Any) -> Iterable[Any]:
    yield value
    if is_dataclass(value):
        for item in fields(value):
            if item.name == "source":
                continue
            yield from _iter_values(getattr(value, item.name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _iter_values(item)


class _EntityIds:
    """语义身份到输出 XML ID 的文档级映射。"""

    _KINDS = {
        sm.Affiliation: "affiliation",
        sm.Correspondence: "correspondence",
        sm.Note: "footnote",
        sm.Paragraph: "paragraph",
        sm.Formula: "formula",
        sm.Figure: "figure",
        sm.FigureGroup: "figure-group",
        sm.TableBlock: "table",
        sm.Glossary: "glossary",
        sm.Section: "section",
        sm.BackSection: "section",
        sm.Reference: "reference",
    }

    def __init__(self, document: sm.SemanticDoc,
                 reserved_ids: Iterable[str] = ()):
        self._allocator = DocIdAllocator()
        self._allocator.reserve(reserved_ids)
        self._kind_by_identity: dict[str, str] = {}
        self._mapped: dict[str, str] = {}
        for value in _iter_values(document):
            kind = self._KINDS.get(type(value))
            identity = getattr(value, "entity_id", None)
            if kind is None or not identity or not self._emits_id(value):
                continue
            old = self._kind_by_identity.setdefault(identity, kind)
            if old != kind:
                raise V2RenderError(f"语义身份类型冲突: {identity}")

    @staticmethod
    def _emits_id(value: Any) -> bool:
        if isinstance(value, sm.Paragraph):
            return value.entity_id is not None
        if isinstance(value, (sm.Section, sm.Glossary, sm.BackSection)):
            return value.entity_id is not None
        if isinstance(value, (sm.Formula, sm.Note)):
            return value.emit_id
        return not isinstance(value, sm.Contributor)

    def get(self, identity: str) -> str:
        if identity not in self._kind_by_identity:
            raise V2RenderError(f"引用的语义实体不产生 XML ID: {identity}")
        if identity not in self._mapped:
            self._mapped[identity] = self._allocator.take(
                self._kind_by_identity[identity]
            )
        return self._mapped[identity]

    def optional(self, identity: Optional[str]) -> Optional[str]:
        return self.get(identity) if identity else None

    def take(self, kind: str) -> str:
        """为没有独立语义身份的子对象发号，仍共用全文发号器。"""
        return self._allocator.take(kind)


class V2Renderer:
    def __init__(self, document: sm.SemanticDoc, media_prefix: str = "media",
                 head_jats_xml: Optional[str] = None):
        document.validate()
        self.document = document
        self.source = document.source
        self._head_article = None
        self._head_article_meta = None
        if head_jats_xml is not None:
            parser = etree.XMLParser(
                resolve_entities=False, load_dtd=False, no_network=True,
                remove_blank_text=True,
            )
            try:
                head_article = etree.fromstring(head_jats_xml.encode("utf-8"), parser)
            except (UnicodeError, etree.XMLSyntaxError) as error:
                raise V2RenderError(f"头部模型返回的 XML 无法解析: {error}") from error
            front = head_article.find("front") if head_article.tag == "article" else None
            article_meta = front.find("article-meta") if front is not None else None
            if article_meta is None:
                raise V2RenderError(
                    "头部模型返回值必须是 article/front/article-meta"
                )
            self._head_article = head_article
            self._head_article_meta = article_meta
        reserved_ids = (
            element.get("id")
            for element in self._head_article.iter()
            if element.get("id")
        ) if self._head_article is not None else ()
        self.ids = _EntityIds(document, reserved_ids)
        self.provenance = ProvenanceBuilder()
        self.media: dict[str, bytes] = {}
        self._media_by_resource: dict[str, str] = {}
        self.media_prefix = str(PurePosixPath(media_prefix))
        self._formulas = {
            formula.entity_id: formula for formula in document.inline_formulas
        }

    def _register_model_text(self, root: etree._Element) -> None:
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            if element.text:
                self.provenance.model_text(element, "text", element.text)
            if element.tail:
                self.provenance.model_text(element, "tail", element.tail)

    # ------------------------------------------------------------------
    # 文字、格式、关系与媒体
    # ------------------------------------------------------------------
    def _append(self, parent: etree._Element, value: str,
                source_range=None) -> None:
        if not value:
            return
        if OBJECT_REPLACEMENT in value:
            raise V2RenderError("对象占位符不得作为普通文字输出")
        if len(parent):
            target = parent[-1]
            slot = "tail"
            start = len(target.tail or "")
            target.tail = (target.tail or "") + value
        else:
            target = parent
            slot = "text"
            start = len(parent.text or "")
            parent.text = (parent.text or "") + value
        if source_range is not None:
            self.provenance.source_text(target, slot, start, value, source_range)

    def _append_resolved(self, parent: etree._Element, resolved,
                         projection: str) -> None:
        if projection not in {
            "preserve", "title", "plain", "subsup", "simple-text",
        }:
            raise V2RenderError(f"未知格式投影槽位: {projection}")
        run = resolved.run
        tags = []
        if run is not None and projection != "plain":
            if run.superscript:
                tags.append("sup")
            elif run.subscript:
                tags.append("sub")
            if run.bold and projection not in {"title", "subsup"}:
                tags.append("bold")
            if run.italic and projection != "subsup":
                tags.append("italic")
        # JATS %simple-text; 允许强调、上下标、行内对象和公式，
        # 但不允许链接容器。超链接的可见文字仍按源区间输出，
        # 只是不在这种槽位内生成非法 <ext-link> 外壳。
        if resolved.hyperlink and projection not in {
            "plain", "subsup", "simple-text",
        }:
            link = _sub(parent, "ext-link", ext_link_type="uri",
                        xlink_href=resolved.hyperlink)
            self.provenance.source_attribute(
                link, f"{{{XLINK}}}href", resolved.hyperlink,
                (resolved.source_range,),
            )
            parent = link
        for tag in tags:
            parent = _sub(parent, tag)
        self._append(parent, resolved.text, resolved.source_range)

    def source_text(self, parent: etree._Element, source: SourceText,
                    projection: str = "plain") -> None:
        for resolved in source.runs(self.source):
            self._append_resolved(parent, resolved, projection)

    def rich(self, parent: etree._Element, rich: sm.RichText,
             projection: str = "preserve") -> None:
        for part in rich.parts:
            if isinstance(part, sm.Text):
                self.source_text(parent, part.source, projection)
            elif isinstance(part, sm.ConfigText):
                if parent.text is None and len(parent) == 0:
                    target = parent
                    slot = "text"
                    start = 0
                    parent.text = part.value
                elif len(parent):
                    target = parent[-1]
                    slot = "tail"
                    start = len(target.tail or "")
                    target.tail = (target.tail or "") + part.value
                else:
                    target = parent
                    slot = "text"
                    start = len(parent.text or "")
                    parent.text = (parent.text or "") + part.value
                self.provenance.config(
                    target, slot, part.value, part.key,
                    start=start, end=start + len(part.value),
                )
            elif isinstance(part, sm.TransformedText):
                if len(parent):
                    target = parent[-1]
                    slot = "tail"
                    start = len(target.tail or "")
                    target.tail = (target.tail or "") + part.value
                else:
                    target = parent
                    slot = "text"
                    start = len(parent.text or "")
                    parent.text = (parent.text or "") + part.value
                self.provenance.transform(
                    target, slot, part.value, part.transform,
                    ranges=part.source.ranges,
                    start=start, end=start + len(part.value),
                )
            elif isinstance(part, sm.Styled):
                wrapper = _sub(parent, part.style)
                self.rich(wrapper, part.content, projection)
            elif isinstance(part, sm.Break):
                _sub(parent, "break")
            elif isinstance(part, sm.ExternalLink):
                link = _sub(parent, "ext-link", ext_link_type=part.link_type,
                            xlink_href=part.href)
                if part.href_config_key:
                    self.provenance.config_attribute(
                        link, f"{{{XLINK}}}href", part.href, part.href_config_key
                    )
                else:
                    ranges = tuple(_rich_ranges(part.content))
                    self.provenance.source_attribute(
                        link, f"{{{XLINK}}}href", part.href, ranges
                    )
                self.rich(link, part.content, projection)
            elif isinstance(part, sm.CrossReference):
                targets = " ".join(self.ids.get(item) for item in part.target_ids)
                xref = _sub(parent, "xref", ref_type=part.ref_type, rid=targets)
                self.rich(xref, part.content, projection)
            elif isinstance(part, sm.EmailInline):
                email = _sub(parent, "email")
                self.rich(email, part.content, projection)
            elif isinstance(part, sm.CitationFieldInline):
                field = _sub(parent, part.field_kind)
                self.rich(field, part.content, projection)
            elif isinstance(part, sm.InlineGraphic):
                self.graphic(
                    parent, part.occurrence_id,
                    "graphic" if part.display else "inline-graphic",
                )
            elif isinstance(part, sm.InlineFormula):
                formula = self._formulas.get(part.formula_id)
                if formula is None:
                    raise V2RenderError(f"缺少行内公式实体: {part.formula_id}")
                parent.append(self.formula(formula))
            else:
                raise V2RenderError(f"未支持的内联类型: {type(part).__name__}")

    def simple_text(self, parent: etree._Element, rich: sm.RichText) -> None:
        """按 JATS ``%simple-text;`` 内容模型投影富文本。

        源 Word 超链接是运行格式事实：该槽位保留其可见文字与
        允许的强调格式，但不生成 DTD 禁止的链接容器。已经进入
        语义层的链接、交叉引用等结构不得静默降级；这说明上游
        把内容放错了槽位，必须在生成 XML 前明确失败。
        """
        self._validate_simple_text(rich)
        self.rich(parent, rich, "simple-text")

    def _validate_simple_text(self, rich: sm.RichText) -> None:
        for part in rich.parts:
            if isinstance(part, (sm.Text, sm.ConfigText, sm.TransformedText)):
                continue
            if isinstance(part, sm.Styled):
                self._validate_simple_text(part.content)
                continue
            if isinstance(part, sm.InlineGraphic):
                if part.display:
                    raise V2RenderError(
                        "JATS simple-text 槽位不允许独立图形"
                    )
                continue
            if isinstance(part, sm.InlineFormula):
                formula = self._formulas.get(part.formula_id)
                if formula is None:
                    raise V2RenderError(f"缺少行内公式实体: {part.formula_id}")
                if formula.display:
                    raise V2RenderError(
                        "JATS simple-text 槽位不允许独立公式"
                    )
                continue
            raise V2RenderError(
                "JATS simple-text 槽位不允许语义内联结构: "
                f"{type(part).__name__}"
            )

    def plain_rich(self, parent: etree._Element, rich: sm.RichText) -> None:
        """为 JATS 明确限定为纯文本的槽位去除所有内联容器。"""
        for part in rich.parts:
            if isinstance(part, sm.Text):
                self.source_text(parent, part.source, "plain")
            elif isinstance(part, sm.ConfigText):
                self.rich(parent, sm.RichText((part,)), "plain")
            elif isinstance(part, sm.TransformedText):
                self.rich(parent, sm.RichText((part,)), "plain")
            elif isinstance(part, (sm.Styled, sm.ExternalLink, sm.CrossReference,
                                   sm.EmailInline, sm.CitationFieldInline)):
                self.plain_rich(parent, part.content)
            elif isinstance(part, sm.Break):
                self._append(parent, " ")
            else:
                raise V2RenderError(
                    f"纯文本槽位不能容纳 {type(part).__name__}"
                )

    def _media_href(self, occurrence_id: str) -> tuple[str, BinaryResource, Any]:
        occurrence = self.source.occurrence(occurrence_id)
        if not occurrence.resource_id:
            raise V2RenderError(f"对象出现没有媒体资源: {occurrence_id}")
        resource = self.source.resource(occurrence.resource_id)
        if not isinstance(resource, BinaryResource):
            raise V2RenderError(f"显示对象不是二进制资源: {occurrence_id}")
        if resource.res_id in self._media_by_resource:
            return self._media_by_resource[resource.res_id], resource, occurrence
        requested = occurrence.properties.get("original_href")
        if requested:
            href = str(PurePosixPath(str(requested)))
        else:
            suffix = f".{resource.fmt}" if resource.fmt else ""
            href = f"{self.media_prefix}/{len(self._media_by_resource) + 1:03d}{suffix}"
        previous = self.media.get(href)
        if previous is not None and previous != resource.blob:
            raise V2RenderError(f"媒体路径指向不同字节: {href}")
        self.media[href] = resource.blob
        self._media_by_resource[resource.res_id] = href
        return href, resource, occurrence

    def graphic(self, parent: etree._Element, occurrence_id: str,
                forced_tag: Optional[str] = None) -> etree._Element:
        href, _, occurrence = self._media_href(occurrence_id)
        tag = forced_tag or (
            "inline-graphic" if occurrence.properties.get("inline") else "graphic"
        )
        graphic = _sub(parent, tag, xlink_href=href)
        if occurrence.properties.get("emit_id"):
            graphic.set("id", self.ids.take("graphic"))
        self.provenance.object(graphic, f"{{{XLINK}}}href", href, occurrence_id)
        return graphic

    # ------------------------------------------------------------------
    # 数学、段落、图表与块
    # ------------------------------------------------------------------
    def math(self, node: sm.MathNode) -> etree._Element:
        element = etree.Element(f"{{{MML}}}{node.tag}")
        for name, value in node.attributes:
            element.set(name, self.ids.take("math") if name == "id" else value)
        element.text = node.text
        if node.text:
            self.provenance.transform(
                element, "text", node.text, "mathml-tree",
                start=0, end=len(node.text),
            )
        for child in node.children:
            element.append(self.math(child))
        return element

    def formula(self, formula: sm.Formula) -> etree._Element:
        tag = "disp-formula" if formula.display else "inline-formula"
        element = _element(tag)
        if formula.emit_id:
            element.set("id", self.ids.get(formula.entity_id))
        if formula.label is not None:
            label = _sub(element, "label")
            self.rich(label, formula.label)
        if formula.presentation == "mathml":
            if formula.math is None:
                raise V2RenderError(f"MathML 公式缺树: {formula.entity_id}")
            math = self.math(formula.math)
            element.append(math)
            if formula.omml_occurrence:
                self.provenance.object_transform(
                    math, formula.omml_occurrence, "omml-to-mathml"
                )
        elif formula.presentation == "image":
            if not formula.image_occurrence:
                raise V2RenderError(f"图形公式缺对象: {formula.entity_id}")
            # JATS 子标签由公式的语义容器决定，不照搬 Word
            # DrawingML 的版式定位方式。
            expected = "graphic" if formula.display else "inline-graphic"
            self.graphic(element, formula.image_occurrence, expected)
        else:
            raise V2RenderError(f"未支持公式展示: {formula.presentation}")
        return element

    def paragraph(self, paragraph: sm.Paragraph) -> etree._Element:
        element = _element("p", id=self.ids.optional(paragraph.entity_id))
        self.rich(element, paragraph.content)
        return element

    def caption(self, parent: etree._Element,
                value: Optional[sm.Caption]) -> None:
        if value is None:
            return
        caption = _sub(parent, "caption")
        if value.title is not None:
            title = _sub(caption, "title")
            self.rich(title, value.title, "title")
        for paragraph in value.paragraphs:
            caption.append(self.paragraph(paragraph))

    def figure(self, figure: sm.Figure) -> etree._Element:
        element = _element("fig", id=self.ids.get(figure.entity_id),
                           position=figure.position)
        if figure.label is not None:
            label = _sub(element, "label")
            # 整行加粗是图号/表号的 Word 视觉样式，标签身份已由
            # JATS 元素表达；只保留可见原字，不重复携带整行样式。
            self.rich(label, figure.label, "plain")
        self.caption(element, figure.caption)
        for occurrence in figure.graphics:
            self.graphic(element, occurrence, "graphic")
        return element

    def figure_group(self, group: sm.FigureGroup) -> etree._Element:
        element = _element("fig-group", id=self.ids.get(group.entity_id))
        if group.label is not None:
            label = _sub(element, "label")
            self.rich(label, group.label, "plain")
        self.caption(element, group.caption)
        for figure in group.figures:
            element.append(self.figure(figure))
        return element

    def table_cell(self, cell: sm.TableCell) -> etree._Element:
        attributes = {
            "align": cell.style.align, "valign": cell.style.valign,
            "style": cell.style.style,
            "colspan": str(cell.colspan) if cell.colspan != 1 else None,
            "rowspan": str(cell.rowspan) if cell.rowspan != 1 else None,
            "scope": cell.header_kind,
        }
        element = _element(cell.cell_type, **attributes)
        self.rich(element, cell.content)
        return element

    def table_row(self, row: sm.TableRow) -> etree._Element:
        element = _element("tr")
        for cell in row.cells:
            element.append(self.table_cell(cell))
        return element

    def note(self, note: sm.Note) -> etree._Element:
        element = _element(
            "fn", id=self.ids.get(note.entity_id) if note.emit_id else None,
            fn_type=note.kind,
        )
        if note.label is not None:
            label = _sub(element, "label")
            self.rich(label, note.label)
        for paragraph in note.paragraphs:
            child = _sub(element, "p")
            self.rich(child, paragraph)
        return element

    def table(self, value: sm.TableBlock) -> etree._Element:
        element = _element("table-wrap", id=self.ids.get(value.entity_id),
                           position=value.position)
        if value.label is not None:
            label = _sub(element, "label")
            self.rich(label, value.label, "plain")
        self.caption(element, value.caption)
        if value.graphic_occurrence:
            self.graphic(element, value.graphic_occurrence, "graphic")
        else:
            table = _sub(element, "table")
            if value.column_widths:
                colgroup = _sub(table, "colgroup")
                for width in value.column_widths:
                    _sub(colgroup, "col", width=width)
            if value.header_rows:
                thead = _sub(table, "thead")
                for row in value.header_rows:
                    thead.append(self.table_row(row))
            if value.body_rows:
                container = table if value.body_container == "direct" else _sub(table, "tbody")
                for row in value.body_rows:
                    container.append(self.table_row(row))
        if value.notes or value.foot_paragraphs:
            foot = _sub(element, "table-wrap-foot")
            order = value.foot_order or (
                tuple(f"note:{index}" for index in range(len(value.notes)))
                + tuple(f"paragraph:{index}" for index in range(len(value.foot_paragraphs)))
            )
            for token in order:
                kind, _, raw_index = token.partition(":")
                index = int(raw_index)
                if kind == "note":
                    foot.append(self.note(value.notes[index]))
                elif kind == "paragraph":
                    paragraph = _sub(foot, "p")
                    self.rich(paragraph, value.foot_paragraphs[index])
                else:
                    raise V2RenderError(f"未支持的表脚槽位: {token}")
        return element

    def definition_list(self, value: sm.DefinitionList) -> etree._Element:
        element = _element("def-list")
        for item in value.items:
            row = _sub(element, "def-item")
            term = _sub(row, "term")
            self.rich(term, item.term)
            definition = _sub(row, "def")
            for paragraph in item.definitions:
                child = _sub(definition, "p")
                self.rich(child, paragraph)
        return element

    def section(self, value: sm.Section) -> etree._Element:
        element = _element("sec", id=self.ids.optional(value.entity_id))
        # Journal Publishing DTD 要求每个 sec 都有 title（可为空）。
        # 空结构元素不增加可见文字；语义层另行把标题未决列为问题。
        title = _sub(element, "title")
        if value.title is not None:
            self.rich(title, value.title, "title")
        for block in value.blocks:
            element.append(self.block(block))
        return element

    def glossary(self, value: sm.Glossary) -> etree._Element:
        element = _element("glossary", id=self.ids.optional(value.entity_id))
        if value.title is not None:
            title = _sub(element, "title")
            self.rich(title, value.title, "title")
        for block in value.blocks:
            element.append(self.block(block))
        return element

    def block(self, value: sm.Block) -> etree._Element:
        if isinstance(value, sm.Paragraph):
            return self.paragraph(value)
        if isinstance(value, sm.Formula):
            return self.formula(value)
        if isinstance(value, sm.Figure):
            return self.figure(value)
        if isinstance(value, sm.FigureGroup):
            return self.figure_group(value)
        if isinstance(value, sm.TableBlock):
            return self.table(value)
        if isinstance(value, sm.DefinitionList):
            return self.definition_list(value)
        if isinstance(value, sm.Glossary):
            return self.glossary(value)
        if isinstance(value, sm.Section):
            return self.section(value)
        raise V2RenderError(f"未支持的块类型: {type(value).__name__}")

    # ------------------------------------------------------------------
    # front
    # ------------------------------------------------------------------
    def journal(self, value: sm.JournalMeta) -> etree._Element:
        element = _element("journal-meta")
        for item in value.identifiers:
            child = _sub(element, "journal-id", journal_id_type=item.kind)
            self.rich(child, item.value)
        if value.title is not None or value.abbreviated_titles:
            group = _sub(element, "journal-title-group")
            if value.title is not None:
                title = _sub(group, "journal-title")
                self.rich(title, value.title)
            for item in value.abbreviated_titles:
                child = _sub(group, "abbrev-journal-title", abbrev_type=item.kind)
                self.rich(child, item.value)
        for item in value.issns:
            child = _sub(element, "issn", pub_type=item.publication_type)
            self.rich(child, item.value)
        if value.publisher_name is not None or value.publisher_location is not None:
            publisher = _sub(element, "publisher")
            if value.publisher_name is not None:
                child = _sub(publisher, "publisher-name")
                self.rich(child, value.publisher_name)
            if value.publisher_location is not None:
                child = _sub(publisher, "publisher-loc")
                self.rich(child, value.publisher_location)
        return element

    def person_name(self, value: sm.PersonName) -> etree._Element:
        element = _element("name")
        surname = _sub(element, "surname")
        self.source_text(surname, value.surname)
        given = _sub(element, "given-names")
        self.source_text(given, value.given_names)
        if value.suffix is not None:
            suffix = _sub(element, "suffix")
            self.source_text(suffix, value.suffix)
        return element

    def address(self, value: sm.Address) -> etree._Element:
        element = _element("address")
        for line in value.lines:
            child = _sub(element, "addr-line")
            # Publishing 1.3 的 addr-line 内容模型是 %simple-text;，
            # 不是通用富文本；链接元素只能作为 address 的直接子元素。
            self.simple_text(child, line)
        if value.postal_code is not None:
            child = _sub(element, "postal-code")
            self.source_text(child, value.postal_code)
        if value.phone is not None:
            child = _sub(element, "phone")
            self.source_text(child, value.phone)
        return element

    def contributor(self, value: sm.Contributor) -> etree._Element:
        element = _element(
            "contrib", contrib_type=value.kind,
            corresp="yes" if value.corresponding else None,
        )

        def emit(token: str) -> None:
            name, _, raw_index = token.partition(":")
            index = int(raw_index) if raw_index else 0
            if name == "name":
                element.append(self.person_name(value.name))
            elif name == "identifier":
                item = value.identifiers[index]
                child = _sub(
                    element, "contrib-id", contrib_id_type=item.kind,
                    authenticated="true" if item.authenticated else None,
                )
                self.rich(child, item.value, "plain")
            elif name == "degrees":
                child = _sub(element, "degrees")
                self.source_text(child, value.degrees[index])
            elif name == "role":
                child = _sub(element, "role")
                self.rich(child, value.roles[index])
            elif name == "reference":
                ref = value.references[index]
                target = " ".join(self.ids.get(item) for item in ref.target_ids)
                child = _sub(element, "xref", ref_type=ref.ref_type, rid=target)
                self.rich(child, ref.content, "preserve")
            elif name == "email":
                child = _sub(element, "email")
                self.source_text(child, value.emails[index], "plain")
            elif name == "address":
                address_id = value.address_ids[index]
                address = next((item for item in self.document.addresses
                                if item.entity_id == address_id), None)
                if address is None:
                    raise V2RenderError(f"贡献者指向未知地址: {address_id}")
                element.append(self.address(address))
            elif name == "author-comment":
                comment = _sub(element, "author-comment")
                comment.append(self.paragraph(value.author_comments[index]))
            else:
                raise V2RenderError(f"未支持的 contrib 子槽位: {token}")

        if value.child_order:
            for token in value.child_order:
                emit(token)
        else:
            emit("name")
            for index in range(len(value.degrees)):
                emit(f"degrees:{index}")
            for index in range(len(value.roles)):
                emit(f"role:{index}")
            for index in range(len(value.identifiers)):
                emit(f"identifier:{index}")
            for index in range(len(value.references)):
                emit(f"reference:{index}")
            for index in range(len(value.emails)):
                emit(f"email:{index}")
            for index in range(len(value.address_ids)):
                emit(f"address:{index}")
            for index in range(len(value.author_comments)):
                emit(f"author-comment:{index}")
        return element

    def permissions(self, value: sm.Permissions) -> etree._Element:
        element = _element("permissions")
        if value.copyright_statement is not None:
            child = _sub(element, "copyright-statement")
            self.rich(child, value.copyright_statement)
        if value.copyright_year is not None:
            child = _sub(element, "copyright-year")
            self.rich(child, value.copyright_year)
        if value.license is not None:
            license_el = _sub(
                element, "license", license_type=value.license.license_type,
                xlink_href=value.license.href,
            )
            if value.license.href:
                if value.license.href_config_key:
                    self.provenance.config_attribute(
                        license_el, f"{{{XLINK}}}href", value.license.href,
                        value.license.href_config_key,
                    )
                else:
                    ranges = tuple(
                        item for paragraph in value.license.paragraphs
                        for item in _rich_ranges(paragraph)
                    )
                    self.provenance.source_attribute(
                        license_el, f"{{{XLINK}}}href", value.license.href, ranges
                    )
            for paragraph in value.license.paragraphs:
                child = _sub(license_el, "license-p")
                self.rich(child, paragraph)
        return element

    def abstract(self, value: sm.Abstract) -> etree._Element:
        if value.element not in {"abstract", "trans-abstract"}:
            raise V2RenderError(f"未支持的摘要元素: {value.element}")
        kind = None if value.kind in {None, "main"} else value.kind
        local_language = (
            value.language
            if value.language and value.language != self.document.language
            else None
        )
        element = _element(
            value.element, abstract_type=kind, xml_lang=local_language,
        )
        if value.label is not None:
            label = _sub(element, "label")
            self.rich(label, value.label, "plain")
        if value.title is not None:
            title = _sub(element, "title")
            self.rich(title, value.title, "title")
        for section in value.sections:
            container = _sub(element, "sec") if section.wrapped else element
            if section.title is not None:
                title = _sub(container, "title")
                self.rich(title, section.title, "title")
            for paragraph in section.paragraphs:
                container.append(self.paragraph(paragraph))
        for block in value.blocks:
            element.append(self.block(block))
        return element

    def article_meta(self) -> etree._Element:
        value = self.document
        element = _element("article-meta")
        for identifier in value.article_identifiers:
            child = _sub(element, "article-id", pub_id_type=identifier.kind)
            self.rich(child, identifier.value)
        if self._head_article_meta is not None:
            for source_child in self._head_article_meta:
                child = deepcopy(source_child)
                element.append(child)
                self._register_model_text(child)
        else:
            if value.categories:
                categories = _sub(element, "article-categories")
                for item in value.categories:
                    group = _sub(categories, "subj-group", subj_group_type=item.kind)
                    subject = _sub(group, "subject")
                    self.rich(subject, item.subject)
            if value.title is not None:
                group = _sub(element, "title-group")
                title = _sub(group, "article-title")
                self.rich(title, value.title, "title")
            for group in value.contributor_groups:
                child = _sub(element, "contrib-group", content_type=group.kind)
                for contributor in group.contributors:
                    child.append(self.contributor(contributor))
            for affiliation in value.affiliations:
                child = _sub(element, "aff", id=self.ids.get(affiliation.entity_id))
                if affiliation.label is not None:
                    label = _sub(child, "label")
                    self.rich(label, affiliation.label)
                self.rich(child, affiliation.content)
            contributor_notes = tuple(
                note for note in value.notes if note.owner_scope == "contrib-group"
            )
            if value.correspondence or contributor_notes or value.author_note_paragraphs:
                author_notes = _sub(element, "author-notes")
                for correspondence in value.correspondence:
                    child = _sub(
                        author_notes, "corresp",
                        id=self.ids.get(correspondence.entity_id),
                    )
                    self.rich(child, correspondence.content)
                for note in contributor_notes:
                    author_notes.append(self.note(note))
                for paragraph in value.author_note_paragraphs:
                    child = _sub(author_notes, "p")
                    self.rich(child, paragraph)
            if value.dates:
                history = _sub(element, "history")
                for date in value.dates:
                    # 语义层用自然的 revised，JATS 枚举值是 rev-recd。
                    date_type = "rev-recd" if date.kind == "revised" else date.kind
                    child = _sub(history, "date", date_type=date_type)
                    if date.day is not None:
                        day = _sub(child, "day"); self.source_text(day, date.day)
                    if date.month is not None:
                        month = _sub(child, "month"); self.source_text(month, date.month)
                    year = _sub(child, "year"); self.source_text(year, date.year)
        if value.permissions is not None:
            element.append(self.permissions(value.permissions))
        for abstract in value.abstracts:
            element.append(self.abstract(abstract))
        for group in value.keyword_groups:
            local_language = (
                group.language
                if group.language and group.language != self.document.language
                else None
            )
            child = _sub(
                element, "kwd-group", kwd_group_type=group.kind,
                xml_lang=local_language,
            )
            if group.label is not None:
                label = _sub(child, "label")
                self.rich(label, group.label, "plain")
            if group.title is not None:
                title = _sub(child, "title")
                self.rich(title, group.title, "title")
            for keyword in group.keywords:
                item = _sub(child, "kwd")
                self.rich(item, keyword)
        return element

    # ------------------------------------------------------------------
    # back 与参考文献
    # ------------------------------------------------------------------
    def person_group(self, value: sm.ReferencePersonGroup) -> etree._Element:
        # person-group-type 是 DTD 封闭枚举；枚举外的语义角色投影到
        # JATS 标准的 custom + custom-type，杜绝非法属性值进入输出。
        if value.kind in PERSON_GROUP_TYPES:
            element = _element("person-group", person_group_type=value.kind)
        else:
            element = _element(
                "person-group", person_group_type="custom", custom_type=value.kind,
            )

        def emit(token: str) -> None:
            kind, _, raw_index = token.partition(":")
            index = int(raw_index) if raw_index else 0
            if kind == "person":
                element.append(self.person_name(value.persons[index]))
            elif kind == "collaboration":
                child = _sub(element, "collab")
                self.rich(child, value.collaborations[index])
            elif kind == "et_al":
                if value.et_al is None:
                    raise V2RenderError("person-group 次序引用空 etal")
                child = _sub(element, "etal")
                # JATS 1.3 的 etal 是纯文本，不允许 italic 等子元素。
                self.plain_rich(child, value.et_al)
            else:
                raise V2RenderError(f"未支持的 person-group 槽位: {token}")

        order = value.child_order or (
            tuple(f"person:{index}" for index in range(len(value.persons)))
            + tuple(f"collaboration:{index}" for index in range(len(value.collaborations)))
            + (("et_al",) if value.et_al is not None else ())
        )
        for token in order:
            emit(token)
        return element

    def reference_identifier(self, value: sm.ReferenceIdentifier) -> etree._Element:
        if value.carrier in {"url", "hyperlink"}:
            visible = value.value.plain_text(self.source)
            href = value.href or visible
            element = _element(
                "ext-link", ext_link_type=value.kind or "uri", xlink_href=href
            )
            self.provenance.source_attribute(
                element, f"{{{XLINK}}}href", href,
                tuple(_rich_ranges(value.value)),
            )
        else:
            element = _element("pub-id", pub_id_type=value.kind)
        # DOI/URL 的显式载体已经由外层元素表达；源 Word 中相同文字
        # 自带的超链接只能贡献可见文字，不能再生成嵌套 ext-link。
        self.plain_rich(element, value.value)
        return element

    def structured_citation(self, value: sm.StructuredCitation) -> etree._Element:
        element = _element("element-citation", publication_type=value.publication_type)
        fields_by_name = {
            # 第三项是 JATS 槽位允许的格式投影，而不是来源格式猜测。
            # 标题只去除“整行是标题”的粗体；期刊/书名保留源强调；
            # 数字书目字段在 Publishing 1.3 DTD 中是纯文本。
            "article_title": ("article-title", value.article_title, "title"),
            "chapter_title": ("chapter-title", value.chapter_title, "title"),
            "source": ("source", value.source, "preserve"),
            "year": ("year", value.year, "plain"),
            "month": ("month", value.month, "plain"),
            "day": ("day", value.day, "plain"),
            "volume": ("volume", value.volume, "plain"),
            "issue": ("issue", value.issue, "plain"),
            "fpage": ("fpage", value.fpage, "plain"),
            "lpage": ("lpage", value.lpage, "plain"),
            "elocation_id": ("elocation-id", value.elocation_id, "plain"),
            "edition": ("edition", value.edition, "subsup"),
            "publisher_name": ("publisher-name", value.publisher_name, "plain"),
            "publisher_location": ("publisher-loc", value.publisher_location, "plain"),
        }
        emitted: set[str] = set()

        def emit(token: str) -> None:
            name, _, raw_index = token.partition(":")
            index = int(raw_index) if raw_index else 0
            if name == "person_group":
                element.append(self.person_group(value.person_groups[index]))
            elif name == "identifier":
                element.append(self.reference_identifier(value.identifiers[index]))
            elif name == "comment":
                child = _sub(element, "comment")
                self.rich(child, value.comments[index])
            elif name in fields_by_name:
                tag, content, projection = fields_by_name[name]
                if content is None:
                    raise V2RenderError(f"著录次序引用空字段: {name}")
                child = _sub(element, tag)
                if projection == "plain":
                    self.plain_rich(child, content)
                else:
                    self.rich(child, content, projection)
            else:
                raise V2RenderError(f"未支持的著录字段槽位: {token}")
            emitted.add(token)

        for token in value.field_order:
            emit(token)
        for index in range(len(value.person_groups)):
            token = f"person_group:{index}"
            if token not in emitted: emit(token)
        for name, (_, content, _) in fields_by_name.items():
            if content is not None and name not in emitted: emit(name)
        for index in range(len(value.identifiers)):
            token = f"identifier:{index}"
            if token not in emitted: emit(token)
        for index in range(len(value.comments)):
            token = f"comment:{index}"
            if token not in emitted: emit(token)
        return element

    def reference(self, value: sm.Reference) -> etree._Element:
        element = _element("ref", id=self.ids.get(value.entity_id))
        if value.label is not None:
            label = _sub(element, "label")
            # 参考编号的粗体/斜体只是整条著录的版式，不属于编号语义。
            self.plain_rich(label, value.label)
        if isinstance(value.citation, sm.StructuredCitation):
            element.append(self.structured_citation(value.citation))
        elif isinstance(value.citation, sm.MixedCitation):
            citation = _sub(
                element, "mixed-citation",
                publication_type=value.citation.publication_type,
            )
            self.rich(citation, value.citation.content)
        else:
            raise V2RenderError(f"未支持的著录类型: {type(value.citation).__name__}")
        return element

    def back_section(self, value: sm.BackSection) -> etree._Element:
        tag = "ack" if value.kind == "ack" else (
            "glossary" if value.kind == "glossary" else "sec"
        )
        element = _element(tag, id=self.ids.optional(value.entity_id))
        if tag == "sec":
            title = _sub(element, "title")
            if value.title is not None:
                self.rich(title, value.title, "title")
        elif value.title is not None:
            title = _sub(element, "title")
            self.rich(title, value.title, "title")
        for block in value.blocks:
            element.append(self.block(block))
        return element

    def render(self) -> V2RenderResult:
        root = etree.Element("article", nsmap=NSMAP)
        root.set("dtd-version", self.document.dtd_version)
        root.set(f"{{{XML}}}lang", self.document.language)
        article_type = (
            self._head_article.get("article-type")
            if self._head_article is not None else self.document.article_type
        )
        if article_type:
            root.set("article-type", article_type)

        front = _sub(root, "front")
        front.append(self.journal(self.document.journal))
        front.append(self.article_meta())
        body = _sub(root, "body")
        for block in self.document.body:
            body.append(self.block(block))
        back = _sub(root, "back")
        for section in self.document.back_sections:
            back.append(self.back_section(section))
        if self.document.reference_list is not None:
            refs = _sub(back, "ref-list")
            if self.document.reference_list.title is not None:
                title = _sub(refs, "title")
                self.rich(title, self.document.reference_list.title, "title")
            for reference in self.document.reference_list.references:
                refs.append(self.reference(reference))
        body_notes = tuple(
            note for note in self.document.notes if note.owner_scope in {"body", "article"}
        )
        if body_notes:
            group = _sub(back, "fn-group")
            for note in body_notes:
                group.append(self.note(note))

        provenance = self.provenance.finalize(root)
        # etree.indent() 会把混合内容中来自源文档的纯空格 tail
        # 改成换行缩进，直接破坏字符守恒。XML 缩进不是语义，
        # 因此 v2 使用不改 DOM 的紧凑序列化。
        xml_body = etree.tostring(root, encoding="unicode", pretty_print=False)
        xml = f"{XML_DECL}\n{DOCTYPE}\n{xml_body}\n".encode("utf-8")
        return V2RenderResult(xml, dict(self.media), provenance)


def _rich_ranges(value: sm.RichText) -> Iterable[tuple[str, int, int]]:
    for part in value.parts:
        if isinstance(part, sm.Text):
            yield from part.source.ranges
        elif isinstance(part, sm.TransformedText):
            yield from part.source.ranges
        elif isinstance(part, sm.Styled):
            yield from _rich_ranges(part.content)
        elif isinstance(part, (sm.ExternalLink, sm.CrossReference, sm.EmailInline,
                               sm.CitationFieldInline)):
            yield from _rich_ranges(part.content)


def render_v2(document: sm.SemanticDoc, media_prefix: str = "media",
              head_jats_xml: Optional[str] = None) -> V2RenderResult:
    return V2Renderer(document, media_prefix, head_jats_xml).render()
