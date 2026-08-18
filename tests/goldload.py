"""金标准反向装载器：JATS -> SemanticDoc v2 + 合成源对象图。

该模块只属于测试基建。它必须把每个结构装入明确语义类型；
遇到未支持标签或属性立即失败，不保存原始 XML 作逃生口。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile
import hashlib

from lxml import etree

from word2jats.build.figures import media_format
from word2jats.model.source import (
    OBJECT_REPLACEMENT,
    BinaryResource,
    ObjectAnchor,
    ObjectOccurrence,
    SourceDocument,
    SourceNode,
    SourcePart,
    SourceText,
)
from word2jats.semantic.model import (
    Abstract,
    AbstractSection,
    Address,
    Affiliation,
    ArticleCategory,
    ArticleIdentifier,
    BackSection,
    Break,
    Caption,
    CitationFieldInline,
    Contributor,
    ContributorGroup,
    ContributorIdentifier,
    Correspondence,
    CrossReference,
    DateValue,
    DefinitionItem,
    DefinitionList,
    EmailInline,
    ExternalLink,
    Figure,
    FigureGroup,
    Formula,
    Glossary,
    InlineFormula,
    InlineGraphic,
    Issn,
    JournalIdentifier,
    JournalMeta,
    KeywordGroup,
    License,
    MathNode,
    MixedCitation,
    Note,
    Paragraph,
    Permissions,
    PersonName,
    Reference,
    ReferenceIdentifier,
    ReferenceIdentity,
    ReferenceList,
    ReferencePersonGroup,
    RichText,
    Section,
    SemanticDoc,
    StructuredCitation,
    Styled,
    TableBlock,
    TableCell,
    TableCellStyle,
    TableRow,
    Text,
    TypedText,
)


XLINK = "http://www.w3.org/1999/xlink"
MML = "http://www.w3.org/1998/Math/MathML"


def _local(value) -> str:
    return etree.QName(value).localname


def _attr(element, name: str, default=None):
    if name == "href":
        return element.get(f"{{{XLINK}}}href", default)
    return element.get(name, default)


def _bool_attr(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in {"true", "1", "yes"}


class GoldLoadError(ValueError):
    pass


class _SyntheticSource:
    _MIME = {
        "jpeg": "image/jpeg", "png": "image/png", "tiff": "image/tiff",
        "gif": "image/gif", "bmp": "image/bmp", "wmf": "image/x-wmf",
        "emf": "image/x-emf", "svg": "image/svg+xml",
    }

    def __init__(self, xml_path: Path, figures_zip: Path | None):
        self.xml_path = xml_path
        self.nodes: list[SourceNode] = []
        self.occurrences: list[ObjectOccurrence] = []
        self.resources: list[BinaryResource] = []
        self._text_no = 0
        self._object_no = 0
        self._resource_by_key: dict[tuple[str, str], str] = {}
        self._zip_files: dict[str, bytes] = {}
        if figures_zip and figures_zip.exists():
            with ZipFile(figures_zip) as archive:
                for name in archive.namelist():
                    if not name.endswith("/"):
                        blob = archive.read(name)
                        self._zip_files[name] = blob
                        self._zip_files.setdefault(Path(name).name, blob)

    def text(self, value: str | None) -> SourceText:
        if value is None or value == "":
            return SourceText()
        self._text_no += 1
        node_id = f"gold/text{self._text_no}"
        self.nodes.append(SourceNode(
            node_id=node_id, part="gold", kind="para", parent=None,
            order=len(self.nodes), text=value,
        ))
        return SourceText(((node_id, 0, len(value)),))

    def graphic(self, element, *, inline: bool) -> str:
        href = _attr(element, "href")
        if not href:
            raise GoldLoadError(f"{self.xml_path}: graphic 缺 xlink:href")
        blob = self._zip_files.get(href) or self._zip_files.get(Path(href).name)
        if blob is None:
            raise GoldLoadError(f"{self.xml_path}: figures.zip 缺 {href}")
        digest = hashlib.sha256(blob).hexdigest()
        key = (href, digest)
        res_id = self._resource_by_key.get(key)
        if res_id is None:
            res_id = f"gold-res{len(self.resources) + 1}"
            fmt = media_format(blob) or "unknown"
            self.resources.append(BinaryResource(
                res_id=res_id, rel_target=href,
                content_type=self._MIME.get(fmt, "application/octet-stream"),
                blob=blob, fmt=fmt, part="gold",
            ))
            self._resource_by_key[key] = res_id
        self._object_no += 1
        occ_id = f"gold-o{self._object_no}"
        node_id = f"gold/object{self._object_no}"
        self.nodes.append(SourceNode(
            node_id=node_id, part="gold", kind="para", parent=None,
            order=len(self.nodes), text=OBJECT_REPLACEMENT,
            objects=[ObjectAnchor(0, occ_id)],
        ))
        self.occurrences.append(ObjectOccurrence(
            occ_id=occ_id, kind="image", node_id=node_id, char_pos=0,
            resource_id=res_id,
            properties={
                "original_href": href,
                "original_id": element.get("id"),
                "emit_id": element.get("id") is not None,
                "inline": inline,
            },
        ))
        return occ_id

    def finish(self) -> SourceDocument:
        part = SourcePart(
            "gold", "gold-standard", str(self.xml_path), "application/xml",
            tuple(node.node_id for node in self.nodes),
        )
        doc = SourceDocument(
            parts=[part], nodes=self.nodes, occurrences=self.occurrences,
            resources=self.resources,
            metadata={"fixture": "goldload-v1", "xml_name": self.xml_path.name},
        )
        doc.validate()
        return doc


class GoldLoader:
    def __init__(self, xml_path: str | Path, figures_zip: str | Path | None = None):
        self.xml_path = Path(xml_path)
        self.root = etree.parse(str(self.xml_path)).getroot()
        self.source = _SyntheticSource(
            self.xml_path, Path(figures_zip) if figures_zip else None
        )
        self._generated: dict[str, int] = {}
        self.inline_formulas: list[Formula] = []
        self.addresses: list[Address] = []
        self._note_targets: dict[str, list[str]] = {}

    def _id(self, prefix: str, element) -> str:
        existing = element.get("id")
        if existing:
            return existing
        number = self._generated.get(prefix, 0) + 1
        self._generated[prefix] = number
        return f"_sem-{prefix}-{number}"

    def _segment(self, value: str | None):
        return Text(self.source.text(value)) if value else None

    def rich(self, element) -> RichText:
        parts = []
        head = self._segment(element.text)
        if head:
            parts.append(head)
        for child in element:
            parts.append(self.inline(child))
            tail = self._segment(child.tail)
            if tail:
                parts.append(tail)
        return RichText(tuple(parts))

    def inline(self, element):
        tag = _local(element)
        if tag in {"bold", "italic", "sub", "sup"}:
            return Styled(tag, self.rich(element))
        if tag == "break":
            if element.text or len(element):
                raise GoldLoadError("break 不应有内容")
            return Break()
        if tag == "ext-link":
            return ExternalLink(
                _attr(element, "ext-link-type", "uri"),
                _attr(element, "href", ""), self.rich(element),
            )
        if tag == "xref":
            rid = tuple(filter(None, _attr(element, "rid", "").split()))
            return CrossReference(
                _attr(element, "ref-type", ""), rid, self.rich(element)
            )
        if tag == "email":
            return EmailInline(self.rich(element))
        if tag == "inline-graphic":
            return InlineGraphic(self.source.graphic(element, inline=True))
        if tag == "graphic":
            # 图文摘要可在 p 内使用 graphic；出现记录的 inline 属性
            # 保留原始 JATS 形态，渲染时不擅自换成 inline-graphic。
            return InlineGraphic(self.source.graphic(element, inline=False))
        if tag == "inline-formula":
            formula = self.formula(element, display=False)
            self.inline_formulas.append(formula)
            return InlineFormula(formula.entity_id)
        if tag == "source":
            return CitationFieldInline("source", self.rich(element))
        raise GoldLoadError(
            f"{self.xml_path}: 未支持的混合内容标签 <{tag}>"
        )

    def plain_source(self, element) -> SourceText:
        if len(element):
            raise GoldLoadError(f"<{_local(element)}> 不应含内联元素")
        return self.source.text(element.text)

    def math(self, element) -> MathNode:
        tag = _local(element)
        attrs = []
        for key, value in element.attrib.items():
            name = _local(key)
            attrs.append((name, value))
        text = element.text
        if text is not None and not text.strip() and len(element):
            text = None
        return MathNode(
            tag=tag, attributes=tuple(sorted(attrs)), text=text,
            children=tuple(self.math(child) for child in element),
        )

    def formula(self, element, *, display: bool) -> Formula:
        entity_id = self._id("formula", element)
        label_el = element.find("label")
        math_el = next((child for child in element if etree.QName(child).namespace == MML), None)
        graphic_el = element.find("graphic")
        graphic_inline = False
        if graphic_el is None:
            graphic_el = element.find("inline-graphic")
            graphic_inline = graphic_el is not None
        if (math_el is None) == (graphic_el is None):
            raise GoldLoadError(
                f"{self.xml_path}: {entity_id} 必须恰有 MathML 或 graphic"
            )
        known = {"label", "graphic", "inline-graphic"}
        unknown = {
            _local(child) for child in element
            if etree.QName(child).namespace != MML and _local(child) not in known
        }
        if unknown:
            raise GoldLoadError(f"formula 未支持: {sorted(unknown)}")
        if graphic_el is not None:
            return Formula(
                entity_id=entity_id, presentation="image",
                image_occurrence=self.source.graphic(
                    graphic_el, inline=graphic_inline
                ),
                label=self.rich(label_el) if label_el is not None else None,
                display=display, emit_id=element.get("id") is not None,
            )
        return Formula(
            entity_id=entity_id, presentation="mathml", math=self.math(math_el),
            label=self.rich(label_el) if label_el is not None else None,
            display=display, emit_id=element.get("id") is not None,
        )

    def person_name(self, element) -> PersonName:
        surname = element.find("surname")
        given = element.find("given-names")
        if surname is None or given is None:
            raise GoldLoadError("name 缺 surname/given-names")
        suffix = element.find("suffix")
        return PersonName(
            surname=self.plain_source(surname), given_names=self.plain_source(given),
            suffix=self.plain_source(suffix) if suffix is not None else None,
        )

    def address(self, element) -> Address:
        entity_id = self._id("address", element)
        lines = tuple(self.rich(child) for child in element if _local(child) == "addr-line")
        postal = element.find("postal-code")
        phone = element.find("phone")
        known = {"addr-line", "postal-code", "phone"}
        unknown = {_local(child) for child in element} - known
        if unknown:
            raise GoldLoadError(f"address 未支持子元素: {sorted(unknown)}")
        value = Address(
            entity_id, lines,
            self.plain_source(postal) if postal is not None else None,
            self.plain_source(phone) if phone is not None else None,
        )
        self.addresses.append(value)
        return value

    def contributor(self, element) -> Contributor:
        entity_id = self._id("contrib", element)
        name_el = element.find("name")
        if name_el is None:
            raise GoldLoadError("contrib 缺 name")
        identifiers = []
        degrees = []
        roles = []
        references = []
        emails = []
        address_ids = []
        author_comments = []
        child_order = []
        for child in element:
            tag = _local(child)
            if tag == "contrib-id":
                index = len(identifiers)
                identifiers.append(ContributorIdentifier(
                    _attr(child, "contrib-id-type", ""), self.plain_source(child),
                    _bool_attr(_attr(child, "authenticated")),
                ))
                child_order.append(f"identifier:{index}")
            elif tag == "name":
                child_order.append("name")
            elif tag == "degrees":
                index = len(degrees); degrees.append(self.plain_source(child))
                child_order.append(f"degrees:{index}")
            elif tag == "role":
                index = len(roles); roles.append(self.plain_source(child))
                child_order.append(f"role:{index}")
            elif tag == "xref":
                index = len(references)
                xref = self.inline(child)
                references.append(xref)
                child_order.append(f"reference:{index}")
                if xref.ref_type == "fn":
                    for target in xref.target_ids:
                        self._note_targets.setdefault(target, []).append(entity_id)
            elif tag == "email":
                index = len(emails); emails.append(self.plain_source(child))
                child_order.append(f"email:{index}")
            elif tag == "address":
                index = len(address_ids)
                address_ids.append(self.address(child).entity_id)
                child_order.append(f"address:{index}")
            elif tag == "author-comment":
                unknown = {_local(item) for item in child} - {"p"}
                if unknown:
                    raise GoldLoadError(f"author-comment 未支持: {sorted(unknown)}")
                index = len(author_comments)
                # 现有金标准每个 author-comment 仅一段；契约以段落序列承载。
                author_comments.extend(self.paragraph(item) for item in child.findall("p"))
                child_order.append(f"author-comment:{index}")
            else:
                raise GoldLoadError(f"contrib 未支持子元素 <{tag}>")
        affiliation_ids = tuple(
            target for ref in references if ref.ref_type == "aff" for target in ref.target_ids
        )
        return Contributor(
            entity_id=entity_id,
            kind=_attr(element, "contrib-type", "author"),
            name=self.person_name(name_el), degrees=tuple(degrees), roles=tuple(roles),
            identifiers=tuple(identifiers), affiliation_ids=affiliation_ids,
            address_ids=tuple(address_ids), references=tuple(references),
            emails=tuple(emails), author_comments=tuple(author_comments),
            corresponding=_bool_attr(_attr(element, "corresp")) is True,
            child_order=tuple(child_order),
        )

    def contributor_group(self, element) -> ContributorGroup:
        unknown = {_local(child) for child in element} - {"contrib"}
        if unknown:
            raise GoldLoadError(f"contrib-group 未支持: {sorted(unknown)}")
        return ContributorGroup(
            _attr(element, "content-type"),
            tuple(self.contributor(child) for child in element),
        )

    def affiliation(self, element) -> Affiliation:
        return Affiliation(
            entity_id=self._id("aff", element), label=None,
            content=self.rich(element), address_ids=(),
        )

    def note(self, element, *, owner: str, target_ids=()) -> Note:
        paragraphs = tuple(self.rich(child) for child in element if _local(child) == "p")
        unknown = {_local(child) for child in element} - {"p", "label"}
        if unknown:
            raise GoldLoadError(f"fn 未支持: {sorted(unknown)}")
        label = element.find("label")
        raw_id = element.get("id")
        entity_id = raw_id or self._id("note", element)
        mapped = tuple(self._note_targets.get(raw_id or "", ())) or tuple(target_ids)
        return Note(
            entity_id=entity_id, kind=_attr(element, "fn-type"),
            label=self.rich(label) if label is not None else None,
            paragraphs=paragraphs, owner_scope=owner, target_ids=mapped,
            emit_id=raw_id is not None,
        )

    def paragraph(self, element) -> Paragraph:
        return Paragraph(element.get("id"), self.rich(element))

    def caption(self, element) -> Caption:
        if element.attrib:
            raise GoldLoadError("caption 属性未纳入契约")
        unknown = {_local(child) for child in element} - {"title", "p"}
        if unknown:
            raise GoldLoadError(f"caption 未支持: {sorted(unknown)}")
        titles = element.findall("title")
        if len(titles) > 1:
            raise GoldLoadError("caption 不应有多个 title")
        return Caption(
            self.rich(titles[0]) if titles else None,
            tuple(self.paragraph(p) for p in element.findall("p")),
        )

    def figure(self, element) -> Figure:
        label = element.find("label")
        caption = element.find("caption")
        graphics = tuple(
            self.source.graphic(child, inline=False)
            for child in element if _local(child) == "graphic"
        )
        known = {"label", "caption", "graphic"}
        unknown = {_local(child) for child in element} - known
        if unknown:
            raise GoldLoadError(f"fig 未支持: {sorted(unknown)}")
        return Figure(
            entity_id=self._id("fig", element),
            label=self.rich(label) if label is not None else None,
            caption=self.caption(caption) if caption is not None else None,
            graphics=graphics, position=_attr(element, "position"),
        )

    def figure_group(self, element) -> FigureGroup:
        label = element.find("label")
        caption = element.find("caption")
        known = {"label", "caption", "fig"}
        unknown = {_local(child) for child in element} - known
        if unknown:
            raise GoldLoadError(f"fig-group 未支持: {sorted(unknown)}")
        return FigureGroup(
            self._id("fig-group", element),
            self.rich(label) if label is not None else None,
            self.caption(caption) if caption is not None else None,
            tuple(self.figure(fig) for fig in element.findall("fig")),
        )

    def table_cell(self, element) -> TableCell:
        known = {"align", "valign", "style", "colspan", "rowspan", "scope"}
        unknown = set(element.attrib) - known
        if unknown:
            raise GoldLoadError(f"{_local(element)} 未支持属性: {sorted(unknown)}")
        return TableCell(
            content=self.rich(element), cell_type=_local(element),
            colspan=int(_attr(element, "colspan", "1")),
            rowspan=int(_attr(element, "rowspan", "1")),
            header_kind=_attr(element, "scope"),
            style=TableCellStyle(
                align=_attr(element, "align"), valign=_attr(element, "valign"),
                style=_attr(element, "style"),
            ),
        )

    def table_row(self, element) -> TableRow:
        if element.attrib:
            raise GoldLoadError("tr 属性未纳入契约")
        return TableRow(tuple(self.table_cell(cell) for cell in element))

    def table(self, element) -> TableBlock:
        entity_id = self._id("table", element)
        label = element.find("label")
        caption = element.find("caption")
        table = element.find("table")
        graphic = element.find("graphic")
        if (table is None) == (graphic is None):
            raise GoldLoadError(f"{entity_id}: table/graphic 必须二选一")
        widths = []
        header_rows = []
        body_rows = []
        direct = []
        if table is not None:
            colgroup = table.find("colgroup")
            if colgroup is not None:
                for col in colgroup.findall("col"):
                    unknown = set(col.attrib) - {"width"}
                    if unknown:
                        raise GoldLoadError(f"col 未支持属性: {sorted(unknown)}")
                    widths.append(_attr(col, "width"))
            thead = table.find("thead")
            if thead is not None:
                header_rows.extend(self.table_row(row) for row in thead.findall("tr"))
            for tbody in table.findall("tbody"):
                body_rows.extend(self.table_row(row) for row in tbody.findall("tr"))
            direct = table.findall("tr")
            if direct:
                body_rows.extend(self.table_row(row) for row in direct)
            unknown = {_local(child) for child in table} - {"colgroup", "thead", "tbody", "tr"}
            if unknown:
                raise GoldLoadError(f"table 未支持子元素: {sorted(unknown)}")
        foot = element.find("table-wrap-foot")
        notes = []
        foot_paragraphs = []
        foot_order = []
        if foot is not None:
            if foot.attrib:
                raise GoldLoadError("table-wrap-foot 属性未纳入契约")
            for child in foot:
                tag = _local(child)
                if tag == "fn":
                    foot_order.append(f"note:{len(notes)}")
                    notes.append(self.note(child, owner="table", target_ids=(entity_id,)))
                elif tag == "p":
                    foot_order.append(f"paragraph:{len(foot_paragraphs)}")
                    foot_paragraphs.append(self.rich(child))
                else:
                    raise GoldLoadError(f"table-wrap-foot 未支持 <{tag}>")
        known = {"label", "caption", "table", "graphic", "table-wrap-foot"}
        unknown = {_local(child) for child in element} - known
        if unknown:
            raise GoldLoadError(f"table-wrap 未支持: {sorted(unknown)}")
        return TableBlock(
            entity_id=entity_id,
            label=self.rich(label) if label is not None else None,
            caption=self.caption(caption) if caption is not None else None,
            column_widths=tuple(widths), header_rows=tuple(header_rows),
            body_rows=tuple(body_rows), notes=tuple(notes),
            foot_paragraphs=tuple(foot_paragraphs), foot_order=tuple(foot_order),
            position=_attr(element, "position"),
            graphic_occurrence=self.source.graphic(graphic, inline=False) if graphic is not None else None,
            body_container="direct" if direct else "tbody",
        )

    def definition_list(self, element) -> DefinitionList:
        items = []
        for item in element.findall("def-item"):
            term = item.find("term")
            definition = item.find("def")
            if term is None or definition is None:
                raise GoldLoadError("def-item 缺 term/def")
            unknown = {_local(child) for child in definition} - {"p"}
            if unknown:
                raise GoldLoadError(f"def 未支持: {sorted(unknown)}")
            items.append(DefinitionItem(
                self.rich(term), tuple(self.rich(p) for p in definition.findall("p"))
            ))
        unknown = {_local(child) for child in element} - {"def-item"}
        if unknown:
            raise GoldLoadError(f"def-list 未支持: {sorted(unknown)}")
        return DefinitionList(tuple(items))

    def block(self, element):
        tag = _local(element)
        if tag == "p":
            return self.paragraph(element)
        if tag == "sec":
            return self.section(element)
        if tag == "fig":
            return self.figure(element)
        if tag == "fig-group":
            return self.figure_group(element)
        if tag == "table-wrap":
            return self.table(element)
        if tag == "disp-formula":
            return self.formula(element, display=True)
        if tag == "def-list":
            return self.definition_list(element)
        if tag == "glossary":
            return self.glossary(element)
        raise GoldLoadError(f"未支持的块标签 <{tag}>")

    def section(self, element) -> Section:
        title = element.find("title")
        blocks = tuple(self.block(child) for child in element if _local(child) != "title")
        return Section(
            element.get("id"), self.rich(title) if title is not None else None, blocks
        )

    def glossary(self, element) -> Glossary:
        title = element.find("title")
        return Glossary(
            element.get("id"), self.rich(title) if title is not None else None,
            tuple(self.block(child) for child in element if _local(child) != "title"),
        )

    def person_group(self, element) -> ReferencePersonGroup:
        persons = []
        collaborations = []
        etal = None
        child_order = []
        for child in element:
            tag = _local(child)
            if tag == "name":
                child_order.append(f"person:{len(persons)}")
                persons.append(self.person_name(child))
            elif tag == "collab":
                child_order.append(f"collaboration:{len(collaborations)}")
                collaborations.append(self.rich(child))
            elif tag == "etal":
                if etal is not None:
                    raise GoldLoadError("person-group 重复 etal")
                child_order.append("et_al")
                etal = self.rich(child)
            else:
                raise GoldLoadError(f"person-group 未支持 <{tag}>")
        return ReferencePersonGroup(
            _attr(element, "person-group-type", "author"), tuple(persons),
            tuple(collaborations), etal, tuple(child_order),
        )

    def structured_citation(self, element) -> StructuredCitation:
        person_groups = []
        identifiers = []
        comments = []
        values = {}
        order = []
        field_map = {
            "article-title": "article_title", "chapter-title": "chapter_title",
            "source": "source", "year": "year", "month": "month", "day": "day",
            "volume": "volume", "issue": "issue", "fpage": "fpage",
            "lpage": "lpage", "elocation-id": "elocation_id", "edition": "edition",
            "publisher-name": "publisher_name", "publisher-loc": "publisher_location",
        }
        for child in element:
            tag = _local(child)
            if tag == "person-group":
                index = len(person_groups); person_groups.append(self.person_group(child))
                order.append(f"person_group:{index}")
            elif tag in field_map:
                name = field_map[tag]
                if name in values:
                    raise GoldLoadError(f"element-citation 重复字段 {tag}")
                values[name] = self.rich(child)
                order.append(name)
            elif tag == "pub-id":
                index = len(identifiers)
                identifiers.append(ReferenceIdentifier(
                    _attr(child, "pub-id-type", ""), self.rich(child), "bare", None
                ))
                order.append(f"identifier:{index}")
            elif tag == "ext-link":
                index = len(identifiers)
                identifiers.append(ReferenceIdentifier(
                    _attr(child, "ext-link-type", "uri"), self.rich(child),
                    "hyperlink", _attr(child, "href"),
                ))
                order.append(f"identifier:{index}")
            elif tag == "comment":
                index = len(comments); comments.append(self.rich(child))
                order.append(f"comment:{index}")
            else:
                raise GoldLoadError(f"element-citation 未支持 <{tag}>")
        return StructuredCitation(
            publication_type=_attr(element, "publication-type", "other"),
            person_groups=tuple(person_groups), identifiers=tuple(identifiers),
            comments=tuple(comments), field_order=tuple(order), **values,
        )

    def reference(self, element) -> Reference:
        label = element.find("label")
        citations = [child for child in element
                     if _local(child) in {"element-citation", "mixed-citation"}]
        if len(citations) != 1:
            raise GoldLoadError("ref 必须恰有一个 citation")
        citation_el = citations[0]
        if _local(citation_el) == "element-citation":
            citation = self.structured_citation(citation_el)
        else:
            citation = MixedCitation(
                _attr(citation_el, "publication-type"), self.rich(citation_el)
            )
        unknown = {_local(child) for child in element} - {
            "label", "element-citation", "mixed-citation"
        }
        if unknown:
            raise GoldLoadError(f"ref 未支持: {sorted(unknown)}")
        return Reference(
            self._id("ref", element),
            self.rich(label) if label is not None else None,
            citation, ReferenceIdentity(),
        )

    def journal_meta(self, element) -> JournalMeta:
        identifiers = []
        title = None
        abbreviated = []
        issns = []
        publisher_name = publisher_location = None
        for child in element:
            tag = _local(child)
            if tag == "journal-id":
                identifiers.append(JournalIdentifier(
                    _attr(child, "journal-id-type", ""), self.rich(child)
                ))
            elif tag == "journal-title-group":
                jt = child.find("journal-title")
                title = self.rich(jt) if jt is not None else None
                abbreviated.extend(TypedText(
                    _attr(abbr, "abbrev-type", ""), self.rich(abbr)
                ) for abbr in child.findall("abbrev-journal-title"))
                unknown = {_local(item) for item in child} - {
                    "journal-title", "abbrev-journal-title"
                }
                if unknown:
                    raise GoldLoadError(f"journal-title-group 未支持: {sorted(unknown)}")
            elif tag == "issn":
                issns.append(Issn(_attr(child, "pub-type", ""), self.rich(child)))
            elif tag == "publisher":
                name = child.find("publisher-name")
                location = child.find("publisher-loc")
                publisher_name = self.rich(name) if name is not None else None
                publisher_location = self.rich(location) if location is not None else None
                unknown = {_local(item) for item in child} - {"publisher-name", "publisher-loc"}
                if unknown:
                    raise GoldLoadError(f"publisher 未支持: {sorted(unknown)}")
            else:
                raise GoldLoadError(f"journal-meta 未支持 <{tag}>")
        return JournalMeta(
            tuple(identifiers), title, tuple(abbreviated), tuple(issns),
            publisher_name, publisher_location,
        )

    def permissions(self, element) -> Permissions:
        statement = element.find("copyright-statement")
        year = element.find("copyright-year")
        license_el = element.find("license")
        if statement is None or year is None or license_el is None:
            raise GoldLoadError("permissions 缺必需子元素")
        unknown = {_local(child) for child in element} - {
            "copyright-statement", "copyright-year", "license"
        }
        if unknown:
            raise GoldLoadError(f"permissions 未支持: {sorted(unknown)}")
        if {_local(child) for child in license_el} - {"license-p"}:
            raise GoldLoadError("license 只支持 license-p")
        license = License(
            _attr(license_el, "license-type"), _attr(license_el, "href"),
            tuple(self.rich(p) for p in license_el.findall("license-p")),
        )
        return Permissions(self.rich(statement), self.rich(year), license)

    def abstract(self, element) -> Abstract:
        kind = _attr(element, "abstract-type", "main")
        if kind == "abstract":
            kind = "main"
        sections = []
        blocks = []
        for child in element:
            tag = _local(child)
            if tag == "sec":
                title = child.find("title")
                unknown = {_local(item) for item in child} - {"title", "p"}
                if unknown:
                    raise GoldLoadError(f"abstract/sec 未支持: {sorted(unknown)}")
                sections.append(AbstractSection(
                    self.rich(title) if title is not None else None,
                    tuple(self.paragraph(p) for p in child.findall("p")),
                    wrapped=True,
                ))
            elif tag == "p":
                blocks.append(self.paragraph(child))
            elif tag == "title":
                # 图文摘要的直接 title 与 p 组成一个摘要小节。
                continue
            else:
                raise GoldLoadError(f"abstract 未支持 <{tag}>")
        direct_title = element.find("title")
        if direct_title is not None:
            paragraphs = tuple(block for block in blocks if isinstance(block, Paragraph))
            sections.append(AbstractSection(
                self.rich(direct_title), paragraphs, wrapped=False
            ))
            blocks = []
        return Abstract(kind, tuple(sections), tuple(blocks))

    def load(self) -> SemanticDoc:
        if _local(self.root) != "article":
            raise GoldLoadError("根元素不是 article")
        allowed_root_attrs = {"article-type", "dtd-version", "lang"}
        root_attrs = {_local(key) for key in self.root.attrib}
        if root_attrs - allowed_root_attrs:
            raise GoldLoadError(f"article 未支持属性: {sorted(root_attrs - allowed_root_attrs)}")
        front = self.root.find("front")
        body = self.root.find("body")
        back = self.root.find("back")
        if front is None or body is None or back is None:
            raise GoldLoadError("article 缺 front/body/back")
        journal_el = front.find("journal-meta")
        article_meta = front.find("article-meta")
        if journal_el is None or article_meta is None:
            raise GoldLoadError("front 缺 journal-meta/article-meta")

        identifiers = []
        categories = []
        title = None
        contributor_groups = []
        affiliations = []
        correspondence = []
        notes = []
        author_note_paragraphs = []
        dates = []
        abstracts = []
        keywords = []
        permissions = None
        for child in article_meta:
            tag = _local(child)
            if tag == "article-id":
                identifiers.append(ArticleIdentifier(
                    _attr(child, "pub-id-type", ""), self.rich(child)
                ))
            elif tag == "article-categories":
                for group in child.findall("subj-group"):
                    subject = group.find("subject")
                    if subject is None:
                        raise GoldLoadError("subj-group 缺 subject")
                    categories.append(ArticleCategory(
                        _attr(group, "subj-group-type", ""), self.rich(subject)
                    ))
            elif tag == "title-group":
                article_title = child.find("article-title")
                if article_title is None:
                    raise GoldLoadError("title-group 缺 article-title")
                title = self.rich(article_title)
            elif tag == "contrib-group":
                contributor_groups.append(self.contributor_group(child))
            elif tag == "aff":
                affiliations.append(self.affiliation(child))
            elif tag == "author-notes":
                for item in child:
                    item_tag = _local(item)
                    if item_tag == "corresp":
                        correspondence.append(Correspondence(
                            self._id("corresp", item), self.rich(item)
                        ))
                    elif item_tag == "fn":
                        notes.append(self.note(item, owner="contrib-group"))
                    elif item_tag == "p":
                        author_note_paragraphs.append(self.rich(item))
                    else:
                        raise GoldLoadError(f"author-notes 未支持 <{item_tag}>")
            elif tag == "history":
                for date in child.findall("date"):
                    year = date.find("year")
                    if year is None:
                        raise GoldLoadError("date 缺 year")
                    month = date.find("month"); day = date.find("day")
                    dates.append(DateValue(
                        _attr(date, "date-type", ""), self.plain_source(year),
                        self.plain_source(month) if month is not None else None,
                        self.plain_source(day) if day is not None else None,
                    ))
            elif tag == "permissions":
                permissions = self.permissions(child)
            elif tag == "abstract":
                abstracts.append(self.abstract(child))
            elif tag == "kwd-group":
                group_title = child.find("title")
                keywords.append(KeywordGroup(
                    _attr(child, "kwd-group-type"),
                    self.rich(group_title) if group_title is not None else None,
                    tuple(self.rich(item) for item in child.findall("kwd")),
                ))
            else:
                raise GoldLoadError(f"article-meta 未支持 <{tag}>")

        body_blocks = tuple(self.block(child) for child in body)
        back_sections = []
        reference_list = None
        back_notes = []
        for child in back:
            tag = _local(child)
            if tag in {"sec", "ack"}:
                title_el = child.find("title")
                back_sections.append(BackSection(
                    "ack" if tag == "ack" else "section", child.get("id"),
                    self.rich(title_el) if title_el is not None else None,
                    tuple(self.block(item) for item in child if _local(item) != "title"),
                ))
            elif tag == "glossary":
                glossary = self.glossary(child)
                back_sections.append(BackSection(
                    "glossary", glossary.entity_id, glossary.title, glossary.blocks
                ))
            elif tag == "ref-list":
                title_el = child.find("title")
                reference_list = ReferenceList(
                    self.rich(title_el) if title_el is not None else None,
                    tuple(self.reference(ref) for ref in child.findall("ref")),
                )
                unknown = {_local(item) for item in child} - {"title", "ref"}
                if unknown:
                    raise GoldLoadError(f"ref-list 未支持: {sorted(unknown)}")
            elif tag == "fn-group":
                back_notes.extend(self.note(fn, owner="body") for fn in child.findall("fn"))
            else:
                raise GoldLoadError(f"back 未支持 <{tag}>")
        notes.extend(back_notes)

        source_doc = self.source.finish()
        semantic = SemanticDoc(
            source=source_doc,
            article_type=_attr(self.root, "article-type", "research-article"),
            language=_attr(self.root, "lang", "en"),
            dtd_version=_attr(self.root, "dtd-version", "1.3"),
            journal=self.journal_meta(journal_el),
            article_identifiers=tuple(identifiers), categories=tuple(categories),
            title=title, contributor_groups=tuple(contributor_groups),
            affiliations=tuple(affiliations), addresses=tuple(self.addresses),
            correspondence=tuple(correspondence),
            author_note_paragraphs=tuple(author_note_paragraphs),
            notes=tuple(notes), dates=tuple(dates), abstracts=tuple(abstracts),
            keyword_groups=tuple(keywords), permissions=permissions,
            inline_formulas=tuple(self.inline_formulas), body=body_blocks,
            back_sections=tuple(back_sections), reference_list=reference_list,
        )
        semantic.validate()
        return semantic


def load_gold(xml_path: str | Path, figures_zip: str | Path | None = None) -> SemanticDoc:
    return GoldLoader(xml_path, figures_zip).load()
