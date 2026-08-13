"""V2 的显式等价与来源政策。

原则：默认严格。只有本文件明确列出的表示差异才会被吸收。
"""

from __future__ import annotations

from typing import Dict, FrozenSet


EVALUATOR_VERSION = "2.0.0"

JATS_PUBLIC_ID = "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.3 20210610//EN"
JATS_SYSTEM_ID = "https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd"
XLINK_NS = "http://www.w3.org/1999/xlink"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
XML_NS = "http://www.w3.org/XML/1998/namespace"

# 内部 ID 的字面名称不是语义；关系会改写成目标对象的稳定语义锚点后比较。
SEMANTIC_ID_ATTRIBUTES: FrozenSet[str] = frozenset({"id"})
RELATION_ATTRIBUTES: FrozenSet[str] = frozenset({"rid"})

# 这些元素的 xlink:href 指向输出包内媒体。比较时改写成文件 SHA-256，允许等字节改名。
LOCAL_MEDIA_ELEMENTS: FrozenSet[str] = frozenset({"graphic", "inline-graphic", "media"})

# 来源核验中允许由固定出版规则生成的区域。该政策只影响“来自 docx”的旁证，
# 不影响候选与金标准之间的严格语义比较。
GENERATED_REGION_REASONS: Dict[str, str] = {
    "journal-meta": "期刊元数据由期刊登记表确定性注入",
    "permissions": "版权和许可证为期刊固定模板",
    "article-id": "文章标识可由任务参数或固定规则注入",
    "label": "编号可由文档结构机械生成",
}

# XML 注释和仅用于源码排版的带换行空白，不承载论文语义。
IGNORED_NODE_REASONS: Dict[str, str] = {
    "comment": "XML 注释不属于 JATS 文章内容",
    "formatting-whitespace": "元素间缩进换行只用于 XML 源码排版",
}

# 只有这些明确等价的属性值会做值归一化。其余值逐字符比较。
NUMERIC_DATE_ELEMENTS: FrozenSet[str] = frozenset({"day", "month"})

# 用于问题归类；没有列入的元素仍由严格树比较处理，绝不会静默忽略。
DOMAIN_ELEMENTS: Dict[str, FrozenSet[str]] = {
    "document": frozenset({"article", "label"}),
    "metadata": frozenset({
        "front", "journal-meta", "journal-id", "journal-title-group", "journal-title",
        "abbrev-journal-title", "issn", "publisher", "publisher-name", "article-meta",
        "article-id", "article-categories", "subj-group", "subject", "title-group",
        "article-title", "history", "date", "day", "month", "year", "permissions",
        "copyright-statement", "copyright-year", "license", "license-p",
    }),
    "contributors": frozenset({
        "contrib-group", "contrib", "name", "surname", "given-names", "suffix", "degrees",
        "role", "contrib-id", "email", "aff", "address", "addr-line", "postal-code",
        "phone", "author-notes", "corresp", "author-comment", "collab",
    }),
    "abstracts": frozenset({"abstract", "kwd-group", "kwd"}),
    "body": frozenset({"body", "sec", "title", "p"}),
    "figures": frozenset({"fig", "fig-group", "caption", "graphic", "inline-graphic"}),
    "tables": frozenset({
        "table-wrap", "table", "colgroup", "col", "thead", "tbody", "tr", "th", "td",
        "table-wrap-foot",
    }),
    "formulas": frozenset({
        "disp-formula", "inline-formula", "math", "mi", "mn", "mo", "mtext", "mrow",
        "msub", "msup", "msubsup", "munder", "mover", "munderover", "mfenced", "mfrac",
        "mroot", "msqrt", "mstyle", "mpadded", "mphantom", "menclose", "mspace", "mtable",
        "mtr", "mtd", "mmultiscripts", "mprescripts", "none", "semantics", "annotation",
        "annotation-xml",
    }),
    "references": frozenset({
        "ref-list", "ref", "element-citation", "mixed-citation", "person-group", "source",
        "article-title", "chapter-title", "year", "month", "day", "volume", "issue", "fpage",
        "lpage", "elocation-id", "edition", "publisher-name", "publisher-loc", "pub-id", "etal",
        "comment", "date-in-citation",
    }),
    "cross_references": frozenset({"xref"}),
    "back": frozenset({
        "back", "ack", "fn-group", "fn", "glossary", "def-list", "def-item", "term", "def",
        "app-group",
    }),
    "inline_format": frozenset({
        "bold", "italic", "sup", "sub", "styled-content", "break", "ext-link",
    }),
}

KNOWN_ELEMENT_NAMES: FrozenSet[str] = frozenset().union(*DOMAIN_ELEMENTS.values())


def domain_for(tag: str, ancestors=()) -> str:
    """按结构上下文归类，而不是仅凭当前标签名猜测。

    JATS 会在不同位置重复使用 ``article-title``、``year``、``source`` 等
    名称。最靠近当前元素的结构容器才是可靠依据。
    """

    if tag == "xref":
        return "cross_references"

    nearest_first = (tag,) + tuple(reversed(tuple(ancestors)))
    contexts = (
        ("references", {"ref", "ref-list", "element-citation", "mixed-citation"}),
        ("tables", {"table-wrap", "table", "thead", "tbody", "tfoot"}),
        ("figures", {"fig", "fig-group"}),
        ("formulas", {"disp-formula", "inline-formula", "math"}),
        ("contributors", {"contrib", "contrib-group", "aff", "author-notes"}),
        ("abstracts", {"abstract", "trans-abstract", "kwd-group"}),
        ("back", {"back", "ack", "app-group", "app", "fn-group", "fn"}),
        ("body", {"body", "sec"}),
        ("metadata", {"front", "article-meta", "journal-meta"}),
    )
    for name in nearest_first:
        for domain, markers in contexts:
            if name in markers:
                return domain

    for domain in DOMAIN_ELEMENTS:
        if tag in DOMAIN_ELEMENTS[domain]:
            return domain
    return "other"
