"""从我们产出的 JATS XML 提取"结构提纲"——Agent 视觉闭环里"XML 声称的结构"一侧。

闭环的对照是双向的:
- 视觉侧(inspect.py):VLM 看渲染页,给出"页面上实际有什么"。
- XML 侧(本模块):确定性解析 XML,给出"我们的 XML 里有什么"。
- 调和侧(reconcile.py):两份提纲对齐,找出缺失/多余/错类。

本模块是**纯确定性**的(只用 lxml 读 XML),既产出供 LLM 调和用的紧凑文本提纲,
也产出供确定性计数交叉校验用的结构化字典(计数核对不依赖模型,是闭环的硬底线)。
"""

from __future__ import annotations

from lxml import etree


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def parse(xml_bytes: bytes):
    return etree.fromstring(xml_bytes)


def counts(root) -> dict:
    """关键结构计数(确定性交叉校验用)。"""
    body = root.find("body")
    back = root.find("back")
    def n(path, scope=root):
        return len(scope.findall(".//" + path)) if scope is not None else 0
    xref_types: dict = {}
    for x in root.findall(".//xref"):
        t = x.get("ref-type", "?")
        xref_types[t] = xref_types.get(t, 0) + 1
    return {
        "title": 1 if root.findtext(".//article-title") else 0,
        "authors": len(root.findall('.//contrib[@contrib-type="author"]')),
        "affiliations": n("aff"),
        "abstract_sections": len(root.findall(".//abstract/sec")),
        "keywords": n("kwd"),
        "body_sections": len(body.findall("sec")) if body is not None else 0,
        "tables": n("table-wrap"),
        "figures": n("fig"),
        "disp_formula": n("disp-formula"),
        "inline_formula": n("inline-formula"),
        "references": len(back.findall(".//ref")) if back is not None else 0,
        "xref_total": len(root.findall(".//xref")),
        "xref_by_type": xref_types,
    }


def _walk_sections(parent, depth, out):
    for sec in parent.findall("sec"):
        title = sec.findtext("title") or ""
        np = len(sec.findall("p"))
        ntab = len(sec.findall(".//table-wrap"))
        nfig = len(sec.findall(".//fig"))
        neq = len(sec.findall(".//disp-formula"))
        extra = []
        if np:
            extra.append("%d段" % np)
        if ntab:
            extra.append("%d表" % ntab)
        if nfig:
            extra.append("%d图" % nfig)
        if neq:
            extra.append("%d式" % neq)
        out.append({"type": "heading", "depth": depth,
                    "text": title.strip(), "info": " ".join(extra)})
        _walk_sections(sec, depth + 1, out)


def blocks(root) -> list:
    """文档顺序的结构块列表(供文本提纲与人工阅读)。"""
    out = []
    front = root.find("front")
    if front is not None:
        title = front.findtext(".//article-title")
        if title:
            out.append({"type": "title", "depth": 0, "text": title.strip()})
        authors = []
        for c in front.findall('.//contrib[@contrib-type="author"]'):
            s = c.findtext(".//surname") or ""
            g = c.findtext(".//given-names") or ""
            authors.append((g + " " + s).strip())
        if authors:
            out.append({"type": "author_block", "depth": 0,
                        "text": "; ".join(authors), "info": "%d位" % len(authors)})
        for aff in front.findall(".//aff"):
            out.append({"type": "affiliation", "depth": 0, "text": _text(aff)[:120]})
        abss = front.findall(".//abstract")
        for ab in abss:
            secs = ab.findall("sec")
            if secs:
                for s in secs:
                    out.append({"type": "abstract_heading", "depth": 1,
                                "text": (s.findtext("title") or "").strip()})
            else:
                out.append({"type": "abstract", "depth": 1,
                            "text": _text(ab)[:160]})
        kwds = front.findall(".//kwd")
        if kwds:
            out.append({"type": "keywords", "depth": 1,
                        "text": "; ".join((k.text or "").strip() for k in kwds)[:160],
                        "info": "%d个" % len(kwds)})
    body = root.find("body")
    if body is not None:
        _walk_sections(body, 0, out)
        # 表/图/式的标签与题注(给视觉侧定位用)
    for tw in root.findall(".//table-wrap"):
        lbl = tw.findtext("label") or ""
        cap = _text(tw.find("caption")) if tw.find("caption") is not None else ""
        nrow = len(tw.findall(".//tr"))
        ncol = len(tw.findall(".//colgroup/col")) or (
            len(tw.findall(".//tr[1]/th")) + len(tw.findall(".//tr[1]/td")))
        out.append({"type": "table", "depth": 1, "text": (lbl + " " + cap).strip()[:120],
                    "info": "%d行×%d列" % (nrow, ncol)})
    for fig in root.findall(".//fig"):
        lbl = fig.findtext("label") or ""
        cap = _text(fig.find("caption")) if fig.find("caption") is not None else ""
        out.append({"type": "figure", "depth": 1, "text": (lbl + " " + cap).strip()[:120]})
    back = root.find("back")
    if back is not None:
        nref = len(back.findall(".//ref"))
        if nref:
            out.append({"type": "reference_list", "depth": 0,
                        "text": "参考文献", "info": "%d条" % nref})
        for sec in back.findall("sec"):
            t = sec.findtext("title")
            if t:
                out.append({"type": "back_section", "depth": 0, "text": t.strip()})
        for ack in back.findall("ack"):
            t = ack.findtext("title") or "Acknowledgments"
            out.append({"type": "back_section", "depth": 0, "text": t.strip()})
        for glo in back.findall("glossary"):
            t = glo.findtext("title") or "Abbreviations"
            out.append({"type": "back_section", "depth": 0, "text": t.strip()})
    return out


def to_text(blocks_list: list) -> str:
    """把结构块渲染成紧凑文本提纲(喂给 LLM 调和)。"""
    lines = []
    for b in blocks_list:
        indent = "  " * b.get("depth", 0)
        info = (" [%s]" % b["info"]) if b.get("info") else ""
        txt = b.get("text", "")
        lines.append("%s<%s>%s %s" % (indent, b["type"], info, txt))
    return "\n".join(lines)


def outline_from_xml(xml_bytes: bytes):
    """便捷入口:返回 (counts, blocks, text)。"""
    root = parse(xml_bytes)
    bl = blocks(root)
    return counts(root), bl, to_text(bl)
