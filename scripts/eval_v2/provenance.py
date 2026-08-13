"""独立核验候选文本和媒体是否能追溯到输入 docx。"""

from __future__ import annotations

from collections import Counter, defaultdict
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Iterable

from lxml import etree

from .artifacts import MediaBlob, local_name, local_path, media_elements, sha256_bytes
from .models import IssueCollector, ProvenanceReport
from .policy import GENERATED_REGION_REASONS, XLINK_NS


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
TEXT_NAMES = {f"{{{W_NS}}}t", f"{{{M_NS}}}t", f"{{{W_NS}}}instrText"}
PARAGRAPH = f"{{{W_NS}}}p"
TAB = f"{{{W_NS}}}tab"
BREAKS = {f"{{{W_NS}}}br", f"{{{W_NS}}}cr"}
TOKEN_RE = re.compile(r"\w+(?:[-'’]\w+)*|[^\w\s]", re.UNICODE)
SOURCE_PART_RE = re.compile(
    r"^word/(document|footnotes|endnotes|comments|header\d+|footer\d+)\.xml$"
)
BLOCK_BOUNDARIES = {
    "article-title", "subtitle", "p", "title", "aff", "corresp", "author-comment",
    "kwd", "td", "th", "list-item", "element-citation", "mixed-citation", "label",
}
XLINK_HREF = f"{{{XLINK_NS}}}href"


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(unicodedata.normalize("NFC", text))


def _normalized_token(token: str) -> str:
    return token.casefold()


def _docx_paragraph_text(paragraph: etree._Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if not isinstance(node.tag, str):
            continue
        if node.tag in TEXT_NAMES and node.text:
            pieces.append(node.text)
        elif node.tag == TAB:
            pieces.append("\t")
        elif node.tag in BREAKS:
            pieces.append("\n")
    return "".join(pieces)


def read_docx_text(docx: Path, issues: IssueCollector) -> tuple[Counter[str], dict[str, Counter[str]], int, int]:
    totals: Counter[str] = Counter()
    display: dict[str, Counter[str]] = defaultdict(Counter)
    part_count = 0
    paragraph_count = 0
    try:
        with zipfile.ZipFile(docx) as archive:
            for name in sorted(archive.namelist()):
                if not SOURCE_PART_RE.match(name):
                    continue
                part_count += 1
                try:
                    root = etree.fromstring(
                        archive.read(name),
                        parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False),
                    )
                except etree.XMLSyntaxError as exc:
                    issues.add(
                        "DOCX_PART_XML_INVALID", "infrastructure", "docx 内部 XML 不能安全解析",
                        severity="critical", actual=name, evidence={"error": str(exc)},
                    )
                    continue
                for paragraph in root.iter(PARAGRAPH):
                    paragraph_count += 1
                    for token in _tokens(_docx_paragraph_text(paragraph)):
                        normalized = _normalized_token(token)
                        totals[normalized] += 1
                        display[normalized][token] += 1
    except (OSError, zipfile.BadZipFile) as exc:
        issues.add(
            "DOCX_UNREADABLE", "infrastructure", "输入 docx 无法读取",
            severity="critical", actual=str(docx), evidence={"error": str(exc)},
        )
    return totals, display, part_count, paragraph_count


def read_docx_media(docx: Path, issues: IssueCollector) -> dict[str, list[str]]:
    hashes: dict[str, list[str]] = defaultdict(list)
    try:
        with zipfile.ZipFile(docx) as archive:
            for name in archive.namelist():
                if name.startswith("word/media/") and not name.endswith("/"):
                    hashes[sha256_bytes(archive.read(name))].append(name)
    except (OSError, zipfile.BadZipFile) as exc:
        issues.add(
            "DOCX_MEDIA_UNREADABLE", "infrastructure", "无法读取 docx 内嵌媒体",
            severity="critical", actual=str(docx), evidence={"error": str(exc)},
        )
    return dict(hashes)


def _candidate_text(root: etree._Element) -> tuple[str, Counter[str]]:
    pieces: list[str] = []
    generated: Counter[str] = Counter()

    def walk(element: etree._Element, suppressed: bool = False) -> None:
        tag = local_name(element.tag)
        if suppressed or tag in GENERATED_REGION_REASONS:
            generated[tag] += 1
            return
        if element.text:
            pieces.append(element.text)
        for child in element:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail:
                pieces.append(child.tail)
        if tag in BLOCK_BOUNDARIES:
            pieces.append("\n")

    walk(root)
    return "".join(pieces), generated


def _token_rows(
    keys: Iterable[str],
    source: Counter[str],
    candidate: Counter[str],
    source_display: dict[str, Counter[str]],
    candidate_display: dict[str, Counter[str]],
) -> list[dict[str, object]]:
    rows = []
    for key in sorted(keys):
        displays = candidate_display.get(key) or source_display.get(key) or Counter({key: 1})
        shown = displays.most_common(1)[0][0]
        rows.append({
            "token": shown,
            "normalized": key,
            "source_count": source[key],
            "candidate_count": candidate[key],
        })
    return rows


def audit_provenance(
    docx: Path,
    candidate_root: etree._Element,
    candidate_blobs: dict[str, MediaBlob],
    issues: IssueCollector,
) -> ProvenanceReport:
    source, source_display, part_count, paragraph_count = read_docx_text(docx, issues)
    candidate_text, generated = _candidate_text(candidate_root)
    candidate: Counter[str] = Counter()
    candidate_display: dict[str, Counter[str]] = defaultdict(Counter)
    for token in _tokens(candidate_text):
        normalized = _normalized_token(token)
        candidate[normalized] += 1
        candidate_display[normalized][token] += 1

    novel = {key for key in candidate if source[key] == 0}
    excess = {key for key in candidate if source[key] > 0 and candidate[key] > source[key]}
    missing = {key for key in source if candidate[key] == 0}

    source_media = read_docx_media(docx, issues)
    not_from_docx: list[dict[str, object]] = []
    referenced_count = 0
    from_docx_count = 0
    for element in media_elements(candidate_root):
        referenced_count += 1
        href = element.get(XLINK_HREF) or ""
        blob = candidate_blobs.get(href)
        if blob is None:
            continue
        if blob.sha256 in source_media:
            from_docx_count += 1
            continue
        row = {
            "href": href,
            "path": local_path(element),
            "sha256": blob.sha256,
            "size": blob.size,
        }
        not_from_docx.append(row)
        issues.add(
            "MEDIA_NOT_FROM_DOCX", "provenance", "候选媒体不是输入 docx 中任何媒体的原始字节",
            severity="error", candidate_path=local_path(element), actual=href, evidence=row,
        )

    return ProvenanceReport(
        source_parts=part_count,
        source_paragraphs=paragraph_count,
        source_token_kinds=len(source),
        candidate_token_kinds=len(candidate),
        novel_tokens=_token_rows(novel, source, candidate, source_display, candidate_display),
        excess_tokens=_token_rows(excess, source, candidate, source_display, candidate_display),
        missing_source_tokens=_token_rows(missing, source, candidate, source_display, candidate_display),
        allowed_generated_regions=dict(sorted(generated.items())),
        referenced_media=referenced_count,
        media_from_docx=from_docx_count,
        media_not_from_docx=not_from_docx,
    )
