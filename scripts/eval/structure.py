"""L2 对位层(设计 §6.3):对冻结的 结构参考.xml,按语义键对齐、逐类产出命中/漏标/多标。

核心纪律:
- 按语义键对齐,不按位置(§3.6):作者按姓、章节按归一标题、图表按归一 caption、参考按归一字段。
- 参考文献按字段比(§3.3):person-group 姓集 / article-title / source / year / volume / fpage / lpage;
  容器是 element 还是 mixed **不作评分轴**——输出用 mixed 即字段全缺,按漏标计入,信号自然正确。
- 无加权总分(§3.7):每类给 命中/漏标(参考有输出无)/多标(输出有参考无)计数 + 缺陷条目。
100% 确定性。
"""
from collections import Counter

from lxml import etree

from .normalize import norm_value, norm_key, itertext

_PARSER = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def _ln(el):
    return etree.QName(el).localname


# ---------- 通用集合对齐:按 key 函数配对,返回命中/漏标/多标 + 值不符 ----------
def _align(ref_items, out_items, keyfn, cmpfn, cat):
    """ref_items/out_items: 元素列表。keyfn(el)->对齐键; cmpfn(ref_el,out_el)->[缺陷字段str]。"""
    ref_map, out_map = {}, {}
    for el in ref_items:
        ref_map.setdefault(keyfn(el), []).append(el)
    for el in out_items:
        out_map.setdefault(keyfn(el), []).append(el)
    hit = missing = extra = 0
    defects = []
    for k, rels in ref_map.items():
        ro = out_map.get(k, [])
        # 同键多实例按序配对
        for i, rel in enumerate(rels):
            if i < len(ro):
                hit += 1
                for fld in cmpfn(rel, ro[i]):
                    defects.append({"cat": cat, "kind": "value", "key": k[:80], "detail": fld})
            else:
                missing += 1
                defects.append({"cat": cat, "kind": "missing", "key": k[:80],
                                "detail": "参考有·输出无"})
    for k, oels in out_map.items():
        nr = len(ref_map.get(k, []))
        for _ in oels[nr:]:
            extra += 1
            defects.append({"cat": cat, "kind": "extra", "key": k[:80], "detail": "输出有·参考无"})
    return {"cat": cat, "hit": hit, "missing": missing, "extra": extra, "defects": defects}


# ---------- 各类抽取 + 键/比较 ----------
def _authors(root):
    return [c for c in root.iter("{*}contrib") if c.get("contrib-type") == "author"]


def _author_key(c):
    sn = norm_key(c.findtext("{*}name/{*}surname") or "")
    gn = norm_key(c.findtext("{*}name/{*}given-names") or "")
    return "%s|%s" % (sn, gn)


def _corresp_emails(root):
    """corresp id -> 邮箱集合(通讯邮箱惯例放 author-notes/corresp,非 contrib 内)。"""
    m = {}
    for corr in root.iter("{*}corresp"):
        cid = corr.get("id")
        if cid:
            m[cid] = frozenset(norm_value(e.text or "") for e in corr.iter("{*}email"))
    return m


def _author_emails(c, cmap):
    """作者邮箱:contrib 内 + 其 corresp-xref 指向的 corresp 里的邮箱(两种 house-style 都认)。"""
    s = set(norm_value(e.text or "") for e in c.iter("{*}email"))
    for x in c.iter("{*}xref"):
        if x.get("ref-type") == "corresp":
            s |= set(cmap.get(x.get("rid"), ()))
    return frozenset(s)


def _compare_authors(ref_root, out_root):
    r_cmap, o_cmap = _corresp_emails(ref_root), _corresp_emails(out_root)

    def affs(c):
        return frozenset(x.get("rid") for x in c.iter("{*}xref") if x.get("ref-type") == "aff")

    def corr(c):
        return any(x.get("ref-type") == "corresp" for x in c.iter("{*}xref"))

    def orcid(c):
        return norm_value(c.findtext("{*}contrib-id") or "")

    def equal(c):
        # 共同贡献:@equal-contrib="yes" 或 指向 author-notes 里 †/# 脚注的 xref
        if (c.get("equal-contrib") or "").lower() == "yes":
            return True
        return any(x.get("ref-type") in ("fn", "author-notes") for x in c.iter("{*}xref"))

    def cmp(r, o):
        d = []
        if affs(r) != affs(o):
            d.append("aff关联 参考=%s 输出=%s" % (sorted(affs(r)), sorted(affs(o))))
        if corr(r) != corr(o):
            d.append("通讯标记 参考=%s 输出=%s" % (corr(r), corr(o)))
        if equal(r) != equal(o):
            d.append("共同贡献标记 参考=%s 输出=%s" % (equal(r), equal(o)))
        if orcid(r) != orcid(o):
            d.append("ORCID 参考=%r 输出=%r" % (orcid(r), orcid(o)))
        re_, oe_ = _author_emails(r, r_cmap), _author_emails(o, o_cmap)
        if re_ != oe_:
            d.append("email 参考=%s 输出=%s" % (sorted(re_), sorted(oe_)))
        return d

    return _align(_authors(ref_root), _authors(out_root), _author_key, cmp, "作者")


import re as _re
_LABEL_PFX = _re.compile(r"^(figure|fig|table|tab|scheme)\.?\s*\d+\.?\s*[:.]?\s*", _re.I)


def _cap_key(el):
    """图/表对齐键:归一 caption,再剥去 'Figure N.'/'Table N.' 标签前缀——
    输出常把标签并进 caption 文本,参考用独立 <label>,不剥会把同一对象判成 missing+extra。"""
    cap = el.find("{*}caption")
    t = norm_key(itertext(cap) if cap is not None else "")
    t = _LABEL_PFX.sub("", t)
    return t or norm_key(el.findtext("{*}label") or "")


def _figs(root):
    return list(root.iter("{*}fig"))


def _fig_key(f):
    return _cap_key(f)


def _href_stem(h):
    """按文件名主干比对(忽略目录与扩展名):转换器统一转 .jpg,参考留原格式(.tif/.png),
    这是格式归一化差异、非'图错';比对看'是否同一张图'。"""
    base = (h or "").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0]


def _fig_cmp(r, o):
    d = []
    rc = norm_value(itertext(r.find("{*}caption")))
    oc = norm_value(itertext(o.find("{*}caption")))
    if rc != oc:
        d.append("caption不符")
    # 只比 graphic 数量:文件名主干/后缀均属命名约定(委员会 fig1 vs 转换器 fig-01、.tif vs .jpg),
    # 转换器无法预知委员会命名,不算质量缺陷;图的身份由 caption 对齐键保证。
    rn = len(list(r.iter("{*}graphic")))
    on = len(list(o.iter("{*}graphic")))
    if rn != on:
        d.append("graphic数 参考=%d 输出=%d" % (rn, on))
    return d


def _tables(root):
    return list(root.iter("{*}table-wrap"))


def _table_key(t):
    return _cap_key(t)


def _table_cmp(r, o):
    d = []
    def shape(t):
        return (len(list(t.iter("{*}tr"))), len(list(t.iter("{*}th"))),
                len(list(t.iter("{*}td"))), len(list(t.iter("{*}graphic"))))
    if shape(r) != shape(o):
        d.append("表形状(tr,th,td,graphic) 参考=%s 输出=%s" % (shape(r), shape(o)))
    rf = r.find("{*}table-wrap-foot") is not None
    of = o.find("{*}table-wrap-foot") is not None
    if rf != of:
        d.append("表脚注 参考=%s 输出=%s" % (rf, of))
    def scopes(t):  # §6.3 表列含 scope:表头 th/@scope 用法
        return Counter((th.get("scope") or "") for th in t.iter("{*}th"))
    if scopes(r) != scopes(o):
        d.append("表头scope 参考=%s 输出=%s" % (dict(scopes(r)), dict(scopes(o))))
    return d


# ---------- 公式 / 关键词 / 摘要(§6.3 各为独立一类) ----------
_MML_TOKENS = ("mi", "mo", "mn", "mtext")


def _formulas(root):
    return [f for f in root.iter() if isinstance(f.tag, str)
            and _ln(f) in ("disp-formula", "inline-formula")]


def _formula_tokens(f):
    """公式机读表示:按文档序取 MathML token(mi/mo/mn)文本序列(§6.3 捕获丢失)。
    无 MathML(仅退化为 graphic/纯文本)→ 标记为无机读公式,与含式者天然判为不同。"""
    toks = [norm_value(el.text or "") for el in f.iter()
            if isinstance(el.tag, str) and _ln(el) in _MML_TOKENS]
    tex = f.find(".//{*}tex-math")
    if toks:
        return "mml:" + " ".join(toks)
    if tex is not None and (tex.text or "").strip():
        return "tex:" + norm_value(tex.text)
    return "∅无机读公式"


def _formula_cat(ref, out):
    rc = Counter(_formula_tokens(f) for f in _formulas(ref))
    oc = Counter(_formula_tokens(f) for f in _formulas(out))
    return _set_cat("公式", rc, oc)


def _kwd_cat(ref, out):
    rc = Counter(norm_value(itertext(k)) for k in ref.iter("{*}kwd"))
    oc = Counter(norm_value(itertext(k)) for k in out.iter("{*}kwd"))
    return _set_cat("关键词", rc, oc)


def _abstract(root):
    """摘要结构:结构化(拆 sec,记各 sec 归一标题)还是单段(flat)。文本逐字属 L1。"""
    ab = root.find(".//{*}abstract")
    if ab is None:
        return []
    secs = ab.findall("{*}sec")
    if secs:
        out = []
        for s in secs:
            t = s.find("{*}title")
            out.append(("sec", norm_key("".join(t.itertext())) if t is not None else ""))
        return out
    return [("flat", "")]


def _abstract_cat(ref, out):
    return _set_cat("摘要", Counter(_abstract(ref)), Counter(_abstract(out)))


# ---------- 覆盖守门(§3.4/§11/附录A):参考里每种元素都要有明确处置,否则报"未覆盖" ----------
# 附录A 实测:10 例参考出现的元素全集(核心68 + 高频 + 稀有);比对器对每一种都已"比对/归一化/结构承载"。
# 参考若出现此全集之外的元素 → 守门报出,提示补规则(杜绝整类静默漏检)。
_COVERED_ELEMENTS = frozenset("""
article front journal-meta journal-id journal-title journal-title-group abbrev-journal-title issn
publisher publisher-name article-meta article-id article-categories subj-group subject title-group
article-title contrib-group contrib name surname given-names xref aff author-notes corresp history
date day month year permissions copyright-statement copyright-year license license-p ext-link
abstract kwd-group kwd body sec title p bold italic sup fig label caption graphic table-wrap table
colgroup col thead tbody tr th td back fn-group fn ref-list ref element-citation person-group source
fpage lpage volume email role table-wrap-foot contrib-id etal ack break mixed-citation collab comment
sub suffix uri math mi mn mo mtext mrow msub msup msubsup munder mover munderover mfenced mfrac mroot msqrt
mstyle mpadded mphantom menclose mspace mtable mtr mtd mmultiscripts mprescripts none semantics annotation
annotation-xml styled-content glossary app-group disp-formula inline-formula date-in-citation edition issue
publisher-loc pub-id degrees def def-item def-list term
""".split())
# 说明:pub-id/degrees/mtext/def-list 系列 = 覆盖守门实测补入(附录A 首版元素普查漏登,已回填);
# pub-id 计入 B 档补全,mtext 计入公式 token,degrees/缩写定义表属容器内容(不单列比对,由 L1 忠实兜底)。


def _coverage_gate(ref, out):
    # 遍历两树并集(§3.4/§11):参考或输出出现的每种元素都要有明确处置,否则报"未覆盖"
    seen = set(_ln(el) for el in ref.iter() if isinstance(el.tag, str))
    seen |= set(_ln(el) for el in out.iter() if isinstance(el.tag, str))
    uncovered = sorted(seen - _COVERED_ELEMENTS)
    defects = [{"cat": "覆盖守门", "kind": "uncovered", "key": ln,
                "detail": "参考/输出含此元素,比对器无明确处置(需补规则或列入忽略)"} for ln in uncovered]
    return {"cat": "覆盖守门", "hit": len(seen & _COVERED_ELEMENTS),
            "missing": 0, "extra": 0, "defects": defects}


# ---------- B 档补全覆盖率(§6.3 步5):可联网补全项单列,不混入结构缺陷 ----------
def _b_coverage(ref, out):
    def counts(root):
        doi = sum(1 for e in root.iter("{*}article-id") if e.get("pub-id-type") == "doi")
        doi += sum(1 for e in root.iter("{*}pub-id") if e.get("pub-id-type") == "doi")
        orcid = sum(1 for e in root.iter("{*}contrib-id") if (e.get("contrib-id-type") or "") == "orcid")
        ext = sum(1 for _ in root.iter("{*}ext-link")) + sum(1 for _ in root.iter("{*}uri"))
        return {"DOI": doi, "ORCID": orcid, "ext-link/uri": ext}
    r, o = counts(ref), counts(out)
    return {k: {"ref": r[k], "out": o[k]} for k in r}


def _refs(root):
    rl = root.find(".//{*}ref-list")
    return list(rl.iter("{*}ref")) if rl is not None else []


def _ref_fields(ref):
    """抽参考文献字段(容器无关:element/mixed 都尽力抽)。"""
    cit = ref.find("{*}element-citation")
    if cit is not None:
        surs = frozenset(norm_key(s.text or "") for s in cit.iter("{*}surname"))
        return {
            "surnames": surs,
            "title": norm_value(itertext(cit.find("{*}article-title"))),
            "source": norm_value(itertext(cit.find("{*}source"))),
            "year": norm_value(cit.findtext("{*}year") or ""),
            "volume": norm_value(cit.findtext("{*}volume") or ""),
            "fpage": norm_value(cit.findtext("{*}fpage") or ""),
            "lpage": norm_value(cit.findtext("{*}lpage") or ""),
            "container": "element",
        }
    # mixed:整串,字段无法结构化取出
    return {"surnames": frozenset(), "title": "", "source": "",
            "year": "", "volume": "", "fpage": "", "lpage": "",
            "container": "mixed", "raw": norm_value(itertext(ref.find("{*}mixed-citation")))}


def _ref_key(ref):
    """对齐键:label 归一(参考与输出都保留 [n] label,最稳)。"""
    return norm_key(ref.findtext("{*}label") or "")


def _refs_align(ref_root, out_root):
    ref_list = {_ref_key(r): r for r in _refs(ref_root)}
    out_list = {_ref_key(r): r for r in _refs(out_root)}
    cat = "参考文献"
    hit = missing = extra = 0
    defects = []
    FIELDS = ("surnames", "title", "source", "year", "volume", "fpage", "lpage")
    for k, rref in ref_list.items():
        oref = out_list.get(k)
        if oref is None:
            missing += 1
            defects.append({"cat": cat, "kind": "missing", "key": k, "detail": "参考有·输出无该条"})
            continue
        hit += 1
        rf, of = _ref_fields(rref), _ref_fields(oref)
        for fld in FIELDS:
            if rf[fld] != of[fld]:
                # 字段差异(含输出用 mixed 导致的字段全缺)
                rv = rf[fld] if fld != "surnames" else sorted(rf[fld])
                ov = of[fld] if fld != "surnames" else sorted(of[fld])
                defects.append({"cat": cat, "kind": "field", "key": k,
                                "detail": "%s 参考=%r 输出=%r" % (fld, rv, ov)})
        if of["container"] == "mixed" and rf["container"] == "element":
            defects.append({"cat": cat, "kind": "unstructured", "key": k,
                            "detail": "输出未结构化(mixed-citation)"})
    for k in out_list:
        if k not in ref_list:
            extra += 1
            defects.append({"cat": cat, "kind": "extra", "key": k, "detail": "输出有·参考无该条"})
    return {"cat": cat, "hit": hit, "missing": missing, "extra": extra, "defects": defects}


def _sections(root):
    """章节树:每个 sec 的 (归一标题, 深度) 作为条目。"""
    body = root.find("{*}body")
    out = []
    if body is None:
        return out

    def walk(el, depth):
        for sec in el.findall("{*}sec"):
            t = sec.find("{*}title")
            # 标题可能含 <italic> 等内联子元素,须取全文,不能只用 findtext(会漏子元素文字)
            title = norm_key("".join(t.itertext())) if t is not None else ""
            out.append((title, depth))
            walk(sec, depth + 1)
    walk(body, 1)
    return out


def _back_decls(root):
    """back 直接子节点的语义容器:(容器类型, 归一标题)。"""
    back = root.find("{*}back")
    out = []
    if back is None:
        return out
    for c in back:
        if not isinstance(c.tag, str):
            continue
        ln = _ln(c)
        if ln == "ref-list":
            continue
        t = c.find("{*}title")
        title = norm_key("".join(t.itertext())) if t is not None else norm_key(c.findtext("{*}label") or "")
        out.append((ln, title))
    return out


def _xrefs(root):
    """交叉引用:(ref-type, rid) 去重集合(presence,不计 occurrence)。
    出现次数受'某文献被引几次'影响、噪声大;比对看'同一目标是否都链到了'。"""
    c = Counter()
    for x in root.iter("{*}xref"):
        c[(x.get("ref-type"), x.get("rid"))] = 1
    return c


def _front_meta(root):
    """front 关键元数据字段字典(逐字段比)。"""
    def t(xp):
        el = root.find(xp)
        return norm_value(itertext(el)) if el is not None else None
    d = {
        "article-type": (root.get("article-type") or "").casefold(),
        "journal-id": t(".//{*}journal-id"),
        "subject": t(".//{*}subj-group/{*}subject"),
        "title": t(".//{*}article-meta//{*}article-title"),
    }
    d["issn"] = frozenset(norm_value(e.text or "") for e in root.iter("{*}issn"))
    d["doi"] = frozenset(norm_value(e.text or "") for e in root.iter("{*}article-id")
                         if e.get("pub-id-type") == "doi")
    def _num(s):  # 归一 "03"→"3",消除零填充差异;非数字原样
        s = (s or "").strip()
        return str(int(s)) if s.isdigit() else s
    hist = {}
    for dt in root.iter("{*}date"):
        k = dt.get("date-type")
        if k:
            hist[k] = "%s/%s/%s" % (_num(dt.findtext("{*}day")), _num(dt.findtext("{*}month")),
                                    _num(dt.findtext("{*}year")))
    d["dates"] = hist
    # permissions(B 档模板:版权年/版权声明/许可链接)——§6.3 front 逐字段含 permissions
    perm = root.find(".//{*}article-meta/{*}permissions")
    d["copyright-year"] = norm_value((perm.findtext("{*}copyright-year") if perm is not None else "") or "")
    d["copyright-statement"] = norm_value(itertext(perm.find("{*}copyright-statement")) if perm is not None else "")
    lic = perm.find("{*}license") if perm is not None else None
    lic_href = ""
    if lic is not None:
        lic_href = lic.get(XLINK_HREF) or ""
        if not lic_href:  # 链接也可能挂在 license 内的 ext-link 上
            el = lic.find(".//{*}ext-link")
            lic_href = (el.get(XLINK_HREF) if el is not None else "") or ""
    d["license-href"] = norm_value(lic_href).rstrip("/")
    return d


from difflib import SequenceMatcher as _SM


def _closest(key, candidates):
    """在 candidates 里找与 key 最相似的一项,返回 (最相似项, 接近度0-1);无候选或 key 空则 (None,0)。"""
    ks = str(key)
    best, br = None, 0.0
    for c in candidates:
        r = _SM(None, ks, str(c)).ratio()
        if r > br:
            best, br = c, r
    return best, br


def _set_cat(cat, ref_ct, out_ct):
    """多重集类(章节/back/xref/公式/关键词等)的命中/漏标/多标。
    漏标与多标并存时,给漏标项标注输出侧最接近的一项 + 接近度(§6.3 soft 匹配),降低近似项的噪声。"""
    hit = missing = extra = 0
    miss_keys, extra_keys = [], []
    for k, rn in ref_ct.items():
        on = out_ct.get(k, 0)
        hit += min(rn, on)
        if rn > on:
            missing += rn - on
            miss_keys.append(k)
    for k, on in out_ct.items():
        rn = ref_ct.get(k, 0)
        if on > rn:
            extra += on - rn
            extra_keys.append(k)
    defects = []
    for k in miss_keys:
        near, r = _closest(k, extra_keys)
        detail = "参考有·输出无"
        if near is not None and r >= 0.6:
            detail += "(输出最接近: %s… 接近度%d%%)" % (str(near)[:40], round(r * 100))
        defects.append({"cat": cat, "kind": "missing", "key": str(k)[:80],
                        "detail": detail, "closeness": round(r, 3) if near is not None else None})
    for k in extra_keys:
        defects.append({"cat": cat, "kind": "extra", "key": str(k)[:80], "detail": "输出有·参考无"})
    return {"cat": cat, "hit": hit, "missing": missing, "extra": extra, "defects": defects}


def run(sample, out_xml):
    ref = etree.parse(sample.ref_xml, _PARSER).getroot()
    out = etree.parse(out_xml, _PARSER).getroot()

    cats = []

    # front 元数据(逐字段;命中=字段一致数,缺陷=不符字段)
    rm, om = _front_meta(ref), _front_meta(out)
    fdef = []
    fhit = 0
    for fld in rm:
        if rm[fld] == om.get(fld):
            fhit += 1
        else:
            fdef.append({"cat": "front元数据", "kind": "value", "key": fld,
                         "detail": "参考=%r 输出=%r" % (rm[fld], om.get(fld))})
    cats.append({"cat": "front元数据", "hit": fhit, "missing": 0,
                 "extra": 0, "defects": fdef})

    cats.append(_compare_authors(ref, out))

    # 单位:对齐键剥掉前导角标数字(<sup>1</sup> 与文本间有无空格属排版差异,不算不同)
    import re as _re

    def _aff_key(a):
        return _re.sub(r"^\d+\s*", "", norm_value(itertext(a)))

    cats.append(_align(list(ref.iter("{*}aff")), list(out.iter("{*}aff")), _aff_key,
                       lambda r, o: [], "单位"))
    cats.append(_abstract_cat(ref, out))
    cats.append(_kwd_cat(ref, out))
    cats.append(_set_cat("章节", Counter(_sections(ref)), Counter(_sections(out))))
    cats.append(_align(_figs(ref), _figs(out), _fig_key, _fig_cmp, "图"))
    cats.append(_align(_tables(ref), _tables(out), _table_key, _table_cmp, "表"))
    cats.append(_formula_cat(ref, out))
    cats.append(_refs_align(ref, out))
    cats.append(_set_cat("交叉引用", _xrefs(ref), _xrefs(out)))
    cats.append(_set_cat("back声明", Counter(_back_decls(ref)), Counter(_back_decls(out))))
    cats.append(_coverage_gate(ref, out))

    total_defects = sum(len(c["defects"]) for c in cats)
    return {"by_category": cats, "defect_n": total_defects,
            "b_coverage": _b_coverage(ref, out)}
