"""构建 JATS ``<front>``（journal-meta + article-meta）。

journal-meta 走期刊查表；article-meta 的作者/单位/ORCID/通讯/摘要/关键词来自
StructuredDoc；DOI/版权年等外部字段来自配置（缺省时合理省略或占位）。
"""

from __future__ import annotations

from typing import Optional

from ..model.structured import StructuredDoc
from .jats import E, append_inline, sub

CC_BY = "https://creativecommons.org/licenses/by/4.0/"


def build_front(sd: StructuredDoc, registry, doi: Optional[str],
                journal_id: Optional[str], math_builder=None,
                default_year=None) -> "etree._Element":
    front = E("front")
    jid = journal_id or registry.guess_from_doi(doi)
    info = registry.get(jid) if jid else None
    # Journal Publishing DTD 要求 front 含 journal-meta，且其含 journal-id+ 与 issn+，
    # 故始终生成；未知期刊用占位值兜底以保证输出始终 DTD 合规（稳定性）。
    _journal_meta(front, registry, info, jid)
    _article_meta(front, sd, registry, doi, journal_id, math_builder, default_year)
    return front


def _journal_meta(front, registry, info, jid):
    jm = sub(front, "journal-meta")
    if info:
        # 用 .get 容错：journals.yaml 人工维护，缺字段不应导致崩溃
        sub(jm, "journal-id", info.get("journal-id") or (jid or "UNKNOWN").upper(),
            **{"journal-id-type": "publisher-id"})
        jtg = sub(jm, "journal-title-group")
        sub(jtg, "journal-title", info.get("journal-title") or (jid or "Journal"))
        if info.get("abbrev-publisher"):
            sub(jtg, "abbrev-journal-title", info["abbrev-publisher"], **{"abbrev-type": "publisher"})
        if info.get("abbrev-pubmed"):
            sub(jtg, "abbrev-journal-title", info["abbrev-pubmed"], **{"abbrev-type": "pubmed"})
        ppub, epub = info.get("issn-ppub"), info.get("issn-epub")
        if ppub:
            sub(jm, "issn", ppub, **{"pub-type": "ppub"})
        if epub:
            sub(jm, "issn", epub, **{"pub-type": "epub"})
        if not ppub and not epub:  # DTD 要求 issn+，至少给一个占位
            sub(jm, "issn", "0000-0000", **{"pub-type": "epub"})
    else:
        # 未知期刊兜底：满足 journal-id+ / issn+ 的最小合规结构（占位 ISSN）。
        # 生产中应在 resources/journals.yaml 补充该期刊的真实元数据。
        sub(jm, "journal-id", (jid or "UNKNOWN").upper(), **{"journal-id-type": "publisher-id"})
        jtg = sub(jm, "journal-title-group")
        sub(jtg, "journal-title", jid or "Unknown Journal")
        sub(jm, "issn", "0000-0000", **{"pub-type": "epub"})
    pub = sub(jm, "publisher")
    sub(pub, "publisher-name", registry.publisher)


def _article_meta(front, sd, registry, doi, journal_id, math_builder, default_year=None):
    am = sub(front, "article-meta")
    if doi:
        sub(am, "article-id", doi, **{"pub-id-type": "doi"})
        sub(am, "article-id", doi, **{"pub-id-type": "publisher-id"})

    if sd.article_category:
        ac = sub(am, "article-categories")
        sg = sub(ac, "subj-group", **{"subj-group-type": "heading"})
        sub(sg, "subject", sd.article_category)

    tg = sub(am, "title-group")
    sub(tg, "article-title", sd.title or "")

    # 作者 contrib-group + 单位
    if sd.authors or sd.affiliations:
        cg = sub(am, "contrib-group")
        aff_ids = {aff.aff_id for aff in sd.affiliations}
        has_equal_fn = _equal_real(sd)
        for a in sd.authors:
            _contrib(cg, a, aff_ids, has_equal_fn)
        for aff in sd.affiliations:
            el = sub(cg, "aff", id=aff.aff_id)
            if aff.label:
                sub(el, "sup", aff.label)
            # 机构正文作为 aff 文本尾随
            if el.text:
                el.text += aff.text
            else:
                # sup 之后用 tail 承载文本
                if len(el):
                    el[-1].tail = aff.text
                else:
                    el.text = aff.text

    # 学术编辑
    if sd.editors:
        cg2 = sub(am, "contrib-group", **{"content-type": "editors"})
        for ed in sd.editors:
            c = sub(cg2, "contrib", **{"contrib-type": "editor"})
            nm = sub(c, "name")
            sub(nm, "surname", ed.surname)
            sub(nm, "given-names", ed.given_names)
            sub(c, "role", ed.role)

    # author-notes
    _author_notes(am, sd)

    # history
    _history(am, sd)

    # permissions
    _permissions(am, sd, default_year)

    # abstract
    _abstract(am, sd, math_builder)

    # keywords
    if sd.keywords:
        kg = sub(am, "kwd-group", **{"kwd-group-type": "author"})
        for kw in sd.keywords:
            sub(kg, "kwd", kw)


def _contrib(cg, a, aff_ids, has_equal_fn):
    c = sub(cg, "contrib", **{"contrib-type": "author"})
    if a.orcid:
        attrs = {"contrib-id-type": "orcid"}
        if a.orcid_authenticated:
            attrs["authenticated"] = "true"
        sub(c, "contrib-id", "https://orcid.org/" + a.orcid, **attrs)
    nm = sub(c, "name")
    sub(nm, "surname", a.surname)
    if a.given_names:
        sub(nm, "given-names", a.given_names)
    # 仅当对应 <aff> 确实存在时才生成 aff xref，杜绝悬空 IDREF
    for lab in a.aff_labels:
        if ("aff" + lab) in aff_ids:
            x = sub(c, "xref", **{"ref-type": "aff", "rid": "aff" + lab})
            sub(x, "sup", lab)
        elif len(lab) > 1 and all(("aff" + d) in aff_ids for d in lab):
            # 多位数字未匹配到单位(如 "12,3" 漏了逗号)、但其各位都对应已有单位 → 拆分
            for d in lab:
                x = sub(c, "xref", **{"ref-type": "aff", "rid": "aff" + d})
                sub(x, "sup", d)
    # 非通讯作者的邮箱作为普通 <email> 挂在 contrib 上(通讯邮箱走 author-notes/corresp)
    if a.email and not a.is_corresponding:
        sub(c, "email", a.email)
    if a.is_corresponding:
        x = sub(c, "xref", **{"ref-type": "corresp", "rid": "cor1"})
        sub(x, "sup", "*")
    # 仅当确实会生成 <fn id="fn1"> 时才发共同贡献 xref
    if a.equal_contrib and has_equal_fn:
        x = sub(c, "xref", **{"ref-type": "fn", "rid": "fn1"})
        sub(x, "sup", "†")


def _equal_real(sd) -> bool:
    """共同贡献是否成立:须有"贡献相同"声明,或**≥2 位**作者共享该标记。
    单个作者带孤立 †/# 且无声明 → 视为噪声,不生成 fn(实测样例 S04:jiang 单独一个 #)。"""
    return bool(sd.equal_contrib_note) or sum(1 for a in sd.authors if a.equal_contrib) >= 2


def _author_notes(am, sd):
    has_corresp = any(a.is_corresponding for a in sd.authors) or sd.corresp_email_map
    has_equal = _equal_real(sd)
    if not has_corresp and not has_equal:
        return
    an = sub(am, "author-notes")
    if sd.corresp_email_map or any(a.is_corresponding for a in sd.authors):
        cor = sub(an, "corresp", id="cor1")
        sub(cor, "sup", "*")
        pairs = [p for p in (sd.corresp_email_map or [
            (a.email, ("%s %s" % (a.given_names, a.surname)).strip())
            for a in sd.authors if a.is_corresponding and a.email]) if p[0]]
        # 仅当确有邮箱时才写 "Correspondence: " 引导语,避免输出悬空标签
        if pairs:
            cor[-1].tail = "Correspondence: "
            first = True
            for em, name in pairs:
                if not first:
                    cor[-1].tail = (cor[-1].tail or "") + "; "
                e = sub(cor, "email", em)
                if name:
                    e.tail = " (%s)" % name
                first = False
    if has_equal:
        note = (sd.equal_contrib_note or "").lstrip("†#*‡§ ").strip()
        fn = sub(an, "fn", id="fn1")
        p = sub(fn, "p")
        s = sub(p, "sup", "†")
        s.tail = note or "These authors contributed equally."


def _history(am, sd):
    d = sd.dates
    if not (d.received or d.revised or d.accepted):
        return
    h = sub(am, "history")
    for dtype, val in (("received", d.received), ("rev-recd", d.revised),
                       ("accepted", d.accepted)):
        if val:
            de = sub(h, "date", **{"date-type": dtype})
            sub(de, "day", val[2])
            sub(de, "month", val[1])
            sub(de, "year", val[0])


def _permissions(am, sd, default_year=None):
    # 版权/许可是 IMR 固定模板,**始终生成**(此前误以为缺录用年就不出 → 4 个样例丢了整块)。
    # 版权年:录用 > 修回 > 收稿 > 外部给定/默认。
    year = None
    for d in (sd.dates.accepted, sd.dates.revised, sd.dates.received):
        if d:
            year = d[0]
            break
    year = year or default_year
    perm = sub(am, "permissions")
    if year:
        sub(perm, "copyright-statement",
            "Copyright: © %s The Author(s). Published by IMR Press." % year)
        sub(perm, "copyright-year", year)
    else:
        sub(perm, "copyright-statement",
            "Copyright: © The Author(s). Published by IMR Press.")
    lic = sub(perm, "license", **{"license-type": "open-access"})
    lp = sub(lic, "license-p")
    lp.text = "This is an open access article under the "
    el = sub(lp, "ext-link", "CC BY 4.0 license", **{"ext-link-type": "uri", "xlink_href": CC_BY})
    el.tail = "."


def _abstract(am, sd, math_builder):
    if sd.abstract:
        ab = sub(am, "abstract")
        structured = any(s.title for s in sd.abstract)
        if structured:
            for s in sd.abstract:
                sec = sub(ab, "sec")
                if s.title:
                    sub(sec, "title", s.title)
                lead = s.lead
                if lead:
                    sub(sec, "p", lead)
                for p in s.paragraphs:
                    pe = sub(sec, "p")
                    append_inline(pe, p.runs, math_builder)
        else:
            for s in sd.abstract:
                lead = s.lead
                if lead:
                    sub(ab, "p", lead)
                for p in s.paragraphs:
                    pe = sub(ab, "p")
                    append_inline(pe, p.runs, math_builder)
    # "Capsule:" 一句话摘要单独成 <abstract abstract-type="precis">(忠实搬运 docx 原句)
    if sd.precis:
        pab = sub(am, "abstract", **{"abstract-type": "precis"})
        sub(pab, "p", sd.precis)
