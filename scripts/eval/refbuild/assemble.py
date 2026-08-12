"""按决策单从块清单取字，装配 JATS 1.3 结构参考。

铁律：正文一个字都不重打，全部按 idx 从 docx 原文取回。决策单里出现的短字段
（姓名/角标/日期/邮箱）逐个做子串校验——不是源块原文的子串就报错，物理杜绝编造。
"""
import io
import json
import os
import re
import unicodedata
import zipfile

from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) \
    if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == "scratchpad" \
    else os.getcwd()
ROOT = "/home/denggf/学术期刊结构化技术创新大赛"
XLINK = "http://www.w3.org/1999/xlink"
MML = "http://www.w3.org/1998/Math/MathML"
NSMAP = {"mml": MML, "xlink": XLINK}
DOCTYPE = ('<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing '
           'DTD v1.3 20210610//EN" '
           '"https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd">')


class BuildError(Exception):
    pass


# ---------------- 取字与校验 ----------------

def norm(s):
    """只折叠空白，用于子串校验。不动任何实际字符。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


class Source:
    def __init__(self, key):
        self.key = key
        with open("%s/tmp/refbuild/%s.blocks.json" % (ROOT, key), encoding="utf-8") as f:
            self.data = json.load(f)
        self.by_idx = {b["idx"]: b for b in self.data["blocks"]}
        self.zip = zipfile.ZipFile("%s/样例数据/%s/初始文件.docx" % (ROOT, key))
        self.used = set()
        self.errors = []

    def block(self, idx):
        b = self.by_idx.get(idx)
        if b is None:
            raise BuildError("idx %s 不存在" % idx)
        self.used.add(idx)
        return b

    def text(self, idx):
        return self.block(idx)["text"]

    def check(self, value, idx, what):
        """子串校验：value 必须逐字出现在 idx 块的原文里（仅折叠空白）。"""
        if value is None or value == "":
            return value
        hay = norm(self.text(idx))
        if norm(value) not in hay:
            self.errors.append("【编造】%s: %r 不是 idx %d 原文的子串\n     原文: %r"
                               % (what, value, idx, hay[:160]))
        return value

    def media_blob(self, target):
        name = target if target.startswith("word/") else "word/" + target
        return self.zip.read(name)


# ---------------- 内联渲染：上标/下标/斜体/粗体/公式 ----------------

def _fmt_key(r):
    if r.get("slot"):
        return ("__slot__", r.get("slot"), r.get("n"))
    return (r.get("b"), r.get("i"), r.get("va"))


def inline(parent, block, src, slot_cb=None, skip_prefix=0, only_text=None):
    """把一块的 run 序列渲染成 parent 下的内联内容，保留格式。
    skip_prefix：跳过开头 n 个字符（剥 "Figure 1." 这类标签前缀）。
    only_text：只渲染这段子串（用于摘要按标记切分、关键词切分）。
    """
    runs = block["runs"]
    if only_text is not None:
        # 在拼接文本里定位子串，按字符切 run
        full = "".join(r["t"] for r in runs)
        pos = full.find(only_text)
        if pos < 0:
            raise BuildError("子串未在块内找到: %r" % only_text[:60])
        runs = _slice_runs(runs, pos, pos + len(only_text))
    elif skip_prefix:
        runs = _slice_runs(runs, skip_prefix, None)

    # 版式 vs 语义：某个格式维度若在整块所有 run 上取值一致，那是 Word 模板的版式
    # （整行标题加粗、整行关键词斜体），不是"与周围不同的强调"，不产出内联标记；
    # 只有块内有差异的维度才是语义（基因名斜体、角标上标、TiO2 的下标），必须保留。
    flat = _uniform_dims(block["runs"])
    if flat:
        runs = [{**r, **{d: None for d in flat}} for r in runs]

    last = None
    for r in _merge(runs):
        if r.get("slot"):
            el = slot_cb(r) if slot_cb else None
            if el is not None:
                parent.append(el)
                last = el
            continue
        t = r["t"]
        if not t:
            continue
        el = None
        if r.get("va") == "superscript":
            el = etree.SubElement(parent, "sup")
        elif r.get("va") == "subscript":
            el = etree.SubElement(parent, "sub")
        elif r.get("i") and r.get("b"):
            el = etree.SubElement(parent, "bold")
            el = etree.SubElement(el, "italic")
        elif r.get("i"):
            el = etree.SubElement(parent, "italic")
        elif r.get("b"):
            el = etree.SubElement(parent, "bold")
        if el is not None:
            el.text = t
            last = el
        else:
            if last is None:
                parent.text = (parent.text or "") + t
            else:
                last.tail = (last.tail or "") + t
    return parent


def _uniform_dims(all_runs):
    """返回在整块内取值完全一致的格式维度名。空块或单一 run 的块不判（无"周围"可比）。"""
    real = [r for r in all_runs if r.get("t")]
    if not real:
        return ()
    out = []
    for dim in ("b", "i", "va"):
        vals = {r.get(dim) for r in real}
        if len(vals) == 1 and vals != {None}:
            out.append(dim)
    return tuple(out)


def _slice_runs(runs, a, b):
    out, pos = [], 0
    for r in runs:
        t = r["t"]
        s, e = pos, pos + len(t)
        pos = e
        lo = max(s, a)
        hi = min(e, b) if b is not None else e
        if lo < hi:
            out.append({**r, "t": t[lo - s:hi - s]})
    return out


def _merge(runs):
    out = []
    for r in runs:
        if out and _fmt_key(out[-1]) == _fmt_key(r):
            out[-1] = {**out[-1], "t": out[-1]["t"] + r["t"]}
        else:
            out.append(dict(r))
    return out


# ---------------- 公式 ----------------

_XSLT = None


def omml_to_mathml(omml_xml):
    global _XSLT
    if _XSLT is None:
        _XSLT = etree.XSLT(etree.parse("%s/src/word2jats/resources/OMML2MML.XSL" % ROOT))
    # OMML 片段需带命名空间声明
    wrapped = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
               ' xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
               '<w:body><w:p>%s</w:p></w:body></w:document>') % omml_xml
    res = _XSLT(etree.fromstring(wrapped.encode()))
    m = res.getroot()
    if m is None:
        return None
    for el in m.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{%s}" % MML):
            if etree.QName(el).localname == "math":
                return el
    return None


def ole_tex(blob):
    """MathType/公式编辑器 OLE → 内嵌的 TeX 源。用 TeX 输入模式写的公式，MTEF 里
    原样存着 LaTeX；没有则返回 None（不去逆向 MTEF 二进制，解错就是改公式）。"""
    import olefile
    try:
        f = olefile.OleFileIO(io.BytesIO(blob))
        if not f.exists("Equation Native"):
            return None
        mtef = f.openstream("Equation Native").read()[28:]
    except Exception:
        return None
    m = re.search(rb"TeX Input Language(.{0,600}?)(?:WinAllBasicCodePages|\x00\x00\x00)",
                  mtef, re.S)
    if not m:
        return None
    tex = "".join(chr(c) for c in m.group(1) if 32 <= c < 127).strip()
    # 记录头部会带一两个长度字节，剥掉前导非 TeX 字符
    tex = re.sub(r"^[^\\a-zA-Z0-9(\[]+", "", tex)
    return tex or None


def latex_to_mathml(tex):
    """LaTeX → MathML。用 latex2mathml（确定性转换，无猜测）。"""
    import latex2mathml.converter as C
    s = C.convert(tex)
    el = etree.fromstring(s.encode())
    return el


# ---------------- 装配 ----------------

def load_journal(jid):
    import yaml
    with open("%s/src/word2jats/resources/journals.yaml" % ROOT, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    j = d["journals"][jid]
    return d, j


def build_front(art, dec, src, jid):
    conf, j = load_journal(jid)
    front = etree.SubElement(art, "front")

    jm = etree.SubElement(front, "journal-meta")
    etree.SubElement(jm, "journal-id", **{"journal-id-type": "publisher-id"}).text = j["journal-id"]
    jtg = etree.SubElement(jm, "journal-title-group")
    etree.SubElement(jtg, "journal-title").text = j["journal-title"]
    etree.SubElement(jtg, "abbrev-journal-title",
                     **{"abbrev-type": "publisher"}).text = j["abbrev-publisher"]
    etree.SubElement(jtg, "abbrev-journal-title",
                     **{"abbrev-type": "pubmed"}).text = j["abbrev-pubmed"]
    if j.get("issn-ppub"):
        etree.SubElement(jm, "issn", **{"pub-type": "ppub"}).text = j["issn-ppub"]
    if j.get("issn-epub"):
        etree.SubElement(jm, "issn", **{"pub-type": "epub"}).text = j["issn-epub"]
    pub = etree.SubElement(jm, "publisher")
    etree.SubElement(pub, "publisher-name").text = conf["publisher"]

    am = etree.SubElement(front, "article-meta")
    # 没有 DOI：投稿件不存在 DOI，不产出 article-id（忠实原则）
    if dec.get("subject"):
        ac = etree.SubElement(am, "article-categories")
        sg = etree.SubElement(ac, "subj-group", **{"subj-group-type": "heading"})
        s = dec["subject"]
        src.check(s.get("text"), s["idx"], "subject")
        sub = etree.SubElement(sg, "subject")
        inline(sub, src.block(s["idx"]), src)

    tg = etree.SubElement(am, "title-group")
    at = etree.SubElement(tg, "article-title")
    for n, i in enumerate(dec["title"]["idx"]):
        if n:
            at_text_join(at, " ")
        inline(at, src.block(i), src)

    build_contribs(am, dec, src)
    build_history(am, dec, src)
    build_permissions(am, conf, dec)
    build_abstract(am, dec, src)
    build_keywords(am, dec, src)
    return front


def at_text_join(el, sep):
    kids = list(el)
    if kids:
        kids[-1].tail = (kids[-1].tail or "") + sep
    else:
        el.text = (el.text or "") + sep


def build_contribs(am, dec, src):
    cg = etree.SubElement(am, "contrib-group")
    for a in dec["authors"]:
        c = etree.SubElement(cg, "contrib", **{"contrib-type": "author"})
        if a.get("equal"):
            c.set("equal-contrib", "yes")
        if a.get("orcid"):
            oidx = a.get("orcid_idx", a["src_idx"])
            src.check(a["orcid"], oidx, "ORCID")
            oid = a["orcid"]
            etree.SubElement(c, "contrib-id", **{"contrib-id-type": "orcid"}).text = (
                oid if oid.startswith("http") else "https://orcid.org/" + oid)
        nm = etree.SubElement(c, "name")
        src.check(a["surname"], a["src_idx"], "作者姓")
        src.check(a["given"], a["src_idx"], "作者名")
        etree.SubElement(nm, "surname").text = a["surname"]
        etree.SubElement(nm, "given-names").text = a["given"]
        for lab in a.get("aff_labels") or []:
            aff = next((x for x in dec["affs"] if x["label"] == lab), None)
            if aff is None:
                src.errors.append("作者 %s 的角标 %r 没有对应单位" % (a["surname"], lab))
                continue
            x = etree.SubElement(c, "xref", **{"ref-type": "aff", "rid": aff["id"]})
            etree.SubElement(x, "sup").text = lab
        if a.get("corresp"):
            cid = (dec["corresp"][0]["id"] if dec.get("corresp") else "cor1")
            for co in dec.get("corresp") or []:
                if co.get("person") and a["surname"] in (co["person"] or ""):
                    cid = co["id"]
                    break
            seen_ids = {c["id"] for c in dec.get("corresp") or []}
            if cid not in seen_ids and seen_ids:
                cid = sorted(seen_ids)[0]
            x = etree.SubElement(c, "xref", **{"ref-type": "corresp", "rid": cid})
            etree.SubElement(x, "sup").text = "*"

    ed = dec.get("editor")
    if ed:
        eg = etree.SubElement(am, "contrib-group", **{"content-type": "editor"})
        c = etree.SubElement(eg, "contrib", **{"contrib-type": "editor"})
        src.check(ed["surname"], ed["idx"], "编辑姓")
        src.check(ed["given"], ed["idx"], "编辑名")
        nm = etree.SubElement(c, "name")
        etree.SubElement(nm, "surname").text = ed["surname"]
        etree.SubElement(nm, "given-names").text = ed["given"]
        etree.SubElement(c, "role").text = "Academic Editor"

    for aff in dec["affs"]:
        el = etree.SubElement(am, "aff", id=aff["id"])
        b = src.block(aff["idx"])
        # 前导角标数字/字母渲染成 <sup>，其余原样
        lab = aff["label"]
        t = b["text"]
        if t.startswith(lab):
            etree.SubElement(el, "sup").text = lab
            inline(el, b, src, skip_prefix=len(lab))
        else:
            inline(el, b, src)

    if dec.get("corresp"):
        an = etree.SubElement(am, "author-notes")
        # 同一源块里的多个通讯作者合成一个 corresp（10 例 house-style：每篇一个 corN）
        groups = {}
        for co in dec["corresp"]:
            groups.setdefault(co["idx"], []).append(co)
        for idx, cos in groups.items():
            el = etree.SubElement(an, "corresp", id=cos[0]["id"])
            b = src.block(idx)
            inline(el, b, src)
            for co in cos:
                src.check(co.get("email"), idx, "通讯邮箱")
                _wrap_email(el, co.get("email"))


def _wrap_email(el, email):
    if not email:
        return
    for e in el.iter("email"):
        if (e.text or "") == email:
            return
    for node in [el] + list(el.iter()):
        if node.text and email in node.text:
            pre, _, post = node.text.partition(email)
            node.text = pre
            e = etree.Element("email")
            e.text = email
            e.tail = post
            node.insert(0, e)
            return
        for ch in list(node):
            if ch.tail and email in ch.tail:
                pre, _, post = ch.tail.partition(email)
                ch.tail = pre
                e = etree.Element("email")
                e.text = email
                e.tail = post
                node.insert(list(node).index(ch) + 1, e)
                return


def build_history(am, dec, src):
    h = dec.get("history") or {}
    if not h:
        return
    hist = etree.SubElement(am, "history")
    for dtype in ("received", "rev-recd", "accepted"):
        d = h.get(dtype)
        if not d:
            continue
        el = etree.SubElement(hist, "date", **{"date-type": dtype})
        for part in ("day", "month", "year"):
            v = d.get(part)
            if not v:
                continue
            if part == "month":
                _check_month(src, v, d["idx"], dtype)
            else:
                src.check(v, d["idx"], "%s.%s" % (dtype, part))
            etree.SubElement(el, part).text = v


_MONTHS = {"1": ("january", "jan"), "2": ("february", "feb"), "3": ("march", "mar"),
           "4": ("april", "apr"), "5": ("may",), "6": ("june", "jun"),
           "7": ("july", "jul"), "8": ("august", "aug"), "9": ("september", "sep", "sept"),
           "10": ("october", "oct"), "11": ("november", "nov"), "12": ("december", "dec")}


def _check_month(src, v, idx, dtype):
    """<month> 用数字（JATS 惯例，10 例结构参考一致）。docx 写数字则直接子串校验；
    写英文月名则校验该月名在原文里——名→数是格式规范化，不是改内容。"""
    hay = norm(src.text(idx)).casefold()
    n = str(int(v)) if str(v).strip().isdigit() else None
    if n and any(w in hay for w in _MONTHS.get(n, ())):
        return
    src.check(v, idx, "%s.month" % dtype)


def build_permissions(am, conf, dec):
    y = None
    h = dec.get("history") or {}
    for k in ("accepted", "rev-recd", "received"):
        if h.get(k) and h[k].get("year"):
            y = h[k]["year"]
            break
    p = etree.SubElement(am, "permissions")
    if y:
        etree.SubElement(p, "copyright-statement").text = "© %s The Author(s). Published by %s." % (
            y, conf["publisher"])
        etree.SubElement(p, "copyright-year").text = y
    lic = etree.SubElement(p, "license", **{
        "{%s}href" % XLINK: "https://creativecommons.org/licenses/by/4.0/"})
    lp = etree.SubElement(lic, "license-p")
    lp.text = ("This is an open access article under the CC BY 4.0 license.")


def build_abstract(am, dec, src):
    ab = dec.get("abstract")
    if not ab:
        return
    if ab.get("heading_idx") is not None:
        src.block(ab["heading_idx"])
    el = etree.SubElement(am, "abstract")
    if not ab.get("structured"):
        for part in ab["parts"]:
            p = etree.SubElement(el, "p")
            inline(p, src.block(part["idx"]), src)
        return
    # 结构化：同一块按 title_marker 切分
    parts = ab["parts"]
    by_block = {}
    for part in parts:
        by_block.setdefault(part["idx"], []).append(part)
    for idx, group in by_block.items():
        b = src.block(idx)
        full = b["text"]
        marks = []
        for part in group:
            m = part["title_marker"]
            pos = full.find(m)
            if pos < 0:
                raise BuildError("摘要标记 %r 未在 idx %d 找到" % (m, idx))
            marks.append((pos, m))
        marks.sort()
        for n, (pos, m) in enumerate(marks):
            end = marks[n + 1][0] if n + 1 < len(marks) else len(full)
            sec = etree.SubElement(el, "sec")
            etree.SubElement(sec, "title").text = m.rstrip(": ").strip() + ":"
            body = full[pos + len(m):end].strip()
            if body:
                p = etree.SubElement(sec, "p")
                inline(p, b, src, only_text=body)


def build_keywords(am, dec, src):
    kw = dec.get("keywords")
    if not kw:
        return
    b = src.block(kw["idx"])
    t = b["text"]
    pre = kw.get("prefix") or ""
    if pre and t.startswith(pre):
        t = t[len(pre):]
    g = etree.SubElement(am, "kwd-group")
    for piece in t.split(kw.get("sep") or ";"):
        piece = piece.strip().rstrip(".")
        if not piece:
            continue
        el = etree.SubElement(g, "kwd")
        try:
            inline(el, b, src, only_text=piece)
        except BuildError:
            el.text = piece
    if kw.get("heading_idx") is not None:
        src.block(kw["heading_idx"])


# ---------------- body ----------------

def build_body(art, dec, src, figs_by_anchor, tables_by_anchor, formulas_by_idx):
    body = etree.SubElement(art, "body")
    stack = [(0, body)]          # (level, element)
    counters = {}

    def _idxs(*vals):
        out = []
        for v in vals:
            if isinstance(v, list):
                out.extend(x for x in v if isinstance(x, int))
            elif isinstance(v, int):
                out.append(v)
        return out

    consumed = set()
    for f in dec.get("figs") or []:
        consumed.update(_idxs(f.get("caption_idx"), f.get("graphic_idx")))
    for t in dec.get("tables") or []:
        consumed.update(_idxs(t.get("caption_idx"), t.get("table_idx"),
                              t.get("footnote_idx")))

    pending = sorted((i, d) for i, d in
                     ((i, (f or {}).get("disp") or []) for i, f in formulas_by_idx.items()) if d)
    pi = 0

    def _flush(upto, parent):
        nonlocal pi
        while pi < len(pending) and pending[pi][0] <= upto:
            for el in pending[pi][1]:
                parent.append(el)
            pi += 1

    for item in dec.get("body") or []:
        idx, role = item["idx"], item["role"]
        if role != "sec-title":
            _flush(idx - 1, stack[-1][1])
        if role == "sec-title":
            lvl = item["level"]
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1]
            sec = etree.SubElement(parent, "sec", id=item["id"])
            title = etree.SubElement(sec, "title")
            inline(title, src.block(idx), src)
            stack.append((lvl, sec))
            continue
        parent = stack[-1][1]
        if idx not in consumed:
            b = src.block(idx)
            fs = formulas_by_idx.get(idx) or {}
            p = etree.SubElement(parent, "p")
            _render_para(p, b, src, fs)
            if not "".join(p.itertext()).strip() and not list(p):
                parent.remove(p)
            _flush(idx, parent)
        for f in figs_by_anchor.pop(idx, []):
            parent.append(f)
        for t in tables_by_anchor.pop(idx, []):
            parent.append(t)

    _flush(10 ** 9, stack[-1][1])

    # 兜底：决策单未把某图/表的锚定块列进 body 时，按 idx 顺序补到最后一个 sec
    leftover = sorted([(k, v, "fig") for k, v in figs_by_anchor.items()]
                      + [(k, v, "tbl") for k, v in tables_by_anchor.items()],
                      key=lambda x: (x[0] is None, x[0]))
    if leftover:
        tail = stack[-1][1] if len(stack) > 1 else body
        for _, els, _kind in leftover:
            for el in els:
                tail.append(el)
    return body


def _render_para(p, b, src, formulas):
    """段落内联：文字 + 内嵌公式。行内公式按 run 位置插回原处。"""
    fs = formulas or {}
    pool = list(fs.get("inline") or [])

    def slot_cb(r):
        if r.get("slot") in ("ole", "omml") and pool:
            return pool.pop(0)
        return None

    inline(p, b, src, slot_cb=slot_cb)
    for el in pool:
        p.append(el)


# ---------------- 图 / 表 ----------------

def _fig_blocks(src, idx):
    """取图的块集合。若 idx 是表格块，图在它的单元格里——多张子图用表格排版对齐是
    常见做法（X02 的 Fig. 1 由 7 张 Origin 子图拼成），必须连表内段落一起收。"""
    blk = src.block(idx)
    if blk["kind"] != "tbl":
        return [blk]
    out = [blk]
    for b in src.data["blocks"]:
        if b["in_table"] == idx:
            src.used.add(b["idx"])
            out.append(b)
    return out


def build_figs(dec, src, out_dir, article_id):
    """返回 {锚定段 idx: [fig 元素]}，同时外部化图片字节。"""
    anchored = {}
    for n, f in enumerate(dec.get("figs") or [], 1):
        fig = etree.Element("fig", id=f["id"])
        if f.get("label"):
            etree.SubElement(fig, "label").text = f["label"]
        _c = f.get("caption_idx")
        _c = _c[0] if isinstance(_c, list) and _c else _c
        if isinstance(_c, int):
            cap = etree.SubElement(fig, "caption")
            cb = src.block(_c)
            pre = f.get("label") or ""
            t = cb["text"]
            skip = 0
            m = re.match(r"^\s*(Figure|Fig\.?|Scheme)\s*\d+\s*[.:]?\s*", t, re.I)
            if m:
                skip = m.end()
            ptxt = etree.SubElement(cap, "p")
            inline(ptxt, cb, src, skip_prefix=skip)
        gidx = f.get("graphic_idx")
        gidx = [gidx] if isinstance(gidx, int) else (gidx or [])
        meds = []
        for gi in gidx:
            for blk in _fig_blocks(src, gi):
                meds.extend(blk["media"])
                # Origin / ChemDraw 这类插图在 docx 里是 OLE 对象，图是它的预览图元，
                # 不走 w:drawing；只认 drawing/pict 会把这些图整个漏掉
                for o in blk["ole"]:
                    if (o.get("progId") or "").startswith("Equation"):
                        continue
                    if o.get("img_target"):
                        meds.append({"kind": "ole", "target": o["img_target"]})
        # 同一文件被引用多次（子图重复排版）只外部化一次
        seen_t, uniq = set(), []
        for m in meds:
            if m["target"] in seen_t:
                continue
            seen_t.add(m["target"])
            uniq.append(m)
        meds = uniq
        if meds:
            for k, med in enumerate(meds, 1):
                blob = src.media_blob(med["target"])
                ext = _ext(blob, med["target"])
                rel = "%s/fig-%02d%s" % (article_id, n, ext) if k == 1 else \
                      "%s/fig-%02d-%d%s" % (article_id, n, k, ext)
                _write(out_dir, rel, blob)
                g = etree.SubElement(fig, "graphic", **{"{%s}href" % XLINK: rel})
                g.set("id", "%s.g%d" % (f["id"], k))
        anchor = f.get("anchor_idx", f.get("caption_idx"))
        if isinstance(anchor, list):
            anchor = anchor[0] if anchor else None
        anchored.setdefault(anchor, []).append(fig)
    return anchored


def _ext(blob, name):
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if blob[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if blob[:4] == b"\xd7\xcd\xc6\x9a" or blob[:4] == b"\x01\x00\x00\x00":
        return ".wmf" if blob[:4] == b"\xd7\xcd\xc6\x9a" else ".emf"
    if blob[:5] == b"<?xml" or b"<svg" in blob[:200]:
        return ".svg"
    return os.path.splitext(name)[1] or ".bin"


def _write(out_dir, rel, blob):
    p = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(blob)


def build_tables(dec, src, out_dir, article_id):
    anchored = {}
    for n, t in enumerate(dec.get("tables") or [], 1):
        wrap = etree.Element("table-wrap", id=t["id"])
        if t.get("label"):
            etree.SubElement(wrap, "label").text = t["label"]
        _tc = t.get("caption_idx")
        _tc = _tc[0] if isinstance(_tc, list) and _tc else _tc
        if isinstance(_tc, int):
            cap = etree.SubElement(wrap, "caption")
            cb = src.block(_tc)
            m = re.match(r"^\s*Table\s*\d+\s*[.:]?\s*", cb["text"], re.I)
            ptxt = etree.SubElement(cap, "p")
            inline(ptxt, cb, src, skip_prefix=m.end() if m else 0)
        _ti = t.get("table_idx")
        _ti = _ti[0] if isinstance(_ti, list) and _ti else _ti
        tb = src.block(_ti) if isinstance(_ti, int) else None
        if tb is not None and tb.get("table"):
            _render_table(wrap, tb["table"])
        elif tb is not None and tb["media"]:
            blob = src.media_blob(tb["media"][0]["target"])
            rel = "%s/table-%02d%s" % (article_id, n, _ext(blob, tb["media"][0]["target"]))
            _write(out_dir, rel, blob)
            g = etree.SubElement(wrap, "graphic", **{"{%s}href" % XLINK: rel})
            g.set("id", "%s.g1" % t["id"])
        for fi in t.get("footnote_idx") or []:
            foot = wrap.find("table-wrap-foot")
            if foot is None:
                foot = etree.SubElement(wrap, "table-wrap-foot")
            fn = etree.SubElement(foot, "fn", id="%s-fn%d" % (t["id"], len(foot) + 1))
            p = etree.SubElement(fn, "p")
            inline(p, src.block(fi), src)
        _a = t.get("anchor_idx", t.get("caption_idx"))
        if isinstance(_a, list):
            _a = _a[0] if _a else None
        anchored.setdefault(_a, []).append(wrap)
    return anchored


def _cell(el, c):
    """单元格内容按 run 渲染，保留上标/下标/斜体——docx 里 "Moderate"+上标"a" 是
    表脚注角标，直接拼文本会粘成 "Moderatea"。"""
    runs = c.get("runs")
    if runs:
        inline(el, {"runs": runs}, None)   # 单元格自成一块，版式判断按格内
    elif c["t"]:
        el.text = c["t"]


def _render_table(wrap, tbl):
    rows = tbl["rows"]
    ncols = max((sum(c["span"] for c in r["cells"]) for r in rows), default=0)
    table = etree.SubElement(wrap, "table")
    cg = etree.SubElement(table, "colgroup")
    for _ in range(ncols):
        etree.SubElement(cg, "col", width="%.1f%%" % (100.0 / ncols) if ncols else "100%")
    hdr = [r for r in rows if r["header"]]
    body_rows = [r for r in rows if not r["header"]]
    if not hdr and rows:
        hdr, body_rows = rows[:1], rows[1:]
    if hdr:
        thead = etree.SubElement(table, "thead")
        for r in hdr:
            tr = etree.SubElement(thead, "tr")
            for c in r["cells"]:
                th = etree.SubElement(tr, "th", scope="col")
                if c["span"] > 1:
                    th.set("colspan", str(c["span"]))
                _cell(th, c)
    tbody = etree.SubElement(table, "tbody")
    for r in body_rows:
        tr = etree.SubElement(tbody, "tr")
        for c in r["cells"]:
            td = etree.SubElement(tr, "td")
            if c["span"] > 1:
                td.set("colspan", str(c["span"]))
            _cell(td, c)


# ---------------- back ----------------

def build_back(art, dec, src, refs_el):
    back = etree.SubElement(art, "back")
    for item in dec.get("back") or []:
        kind = item["kind"]
        if kind == "ack":
            el = etree.SubElement(back, "ack")
            if item.get("title_idx") is not None:
                t = etree.SubElement(el, "title")
                inline(t, src.block(item["title_idx"]), src)
            for i in item.get("body_idx") or []:
                p = etree.SubElement(el, "p")
                inline(p, src.block(i), src)
        elif kind == "sec":
            el = etree.SubElement(back, "sec")
            if item.get("title_idx") is not None:
                t = etree.SubElement(el, "title")
                inline(t, src.block(item["title_idx"]), src)
            for i in item.get("body_idx") or []:
                p = etree.SubElement(el, "p")
                inline(p, src.block(i), src)
        elif kind == "fn-group":
            el = etree.SubElement(back, "fn-group")
            for n, i in enumerate(item.get("body_idx") or [], 1):
                fn = etree.SubElement(el, "fn", id="fn%d" % n)
                p = etree.SubElement(fn, "p")
                inline(p, src.block(i), src)
        elif kind == "app":
            ag = back.find("app-group")
            if ag is None:
                ag = etree.SubElement(back, "app-group")
            el = etree.SubElement(ag, "app")
            if item.get("title_idx") is not None:
                t = etree.SubElement(el, "title")
                inline(t, src.block(item["title_idx"]), src)
            for i in item.get("body_idx") or []:
                p = etree.SubElement(el, "p")
                inline(p, src.block(i), src)
    if refs_el is not None:
        # ref-list 按 JATS 惯例放在 back 内（10 例均如此，位置在声明小节之后）
        back.append(refs_el)
    return back


# ---------------- 交叉引用 ----------------

def apply_xrefs(root, n_refs, fig_ids, table_ids):
    """把正文里的 [1]、[3-5]、Figure 1、Table 2 转成 <xref>。纯机械正则，
    只在编号确实存在对应目标时才转，否则原样保留（不造悬空引用）。"""
    bibr = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
    figre = re.compile(r"\b(Fig\.?|Figure)\s*(\d+)", re.I)
    tabre = re.compile(r"\bTable\s*(\d+)", re.I)

    def split_text(node, attr):
        s = getattr(node, attr)
        if not s:
            return
        pieces = _tokenize(s, n_refs, fig_ids, table_ids, bibr, figre, tabre)
        if pieces is None:
            return
        setattr(node, attr, "")
        if attr == "text":
            anchor, insert_at = node, 0
            head = pieces[0] if isinstance(pieces[0], str) else None
            node.text = head or None
            rest = pieces[1:] if head is not None else pieces
            pos = 0
            for pc in rest:
                if isinstance(pc, str):
                    kids = list(node)
                    if kids and pos > 0:
                        kids[pos - 1].tail = (kids[pos - 1].tail or "") + pc
                    else:
                        node.text = (node.text or "") + pc
                else:
                    node.insert(pos, pc)
                    pos += 1
        else:
            parent = node.getparent()
            at = list(parent).index(node)
            head = pieces[0] if isinstance(pieces[0], str) else None
            node.tail = head or None
            rest = pieces[1:] if head is not None else pieces
            off = 1
            for pc in rest:
                if isinstance(pc, str):
                    prev = list(parent)[at + off - 1]
                    prev.tail = (prev.tail or "") + pc
                else:
                    parent.insert(at + off, pc)
                    off += 1

    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname in ("element-citation", "mixed-citation", "label"):
            continue
        if el.tag.startswith("{%s}" % MML):
            continue
        split_text(el, "text")
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or el.getparent() is None:
            continue
        anc = [etree.QName(a).localname for a in el.iterancestors() if isinstance(a.tag, str)]
        if "element-citation" in anc or "mixed-citation" in anc or "ref-list" in anc:
            continue
        split_text(el, "tail")


def _tokenize(s, n_refs, fig_ids, table_ids, bibr, figre, tabre):
    hits = []
    for m in bibr.finditer(s):
        hits.append((m.start(), m.end(), "bibr", m.group(1)))
    for m in figre.finditer(s):
        hits.append((m.start(), m.end(), "fig", m.group(2), m.group(0)))
    for m in tabre.finditer(s):
        hits.append((m.start(), m.end(), "table", m.group(1), m.group(0)))
    if not hits:
        return None
    hits.sort()
    out, pos = [], 0
    for h in hits:
        a, b, kind = h[0], h[1], h[2]
        if a < pos:
            continue
        if a > pos:
            out.append(s[pos:a])
        if kind == "bibr":
            nums = _expand(h[3])
            if not nums or any(n < 1 or n > n_refs for n in nums):
                out.append(s[a:b])
            else:
                out.append("[")
                for k, n in enumerate(nums):
                    if k:
                        out.append(",")
                    x = etree.Element("xref", **{"ref-type": "bibr", "rid": "b%d" % n})
                    x.text = str(n)
                    out.append(x)
                out.append("]")
        else:
            n = int(h[3])
            ids = fig_ids if kind == "fig" else table_ids
            rid = "%s%d" % ("F" if kind == "fig" else "T", n)
            if rid not in ids:
                out.append(s[a:b])
            else:
                x = etree.Element("xref", **{"ref-type": kind, "rid": rid})
                x.text = h[4]
                out.append(x)
        pos = b
    if pos < len(s):
        out.append(s[pos:])
    return out


def _expand(spec):
    nums = []
    for part in re.split(r"\s*,\s*", spec):
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b < a or b - a > 200:
                return None
            nums.extend(range(a, b + 1))
        elif part.strip().isdigit():
            nums.append(int(part))
        else:
            return None
    return nums


# ---------------- 出口 ----------------

def serialize(root):
    body = etree.tostring(root, pretty_print=True, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + DOCTYPE.encode() + b"\n" + body


def dtd_validate(xml_bytes):
    dtd = etree.DTD("%s/src/word2jats/resources/dtd/JATS-Publishing-1-3-MathML3-DTD/"
                    "JATS-journalpublishing1-3-mathml3.dtd" % ROOT)
    doc = etree.fromstring(xml_bytes, etree.XMLParser(load_dtd=False, no_network=True,
                                                      resolve_entities=False))
    ok = dtd.validate(doc)
    return ok, [str(e) for e in dtd.error_log]


def coverage_report(src, dec):
    """块覆盖闭合：每个顶层块要么被用，要么在 dropped 里写明理由。"""
    top = {b["idx"] for b in src.data["blocks"] if b["in_table"] is None}
    dropped = {d["idx"] for d in dec.get("dropped") or []}
    unexplained = sorted(top - src.used - dropped)
    # 空块自动豁免
    really = [i for i in unexplained if src.by_idx[i]["text"].strip()
              or src.by_idx[i]["media"] or src.by_idx[i]["omml"] or src.by_idx[i]["ole"]
              or src.by_idx[i]["kind"] == "tbl"]
    return {"n_top": len(top), "n_used": len(top & src.used), "n_dropped": len(dropped),
            "unexplained": really}


def check_graphics(src, out_dir, xml_bytes):
    """第四道出口校验（说明.md 第 6 条）：每个 <graphic xlink:href> 都要指向
    一个真实存在、能解码为真图、且字节与 docx 内嵌媒体逐字节相同的外部化文件。
    md5 一致是"字节忠实、不重编码"的硬证据。"""
    import hashlib
    root = etree.fromstring(xml_bytes, etree.XMLParser(load_dtd=False, no_network=True,
                                                       resolve_entities=False))
    docx_md5 = {}
    for name in src.zip.namelist():
        if name.startswith("word/media/"):
            docx_md5[hashlib.md5(src.zip.read(name)).hexdigest()] = name

    rows, bad = [], []
    seen = set()
    for g in root.iter():
        if not isinstance(g.tag, str):
            continue
        if etree.QName(g).localname not in ("graphic", "inline-graphic"):
            continue
        href = g.get("{%s}href" % XLINK) or ""
        if not href or href in seen:
            continue
        seen.add(href)
        path = os.path.join(out_dir, href)
        exists = os.path.exists(path)
        decodable = _decodable(path) if exists else False
        md5 = hashlib.md5(open(path, "rb").read()).hexdigest() if exists else ""
        origin = docx_md5.get(md5)
        rows.append({"href": href, "exists": exists, "decodable": decodable,
                     "md5": md5[:8], "docx_media": origin})
        if not exists:
            bad.append("%s 文件不存在" % href)
        elif not decodable:
            bad.append("%s 无法解码为真图" % href)
        elif origin is None:
            bad.append("%s 字节与 docx 任何内嵌媒体都不一致（疑似重编码）" % href)
    return {"n": len(rows), "rows": rows, "bad": bad}


def _decodable(path):
    """真图判定：能被 PIL 解码，或有合法图片魔数且非空壳（容 PIL 不支持的矢量格式）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            head = f.read(44)
        size = os.path.getsize(path)
    except OSError:
        return False
    known = (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\x08"[:8]
             or head[:8] == b"\x89PNG\r\n\x1a\n"
             or head[:4] in (b"II*\x00", b"MM\x00*") or head[:6] in (b"GIF87a", b"GIF89a")
             or head[:2] == b"BM" or head[:4] == b"\xd7\xcd\xc6\x9a"          # wmf
             or head[40:44] == b" EMF" or head[:4] == b"\x01\x00\x00\x00"      # emf
             or head[:5] == b"<?xml" or b"<svg" in head)
    return known and size > 64
