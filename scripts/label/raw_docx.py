#!/usr/bin/env python
"""**完全独立于 word2jats** 的原始 docx 读取器(直接解析 word/document.xml)。

用于伪标签生成:标签的源视角必须独立于被测系统,绝不复用 word2jats 的解析器。
仅依赖标准库 zipfile + lxml。

用法:
  python scripts/label/raw_docx.py <docx> paras [--start N --end M]   # 段落(样式/上标/全文)
  python scripts/label/raw_docx.py <docx> tables                       # 所有表格网格(gridSpan/vMerge)
  python scripts/label/raw_docx.py <docx> table <i>                    # 第 i 张表完整网格
  python scripts/label/raw_docx.py <docx> images                       # 图片清单(part 名)
  python scripts/label/raw_docx.py <docx> headers                      # 页眉页脚文本
  python scripts/label/raw_docx.py <docx> dumpimg <i> <out.png>        # 导出第 i 张内嵌图
"""
import sys
import zipfile
from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _doc_xml(z):
    return etree.fromstring(z.read("word/document.xml"))


def _runs(p):
    """返回 [(text, bold, italic, vertAlign)]。"""
    out = []
    for r in p.iter(W + "r"):
        text = "".join(t.text or "" for t in r.iter(W + "t"))
        if not text:
            # 也可能是 tab/br
            if r.find(W + "tab") is not None:
                text = "\t"
            elif r.find(W + "br") is not None:
                text = "\n"
        rpr = r.find(W + "rPr")
        b = i = False
        va = ""
        if rpr is not None:
            b = rpr.find(W + "b") is not None
            i = rpr.find(W + "i") is not None
            v = rpr.find(W + "vertAlign")
            if v is not None:
                va = v.get(W + "val", "")
        if text:
            out.append((text, b, i, va))
    return out


def _ptext(p):
    return "".join(r[0] for r in _runs(p))


def _pstyle(p):
    ppr = p.find(W + "pPr")
    if ppr is not None:
        s = ppr.find(W + "pStyle")
        if s is not None:
            return s.get(W + "val", "")
    return ""


def _cell_text(tc):
    return " ".join(_ptext(p).strip() for p in tc.findall(W + "p")).strip()


def _cell_props(tc):
    gs, vm = 1, None
    tcpr = tc.find(W + "tcPr")
    if tcpr is not None:
        g = tcpr.find(W + "gridSpan")
        if g is not None:
            gs = int(g.get(W + "val", "1"))
        v = tcpr.find(W + "vMerge")
        if v is not None:
            vm = v.get(W + "val", "continue")  # 缺省 val = continue
    return gs, vm


def iter_body(doc):
    body = doc.find(W + "body")
    return list(body) if body is not None else []


def main():
    docx, cmd = sys.argv[1], sys.argv[2]
    z = zipfile.ZipFile(docx)
    doc = _doc_xml(z)

    if cmd == "paras":
        start = end = None
        if "--start" in sys.argv:
            start = int(sys.argv[sys.argv.index("--start") + 1])
        if "--end" in sys.argv:
            end = int(sys.argv[sys.argv.index("--end") + 1])
        idx = 0
        for el in iter_body(doc):
            if el.tag == W + "p":
                txt = _ptext(el).rstrip()
                if not txt.strip():
                    continue
                if (start is None or idx >= start) and (end is None or idx <= end):
                    sup = "".join(t for t, b, i, va in _runs(el) if va == "superscript")
                    sub = "".join(t for t, b, i, va in _runs(el) if va == "subscript")
                    tag = "[SUP:%s]" % sup if sup else ""
                    tag += "[SUB:%s]" % sub if sub else ""
                    print("P%-4d <%s>%s %s" % (idx, _pstyle(el) or "-", tag, txt[:320]))
                idx += 1
            elif el.tag == W + "tbl":
                print("TBL%-4d (%d 行)" % (idx, len(el.findall(W + "tr"))))
                idx += 1

    elif cmd in ("tables", "table"):
        tbls = [el for el in iter_body(doc) if el.tag == W + "tbl"]
        sel = range(len(tbls)) if cmd == "tables" else [int(sys.argv[3])]
        for ti in sel:
            tb = tbls[ti]
            rows = tb.findall(W + "tr")
            grid = tb.find(W + "tblGrid")
            ncol = len(grid.findall(W + "gridCol")) if grid is not None else None
            print("== 表%d: %d 行, gridCols=%s ==" % (ti, len(rows), ncol))
            for ri, tr in enumerate(rows if cmd == "table" else rows[:5]):
                cells = []
                for tc in tr.findall(W + "tc"):
                    gs, vm = _cell_props(tc)
                    cells.append("%r(gs=%d,vm=%s)" % (_cell_text(tc)[:30], gs, vm))
                print("  行%d: %s" % (ri, " | ".join(cells)))

    elif cmd == "images":
        n = 0
        for el in iter_body(doc):
            for blip in el.iter(A + "blip"):
                n += 1
        media = [x for x in z.namelist() if x.startswith("word/media/")]
        print("正文 blip 引用数:", n, "| media 文件:", media)

    elif cmd == "headers":
        for name in z.namelist():
            if name.startswith("word/header") or name.startswith("word/footer"):
                d = etree.fromstring(z.read(name))
                txt = " ".join(_ptext(p).strip() for p in d.iter(W + "p") if _ptext(p).strip())
                if txt:
                    print("%s: %s" % (name, txt[:200]))

    elif cmd == "dumpimg":
        i, out = int(sys.argv[3]), sys.argv[4]
        media = sorted(x for x in z.namelist() if x.startswith("word/media/"))
        with open(out, "wb") as f:
            f.write(z.read(media[i]))
        print("wrote", out, "from", media[i])


if __name__ == "__main__":
    main()
