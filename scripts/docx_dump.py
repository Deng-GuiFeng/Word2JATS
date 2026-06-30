#!/usr/bin/env python
"""把 docx 结构化转储为纯文本(段落含样式名、表格、图片清单),供标签 Team 核对源文档。

用法: python scripts/docx_dump.py <docx> [--tables] [--start N --end M]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from word2jats.parse.docx_reader import read_docx  # noqa: E402
from word2jats.model.blocks import Paragraph, Table  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--tables", action="store_true", help="展开表格单元格(含 grid_span/v_merge)")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10**9)
    args = ap.parse_args()
    doc = read_docx(args.docx)

    blocks = doc.blocks
    pi = 0
    for b in blocks:
        if isinstance(b, Paragraph):
            t = b.text.rstrip()
            if t or b.images or b.maths:
                if args.start <= pi <= args.end:
                    extra = ""
                    if b.images:
                        extra += " [IMG x%d]" % len(b.images)
                    if b.maths:
                        extra += " [MATH x%d]" % len(b.maths)
                    # 上标片段(用于看作者/单位标记)
                    sup = "".join(r.text for r in b.runs
                                  if hasattr(r, "superscript") and r.superscript and r.text)
                    if sup:
                        extra += " [SUP:%s]" % sup
                    print("P%-4d [%s]%s %s" % (pi, b.style_name or "-", extra, t[:300]))
                pi += 1
        elif isinstance(b, Table):
            if args.start <= pi <= args.end:
                print("TABLE%-3d 行=%d" % (pi, len(b.rows)))
                if args.tables:
                    for ri, row in enumerate(b.rows):
                        cells = ["%r(gs=%s,vm=%s)" % (c.text.strip()[:25], c.grid_span, c.v_merge)
                                 for c in row.cells]
                        print("   行%d: %s" % (ri, " | ".join(cells)))
            pi += 1


if __name__ == "__main__":
    main()
