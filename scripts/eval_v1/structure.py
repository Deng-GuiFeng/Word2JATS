"""L2 对位层(设计 §6.3):对冻结的金标准(结构参考.xml + figures.zip),按语义键对齐、逐类产出命中/漏标/多标。

核心纪律:
- **按语义对齐,不按位置,也不按 id 字面**(§3.6)。id 是文档内部标识,委员会取 `aff1`、转换器取
  `A1`,都对;引用关系闭合、指向同一对象即可。所以所有 @rid 一律先解析成"目标是什么"
  (目标元素类型 + 目标的语义键)再比 —— 2026-08 实测:不这么做,一次纯 id 重命名(引用关系
  与可见内容全不变)会被误判 97 条缺陷。
- **顺序用相邻对捕获**:作者顺序、章节顺序是硬语义(第一作者、Introduction 在 Methods 前),
  必须比。但按绝对序号比会级联——漏掉第 2 节会让后面每一节都错位、虚报一片。故比
  (前驱, 当前) 二元组多重集:换位必然改变相邻关系被抓住,漏一项只波及相邻两对,不级联。
- **合并单元格按网格展开**:rowspan/colspan 决定的是"这个值管辖哪几格",逐格比才抓得住
  rowspan=2 写成 rowspan=99 这类错误。
- **图片比字节**:金标准 = 结构参考.xml + figures.zip,图的身份是它的字节,不是文件名
  (委员会叫 fig1.tif、转换器叫 fig-01.jpg,同一张图)。故按语义槽位比 SHA-256。
- 参考文献按字段比(§3.3);容器是 element 还是 mixed **不作评分轴**——输出用 mixed 即字段
  全缺,按漏标计入,信号自然正确。
- 无加权总分(§3.7):每类给 命中/漏标(参考有输出无)/多标(输出有参考无)计数 + 缺陷条目。
100% 确定性。
"""
import hashlib
import os
import re as _re
import zipfile
from collections import Counter
from difflib import SequenceMatcher as _SM

from lxml import etree

from .normalize import norm_value, norm_key, itertext

_PARSER = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"


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
                    defects.append({"cat": cat, "kind": "value", "key": str(k)[:80], "detail": fld})
            else:
                missing += 1
                defects.append({"cat": cat, "kind": "missing", "key": str(k)[:80],
                                "detail": "参考有·输出无"})
    for k, oels in out_map.items():
        nr = len(ref_map.get(k, []))
        for _ in oels[nr:]:
            extra += 1
            defects.append({"cat": cat, "kind": "extra", "key": str(k)[:80], "detail": "输出有·参考无"})
    return {"cat": cat, "hit": hit, "missing": missing, "extra": extra, "defects": defects}


# ================= id 语义化:把 @rid 从"字面 id"翻译成"指向什么" =================
_LABEL_PFX = _re.compile(r"^(figure|fig|table|tab|scheme|supplementary)\.?\s*\d+\.?\s*[:.]?\s*", _re.I)
# 脚注/通讯项的前导标记符号:*、†、‡、#、§、¶、数字及其重复(**、††)
_MARK_PFX = _re.compile(r"^[\*†‡#§¶\d\s]+")


_NUM_IN_LABEL = _re.compile(r"(\d+)")


def _cap_key(el):
    """图/表对齐键:**优先用编号**(Figure 1 / Table 3),编号取不到才退到归一 caption。

    图的身份在一篇文章里就是它的编号,caption 是描述。用整段 caption 作对齐键太脆:
    转换器少抄半句图注,同一张图就配不上,在 图/媒体字节/交叉引用 三个类目一起虚报
    missing+extra(2026-08 实测 01 一处 caption 差异炸出 5 条)。编号对齐后,caption 差异
    落到"caption不符"这一条字段缺陷上,定位准、不扩散。
    编号从独立 <label> 取,取不到再从 caption 开头的 'Figure N.' 前缀取(输出常把标签并进
    caption 文本,参考用独立 <label>)。"""
    lab = norm_key(el.findtext("{*}label") or "")
    cap = el.find("{*}caption")
    cap_t = norm_key(itertext(cap) if cap is not None else "")
    m = _NUM_IN_LABEL.search(lab)
    if not m:
        pfx = _LABEL_PFX.match(cap_t)
        if pfx:
            m = _NUM_IN_LABEL.search(pfx.group(0))
    if m:
        return "#%s" % m.group(1)
    return _LABEL_PFX.sub("", cap_t) or lab


def _aff_key(a):
    """单位对齐键:剥掉前导角标数字(<sup>1</sup> 与文本间有无空格属排版差异,不算不同)。"""
    return _re.sub(r"^\d+\s*", "", norm_value(itertext(a)))


def _ref_content_key(ref):
    """参考文献的**内容指纹**:第一作者姓 + 年 + 标题前 48 字。
    label 缺失时(X03 全部 35 条无编号)作对齐键用;不依赖编号,也不依赖位置。"""
    cit = ref.find("{*}element-citation")
    if cit is None:
        cit = ref.find("{*}mixed-citation")
    if cit is None:
        return norm_key(itertext(ref))[:64]
    sur = cit.find(".//{*}surname")
    yr = cit.findtext("{*}year") or ""
    ti = itertext(cit.find("{*}article-title")) or itertext(cit.find("{*}chapter-title"))
    if not ti:
        ti = norm_key(itertext(cit))
    return "%s|%s|%s" % (norm_key(sur.text if sur is not None else ""), norm_value(yr),
                         norm_key(ti)[:48])


def _ref_align_key(ref):
    """参考文献对齐键:有 label 用 label(最稳),无 label 退到内容指纹。
    **不可用字典推导建表**——X03 的 35 条全无 label,那样会塌缩成 1 条(2026-08 实测)。"""
    lab = norm_key(ref.findtext("{*}label") or "")
    return lab or ("~" + _ref_content_key(ref))


def _sec_title(sec):
    t = sec.find("{*}title")
    return norm_key("".join(t.itertext())) if t is not None else ""


def _id_semantics(root):
    """id → (目标元素类型, 目标语义键)。所有 @rid 比较都先过这张表,消除 id 命名差异。"""
    m = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        i = el.get("id")
        if not i:
            continue
        t = _ln(el)
        if t == "ref":
            k = _ref_align_key(el)
        elif t in ("fig", "table-wrap", "fig-group"):
            k = _cap_key(el)
        elif t == "aff":
            k = _aff_key(el)
        elif t == "sec":
            k = _sec_title(el)
        elif t in ("disp-formula", "inline-formula"):
            k = _formula_tokens(el)
        elif t == "graphic":
            k = norm_value(el.get(XLINK_HREF) or "")
        elif t in ("fn", "corresp"):
            # 剥掉前导标记符号(*/†/‡/#/§/¶/数字):脚注用哪个符号标属 house-style,
            # 而 xref 的语义是"指向哪条注",符号换了不该让整个交叉引用类目跟着错配
            k = _MARK_PFX.sub("", norm_value(itertext(el)))[:120]
        else:
            # contrib / app / glossary…:用可见文字作语义键
            k = norm_value(itertext(el))[:120]
        m[i] = (t, k)
    return m


def _rid_targets(el, idmap, ref_type=None):
    """把一个带 @rid 的元素解析成它指向的语义目标集合。目标不存在时留 ('?', 原始rid)
    以便悬空引用仍与参考侧不同(悬空本身由 L0 报 error)。"""
    out = []
    for tok in (el.get("rid") or "").split():
        out.append(idmap.get(tok, ("?", tok)))
    return frozenset(out)


def _adjacent(seq):
    """顺序的抗级联表示:(前驱, 当前) 二元组多重集。首项前驱记为 '^'。"""
    c = Counter()
    prev = "^"
    for x in seq:
        c[(prev, x)] += 1
        prev = x
    return c


def _order_defects(cat, ref_seq, out_seq, label):
    """比较两个序列的相对顺序,**只看两侧都存在的项**。

    漏项/多项已由所属类目按 missing/extra 计过,顺序类目再算一遍就是同一处错误重复计数
    (02 漏 10 个小节,章节类目报 10 条、顺序类目又报 12 条)。取交集后比相邻对,既不重复,
    又能抓住"集合一致但排列不同"这种只有顺序看得见的错误。"""
    common = set(ref_seq) & set(out_seq)
    ro = _adjacent([x for x in ref_seq if x in common])
    oo = _adjacent([x for x in out_seq if x in common])
    out = []
    for k in sorted(set(ro) | set(oo), key=str):
        if ro.get(k, 0) != oo.get(k, 0):
            out.append({"cat": cat, "kind": "order", "key": str(k[1])[:60],
                        "detail": "%s:前一项 参考侧=%r 出现%d次 输出侧出现%d次" % (
                            label, str(k[0])[:40], ro.get(k, 0), oo.get(k, 0))})
    return out


# ---------- 作者 ----------
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


def _address_sig(c):
    """作者联系地址:按字段取值,与作者绑定(§口径5)。整块地址错挂到另一作者时词多重集不变,
    L1 查不出来,只能在这里按归属比。"""
    ad = c.find("{*}address")
    if ad is None:
        return None
    parts = []
    for ch in ad:
        if isinstance(ch.tag, str):
            parts.append((_ln(ch), norm_value(itertext(ch))))
    return tuple(parts) or (("address", norm_value(itertext(ad))),)


def _compare_authors(ref_root, out_root, r_ids, o_ids):
    r_cmap, o_cmap = _corresp_emails(ref_root), _corresp_emails(out_root)

    def affs(c, idmap):
        """aff 关联:比"指向哪个单位",不比 rid 字面。"""
        s = set()
        for x in c.iter("{*}xref"):
            if x.get("ref-type") == "aff":
                s |= _rid_targets(x, idmap)
        return frozenset(s)

    def corr(c):
        return any(x.get("ref-type") == "corresp" for x in c.iter("{*}xref"))

    def orcid(c):
        return norm_value(c.findtext("{*}contrib-id") or "")

    def equal(c):
        # 共同贡献:@equal-contrib="yes" 或 指向 author-notes 里 †/# 脚注的 xref
        if (c.get("equal-contrib") or "").lower() == "yes":
            return True
        return any(x.get("ref-type") in ("fn", "author-notes") for x in c.iter("{*}xref"))

    def degrees(c):
        return tuple(norm_value(d.text or "") for d in c.iter("{*}degrees"))

    def role(c):
        return frozenset(norm_value(itertext(x)) for x in c.iter("{*}role"))

    def comment(c):
        return tuple(norm_value(itertext(x)) for x in c.iter("{*}author-comment"))

    def cmp(r, o):
        d = []
        ra, oa = affs(r, r_ids), affs(o, o_ids)
        if ra != oa:
            d.append("aff关联 参考=%s 输出=%s" % (sorted(str(x) for x in ra), sorted(str(x) for x in oa)))
        if corr(r) != corr(o):
            d.append("通讯标记 参考=%s 输出=%s" % (corr(r), corr(o)))
        if equal(r) != equal(o):
            d.append("共同贡献标记 参考=%s 输出=%s" % (equal(r), equal(o)))
        if orcid(r) != orcid(o):
            d.append("ORCID 参考=%r 输出=%r" % (orcid(r), orcid(o)))
        re_, oe_ = _author_emails(r, r_cmap), _author_emails(o, o_cmap)
        if re_ != oe_:
            d.append("email 参考=%s 输出=%s" % (sorted(re_), sorted(oe_)))
        if _address_sig(r) != _address_sig(o):
            d.append("联系地址 参考=%s 输出=%s" % (_address_sig(r), _address_sig(o)))
        if degrees(r) != degrees(o):
            d.append("degrees 参考=%s 输出=%s" % (degrees(r), degrees(o)))
        if role(r) != role(o):
            d.append("role 参考=%s 输出=%s" % (sorted(role(r)), sorted(role(o))))
        if comment(r) != comment(o):
            d.append("author-comment 参考=%s 输出=%s" % (comment(r), comment(o)))
        return d

    res = _align(_authors(ref_root), _authors(out_root), _author_key, cmp, "作者")
    # 作者顺序:第一作者/末位通讯是硬语义,必须比;用相邻对避免漏一位造成的级联虚报
    res["defects"].extend(_order_defects(
        "作者", [_author_key(c) for c in _authors(ref_root)],
        [_author_key(c) for c in _authors(out_root)], "署名顺序"))
    return res


# ---------- 图 / 表 ----------
def _figs(root):
    return list(root.iter("{*}fig"))


def _fig_cmp(r, o):
    d = []
    rc = norm_value(itertext(r.find("{*}caption")))
    oc = norm_value(itertext(o.find("{*}caption")))
    if rc != oc:
        d.append("caption不符")
    # 文件名主干/后缀属命名约定(委员会 fig1 vs 转换器 fig-01、.tif vs .jpg),转换器无法预知,
    # 不算质量缺陷;图的**身份**由"媒体字节"类目按槽位比 SHA-256 把关,这里只比数量。
    rn = len(list(r.iter("{*}graphic")))
    on = len(list(o.iter("{*}graphic")))
    if rn != on:
        d.append("graphic数 参考=%d 输出=%d" % (rn, on))
    return d


def _tables(root):
    return list(root.iter("{*}table-wrap"))


def _grid(table):
    """把 table 展开成逻辑网格 {(行,列): (单元格类型, 归一文本)}。
    rowspan/colspan 决定"这个值管辖哪几格",合并区的每一格都填同一个值——
    故 rowspan=2 误写成 rowspan=99、或两格内容对调,都会改变网格,逐格比即可抓住。"""
    grid = {}
    r = 0
    for tr in table.iter("{*}tr"):
        c = 0
        for cell in tr:
            if not isinstance(cell.tag, str) or _ln(cell) not in ("td", "th"):
                continue
            while (r, c) in grid:
                c += 1
            try:
                rs = max(1, int(cell.get("rowspan") or 1))
                cs = max(1, int(cell.get("colspan") or 1))
            except ValueError:
                rs = cs = 1
            v = (_ln(cell), norm_value(itertext(cell)))
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = v
            c += cs
        r += 1
    return grid


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
    elif rf and of:
        rft = norm_value(itertext(r.find("{*}table-wrap-foot")))
        oft = norm_value(itertext(o.find("{*}table-wrap-foot")))
        if rft != oft:
            d.append("表脚注内容不符")

    def scopes(t):  # §6.3 表列含 scope:表头 th/@scope 用法
        return Counter((th.get("scope") or "") for th in t.iter("{*}th"))
    if scopes(r) != scopes(o):
        d.append("表头scope 参考=%s 输出=%s" % (dict(scopes(r)), dict(scopes(o))))

    # 单元格矩阵:逐格比(含合并展开)。差异多时只列前几格,避免一张错表刷屏
    rt, ot = r.find(".//{*}table"), o.find(".//{*}table")
    if rt is not None and ot is not None:
        rg, og = _grid(rt), _grid(ot)
        bad = sorted(k for k in set(rg) | set(og) if rg.get(k) != og.get(k))
        if bad:
            sample = "; ".join("(%d,%d) 参考=%r 输出=%r" % (
                k[0], k[1], (rg.get(k) or ("", ""))[1][:24], (og.get(k) or ("", ""))[1][:24])
                for k in bad[:3])
            d.append("单元格矩阵 %d 格不符: %s%s" % (
                len(bad), sample, "…" if len(bad) > 3 else ""))
    return d


# ---------- 公式 ----------
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
        return "mml:" + " ".join(toks)
    if tex is not None and (tex.text or "").strip():
        return "tex:" + norm_value(tex.text)
    return "∅无机读公式"


def _mathml_tree(f):
    """公式的**运算树**签名:mfrac(mi:a,mi:b) 形式的 S 表达式。
    字符序列相同而结构不同(分式写成并列、上标写成下标)在 token 序列里看不出来,只能比树。

    先做 MathML 规范化,三条都是 W3C MathML 规范内的等价形,不归一则每个式子都会因包装
    风格不同虚报树不符(2026-08 实测 03 的 18 个式子全中):
      ① 只有一个子节点的 <mrow> 是冗余分组:msup(mrow(mi:χ),mrow(mn:2)) ≡ msup(mi:χ,mn:2);
      ② <semantics> 是 presentation + content 的捆绑容器,只取第一个(presentation)子树,
         annotation/annotation-xml 是另一套编码的同一式子,不参与结构比;
      ③ **推断 mrow**(inferred mrow):<math> 等容器的多个子女在规范上隐含成一个 mrow,
         故 math(a,b,c) ≡ math(mrow(a,b,c))。"""
    _INFERRED_MROW = {"math", "mstyle", "mtd", "msqrt", "menclose", "mpadded", "mphantom"}

    def walk(el):
        if not isinstance(el.tag, str):
            return ""
        name = _ln(el)
        children = [c for c in el if isinstance(c.tag, str)]
        if name == "semantics":
            pres = [c for c in children if _ln(c) not in ("annotation", "annotation-xml")]
            return walk(pres[0]) if pres else ""
        kids = [walk(c) for c in children]
        kids = [k for k in kids if k]
        if name == "mrow" and len(kids) == 1:
            return kids[0]
        # 推断 mrow 要在**归一后的结果**上判断:参考侧 math 的直接子女可能是 semantics,
        # 剥掉它之后才露出那层 mrow;只看原始子元素会漏判(2026-08 实测 03 的 18 个式子全漏)
        if name in _INFERRED_MROW and len(kids) == 1 \
                and kids[0].startswith("mrow(") and kids[0].endswith(")"):
            kids = [kids[0][len("mrow("):-1]]
        txt = norm_value(el.text or "")
        if kids:
            return "%s(%s)" % (name, ",".join(kids))
        return "%s:%s" % (name, txt) if txt else name
    m = f.find(".//{%s}math" % MATHML_NS)
    if m is None:
        m = f.find(".//{*}math")
    return walk(m) if m is not None else ""


def _formula_cat(ref, out):
    """公式:token 序列作对齐键(稳),运算树作字段比(细)。"""
    def bucket(root):
        d = {}
        for f in _formulas(root):
            d.setdefault(_formula_tokens(f), []).append(f)
        return d
    rb, ob = bucket(ref), bucket(out)
    hit = missing = extra = 0
    defects = []
    for k, rfs in rb.items():
        ofs = ob.get(k, [])
        for i, rf in enumerate(rfs):
            if i < len(ofs):
                hit += 1
                rt, ot = _mathml_tree(rf), _mathml_tree(ofs[i])
                if rt != ot:
                    defects.append({"cat": "公式", "kind": "tree", "key": str(k)[:80],
                                    "detail": "MathML 运算树不符 参考=%s… 输出=%s…" % (rt[:70], ot[:70])})
            else:
                missing += 1
                near, r = _closest(k, [x for x in ob if x not in rb])
                detail = "参考有·输出无"
                if near is not None and r >= 0.6:
                    detail += "(输出最接近: %s… 接近度%d%%)" % (str(near)[:40], round(r * 100))
                defects.append({"cat": "公式", "kind": "missing", "key": str(k)[:80], "detail": detail})
    for k, ofs in ob.items():
        for _ in ofs[len(rb.get(k, [])):]:
            extra += 1
            defects.append({"cat": "公式", "kind": "extra", "key": str(k)[:80], "detail": "输出有·参考无"})
    return {"cat": "公式", "hit": hit, "missing": missing, "extra": extra, "defects": defects}


def _kwd_cat(ref, out):
    rc = Counter(norm_value(itertext(k)) for k in ref.iter("{*}kwd"))
    oc = Counter(norm_value(itertext(k)) for k in out.iter("{*}kwd"))
    return _set_cat("关键词", rc, oc)


# ---------- 摘要 ----------
def _abstracts(root):
    """**全部**摘要,不只第一份:图文摘要(graphical)、precis 都是独立摘要,
    只读 root.find('.//abstract') 会让它们完全不进比较(2026-08 实测 S03/S05/X02 三例)。
    每份记 (abstract-type, 结构形态)。文本逐字属 L1。"""
    out = []
    for ab in root.iter("{*}abstract"):
        typ = ab.get("abstract-type") or ""
        secs = ab.findall("{*}sec")
        if secs:
            shape = tuple(("sec", norm_key("".join(s.find("{*}title").itertext()))
                           if s.find("{*}title") is not None else "") for s in secs)
        else:
            shape = (("flat", ""),)
        has_media = bool(list(ab.iter("{*}graphic")) or list(ab.iter("{*}inline-graphic")))
        out.append((typ, shape, has_media))
    return out


def _abstract_cat(ref, out):
    return _set_cat("摘要", Counter(_abstracts(ref)), Counter(_abstracts(out)))


# ---------- 内联格式 ----------
_INLINE_FMT = ("bold", "italic", "sup", "sub", "underline", "sc", "monospace", "overline", "strike")


def _fmt_buckets(root):
    """{格式集合: 该格式下的词多重集}。bold 写成 italic、正文上标写成下标,词多重集完全不变、
    L1 查不出来,只能在这里比。

    按**字符承载的格式**分桶,不按"哪个元素包着哪段文字":`<bold>A</bold> <bold>B</bold>` 与
    `<bold>A B</bold>` 渲染一致、语义一致,按元素比会虚报 missing+extra,按格式桶比则等价。"""
    from .normalize import tokens
    buckets = {}

    def walk(el, fmts):
        n = _ln(el) if isinstance(el.tag, str) else ""
        f2 = (fmts | {n}) if n in _INLINE_FMT else fmts
        if el.text and f2:
            buckets.setdefault(frozenset(f2), Counter()).update(tokens(el.text))
        for c in el:
            if isinstance(c.tag, str):
                walk(c, f2)
            if c.tail and f2:   # tail 在 el 内部,承载的是 el 的格式
                buckets.setdefault(frozenset(f2), Counter()).update(tokens(c.tail))
    walk(root, frozenset())
    return buckets


def _inline_fmt_cat(ref, out):
    """内联格式类目。缺陷**按格式桶聚合**:转换器把 31 处章节标题冗余包上 <bold>,是一个
    系统性问题,不该炸成 31+ 条淹没别的类目;每桶最多两条(缺/多),detail 给样例与总数。"""
    rb, ob = _fmt_buckets(ref), _fmt_buckets(out)
    hit = missing = extra = 0
    defects = []
    for k in sorted(set(rb) | set(ob), key=lambda s: "+".join(sorted(s))):
        r, o = rb.get(k, Counter()), ob.get(k, Counter())
        name = "+".join(sorted(k))
        hit += sum((r & o).values())
        lost, more = r - o, o - r
        if lost:
            missing += len(lost)
            defects.append({"cat": "内联格式", "kind": "missing", "key": name,
                            "detail": "参考此格式下有·输出无 共%d词种: %s%s" % (
                                len(lost), ", ".join(list(lost)[:8]), "…" if len(lost) > 8 else "")})
        if more:
            extra += len(more)
            defects.append({"cat": "内联格式", "kind": "extra", "key": name,
                            "detail": "输出此格式下有·参考无 共%d词种: %s%s" % (
                                len(more), ", ".join(list(more)[:8]), "…" if len(more) > 8 else "")})
    return {"cat": "内联格式", "hit": hit, "missing": missing, "extra": extra, "defects": defects}


# ---------- 媒体字节(金标准 = 结构参考.xml + figures.zip) ----------
class _ZipReader:
    """从 figures.zip 按 basename 取字节(包内可能带目录前缀)。"""

    def __init__(self, path):
        self._m = {}
        if path and os.path.exists(path):
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    if not n.endswith("/"):
                        self._m[os.path.basename(n)] = z.read(n)

    def get(self, href):
        return self._m.get(os.path.basename(href or ""))


class _DirReader:
    """从转换输出目录按相对路径取字节。"""

    def __init__(self, root):
        self.root = root

    def get(self, href):
        if not href or not self.root:
            return None
        p = os.path.join(self.root, href)
        if not os.path.exists(p):
            p = os.path.join(self.root, os.path.basename(href))
        try:
            with open(p, "rb") as f:
                return f.read()
        except OSError:
            return None


_MEDIA_HOSTS = ("fig", "table-wrap", "disp-formula", "inline-formula", "abstract", "fig-group",
                "supplementary-material", "app", "boxed-text")


def _media_slots(root, reader):
    """{(承载对象类型, 承载对象语义键): (字节sha 序列)}。
    图的身份是它的字节,不是文件名——委员会命名 fig1.tif、转换器命名 fig-01.jpg 是同一张图;
    反过来两张图 href 对调,文件都在、都能解码,只有比字节才发现张冠李戴。"""
    slots = {}
    for g in root.iter():
        if not isinstance(g.tag, str) or _ln(g) not in ("graphic", "inline-graphic"):
            continue
        href = g.get(XLINK_HREF) or ""
        host, hkey = "body", ""
        p = g.getparent()
        while p is not None:
            if isinstance(p.tag, str) and _ln(p) in _MEDIA_HOSTS:
                host = _ln(p)
                if host in ("fig", "table-wrap", "fig-group"):
                    hkey = _cap_key(p)
                elif host == "abstract":
                    hkey = p.get("abstract-type") or ""
                else:
                    hkey = norm_key(itertext(p))[:48]
                break
            p = p.getparent()
        blob = reader.get(href)
        sha = hashlib.sha256(blob).hexdigest()[:16] if blob else "∅缺文件"
        slots.setdefault((host, hkey), []).append(sha)
    return {k: tuple(v) for k, v in slots.items()}


def _media_cat(ref, out, ref_reader, out_reader):
    rs, os_ = _media_slots(ref, ref_reader), _media_slots(out, out_reader)
    hit = missing = extra = 0
    defects = []
    for k, rv in rs.items():
        if k not in os_:
            missing += 1
            defects.append({"cat": "媒体字节", "kind": "missing", "key": "%s/%s" % (k[0], str(k[1])[:50]),
                            "detail": "参考有此图位·输出无"})
        else:
            hit += 1
            if rs[k] != os_[k]:
                defects.append({"cat": "媒体字节", "kind": "bytes",
                                "key": "%s/%s" % (k[0], str(k[1])[:50]),
                                "detail": "图片字节不符 参考=%s 输出=%s" % (list(rv), list(os_[k]))})
    for k in os_:
        if k not in rs:
            extra += 1
            defects.append({"cat": "媒体字节", "kind": "extra", "key": "%s/%s" % (k[0], str(k[1])[:50]),
                            "detail": "输出有此图位·参考无"})
    return {"cat": "媒体字节", "hit": hit, "missing": missing, "extra": extra, "defects": defects}


# ---------- 覆盖守门(§3.4/§11/附录A) ----------
# **处置登记,不是白名单**:每种元素必须写明"由哪个比较器负责"。
# 2026-08 教训:把元素名塞进一个字符串集合只让守门闭嘴,不代表它被比较过——
# address/addr-line 曾"已覆盖"却无人比对,把整块地址改挂到另一位作者,三层全部零缺陷。
# 值 = 负责的类目名;"L1兜底" 表示有意不在 L2 单列比对、由 L1 逐字守恒承担(报告会点名)。
_DISPOSITION = {}


def _reg(cat, names):
    for n in names.split():
        _DISPOSITION[n] = cat


_reg("front元数据", """article front journal-meta journal-id journal-title journal-title-group
     abbrev-journal-title issn publisher publisher-name article-meta article-id article-categories
     subj-group subject title-group article-title history date day month year permissions
     copyright-statement copyright-year license license-p volume issue fpage lpage elocation-id
     supplementary-material""")
_reg("作者", """contrib-group contrib name surname given-names suffix collab aff author-notes corresp
     email contrib-id degrees role address addr-line postal-code phone author-comment etal""")
_reg("摘要", "abstract")
_reg("关键词", "kwd-group kwd")
_reg("章节", "body sec title p")
_reg("图", "fig caption")
_reg("表", "table-wrap table colgroup col thead tbody tr th td table-wrap-foot")
_reg("公式", """disp-formula inline-formula math mi mn mo mtext mrow msub msup msubsup munder mover
     munderover mfenced mfrac mroot msqrt mstyle mpadded mphantom menclose mspace mtable mtr mtd
     mmultiscripts mprescripts none semantics annotation annotation-xml""")
_reg("参考文献", """back ref-list ref element-citation mixed-citation person-group source
     chapter-title edition publisher-loc pub-id date-in-citation comment""")
_reg("交叉引用", "xref")
_reg("back声明", "fn-group fn ack app-group app glossary def def-item def-list term")
_reg("媒体字节", "graphic inline-graphic fig-group")
_reg("内联格式", "bold italic sup sub underline sc monospace overline strike styled-content")
_reg("L1兜底", "label break ext-link uri named-content list list-item")


def _coverage_gate(ref, out):
    seen = set(_ln(el) for el in ref.iter() if isinstance(el.tag, str))
    seen |= set(_ln(el) for el in out.iter() if isinstance(el.tag, str))
    uncovered = sorted(seen - set(_DISPOSITION))
    defects = [{"cat": "覆盖守门", "kind": "uncovered", "key": ln,
                "detail": "参考/输出含此元素,比对器无明确处置(需登记到某个类目或显式列入 L1兜底)"}
               for ln in uncovered]
    l1_only = sorted(n for n in seen if _DISPOSITION.get(n) == "L1兜底")
    return {"cat": "覆盖守门", "hit": len(seen) - len(uncovered), "missing": 0, "extra": 0,
            "defects": defects, "l1_only": l1_only}


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


# ---------- 参考文献 ----------
def _refs(root):
    rl = root.find(".//{*}ref-list")
    return list(rl.iter("{*}ref")) if rl is not None else []


def _ref_fields(ref):
    """抽参考文献字段(容器无关:element/mixed 都尽力抽)。"""
    cit = ref.find("{*}element-citation")
    if cit is not None:
        def t(tag):
            return norm_value(itertext(cit.find("{*}" + tag)))

        def pid(kind):
            return norm_value("".join(
                e.text or "" for e in cit.iter("{*}pub-id") if e.get("pub-id-type") == kind))
        return {
            # 作者**有序**:并列作者的次序是著录的一部分,用 frozenset 会让换位无感
            "authors": tuple(norm_key(s.text or "") for s in cit.iter("{*}surname")),
            "title": t("article-title") or t("chapter-title"),
            "source": t("source"),
            "year": norm_value(cit.findtext("{*}year") or ""),
            "volume": norm_value(cit.findtext("{*}volume") or ""),
            "issue": norm_value(cit.findtext("{*}issue") or ""),
            "fpage": norm_value(cit.findtext("{*}fpage") or ""),
            "lpage": norm_value(cit.findtext("{*}lpage") or ""),
            "elocation-id": norm_value(cit.findtext("{*}elocation-id") or ""),
            "doi": pid("doi"),
            "pmid": pid("pmid"),
            "publisher": (t("publisher-name") + " " + t("publisher-loc")).strip(),
            "raw": "",
            "container": "element",
        }
    # mixed:字段无法结构化取出,只能整串比 —— **raw 必须参与比较**,
    # 否则两条 mixed-citation 内容对调时字段全空、逐字段一致,变成零缺陷(2026-08 实测)
    return {"authors": (), "title": "", "source": "", "year": "", "volume": "", "issue": "",
            "fpage": "", "lpage": "", "elocation-id": "", "doi": "", "pmid": "", "publisher": "",
            "container": "mixed", "raw": norm_value(itertext(ref.find("{*}mixed-citation")))}


_REF_FIELDS = ("authors", "title", "source", "year", "volume", "issue", "fpage", "lpage",
               "elocation-id", "doi", "pmid", "publisher", "raw")


def _refs_align(ref_root, out_root):
    """**同键多实例按序配对,不可用字典推导**——无 label 的参考文献键相同,
    `{k: r for r in refs}` 会让 35 条只剩最后 1 条,前 34 条静默消失(X03 实测)。"""
    cat = "参考文献"
    ref_map, out_map = {}, {}
    for r in _refs(ref_root):
        ref_map.setdefault(_ref_align_key(r), []).append(r)
    for r in _refs(out_root):
        out_map.setdefault(_ref_align_key(r), []).append(r)
    hit = missing = extra = 0
    defects = []
    for k, rrefs in ref_map.items():
        orefs = out_map.get(k, [])
        for i, rref in enumerate(rrefs):
            if i >= len(orefs):
                missing += 1
                defects.append({"cat": cat, "kind": "missing", "key": str(k)[:80],
                                "detail": "参考有·输出无该条"})
                continue
            hit += 1
            rf, of = _ref_fields(rref), _ref_fields(orefs[i])
            if rf["container"] != of["container"]:
                # 容器形态不同时**只报一条**:两侧字段体系压根不可比(mixed 侧字段必然全空),
                # 逐字段比会把同一件事报成 5-6 条
                defects.append({
                    "cat": cat, "kind": "container", "key": str(k)[:80],
                    "detail": ("输出未结构化(mixed-citation),参考是 element-citation"
                               if of["container"] == "mixed" else
                               "参考用 mixed-citation(该条不可结构化著录),输出强行拆成了 element-citation")})
            else:
                for fld in _REF_FIELDS:
                    if rf[fld] != of[fld]:
                        defects.append({"cat": cat, "kind": "field", "key": str(k)[:80],
                                        "detail": "%s 参考=%r 输出=%r" % (fld, rf[fld], of[fld])})
    for k, orefs in out_map.items():
        for _ in orefs[len(ref_map.get(k, [])):]:
            extra += 1
            defects.append({"cat": cat, "kind": "extra", "key": str(k)[:80],
                            "detail": "输出有·参考无该条"})
    # 著录顺序:ref-list 是有序的,无 label 时(X03 全部 35 条)顺序就是唯一的编号依据,
    # 两条内容对调后各自仍能按内容指纹配上,只有比顺序才发现它们换了位置
    defects.extend(_order_defects(
        cat, [_ref_align_key(r) for r in _refs(ref_root)],
        [_ref_align_key(r) for r in _refs(out_root)], "著录顺序"))
    return {"cat": cat, "hit": hit, "missing": missing, "extra": extra, "defects": defects}


# ---------- 章节 / back / 交叉引用 ----------
def _sections(root):
    """章节:每个 sec 记 (直接父标题, 归一标题)。直接父捕获层级归属——把子节提到顶层、
    或挂到别的父节点下,只比 (标题,深度) 是看不出来的。

    用**直接父**而非完整路径:完整路径下,某一层错位会让它底下每个子孙的键全变,一处错误
    虚报一整棵子树;直接父只波及该节点的直接子女,错误定位准、不级联。"""
    body = root.find("{*}body")
    out = []
    if body is None:
        return out

    def walk(el, parent):
        for sec in el.findall("{*}sec"):
            title = _sec_title(sec)
            out.append((parent, title))
            walk(sec, title)
    walk(body, "^body")
    return out


def _section_seq(root):
    """章节的文档序序列(带直接父),供顺序比较用。"""
    return _sections(root)


def _back_decls(root):
    """back 直接子节点的语义容器:(序号, 容器类型, 归一标题)。序号捕获声明块的先后。"""
    back = root.find("{*}back")
    out = []
    if back is None:
        return out
    i = 0
    for c in back:
        if not isinstance(c.tag, str):
            continue
        n = _ln(c)
        if n == "ref-list":
            continue
        t = c.find("{*}title")
        title = norm_key("".join(t.itertext())) if t is not None else norm_key(c.findtext("{*}label") or "")
        out.append((i, n, title))
        i += 1
    return out


def _xrefs(root, idmap):
    """交叉引用:(ref-type, 目标类型, 目标语义键) **按出现次数**计。
    - 比语义目标不比 rid 字面:id 命名是转换器自由,委员会叫 b12、转换器叫 R012 都对。
    - 计 occurrence 不计 presence:"这条文献正文里被引 3 次"是 docx 的事实,
      漏掉其中一次就是漏了一处引用,按去重集合比会完全看不见。"""
    c = Counter()
    for x in root.iter("{*}xref"):
        for tgt in _rid_targets(x, idmap):
            c[(x.get("ref-type"), tgt[0], str(tgt[1])[:60])] += 1
    return c


# ---------- front ----------
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
    # ISSN/article-id **带类型**:print 与 electronic 对调、doi 与 publisher-id 混用,
    # 只比文本集合是看不出来的
    d["issn"] = frozenset((e.get("pub-type") or "", norm_value(e.text or ""))
                          for e in root.iter("{*}issn"))
    d["article-id"] = frozenset((e.get("pub-id-type") or "", norm_value(e.text or ""))
                                for e in root.iter("{*}article-id"))
    am = root.find(".//{*}article-meta")
    for f in ("volume", "issue", "fpage", "lpage", "elocation-id"):
        el = am.find("{*}" + f) if am is not None else None
        d[f] = norm_value(itertext(el)) if el is not None else None

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


def _closest(key, candidates):
    """在 candidates 里找与 key 最相似的一项,返回 (最相似项, 接近度0-1);无候选或 key 空则 (None,0)。"""
    ks = str(key)
    best, br = None, 0.0
    for c in candidates:
        r = _SM(None, ks, str(c)).ratio()
        if r > br:
            best, br = c, r
    return best, br


def _set_cat(cat, ref_ct, out_ct, limit=None):
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


def run(sample, out_xml, out_dir=None):
    """out_dir:转换输出目录,用于取输出侧图片字节。为 None 时输出侧也从 figures.zip 取
    (自比场景:证明比对器自洽)。参考侧永远从 sample.figures_zip 取。"""
    ref = etree.parse(sample.ref_xml, _PARSER).getroot()
    out = etree.parse(out_xml, _PARSER).getroot()
    r_ids, o_ids = _id_semantics(ref), _id_semantics(out)
    fz = getattr(sample, "figures_zip", None)
    ref_reader = _ZipReader(fz)
    out_reader = _DirReader(out_dir) if out_dir else ref_reader

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
    cats.append({"cat": "front元数据", "hit": fhit, "missing": 0, "extra": 0, "defects": fdef})

    cats.append(_compare_authors(ref, out, r_ids, o_ids))
    cats.append(_align(list(ref.iter("{*}aff")), list(out.iter("{*}aff")), _aff_key,
                       lambda r, o: [], "单位"))
    cats.append(_abstract_cat(ref, out))
    cats.append(_kwd_cat(ref, out))
    sec_cat = _set_cat("章节", Counter(_sections(ref)), Counter(_sections(out)))
    sec_cat["defects"].extend(_order_defects(
        "章节", _section_seq(ref), _section_seq(out), "章节顺序"))
    cats.append(sec_cat)
    cats.append(_align(_figs(ref), _figs(out), _cap_key, _fig_cmp, "图"))
    cats.append(_align(_tables(ref), _tables(out), _cap_key, _table_cmp, "表"))
    cats.append(_formula_cat(ref, out))
    cats.append(_media_cat(ref, out, ref_reader, out_reader))
    cats.append(_inline_fmt_cat(ref, out))
    cats.append(_refs_align(ref, out))
    cats.append(_set_cat("交叉引用", _xrefs(ref, r_ids), _xrefs(out, o_ids)))
    cats.append(_set_cat("back声明", Counter(_back_decls(ref)), Counter(_back_decls(out))))
    cats.append(_coverage_gate(ref, out))

    total_defects = sum(len(c["defects"]) for c in cats)
    return {"by_category": cats, "defect_n": total_defects,
            "b_coverage": _b_coverage(ref, out)}
