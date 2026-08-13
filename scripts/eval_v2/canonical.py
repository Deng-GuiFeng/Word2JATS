"""把 JATS 树投影成无损、可比较的语义树。

这里只吸收四种已经明确定义为等价的表示差异：内部 ID 的拼写、媒体文件名、
XML 源码缩进，以及 day/month 数值前面的零。除此之外的内容全部保留并比较。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import hashlib
import re
from typing import Callable

from lxml import etree

from .artifacts import local_name, local_path
from .models import CoverageReport
from .policy import (
    KNOWN_ELEMENT_NAMES,
    LOCAL_MEDIA_ELEMENTS,
    MATHML_NS,
    NUMERIC_DATE_ELEMENTS,
    XLINK_NS,
    domain_for,
)


MediaResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class CanonicalNode:
    tag: str
    local_tag: str
    path: str
    domain: str
    attrs: tuple[tuple[str, str], ...]
    text: str
    tail: str
    children: tuple["CanonicalNode", ...]
    match_key: str

    def digest(self) -> str:
        raw = repr((
            self.tag,
            self.attrs,
            self.text,
            self.tail,
            tuple(child.digest() for child in self.children),
        )).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass
class CanonicalDocument:
    root: CanonicalNode
    coverage: CoverageReport
    tag_counts: Counter[str] = field(default_factory=Counter)
    attr_counts: Counter[str] = field(default_factory=Counter)
    fallback_tags: Counter[str] = field(default_factory=Counter)


def _qname(name: str) -> str:
    if not name.startswith("{"):
        return name
    uri, local = name[1:].split("}", 1)
    known = {
        XLINK_NS: "xlink",
        "http://www.w3.org/XML/1998/namespace": "xml",
        "http://www.w3.org/1998/Math/MathML": "mml",
    }
    return f"{known[uri]}:{local}" if uri in known else f"{{{uri}}}{local}"


def _anchor_text(element: etree._Element) -> str:
    pieces: list[str] = []

    def append(value: str | None) -> None:
        if value is None:
            return
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip() and "\n" in normalized:
            return
        pieces.append(normalized)

    def walk(current: etree._Element) -> None:
        append(current.text)
        for child in current:
            if isinstance(child.tag, str):
                walk(child)
            append(child.tail)

    walk(element)
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _child_text(element: etree._Element, name: str) -> str:
    values = [
        _anchor_text(child)
        for child in element.iter()
        if isinstance(child.tag, str) and local_name(child.tag) == name
    ]
    return "|".join(value for value in values if value)


def _base_anchor(element: etree._Element) -> str:
    """构造与 XML ``id`` 无关、可处理重复项的对象身份。"""

    tag = local_name(element.tag)
    text = _anchor_text(element)
    if tag == "ref":
        label = _child_text(element, "label")
        doi = "|".join(
            _anchor_text(child)
            for child in element.iter()
            if isinstance(child.tag, str)
            and local_name(child.tag) in {"pub-id", "ext-link"}
            and (
                child.get("pub-id-type") == "doi"
                or "doi.org/" in (child.get(f"{{{XLINK_NS}}}href") or "")
            )
        )
        citation = "|".join(filter(None, (
            _child_text(element, "surname"),
            _child_text(element, "year"),
            _child_text(element, "article-title"),
            _child_text(element, "source"),
        )))
        return f"ref:{label or doi or citation or _short_hash(text)}"
    if tag == "contrib":
        name = "|".join(filter(None, (
            _child_text(element, "surname"), _child_text(element, "given-names")
        )))
        return f"contrib:{element.get('contrib-type', '')}:{name or _short_hash(text)}"
    if tag in {"fig", "table-wrap"}:
        identity = _child_text(element, "label") or _child_text(element, "caption")
        return f"{tag}:{identity or _short_hash(text)}"
    if tag in {"sec", "abstract", "trans-abstract", "app"}:
        identity = _child_text(element, "title")
        return f"{tag}:{identity or _short_hash(text)}"
    if tag in {"aff", "corresp", "fn", "kwd"}:
        return f"{tag}:{_short_hash(text)}"
    if tag == "xref":
        return f"xref:{element.get('ref-type', '')}:{text}"
    if tag in {
        "p", "title", "article-title", "label", "tr", "td", "th", "list-item",
        "name", "person-group", "element-citation", "mixed-citation",
    }:
        return f"{tag}:{_short_hash(text)}"
    identity = _child_text(element, "label") or _child_text(element, "title")
    return f"{tag}:{identity}" if identity else tag


def _id_anchors(root: etree._Element) -> dict[str, str]:
    occurrences: Counter[str] = Counter()
    result: dict[str, str] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        xml_id = element.get("id")
        if not xml_id:
            continue
        base = _base_anchor(element)
        occurrences[base] += 1
        result[xml_id] = f"{base}#{occurrences[base]}"
    return result


def _normalized_text(
    value: str | None,
    owner_tag: str,
    coverage: CoverageReport,
    *,
    tail: bool,
) -> str:
    if value is None:
        return ""
    if tail:
        coverage.tails += 1
    else:
        coverage.text_nodes += 1
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip() and "\n" in value:
        coverage.formatting_whitespace_ignored += 1
        return ""
    if not tail and owner_tag in NUMERIC_DATE_ELEMENTS and re.fullmatch(r"\s*\d+\s*", value):
        return str(int(value.strip()))
    return value


def _match_key(
    element: etree._Element,
    canonical_attrs: tuple[tuple[str, str], ...],
    text: str,
) -> str:
    tag = local_name(element.tag)
    if tag in LOCAL_MEDIA_ELEMENTS:
        return f"{tag}:{dict(canonical_attrs).get('xlink:href', '')}"
    if tag == "xref":
        attrs = dict(canonical_attrs)
        return f"xref:{attrs.get('ref-type', '')}:{text}:{attrs.get('rid', '')}"
    return _base_anchor(element)


def canonicalize(
    root: etree._Element,
    media_resolver: MediaResolver,
    *,
    side: str,
) -> CanonicalDocument:
    coverage = CoverageReport(side=side)
    tag_counts: Counter[str] = Counter()
    attr_counts: Counter[str] = Counter()
    fallback_tags: Counter[str] = Counter()
    anchors = _id_anchors(root)

    def visit(element: etree._Element, ancestors: tuple[str, ...]) -> CanonicalNode:
        tag = local_name(element.tag)
        full_tag = _qname(element.tag)
        coverage.elements += 1
        tag_counts[full_tag] += 1
        domain = domain_for(tag, ancestors)
        namespace = etree.QName(element).namespace
        if tag not in KNOWN_ELEMENT_NAMES or namespace not in {None, MATHML_NS}:
            fallback_tags[full_tag] += 1

        attrs: list[tuple[str, str]] = []
        for raw_name, raw_value in element.attrib.items():
            coverage.attributes += 1
            name = _qname(raw_name)
            attr_counts[name] += 1
            if name == "id":
                coverage.relations += 1
                attrs.append((name, "<semantic-id-present>"))
                continue
            if name == "rid":
                coverage.relations += 1
                value = " ".join(
                    anchors.get(part, f"!unresolved:{part}") for part in raw_value.split()
                )
            elif name == "xlink:href" and tag in LOCAL_MEDIA_ELEMENTS:
                coverage.media_links += 1
                digest = media_resolver(raw_value)
                value = f"sha256:{digest}" if digest else f"!missing-media:{raw_value}"
            else:
                value = raw_value.replace("\r\n", "\n").replace("\r", "\n")
            attrs.append((name, value))
        attrs_tuple = tuple(sorted(attrs))

        text = _normalized_text(element.text, tag, coverage, tail=False)
        tail = _normalized_text(element.tail, tag, coverage, tail=True)
        children: list[CanonicalNode] = []
        for child in element:
            if not isinstance(child.tag, str):
                if isinstance(child, etree._Comment):
                    coverage.comments_ignored += 1
                else:
                    coverage.unhandled.append({
                        "path": local_path(element),
                        "kind": type(child).__name__,
                    })
                # 注释或处理指令本身不进入语义树，但它后面的文字仍属于
                # 父元素的混合内容，不能随节点一起丢掉。
                carried_tail = _normalized_text(child.tail, tag, coverage, tail=True)
                if carried_tail:
                    if children:
                        children[-1] = replace(
                            children[-1], tail=children[-1].tail + carried_tail
                        )
                    else:
                        text += carried_tail
                continue
            children.append(visit(child, ancestors + (tag,)))
        return CanonicalNode(
            tag=full_tag,
            local_tag=tag,
            path=local_path(element),
            domain=domain,
            attrs=attrs_tuple,
            text=text,
            tail=tail,
            children=tuple(children),
            match_key=_match_key(element, attrs_tuple, text),
        )

    canonical_root = visit(root, ())
    for sibling in tuple(root.itersiblings(preceding=True)) + tuple(root.itersiblings()):
        if isinstance(sibling, etree._Comment):
            coverage.comments_ignored += 1
        else:
            coverage.unhandled.append({"path": "/", "kind": type(sibling).__name__})
    return CanonicalDocument(
        root=canonical_root,
        coverage=coverage,
        tag_counts=tag_counts,
        attr_counts=attr_counts,
        fallback_tags=fallback_tags,
    )
