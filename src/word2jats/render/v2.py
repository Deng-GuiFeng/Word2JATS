"""SemanticDoc v2 的确定性 JATS 渲染器。

本模块只消费类型化语义对象和源地址：可见文字从 SourceDocument
机械取回，媒体从 BinaryResource 机械取回。遇到不支持的语义
类型立即失败，不保留原始 XML 作为逃生口。
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Optional

from lxml import etree

from ..build.ids import DocIdAllocator
from ..build.jats import DOCTYPE, MML, NSMAP, XLINK, XML, XML_DECL
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

    def __init__(self, document: sm.SemanticDoc):
        self._allocator = DocIdAllocator()
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
    def __init__(self, document: sm.SemanticDoc):
        document.validate()
        self.document = document
        self.source = document.source
        self.ids = _EntityIds(document)
        self.provenance = ProvenanceBuilder()
        self.media: dict[str, bytes] = {}
        self._media_by_resource: dict[str, str] = {}
        self._formulas = {
            formula.entity_id: formula for formula in document.inline_formulas
        }

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
        if projection not in {"preserve", "title", "plain"}:
            raise V2RenderError(f"未知格式投影槽位: {projection}")
        run = resolved.run
        tags = []
        if run is not None and projection != "plain":
            if run.superscript:
                tags.append("sup")
            elif run.subscript:
                tags.append("sub")
            if run.bold and projection != "title":
                tags.append("bold")
            if run.italic:
                tags.append("italic")
        if resolved.hyperlink:
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
            elif isinstance(part, sm.Styled):
                wrapper = _sub(parent, part.style)
                self.rich(wrapper, part.content, projection)
            elif isinstance(part, sm.Break):
                _sub(parent, "break")
            elif isinstance(part, sm.ExternalLink):
                link = _sub(parent, "ext-link", ext_link_type=part.link_type,
                            xlink_href=part.href)
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
                self.graphic(parent, part.occurrence_id)
            elif isinstance(part, sm.InlineFormula):
                formula = self._formulas.get(part.formula_id)
                if formula is None:
                    raise V2RenderError(f"缺少行内公式实体: {part.formula_id}")
                parent.append(self.formula(formula))
            else:
                raise V2RenderError(f"未支持的内联类型: {type(part).__name__}")

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
            href = f"media/{len(self._media_by_resource) + 1:03d}{suffix}"
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
            element.append(self.math(formula.math))
        elif formula.presentation == "image":
            if not formula.image_occurrence:
                raise V2RenderError(f"图形公式缺对象: {formula.entity_id}")
            occurrence = self.source.occurrence(formula.image_occurrence)
            expected = "inline-graphic" if occurrence.properties.get("inline") else "graphic"
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
            self.rich(label, figure.label)
        self.caption(element, figure.caption)
        for occurrence in figure.graphics:
            self.graphic(element, occurrence, "graphic")
        return element

    def figure_group(self, group: sm.FigureGroup) -> etree._Element:
        element = _element("fig-group", id=self.ids.get(group.entity_id))
        if group.label is not None:
            label = _sub(element, "label")
            self.rich(label, group.label)
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
            self.rich(label, value.label)
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
        if value.title is not None:
            title = _sub(element, "title")
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
            self.rich(child, line)
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
                self.source_text(child, item.value)
            elif name == "degrees":
                child = _sub(element, "degrees")
                self.source_text(child, value.degrees[index])
            elif name == "role":
                child = _sub(element, "role")
                self.source_text(child, value.roles[index])
            elif name == "reference":
                ref = value.references[index]
                target = " ".join(self.ids.get(item) for item in ref.target_ids)
                child = _sub(element, "xref", ref_type=ref.ref_type, rid=target)
                self.rich(child, ref.content)
            elif name == "email":
                child = _sub(element, "email")
                self.source_text(child, value.emails[index])
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
        kind = None if value.kind == "main" else value.kind
        element = _element("abstract", abstract_type=kind)
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
        author_notes = _sub(element, "author-notes")
        for correspondence in value.correspondence:
            child = _sub(
                author_notes, "corresp", id=self.ids.get(correspondence.entity_id)
            )
            self.rich(child, correspondence.content)
        for note in value.notes:
            if note.owner_scope == "contrib-group":
                author_notes.append(self.note(note))
        for paragraph in value.author_note_paragraphs:
            child = _sub(author_notes, "p")
            self.rich(child, paragraph)
        if value.dates:
            history = _sub(element, "history")
            for date in value.dates:
                child = _sub(history, "date", date_type=date.kind)
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
            child = _sub(element, "kwd-group", kwd_group_type=group.kind)
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
        element = _element("person-group", person_group_type=value.kind)

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
                self.rich(child, value.et_al)
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
        self.rich(element, value.value)
        return element

    def structured_citation(self, value: sm.StructuredCitation) -> etree._Element:
        element = _element("element-citation", publication_type=value.publication_type)
        fields_by_name = {
            "article_title": ("article-title", value.article_title),
            "chapter_title": ("chapter-title", value.chapter_title),
            "source": ("source", value.source), "year": ("year", value.year),
            "month": ("month", value.month), "day": ("day", value.day),
            "volume": ("volume", value.volume), "issue": ("issue", value.issue),
            "fpage": ("fpage", value.fpage), "lpage": ("lpage", value.lpage),
            "elocation_id": ("elocation-id", value.elocation_id),
            "edition": ("edition", value.edition),
            "publisher_name": ("publisher-name", value.publisher_name),
            "publisher_location": ("publisher-loc", value.publisher_location),
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
                tag, content = fields_by_name[name]
                if content is None:
                    raise V2RenderError(f"著录次序引用空字段: {name}")
                child = _sub(element, tag)
                self.rich(child, content)
            else:
                raise V2RenderError(f"未支持的著录字段槽位: {token}")
            emitted.add(token)

        for token in value.field_order:
            emit(token)
        for index in range(len(value.person_groups)):
            token = f"person_group:{index}"
            if token not in emitted: emit(token)
        for name, (_, content) in fields_by_name.items():
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
            self.rich(label, value.label)
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
        if value.title is not None:
            title = _sub(element, "title")
            self.rich(title, value.title, "title")
        for block in value.blocks:
            element.append(self.block(block))
        return element

    def render(self) -> V2RenderResult:
        root = etree.Element("article", nsmap=NSMAP)
        root.set("dtd-version", self.document.dtd_version)
        root.set(f"{{{XML}}}lang", self.document.language)
        root.set("article-type", self.document.article_type)

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
        elif isinstance(part, sm.Styled):
            yield from _rich_ranges(part.content)
        elif isinstance(part, (sm.ExternalLink, sm.CrossReference, sm.EmailInline,
                               sm.CitationFieldInline)):
            yield from _rich_ranges(part.content)


def render_v2(document: sm.SemanticDoc) -> V2RenderResult:
    return V2Renderer(document).render()
