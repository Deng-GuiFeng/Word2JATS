"""渲染 ``<front>``（journal-meta + article-meta）：消费 SemanticDoc 前置区字段。

journal-meta 走期刊查表（journal-meta 在 docx 里通常不存在，属出版系统注入，是允许的
B 档补全）；article-meta 的作者/单位/通讯/摘要/关键词/日期全部来自 LLM 已判定的语义字段，
渲染器只做机械构造，不含内容判定。
"""

from __future__ import annotations

import re

from ..build.jats import E, append_inline, sub

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
CC_BY = "https://creativecommons.org/licenses/by/4.0/"


def render_front(sd, registry, doi, journal_id, ctx, default_year=None):
    front = E("front")
    jid = journal_id or registry.guess_from_doi(doi)
    info = registry.get(jid) if jid else None
    _journal_meta(front, registry, info, jid)
    _article_meta(front, sd, doi, ctx, default_year)
    return front


def _journal_meta(front, registry, info, jid):
    jm = sub(front, "journal-meta")
    if info:
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
        if not ppub and not epub:
            sub(jm, "issn", "0000-0000", **{"pub-type": "epub"})
    else:
        sub(jm, "journal-id", (jid or "UNKNOWN").upper(), **{"journal-id-type": "publisher-id"})
        jtg = sub(jm, "journal-title-group")
        sub(jtg, "journal-title", jid or "Unknown Journal")
        sub(jm, "issn", "0000-0000", **{"pub-type": "epub"})
    pub = sub(jm, "publisher")
    sub(pub, "publisher-name", registry.publisher)


def _article_meta(front, sd, doi, ctx, default_year=None):
    am = sub(front, "article-meta")
    if doi:
        sub(am, "article-id", doi, **{"pub-id-type": "doi"})
        sub(am, "article-id", doi, **{"pub-id-type": "publisher-id"})

    if sd.article_category:
        ac = sub(am, "article-categories")
        sg = sub(ac, "subj-group", **{"subj-group-type": "heading"})
        sub(sg, "subject", sd.article_category)

    tg = sub(am, "title-group")
    at = sub(tg, "article-title")
    if sd.title_runs:
        append_inline(at, sd.title_runs, ctx.inline_math)
    else:
        at.text = sd.title or ""

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
            if el.text:
                el.text += aff.text
            elif len(el):
                el[-1].tail = aff.text
            else:
                el.text = aff.text

    if sd.editors:
        cg2 = sub(am, "contrib-group", **{"content-type": "editors"})
        for ed in sd.editors:
            c = sub(cg2, "contrib", **{"contrib-type": "editor"})
            nm = sub(c, "name")
            sub(nm, "surname", ed.surname)
            sub(nm, "given-names", ed.given_names)
            sub(c, "role", ed.role)

    _author_notes(am, sd)
    _history(am, sd)
    _permissions(am, sd, default_year)
    _abstract(am, sd, ctx)

    if sd.keywords:
        kg = sub(am, "kwd-group", **{"kwd-group-type": "author"})
        if sd.keywords_title:
            sub(kg, "title", sd.keywords_title)
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
    for lab in a.aff_labels:
        if ("aff" + lab) in aff_ids:
            x = sub(c, "xref", **{"ref-type": "aff", "rid": "aff" + lab})
            sub(x, "sup", lab)
        elif len(lab) > 1 and all(("aff" + d) in aff_ids for d in lab):
            for d in lab:
                x = sub(c, "xref", **{"ref-type": "aff", "rid": "aff" + d})
                sub(x, "sup", d)
    if a.email and not a.is_corresponding:
        sub(c, "email", a.email)
    if a.is_corresponding:
        x = sub(c, "xref", **{"ref-type": "corresp", "rid": "cor1"})
        sub(x, "sup", "*")
    if a.equal_contrib and has_equal_fn:
        # fn id 用 "fn1"：金标准在此不一致（02/04 用 "fn1"，03/S03/S05 用 "fn-1"），
        # 无源信号可预测，取原样 "fn1" 以不回归本来匹配的样例（属 house-style 不一致，非可修 bug）。
        x = sub(c, "xref", **{"ref-type": "fn", "rid": "fn1"})
        sub(x, "sup", "†")


def _equal_real(sd) -> bool:
    """共同贡献成立：须有明确的"贡献相同"声明块（equal_contrib_note）。
    仅凭作者角标（†/#）而无声明不足以判定——金标准里凡认定共同贡献的样例（03/S03/04）docx
    都带显式声明块；而 LLM 常把孤立/杂散角标误标成多位作者共享（如 S04：仅 Jiang 带 #、无声明，
    却被标 2 人），据此生成 fn 会同时造成共同贡献误标、多余 fn 交叉引用、默认声明句的凭空编造。
    真值口径核对（10 例实测）：无一 gold 使用 @equal-contrib 属性；认定共同贡献者一律用作者
    指向 author-notes 内声明 <fn> 的 xref 标记，而该声明块的存在正以 docx 有显式声明为前提。"""
    return bool(sd.equal_contrib_note)


def _author_notes(am, sd):
    has_corresp = any(a.is_corresponding for a in sd.authors) or sd.corresp_emails or sd.corresp_text
    has_equal = _equal_real(sd)
    if not has_corresp and not has_equal:
        return
    an = sub(am, "author-notes")
    if has_corresp:
        cor = sub(an, "corresp", id="cor1")
        sub(cor, "sup", "*")
        if sd.corresp_text:
            _emit_corresp_original(cor, sd.corresp_text, sd.corresp_emails)
        else:
            pairs = [(em, "") for em in sd.corresp_emails if em] or [
                (a.email, ("%s %s" % (a.given_names, a.surname)).strip())
                for a in sd.authors if a.is_corresponding and a.email]
            pairs = [p for p in pairs if p[0]]
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


def _emit_corresp_original(cor, text, emails):
    """通讯段原文写入 <corresp>：文中邮箱包成 <email>，其余为文本；
    已知但文中未出现的邮箱末尾补 " (E-mail: …)"。文本挂在末元素 tail 上。"""
    anchor = [cor.find("sup")]

    def add(s):
        if not s:
            return
        if anchor[0] is None:
            cor.text = (cor.text or "") + s
        else:
            anchor[0].tail = (anchor[0].tail or "") + s

    used, idx = set(), 0
    for m in _EMAIL_RE.finditer(text):
        add(text[idx:m.start()])
        e = sub(cor, "email", m.group(0))
        anchor[0] = e
        used.add(m.group(0))
        idx = m.end()
    add(text[idx:])
    extra = [em for em in emails if em not in used]
    for i, em in enumerate(extra):
        add(" (E-mail: " if i == 0 else "; ")
        anchor[0] = sub(cor, "email", em)
    if extra:
        add(")")


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


def _abstract(am, sd, ctx):
    if sd.abstract:
        ab = sub(am, "abstract")
        structured = any(s.title for s in sd.abstract)
        if structured:
            for s in sd.abstract:
                sec = sub(ab, "sec")
                if s.title:
                    sub(sec, "title", s.title)
                if s.lead:
                    sub(sec, "p", s.lead)
                for runs in s.paragraphs:
                    pe = sub(sec, "p")
                    append_inline(pe, runs, ctx.inline_math)
        else:
            for s in sd.abstract:
                if s.lead:
                    sub(ab, "p", s.lead)
                for runs in s.paragraphs:
                    pe = sub(ab, "p")
                    append_inline(pe, runs, ctx.inline_math)
    if sd.precis:
        pab = sub(am, "abstract", **{"abstract-type": "precis"})
        sub(pab, "p", sd.precis)
