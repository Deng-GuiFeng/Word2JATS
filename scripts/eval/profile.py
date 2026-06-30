"""把一篇文章(我们的 JATS 输出 / 委员会金标准 XML / 伪标签 JSON)抽成统一"画像"。

画像是评测的中间表示:无论来源是 XML 还是 JSON,都归一到同一组字段,
compare.py 再对两份画像逐维度比对。这样金标准、伪标签、我们的输出走同一套度量。

纯确定性,只读、不联网、不调用模型。
"""
from __future__ import annotations

import re

from lxml import etree

# xlink 命名空间(JATS 里 graphic/ext-link 的 href 在此命名空间)
XLINK = "http://www.w3.org/1999/xlink"


# --------------------------- 通用归一 --------------------------- #
def norm_ws(s) -> str:
    return " ".join((s or "").split())


def norm_title(s) -> str:
    """章节标题归一:去掉行首编号(1 / 2.3.6 / 2.3.6.)、小写、压空白、去尾标点。
    用于跨"有编号 vs 无编号"匹配标题内容。"""
    s = norm_ws(s)
    s = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", s)   # 去行首章节号
    s = s.lower().strip(" .:：、,，;；")
    s = s.replace(" & ", " and ")                    # & 与 and 视同
    return s


def parse_section_number(s) -> str | None:
    """从标题行首解析章节号,如 '2.3.6 Model Validation' → '2.3.6';无则 None。"""
    m = re.match(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S", norm_ws(s))
    return m.group(1) if m else None


def norm_orcid(s) -> str:
    """归一 ORCID:取裸 ID(去掉 https://orcid.org/ 前缀)。"""
    s = norm_ws(s)
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dxX])", s)
    return m.group(1).upper() if m else ""


def surname_key(s) -> str:
    return norm_ws(s).lower().strip(".,")


# --------------------------- XML 侧 --------------------------- #
def _root_from_bytes(xml_bytes: bytes):
    """去掉 DOCTYPE(避免联网取 DTD)后解析。"""
    text = re.sub(r"<!DOCTYPE.*?>", "", xml_bytes.decode("utf-8", "ignore"), flags=re.S)
    return etree.fromstring(text.encode("utf-8"))


def _itertext(el) -> str:
    return norm_ws("".join(el.itertext())) if el is not None else ""


def _sections(parent, depth, out):
    """递归收集 body 章节树为扁平列表 [{depth, title, number, n_p, n_tab, n_fig, n_eq}]。"""
    for sec in parent.findall("sec"):
        title = ""
        # 直接子 title(不串到子 sec 的 title)
        t = sec.find("title")
        if t is not None:
            title = _itertext(t)
        lbl = sec.find("label")
        label_txt = _itertext(lbl) if lbl is not None else ""
        full = norm_ws((label_txt + " " + title).strip())
        out.append({
            "depth": depth,
            "title": title,
            "title_with_label": full,
            "number": parse_section_number(full) or parse_section_number(title),
            "n_p": len(sec.findall("p")),
            "n_tab": len(sec.findall(".//table-wrap")),
            "n_fig": len(sec.findall(".//fig")),
            "n_eq": len(sec.findall(".//disp-formula")),
        })
        _sections(sec, depth + 1, out)


def _first_surname_year(ref):
    """从 <ref> 抽首作者姓 + 年份(兼容 element-citation / mixed-citation)。"""
    surname = ""
    s = ref.find(".//surname")
    if s is not None:
        surname = surname_key(_itertext(s))
    else:
        sn = ref.find(".//string-name")
        if sn is not None:
            surname = surname_key(_itertext(sn).split(",")[0].split()[0] if _itertext(sn) else "")
    year = ""
    y = ref.find(".//year")
    if y is not None:
        year = norm_ws(_itertext(y))
    if not year:
        m = re.search(r"\b(19|20)\d{2}\b", _itertext(ref))
        if m:
            year = m.group(0)
    return surname, year


def profile_from_xml(xml_bytes: bytes) -> dict:
    root = _root_from_bytes(xml_bytes)
    front = root.find("front")
    body = root.find("body")
    back = root.find("back")

    # 标题
    title = ""
    at = root.find(".//article-title")
    if at is not None:
        title = _itertext(at)

    # 单位 id → 显示编号映射(作者 xref 的 rid 是内部 id 如 'aff1',显示编号是 <sup>1</sup>;
    # 按显示编号比对才能与伪标签的 '1','2' 对齐,避免 'aff1'≠'1' 的假差异)
    aff_label_by_id = {}
    for aff in root.findall(".//aff"):
        aid = aff.get("id") or ""
        sup = aff.find("sup")
        lbl = norm_ws(_itertext(sup)) if sup is not None else ""
        if aid:
            aff_label_by_id[aid] = lbl or re.sub(r"^aff", "", aid)

    # 作者(只取 contrib-type=author)
    authors = []
    for c in root.findall('.//contrib[@contrib-type="author"]'):
        surname = _itertext(c.find(".//surname"))
        given = _itertext(c.find(".//given-names"))
        affs = []
        for x in c.findall('xref[@ref-type="aff"]'):
            rid = x.get("rid") or ""
            for one in rid.split():
                affs.append(aff_label_by_id.get(one, re.sub(r"^aff", "", one)))
        corr = bool(c.findall('xref[@ref-type="corresp"]')) or \
            (c.get("corresp") == "yes")
        # 共同贡献:常见以 xref ref-type=fn / @equal-contrib 表示
        equal = (c.get("equal-contrib") == "yes") or bool(
            c.xpath('.//xref[@ref-type="fn"]'))
        orcid = ""
        cid = c.find('.//contrib-id[@contrib-id-type="orcid"]')
        if cid is not None:
            orcid = norm_orcid(_itertext(cid))
        email = _itertext(c.find(".//email"))
        authors.append({
            "surname": surname, "given": given,
            "affs": affs, "corresponding": corr, "equal": equal,
            "orcid": orcid, "email": email,
        })

    # 编辑
    editors = []
    for c in root.findall('.//contrib[@contrib-type="editor"]'):
        editors.append({"surname": _itertext(c.find(".//surname")),
                        "given": _itertext(c.find(".//given-names"))})

    # 单位
    affs = []
    for aff in root.findall(".//aff"):
        label = ""
        sup = aff.find("sup")
        if sup is not None:
            label = norm_ws(_itertext(sup))
        if not label:
            label = norm_ws(aff.get("id") or "")
        full = _itertext(aff)
        # 去掉前导编号(sup 文本)得正文
        text = full
        if label and full.startswith(label):
            text = norm_ws(full[len(label):])
        affs.append({"label": label, "text": text})

    # 摘要
    abstract = {"structured": False, "subheads": []}
    ab = front.find(".//abstract") if front is not None else None
    if ab is not None:
        secs = ab.findall("sec")
        if secs:
            abstract["structured"] = True
            abstract["subheads"] = [norm_ws(_itertext(s.find("title"))).strip(":：")
                                    for s in secs if s.find("title") is not None]

    # 关键词
    keywords = [norm_ws(_itertext(k)) for k in root.findall(".//kwd")]

    # 章节树
    sections = []
    if body is not None:
        _sections(body, 0, sections)

    # 图
    figures = []
    for fig in root.findall(".//fig"):
        lbl = _itertext(fig.find("label"))
        cap = _itertext(fig.find("caption"))
        num = parse_int(lbl) or parse_int(cap)
        figures.append({"number": num, "label": lbl, "caption": cap})

    # 表
    tables = []
    for tw in root.findall(".//table-wrap"):
        lbl = _itertext(tw.find("label"))
        cap = _itertext(tw.find("caption"))
        num = parse_int(lbl) or parse_int(cap)
        ncol = len(tw.findall(".//colgroup/col")) or (
            len(tw.findall(".//tr[1]/th")) + len(tw.findall(".//tr[1]/td")))
        has_foot = tw.find(".//table-wrap-foot") is not None or tw.find(".//fn") is not None
        is_graphic = tw.find(".//table") is None and tw.find(".//graphic") is not None
        tables.append({"number": num, "label": lbl, "caption": cap,
                       "ncols": ncol, "has_footnote": has_foot, "is_graphic": is_graphic})

    # 公式
    disp = len(root.findall(".//disp-formula"))
    inline = len(root.findall(".//inline-formula"))

    # 参考文献
    refs = []
    if back is not None:
        for ref in back.findall(".//ref"):
            label = _itertext(ref.find("label"))
            sur, yr = _first_surname_year(ref)
            refs.append({"label": label, "first_surname": sur, "year": yr})

    # xref
    xref_by_type = {}
    dangling = 0
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    for x in root.findall(".//xref"):
        t = x.get("ref-type", "?")
        xref_by_type[t] = xref_by_type.get(t, 0) + 1
        rid = x.get("rid") or ""
        for one in rid.split():
            if one not in ids:
                dangling += 1

    # 文档级邮箱集合(邮箱可能在 contrib 内,也可能只在 author-notes/corresp 里;
    # 按文档级集合比对,避免"邮箱在 corresp 而非 contrib"被误判为丢失)
    all_emails = sorted({_itertext(e).lower() for e in root.findall(".//email")
                         if _itertext(e)})

    # back 小节
    back_sections = []
    if back is not None:
        for sec in back.findall("sec"):
            t = _itertext(sec.find("title"))
            if t:
                back_sections.append(t)
        for ack in back.findall("ack"):
            back_sections.append(_itertext(ack.find("title")) or "Acknowledgments")
        for glo in back.findall("glossary"):
            back_sections.append(_itertext(glo.find("title")) or "Abbreviations")

    return {
        "source": "xml",
        "title": title,
        "authors": authors,
        "emails": all_emails,
        "editors": editors,
        "affiliations": affs,
        "abstract": abstract,
        "keywords": keywords,
        "sections": sections,
        "figures": figures,
        "tables": tables,
        "disp_formula": disp,
        "inline_formula": inline,
        "references": {"count": len(refs), "items": refs},
        "xref": {"total": sum(xref_by_type.values()), "by_type": xref_by_type, "dangling": dangling},
        "back_sections": back_sections,
    }


# --------------------------- 伪标签 JSON 侧 --------------------------- #
def profile_from_pseudo(label: dict) -> dict:
    """补充案例伪标签 JSON → 画像(只填标签提供的字段;body 章节树伪标签无,留空)。"""
    authors = []
    for a in label.get("authors", []):
        authors.append({
            "surname": a.get("surname", ""), "given": a.get("given", ""),
            "affs": [str(x) for x in a.get("aff_labels", [])],
            "corresponding": bool(a.get("corresponding")),
            "equal": bool(a.get("equal_contrib")),
            "orcid": norm_orcid(a.get("orcid", "")),
            "email": a.get("email", "") or "",
        })
    affs = [{"label": str(x.get("label", "")), "text": x.get("text", "")}
            for x in label.get("affiliations", [])]
    figures = [{"number": f.get("number"), "label": "", "caption": f.get("caption", "")}
               for f in label.get("figures", [])]
    tables = [{"number": t.get("number"), "label": "", "caption": "",
               "ncols": t.get("n_cols"), "has_footnote": bool(t.get("has_footnote")),
               "is_graphic": False}
              for t in label.get("tables", [])]
    return {
        "source": "pseudo",
        "title": label.get("title", ""),
        "authors": authors,
        "emails": sorted({a["email"].lower() for a in authors if a["email"]}),
        "editors": [{"surname": e.get("surname", ""), "given": e.get("given", "")}
                    for e in label.get("editors", [])],
        "affiliations": affs,
        "abstract": {"structured": bool(label.get("abstract_structured")),
                     "subheads": [norm_ws(s).strip(":：") for s in label.get("abstract_subheads", [])]},
        "keywords": list(label.get("keywords", [])),
        "sections": [],   # 伪标签未含 body 章节树
        "sections_available": False,
        "figures": figures,
        "tables": tables,
        "disp_formula": None,
        "inline_formula": None,
        "references": {"count": int(label.get("reference_count", 0) or 0),
                       "items": [{"label": str(r.get("label", "")),
                                  "first_surname": surname_key(r.get("first_author_surname", "")),
                                  "year": str(r.get("year", ""))}
                                 for r in label.get("references_head", [])]},
        "xref": None,
        "back_sections": list(label.get("back_section_titles", [])),
    }


def parse_int(s):
    if s is None:
        return None
    m = re.search(r"(\d+)", str(s))
    return int(m.group(1)) if m else None


def load_profile_from_xml_path(path: str) -> dict:
    with open(path, "rb") as f:
        return profile_from_xml(f.read())
