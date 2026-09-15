"""读取 Word 正文中按字面文字保存的完整 JATS 文献，不改写著录内容。"""
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re

from lxml import etree

from ..model.source import SourceText
from ..semantic import model as sm


_TAGS = {
    "ref", "label", "element-citation", "person-group", "name", "surname",
    "given-names", "suffix", "collab", "etal", "article-title", "chapter-title",
    "source", "year", "month", "day", "volume", "issue", "fpage", "lpage",
    "elocation-id", "edition", "publisher-name", "publisher-loc", "ext-link",
    "pub-id", "comment",
    "italic", "bold", "sub", "sup",
}
_SCALARS = {
    "article-title": "article_title", "chapter-title": "chapter_title", "source": "source",
    "year": "year", "month": "month", "day": "day", "volume": "volume", "issue": "issue",
    "fpage": "fpage", "lpage": "lpage", "elocation-id": "elocation_id", "edition": "edition",
    "publisher-name": "publisher_name", "publisher-loc": "publisher_location",
}


@dataclass(frozen=True)
class EmbeddedReference:
    label: sm.RichText | None
    citation: sm.StructuredCitation
    identity: sm.ReferenceIdentity
    markup: tuple


def embedded_reference(source, span):
    """只接受以该文献起点定位到的完整 ref；普通正文和残缺标记不走此路径。"""
    nodes = sorted((n for n in source.nodes if n.part == "document" and n.parent is None
                    and n.kind == "para"), key=lambda n:n.order)
    position = next((i for i,n in enumerate(nodes) if n.node_id == span.head[0]), None)
    if position is None:
        return None
    start = position
    while start >= 0 and not re.match(r"\s*<ref(?:\s|>)",nodes[start].text):
        if start < position and "</ref>" in nodes[start].text:
            return None
        start -= 1
    if start < 0:
        return None
    end = start
    while end < len(nodes) and "</ref>" not in nodes[end].text:
        if end > start and re.match(r"\s*<ref(?:\s|>)",nodes[end].text):
            return None
        end += 1
    if end >= len(nodes) or not start <= position <= end:
        return None
    records = nodes[start:end+1]
    raw = "".join(n.text for n in records)
    if "<!" in raw or "<?" in raw:
        return None
    # 只修复属性引号的字面排版形式；正文中的中文引号不改动。
    normalized = re.sub(r"<[^>]+>",lambda m:m[0].replace('“','"').replace('”','"'),raw)
    try:
        wrapper = etree.fromstring((
            '<root xmlns:xlink="http://www.w3.org/1999/xlink">'+normalized+'</root>'
        ).encode(),etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
    except (etree.XMLSyntaxError,ValueError):
        return None
    if len(wrapper)!=1 or wrapper[0].tag!='ref' or (wrapper.text or '').strip():
        return None
    root = wrapper[0]
    citation = root.find('element-citation')
    if (citation is None or not citation.get('publication-type')
            or any(e.tag not in _TAGS for e in root.iter())
            or len(root.findall('element-citation'))!=1
            or any(c.tag not in {'label','element-citation'} for c in root)
            or (root.tail or '').strip()):
        return None

    offsets=[]
    cursor=0
    for node in records:
        offsets.append((cursor,cursor+len(node.text),node.node_id))
        cursor+=len(node.text)

    def ranges(left,right):
        return tuple((nid,max(left,a)-a,min(right,b)-a) for a,b,nid in offsets
                     if max(left,a)<min(right,b))

    # XML 只用于确认结构；可见字段仍保存源 Word 的字符地址。
    elements=iter(root.iter())
    stack=[]
    text_ranges={}
    content_order={}
    markup=[]
    for token in re.finditer(r'<[^>]+>|[^<]+',raw):
        value=token[0]
        if value.startswith('<'):
            markup.extend(ranges(token.start(),token.end()))
            if value.startswith('</'):
                if not stack:
                    return None
                stack.pop()
            else:
                element=next(elements,None)
                if element is None:
                    return None
                text_ranges[element]=[]
                content_order[element]=[]
                if stack:
                    content_order[stack[-1]].append(element)
                if not value.endswith('/>'):
                    stack.append(element)
        elif stack:
            source_ranges=ranges(token.start(),token.end())
            text_ranges[stack[-1]].extend(source_ranges)
            content_order[stack[-1]].append(SourceText(source_ranges))
        elif value.strip():
            return None
    if stack:
        return None

    def source_value(element):
        if element is None:
            return None
        value=SourceText(tuple(text_ranges.get(element,())))
        return value if value.ranges and value.text(source).strip() else None

    used=set()

    def rich(element):
        if element is None or any(c.tag not in {'italic','bold','sub','sup'} for c in element):
            return None
        used.add(element)
        parts=[]
        for item in content_order[element]:
            if isinstance(item,SourceText):
                literal=item.text(source)
                decoded=unescape(literal)
                parts.append(sm.TransformedText(item,decoded,'xml-entity-decode')
                             if decoded!=literal else sm.Text(item))
            else:
                inner=rich(item)
                if inner is None:
                    return None
                parts.append(sm.Styled(item.tag,inner))
        value=sm.RichText(tuple(parts))
        return value if value.plain_text(source).strip() else None

    groups=[]
    identifiers=[]
    comments=[]
    scalars={}
    order=[]
    surnames=[]
    for child in citation:
        if child.tag=='person-group':
            persons=[]; collaborations=[]; etal=None; child_order=[]
            for member in child:
                if member.tag=='name':
                    if (any(n.tag not in {'surname','given-names','suffix'} for n in member)
                            or any(len(member.findall(tag))>1 for tag in {'surname','given-names','suffix'})):
                        return None
                    surname=source_value(member.find('surname'))
                    given=source_value(member.find('given-names'))
                    suffix=source_value(member.find('suffix'))
                    if surname is None or given is None or any('&' in v.text(source) for v in (surname,given,suffix) if v):
                        return None
                    persons.append(sm.PersonName(surname,given,suffix))
                    used.update(member)
                    child_order.append(f'person:{len(persons)-1}')
                    if child.get('person-group-type','author')=='author':
                        surnames.append(surname.text(source))
                elif member.tag=='collab':
                    value=rich(member)
                    if value is None:
                        return None
                    collaborations.append(value); child_order.append(f'collaboration:{len(collaborations)-1}')
                elif member.tag=='etal' and etal is None:
                    etal=rich(member); child_order.append('et_al')
                else:
                    return None
            groups.append(sm.ReferencePersonGroup(child.get('person-group-type','author'),
                tuple(persons),tuple(collaborations),etal,tuple(child_order)))
            order.append(f'person_group:{len(groups)-1}')
        elif child.tag in _SCALARS:
            key=_SCALARS[child.tag]
            if key in scalars or rich(child) is None:
                return None
            scalars[key]=rich(child); order.append(key)
        elif child.tag in {'ext-link','pub-id'}:
            value=rich(child)
            if value is None:
                return None
            visible=value.plain_text(source)
            kind=child.get('pub-id-type') or ('doi' if re.search(r'doi\.org/',visible,re.I) else None)
            if kind not in {'doi','pmid'}:
                return None
            identifiers.append(sm.ReferenceIdentifier(kind,value,'url' if '://' in visible else 'bare'))
            order.append(f'identifier:{len(identifiers)-1}')
        elif child.tag=='comment':
            value=rich(child)
            if value is None:
                return None
            comments.append(value); order.append(f'comment:{len(comments)-1}')
        else:
            return None
    label=rich(root.find('label'))
    # 不能把无法表达的正文当成 XML 外壳丢掉。
    if any(e not in used and source_value(e) is not None for e in root.iter()):
        return None
    structured=sm.StructuredCitation(citation.get('publication-type'),tuple(groups),
        identifiers=tuple(identifiers),comments=tuple(comments),field_order=tuple(order),**scalars)
    identity=sm.ReferenceIdentity(tuple(surnames),scalars['year'].plain_text(source) if scalars.get('year') else None,
        title_key=scalars['article_title'].plain_text(source) if scalars.get('article_title') else None)
    return EmbeddedReference(label,structured,identity,tuple(markup))
