"""主程序：读决策单 → 装配 → 三道校验。"""
import json
import os
import re
import sys

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble as A

ROOT = A.ROOT
JOURNAL = {"X01": "RN", "X02": "DJNB", "X03": "BP", "X04": "DJNB"}


def build_refs_x01(src):
    """X01：作者把 JATS 参考文献标签手工敲进了 Word。还原 Word 自动替换的弯引号后
    直接解析——这些标签本身就是结构，不是正文内容。"""
    blocks = [b for b in src.data["blocks"] if b["style"] == "Reference"]
    for b in blocks:
        src.used.add(b["idx"])
    raw = "\n".join(b["text"] for b in blocks)
    fixed = (raw.replace("“", '"').replace("”", '"')
                .replace("‘", "'").replace("’", "'"))
    wrapped = ('<ref-list xmlns:xlink="http://www.w3.org/1999/xlink">%s</ref-list>' % fixed)
    rl = etree.fromstring(wrapped.encode())
    # 撇号还原：article-title 等正文文字里的弯撇号属于内容，要还原回去
    _restore_apostrophes(rl, blocks)
    return rl, len(rl.findall("ref"))


def _restore_apostrophes(rl, blocks):
    """上一步为了让 XML 可解析，把弯引号统一换成了直引号；但正文文字里的弯撇号
    （Parkinson's 的 ’）是作者内容，必须还原。做法：拿原文逐块比对，只还原文本节点。"""
    orig = "\n".join(b["text"] for b in blocks)
    for el in rl.iter():
        for attr in ("text", "tail"):
            s = getattr(el, attr)
            if not s or "'" not in s:
                continue
            cand = s.replace("'", "’")
            if cand in orig and s not in orig:
                setattr(el, attr, cand)


def build_refs_plain(src, dec, key):
    """其余三例：参考文献一段一条。先用确定性规则拆字段，拆不动的查人工拆分表，
    再拆不动才落 mixed-citation。docx 里没有编号，故不产出 <label>（忠实原则）。"""
    import refs as RP
    r = dec.get("refs") or {}
    items = r.get("items") or []
    if not items:
        return None, 0, {}
    manual = {}
    mpath = "%s/tmp/refbuild/refs_manual.json" % ROOT
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            for it in (json.load(f).get(key) or []):
                if it.get("parsed"):
                    manual[it["idx"]] = it

    rl = etree.Element("ref-list")
    ttl = r.get("heading_idx")
    if ttl is not None:
        t = etree.SubElement(rl, "title")
        A.inline(t, src.block(ttl), src)
    labels = _auto_labels(src, [it["idx"] for it in items])
    stat = {"element": 0, "mixed": 0, "manual": 0, "bad": [], "label": len(labels)}
    for n, it in enumerate(items, 1):
        idx = it["idx"]
        blk = src.block(idx)
        text = blk["text"]
        ref = etree.SubElement(rl, "ref", id="b%d" % n)
        if labels.get(idx):
            etree.SubElement(ref, "label").text = labels[idx]
        d = RP.parse(key, text, blk)
        if d is not None and RP.verify(d, text):
            d = None
        if d is None and idx in manual:
            d = _from_manual(manual[idx], text, stat)
            if d is not None:
                stat["manual"] += 1
        if d is None:
            mc = etree.SubElement(ref, "mixed-citation")
            A.inline(mc, src.block(idx), src)
            stat["mixed"] += 1
            continue
        _emit_citation(ref, d, src.block(idx), src)
        stat["element"] += 1
    return rl, len(items), stat


def _auto_labels(src, idxs):
    """Word 自动编号列表的序号：编号存在 numbering.xml 而非 run 文本里，但它确实是
    文档内容（渲染出来就是 "1." "2."），按 numPr + numFmt 机械推出，不含判断。
    docx 里真的没有编号（无 numPr）时返回空，不产 label。"""
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    blocks = {b["idx"]: b for b in src.data["blocks"]}
    numbered = [(i, blocks[i]["num"]) for i in idxs if blocks.get(i) and blocks[i].get("num")]
    if len(numbered) != len(idxs) or not numbered:
        return {}
    numid = numbered[0][1]["numId"]
    if any(n["numId"] != numid for _, n in numbered):
        return {}
    try:
        nb = etree.fromstring(src.zip.read("word/numbering.xml"))
    except KeyError:
        return {}
    aid = None
    for num in nb.iter(W + "num"):
        if num.get(W + "numId") == numid:
            e = num.find(W + "abstractNumId")
            aid = e.get(W + "val") if e is not None else None
            break
    if aid is None:
        return {}
    fmt = text_tpl = start = None
    for an in nb.iter(W + "abstractNum"):
        if an.get(W + "abstractNumId") != aid:
            continue
        lvl = an.find(W + "lvl")
        if lvl is None:
            return {}
        f = lvl.find(W + "numFmt"); t = lvl.find(W + "lvlText"); st = lvl.find(W + "start")
        fmt = f.get(W + "val") if f is not None else None
        text_tpl = t.get(W + "val") if t is not None else None
        start = int(st.get(W + "val")) if st is not None else 1
        break
    if fmt != "decimal" or not text_tpl:
        return {}          # 非十进制编号不臆测渲染形态
    out = {}
    for k, (i, _n) in enumerate(numbered):
        out[i] = text_tpl.replace("%1", str(start + k))
    return out


def _from_manual(m, text, stat):
    """人工拆分表 → 内部字段结构，逐字校验后才采用。"""
    import refs as RP
    d = {"persons": [tuple(p) for p in (m.get("persons") or [])],
         "etal": bool(m.get("etal")),
         "article-title": m.get("article-title"), "source": m.get("source"),
         "year": m.get("year"), "volume": m.get("volume"), "issue": m.get("issue"),
         "fpage": m.get("fpage"), "lpage": m.get("lpage"), "uri": m.get("uri"),
         "publication-type": m.get("publication-type") or "journal",
         "editors": [tuple(p) for p in (m.get("editors") or [])],
         "publisher-name": m.get("publisher-name"),
         "publisher-loc": m.get("publisher-loc"),
         "comment": m.get("comment")}
    bad = RP.verify(d, text)
    for k2 in ("publisher-name", "publisher-loc", "comment"):
        v = d.get(k2)
        if v and re.sub(r"\s+", " ", str(v)) not in re.sub(r"\s+", " ", text.replace(" ", " ")):
            bad.append("%s=%r" % (k2, v))
    for sn, gn in d["editors"]:
        if sn and sn not in text:
            bad.append("editor=%r" % sn)
    if bad:
        stat["bad"].append((m["idx"], bad))
        return None
    return d


def _fld(parent, tag, value, blk, src):
    """字段值按原文区间取 run，保留下标/斜体（刊名的 <italic>、TiO2 的下标）。"""
    el = etree.SubElement(parent, tag)
    try:
        A.inline(el, blk, src, only_text=value, drop_bold=True)
    except Exception:
        el.text = value
    return el


def _emit_citation(ref, d, blk=None, src=None):
    cit = etree.SubElement(ref, "element-citation",
                           **{"publication-type": d.get("publication-type") or "journal"})
    if d["persons"]:
        pg = etree.SubElement(cit, "person-group", **{"person-group-type": "author"})
        for sn, gn in d["persons"]:
            nm = etree.SubElement(pg, "name")
            etree.SubElement(nm, "surname").text = sn
            if gn:
                etree.SubElement(nm, "given-names").text = gn
        if d.get("etal"):
            etree.SubElement(pg, "etal")
    if d.get("editors"):
        pg = etree.SubElement(cit, "person-group", **{"person-group-type": "editor"})
        for sn, gn in d["editors"]:
            nm = etree.SubElement(pg, "name")
            etree.SubElement(nm, "surname").text = sn
            if gn:
                etree.SubElement(nm, "given-names").text = gn
    fp = d.get("fpage")
    if fp and not d.get("lpage") and re.match(r"^(?:Article|Artigo|No\.?)\s*\d+$", str(fp), re.I):
        d = dict(d, fpage=None, elocation=fp)
    for tag, key2 in (("article-title", "article-title"), ("source", "source"),
                      ("year", "year"), ("volume", "volume"), ("issue", "issue"),
                      ("fpage", "fpage"), ("lpage", "lpage"), ("elocation-id", "elocation"),
                      ("publisher-loc", "publisher-loc"), ("publisher-name", "publisher-name")):
        v = d.get(key2)
        if v:
            if blk is not None and tag in ("article-title", "source"):
                _fld(cit, tag, v, blk, src)
            else:
                etree.SubElement(cit, tag).text = v
    if d.get("uri"):
        u = d["uri"]
        m = re.search(r"10\.\d{4,9}/\S+", u)
        if m:
            etree.SubElement(cit, "pub-id", **{"pub-id-type": "doi"}).text = m.group(0)
        el = etree.SubElement(cit, "ext-link", **{"ext-link-type": "uri",
                                                  "{%s}href" % A.XLINK: u})
        el.text = u
    if d.get("comment"):
        etree.SubElement(cit, "comment").text = d["comment"]


def build(key, out_root):
    with open("%s/tmp/refbuild/%s.decisions.json" % (ROOT, key), encoding="utf-8") as f:
        dec = json.load(f)
    src = A.Source(key)
    out_dir = os.path.join(out_root, key)
    os.makedirs(out_dir, exist_ok=True)
    article_id = "article"          # 投稿件无 DOI，与转换器缺省一致

    art = etree.Element("article", nsmap=A.NSMAP)
    art.set("dtd-version", "1.3")
    art.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
    art.set("article-type", dec["article_type"])

    A.build_front(art, dec, src, JOURNAL[key])

    figs = A.build_figs(dec, src, out_dir, article_id)
    tables = A.build_tables(dec, src, out_dir, article_id)
    formulas = build_formulas(dec, src, out_dir, article_id)
    A.build_body(art, dec, src, figs, tables, formulas)

    if key == "X01":
        rl, n_refs = build_refs_x01(src)
        ref_stat = {"element": n_refs, "mixed": 0, "manual": 0, "bad": []}
        if (dec.get("refs") or {}).get("heading_idx") is not None:
            t = etree.Element("title")
            A.inline(t, src.block(dec["refs"]["heading_idx"]), src)
            rl.insert(0, t)
    else:
        rl, n_refs, ref_stat = build_refs_plain(src, dec, key)

    A.build_back(art, dec, src, rl)

    fig_ids = {f["id"] for f in dec.get("figs") or []}
    tbl_ids = {t["id"] for t in dec.get("tables") or []}
    A.apply_xrefs(art, n_refs, fig_ids, tbl_ids)

    xml = A.serialize(art)
    path = os.path.join(out_dir, "结构参考.xml")
    with open(path, "wb") as f:
        f.write(xml)

    ok, errs = A.dtd_validate(xml)
    cov = A.coverage_report(src, dec)
    gfx = A.check_graphics(src, out_dir, xml)
    mchk = A.media_check(src, out_dir, xml, dec)
    return {"key": key, "path": path, "dtd_ok": ok, "dtd_errs": errs[:12],
            "coverage": cov, "subst_errors": src.errors, "n_refs": n_refs,
            "size": len(xml), "refs": ref_stat, "graphics": gfx, "media": mchk}


def build_formulas(dec, src, out_dir, article_id):
    """公式载体三种，按可得信息量降级——降级都是因为信息物理上不存在，不是图省事：
      OMML                       → XSLT 转 MathML
      MathType OLE 内嵌 TeX      → latex2mathml 转 MathML
      纯图 / 无 TeX 的 OLE 图元   → graphic（图片里没有可提取的公式结构，OCR 就是猜）
    返回 {idx: {"slot": 回调, "disp": [独立公式元素]}}。
    """
    out = {}
    counter = [0]
    eqn_files = {}

    for f in dec.get("formulas") or []:
        idx = f["idx"]
        b = src.block(idx)
        if f.get("label_idx") is not None:
            src.block(f["label_idx"])          # 编号在邻块，登记为已用
        kind = f.get("kind") or "inline"
        tag = "disp-formula" if kind == "disp" else "inline-formula"
        made = []

        def _new(math_or_graphic, label=None):
            counter[0] += 1
            el = etree.Element(tag, id="E%d" % counter[0])
            if label and tag == "disp-formula":
                etree.SubElement(el, "label").text = label
            el.append(math_or_graphic)
            return el

        def _graphic_for(target, n):
            if target in eqn_files:
                rel = eqn_files[target]
                gtag = "graphic" if kind == "disp" else "inline-graphic"
                return etree.Element(gtag, **{"{%s}href" % A.XLINK: rel})
            blob = src.media_blob(target)
            ext = A._ext(blob, target)
            rel = "%s/eqn-%02d%s" % (article_id, n, ext)
            eqn_files[target] = rel
            A._write(out_dir, rel, blob)
            gtag = "graphic" if kind == "disp" else "inline-graphic"
            return etree.Element(gtag, **{"{%s}href" % A.XLINK: rel})

        # 1) OMML
        for m in b["omml"]:
            math = A.omml_to_mathml(m["xml"])
            if math is not None:
                made.append(_new(math, f.get("label")))
        want = f.get("carrier")           # "ole:<n>" 或 "media:<n>"，缺省按下面的通道判
        if want:
            kindw, _, nw = want.partition(":")
            nw = int(nw)
            tgt = (b["ole"][nw].get("img_target") if kindw == "ole" else b["media"][nw]["target"])
            counter[0] += 1
            made.append(_new(_graphic_for(tgt, counter[0]), f.get("label")))
            b_ole = b_media = []
        # 2) OLE
        for o in (b["ole"] if not want else []):
            prog = (o.get("progId") or "")
            if not prog.startswith("Equation"):
                continue          # ChemDraw / Origin 一般是插图；确为编号公式者用 carrier 显式指定
            tex = None
            if o.get("target"):
                tex = A.ole_tex(src.media_blob(o["target"]))
            if tex:
                try:
                    made.append(_new(A.latex_to_mathml(tex), f.get("label")))
                    continue
                except Exception:
                    pass
            if o.get("img_target"):
                counter[0] += 1
                made.append(_new(_graphic_for(o["img_target"], counter[0]), f.get("label")))
        # 3) 纯图公式
        if not made and not want and b["media"]:
            if kind == "inline":
                # 行内公式：每个 media 槽各一条，按 run 顺序插回；重复引用同一文件时复用文件名
                for med in b["media"]:
                    counter[0] += 1
                    made.append(_new(_graphic_for(med["target"], counter[0]), None))
            else:
                counter[0] += 1
                made.append(_new(_graphic_for(b["media"][0]["target"], counter[0]),
                                 f.get("label")))
        # 4) 只有编号、公式图浮动锚在别处 → 记为无载体，报出来
        if not made:
            made = []

        slot = out.setdefault(idx, {"inline": [], "disp": [], "lead": []})
        if kind == "inline":
            slot["inline"].extend(made)
        else:
            # 块里除公式编号外还有正文句时，正文必须单独成 <p>：整块当公式会把这句吞掉
            lead = _lead_text(b["text"], f.get("label"))
            if lead:
                pel = etree.Element("p")
                try:
                    A.inline(pel, b, src, only_text=lead)
                except Exception:
                    pel.text = lead
                slot["lead"].append(pel)
            slot["disp"].extend(made)
    return out


def _lead_text(text, label):
    """块全文里除公式编号之外的正文残余。编号进 <label>，正文进 <p>，都不许丢。"""
    t = text.replace("\x00OLE\x00", " ").replace("\x00OMML\x00", " ")
    if label:
        t = t.replace(label, " ")
    t = re.sub(r"\(\s*\d+\s*\)", " ", t)      # 其它形态的编号
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 4 else ""


if __name__ == "__main__":
    out_root = "%s/tmp/refbuild/out" % ROOT
    for key in sys.argv[1:]:
        try:
            r = build(key, out_root)
        except Exception as e:
            import traceback
            print("== %s 装配失败: %s" % (key, e))
            traceback.print_exc()
            continue
        rs = r["refs"]
        print("== %s  %d 字节  参考文献 %d 条（字段级 %d，其中人工拆 %d；未拆 %d）"
              % (r["key"], r["size"], r["n_refs"], rs["element"], rs["manual"], rs["mixed"])
              + ("，label %d 条（Word 自动编号）" % rs["label"] if rs.get("label") else "，无 label"))
        for idx, bad in rs["bad"]:
            print("   ⚠️ 人工拆分 idx %s 字段核对失败，已退回 mixed: %s" % (idx, bad))
        print("   DTD: %s" % ("通过" if r["dtd_ok"] else "失败"))
        for e in r["dtd_errs"]:
            print("     %s" % e)
        c = r["coverage"]
        print("   块覆盖: 顶层 %d，已用 %d，弃用 %d，**未交代 %d**"
              % (c["n_top"], c["n_used"], c["n_dropped"], len(c["unexplained"])))
        if c["unexplained"]:
            print("     未交代 idx: %s" % c["unexplained"][:30])
        g = r["graphics"]
        if g["bad"]:
            print("   图片校验: %d 个 graphic，**%d 个不合格**" % (g["n"], len(g["bad"])))
            for x in g["bad"][:8]:
                print("     %s" % x)
        else:
            print("   图片校验: %d 个 graphic 全部存在、可解码、字节与 docx 内嵌媒体 md5 一致" % g["n"])
        m = r["media"]
        if m["missing"]:
            print("   媒体闭合: docx %d 个内嵌媒体，已用 %d，已说明 %d，**未交代 %d**"
                  % (m["n_docx"], m["n_used"], m["n_explained"], len(m["missing"])))
            print("     未交代: %s" % ", ".join(m["missing"][:14]))
        else:
            print("   媒体闭合: docx %d 个内嵌媒体全部有交代（引用 %d / 说明不用 %d）"
                  % (m["n_docx"], m["n_used"], m["n_explained"]))
        if r["subst_errors"]:
            print("   子串校验失败 %d 处:" % len(r["subst_errors"]))
            for e in r["subst_errors"][:10]:
                print("     %s" % e)
        else:
            print("   子串校验: 全部通过")
