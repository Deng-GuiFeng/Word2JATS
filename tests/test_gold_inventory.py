"""金标准结构清单与 SemanticDoc v2 容量下界测试。"""

from pathlib import Path

from lxml import etree

from tests.goldload import load_gold


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "样例数据"

# 独立遍历 14 份定稿金标准得到的冻结清单。更换金标准结构时，
# 必须先审查新结构的语义承载方式，不得只改数字让测试通过。
EXPECTED_TAGS = {
    "abbrev-journal-title", "abstract", "ack", "addr-line", "address",
    "aff", "article", "article-categories", "article-id", "article-meta",
    "article-title", "author-comment", "author-notes", "back", "body",
    "bold", "break", "caption", "chapter-title", "col", "colgroup",
    "collab", "comment", "contrib", "contrib-group", "contrib-id",
    "copyright-statement", "copyright-year", "corresp", "date", "day",
    "def", "def-item", "def-list", "degrees", "disp-formula", "edition",
    "element-citation", "elocation-id", "email", "etal", "ext-link", "fig",
    "fig-group", "fn", "fn-group", "fpage", "front", "given-names",
    "glossary", "graphic", "history", "inline-formula", "inline-graphic",
    "issn", "issue", "italic", "journal-id", "journal-meta", "journal-title",
    "journal-title-group", "kwd", "kwd-group", "label", "license", "license-p",
    "lpage", "math", "mfenced", "mfrac", "mi", "mixed-citation", "mn", "mo",
    "month", "mover", "mrow", "msqrt", "msub", "msubsup", "msup", "mtext",
    "munder", "name", "p", "permissions", "person-group", "phone",
    "postal-code", "pub-id", "publisher", "publisher-loc", "publisher-name",
    "ref", "ref-list", "role", "sec", "semantics", "source", "sub",
    "subj-group", "subject", "suffix", "sup", "surname", "table",
    "table-wrap", "table-wrap-foot", "tbody", "td", "term", "th", "thead",
    "title", "title-group", "tr", "volume", "xref", "year",
}

EXPECTED_ATTRIBUTES = {
    ("abbrev-journal-title", "abbrev-type"), ("abstract", "abstract-type"),
    ("ack", "id"), ("aff", "id"), ("article", "article-type"),
    ("article", "dtd-version"), ("article", "lang"),
    ("article-id", "pub-id-type"), ("col", "width"),
    # contrib/@corresp 已从金标准移除：5 份上线版本一次都没用过它，通信作者一律
    # 只靠指向 <corresp> 的 xref 表示；原先只有 01 用了 2 处，14 份里独此一份。
    ("contrib", "contrib-type"),
    ("contrib-group", "content-type"), ("contrib-id", "authenticated"),
    ("contrib-id", "contrib-id-type"), ("corresp", "id"),
    ("date", "date-type"), ("disp-formula", "id"),
    ("element-citation", "publication-type"),
    ("ext-link", "ext-link-type"), ("ext-link", "href"),
    ("fig", "id"), ("fig", "position"), ("fig-group", "id"),
    ("fn", "fn-type"), ("fn", "id"), ("glossary", "id"),
    ("graphic", "href"), ("graphic", "id"), ("inline-formula", "id"),
    ("inline-graphic", "href"), ("issn", "pub-type"),
    ("journal-id", "journal-id-type"), ("kwd-group", "kwd-group-type"),
    ("license", "href"), ("license", "license-type"),
    ("math", "alttext"), ("math", "display"), ("math", "id"),
    ("mfenced", "close"), ("mfenced", "open"), ("mfenced", "separators"),
    ("mi", "mathvariant"), ("mixed-citation", "publication-type"),
    ("mo", "stretchy"), ("mover", "accent"), ("p", "id"),
    ("person-group", "person-group-type"), ("pub-id", "pub-id-type"),
    ("ref", "id"), ("sec", "id"), ("subj-group", "subj-group-type"),
    ("table-wrap", "id"), ("table-wrap", "position"),
    ("td", "align"), ("td", "colspan"), ("td", "rowspan"),
    ("td", "scope"), ("td", "style"), ("td", "valign"),
    ("th", "align"), ("th", "colspan"), ("th", "rowspan"),
    ("th", "scope"), ("th", "style"), ("th", "valign"),
    ("xref", "ref-type"), ("xref", "rid"),
}


def _gold_paths():
    return sorted(SAMPLES.glob("*/结构参考.xml"))


def test_gold_inventory_is_frozen_and_complete():
    tags = set()
    attributes = set()
    attribute_values = set()
    element_count = media_count = mixed_slots = 0
    for path in _gold_paths():
        for element in etree.parse(str(path)).iter():
            tag = etree.QName(element).localname
            tags.add(tag)
            element_count += 1
            for name in element.attrib:
                local_name = etree.QName(name).localname
                attributes.add((tag, local_name))
                attribute_values.add((tag, local_name, element.attrib[name]))
            for child in element:
                if child.tail and child.tail.strip():
                    mixed_slots += 1
            if element.text and element.text.strip() and len(element):
                mixed_slots += 1
            if tag in {"graphic", "inline-graphic"}:
                media_count += 1
    assert len(_gold_paths()) == 14
    assert tags == EXPECTED_TAGS
    assert attributes == EXPECTED_ATTRIBUTES
    assert (element_count, len(attribute_values), media_count, mixed_slots) == (
        # 属性取值从 1511 降到 1510：contrib/@corresp="yes" 是该属性的唯一取值，
        # 随它一起移除。元素数、媒体数、混合内容槽位数不变——aff 里编号的位置调整
        # 只是在同一个父元素内换了子元素次序。
        31015, 1510, 103, 2853,
    )


def test_every_gold_document_fits_typed_semantic_contract():
    for xml_path in _gold_paths():
        document = load_gold(xml_path, xml_path.with_name("figures.zip"))
        document.validate()
