"""把 docx 拆成块清单：每块 idx + 逐字文本 + 格式 + 媒体/公式载体位置。
确定性，无判断。后续 agent 只在此清单上做"哪块归哪个元素"的判定，绝不重打文字。
"""
import json
import sys
import zipfile

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
O = "{urn:schemas-microsoft-com:office:office}"
WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"


def _ln(el):
    return etree.QName(el).localname


def rels(z, part="word/document.xml"):
    """rId -> 目标路径。"""
    d = part.rsplit("/", 1)
    name = "%s/_rels/%s.rels" % (d[0], d[1])
    try:
        root = etree.fromstring(z.read(name))
    except KeyError:
        return {}
    return {r.get("Id"): r.get("Target") for r in root}


def run_text(r):
    """一个 run 的文字：w:t + tab/br/noBreakHyphen 的等价字符。"""
    out = []
    for el in r.iter():
        if not isinstance(el.tag, str):
            continue
        ln = _ln(el)
        if el.tag == W + "t":
            out.append(el.text or "")
        elif ln == "tab":
            out.append("\t")
        elif ln in ("br", "cr"):
            out.append("\n")
        elif ln == "noBreakHyphen":
            out.append("-")
    return "".join(out)


def run_props(r):
    p = r.find(W + "rPr")
    if p is None:
        return {}
    d = {}
    if p.find(W + "b") is not None:
        d["b"] = 1
    if p.find(W + "i") is not None:
        d["i"] = 1
    if p.find(W + "u") is not None:
        d["u"] = 1
    va = p.find(W + "vertAlign")
    if va is not None:
        d["va"] = va.get(W + "val")
    return d


def para_style(p):
    s = p.find(W + "pPr/" + W + "pStyle")
    return s.get(W + "val") if s is not None else ""


def para_outline(p):
    o = p.find(W + "pPr/" + W + "outlineLvl")
    return o.get(W + "val") if o is not None else ""


def para_numpr(p):
    n = p.find(W + "pPr/" + W + "numPr")
    if n is None:
        return None
    ilvl = n.find(W + "ilvl")
    numid = n.find(W + "numId")
    return {"ilvl": ilvl.get(W + "val") if ilvl is not None else None,
            "numId": numid.get(W + "val") if numid is not None else None}


def para_jc(p):
    j = p.find(W + "pPr/" + W + "jc")
    return j.get(W + "val") if j is not None else ""


def omml_text(m):
    """OMML 公式的文字内容（m:t），逐字。"""
    return "".join(t.text or "" for t in m.iter(M + "t"))


def scan_para(p, rmap):
    """一个段落 → {text, runs, media, omml, ole}。文字逐字，不做任何归一。"""
    runs, media, ommls, oles = [], [], [], []
    text_parts = []

    for child in p.iter():
        if not isinstance(child.tag, str):
            continue
        tag = child.tag
        if tag == W + "r":
            # run 内可能嵌 drawing/pict/object，文字单独取
            t = run_text(child)
            if t:
                runs.append({"t": t, **run_props(child)})
                text_parts.append(t)
        elif tag == M + "oMath":
            ommls.append({"t": omml_text(child), "xml": etree.tostring(child, encoding="unicode")})
            runs.append({"t": "", "slot": "omml", "n": len(ommls) - 1})
            text_parts.append("\x00OMML\x00")
        elif tag == W + "drawing":
            for b in child.iter(A + "blip"):
                rid = b.get(R + "embed") or b.get(R + "link")
                if rid:
                    media.append({"kind": "drawing", "rId": rid, "target": rmap.get(rid)})
                    runs.append({"t": "", "slot": "media", "n": len(media) - 1})
            # 文本框内文字
        elif tag == W + "object":
            ole = child.find(O + "OLEObject")
            shape_img = None
            for im in child.iter():
                if isinstance(im.tag, str) and _ln(im) == "imagedata":
                    shape_img = im.get(R + "id")
            oles.append({
                "progId": ole.get("ProgID") if ole is not None else None,
                "rId": ole.get(R + "id") if ole is not None else None,
                "target": rmap.get(ole.get(R + "id")) if ole is not None else None,
                "img_rId": shape_img,
                "img_target": rmap.get(shape_img) if shape_img else None,
            })
            runs.append({"t": "", "slot": "ole", "n": len(oles) - 1})
            text_parts.append("\x00OLE\x00")
        elif tag == W + "pict":
            for im in child.iter():
                if isinstance(im.tag, str) and _ln(im) == "imagedata":
                    rid = im.get(R + "id")
                    if rid and not any(o.get("img_rId") == rid for o in oles):
                        media.append({"kind": "pict", "rId": rid, "target": rmap.get(rid)})

    return {"text": "".join(text_parts), "runs": runs,
            "media": media, "omml": ommls, "ole": oles}


def scan_table(tbl, rmap, idx0):
    """表格 → 行列结构 + 每格文字。单元格内段落也登记为块（可被引用）。"""
    rows = []
    for tr in tbl.findall(W + "tr"):
        is_hdr = tr.find(W + "trPr/" + W + "tblHeader") is not None
        cells = []
        for tc in tr.findall(W + "tc"):
            tcpr = tc.find(W + "tcPr")
            span = 1
            vmerge = None
            if tcpr is not None:
                gs = tcpr.find(W + "gridSpan")
                if gs is not None:
                    span = int(gs.get(W + "val"))
                vm = tcpr.find(W + "vMerge")
                if vm is not None:
                    vmerge = vm.get(W + "val") or "continue"
            texts, cruns = [], []
            for p in tc.findall(W + "p"):
                d = scan_para(p, rmap)
                texts.append(d["text"])
                if cruns and d["runs"]:
                    cruns.append({"t": "\n"})
                cruns.extend(d["runs"])
            cells.append({"t": "\n".join(texts).strip(), "runs": cruns,
                          "span": span, "vmerge": vmerge})
        rows.append({"header": is_hdr, "cells": cells})
    return {"rows": rows}


def build(docx_path):
    z = zipfile.ZipFile(docx_path)
    rmap = rels(z)
    doc = etree.fromstring(z.read("word/document.xml"))
    body = doc.find(W + "body")

    blocks = []
    idx = 0

    def walk(container, depth=0, in_tbl=None):
        nonlocal idx
        for child in container:
            if not isinstance(child.tag, str):
                continue
            if child.tag == W + "p":
                d = scan_para(child, rmap)
                blocks.append({
                    "idx": idx, "kind": "p", "style": para_style(child),
                    "outline": para_outline(child), "num": para_numpr(child),
                    "jc": para_jc(child), "in_table": in_tbl,
                    **d,
                })
                idx += 1
            elif child.tag == W + "tbl":
                tid = idx
                blocks.append({
                    "idx": idx, "kind": "tbl", "style": "", "outline": "", "num": None,
                    "jc": "", "in_table": in_tbl, "text": "",
                    "runs": [], "media": [], "omml": [], "ole": [],
                    "table": scan_table(child, rmap, idx),
                })
                idx += 1
                # 表内段落也登记，便于逐字核对（标 in_table）
                for tr in child.findall(W + "tr"):
                    for tc in tr.findall(W + "tc"):
                        walk(tc, depth + 1, in_tbl=tid)
            elif child.tag == W + "sdt":
                c = child.find(W + "sdtContent")
                if c is not None:
                    walk(c, depth, in_tbl)

    walk(body)

    media_files = sorted(n for n in z.namelist() if n.startswith("word/media/"))
    return {
        "docx": docx_path,
        "n_blocks": len(blocks),
        "blocks": blocks,
        "media_files": [{"name": n, "size": z.getinfo(n).file_size} for n in media_files],
        "rels": rmap,
    }


if __name__ == "__main__":
    for key in sys.argv[1:]:
        data = build("样例数据/%s/初始文件.docx" % key)
        out = "tmp/refbuild/%s.blocks.json" % key
        import os
        os.makedirs("tmp/refbuild", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        top = [b for b in data["blocks"] if b["in_table"] is None]
        print("%s: %d 块（顶层 %d），media %d 个 → %s"
              % (key, data["n_blocks"], len(top), len(data["media_files"]), out))
