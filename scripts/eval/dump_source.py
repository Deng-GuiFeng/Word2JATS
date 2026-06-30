#!/usr/bin/env python
"""把源 docx dump 成"人/judge 可读"的结构化文本——Layer-2 逐字审查的源真值。

为什么需要:正确性的真值是"忠实于源 docx",而金标准含出版层编辑(新增声明节、
分配 DOI/日期、拆表等)。judge 要据源 docx 判定每处差异是真缺陷还是出版层差异。
本 dump 按文档顺序给出每段文字 + 加粗/样式提示 + 表格信息,让 judge 不必自己解析 docx。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from word2jats.model.blocks import Paragraph, Table  # noqa: E402
from word2jats.parse.docx_reader import read_docx  # noqa: E402


def dump(docx_path: str) -> str:
    doc = read_docx(docx_path)
    lines = []
    n_tbl = 0
    tcaps = []
    for b in doc.blocks:
        if isinstance(b, Table):
            n_tbl += 1
            nr = len(b.rows)
            nc = max((len(r.cells) for r in b.rows), default=0)
            lines.append("[原生表格 #%d: %d行×%d列]" % (n_tbl, nr, nc))
            continue
        if not isinstance(b, Paragraph):
            continue
        txt = b.text.strip()
        if not txt:
            continue
        flags = []
        if b.is_bold:
            flags.append("B")
        if getattr(b, "style_name", None):
            flags.append("style=%s" % b.style_name)
        tag = ("{%s} " % ",".join(flags)) if flags else ""
        lines.append(tag + txt)
        if re.match(r"\s*table\s+\d+", txt, re.I):
            tcaps.append(txt[:80])
    head = [
        "源文档:%s" % os.path.basename(docx_path),
        "段落数(非空):%d  原生<w:tbl>表数:%d  Table题注数:%d"
        % (sum(1 for l in lines if not l.startswith("[原生表格")), n_tbl, len(tcaps)),
        "Table 题注:" + " | ".join(tcaps) if tcaps else "(无 Table 题注)",
        "=" * 60,
    ]
    return "\n".join(head + lines)


SAMPLES = {
    "1": "样例1/第一组/初始word.docx",
    "2": "样例2/第一组/初始文件.docx",
    "3": "样例3/第一组/初始文件.docx",
    "4": "样例4/第一组/初始文件.docx",
    "5": "样例5/第一组/初始文件.docx",
    "S1": "补充案例-仅输入/选题一/样例1.docx",
    "S2": "补充案例-仅输入/选题一/样例2.docx",
    "S3": "补充案例-仅输入/选题一/样例3.docx",
    "S4": "补充案例-仅输入/选题一/样例4.docx",
    "S5": "补充案例-仅输入/选题一/样例5.docx",
}


def main():
    base = os.path.join(ROOT, "样例")
    out_dir = os.path.join(ROOT, "reports", "eval_judge", "source")
    os.makedirs(out_dir, exist_ok=True)
    for key, rel in SAMPLES.items():
        text = dump(os.path.join(base, rel))
        p = os.path.join(out_dir, "source_%s.txt" % key)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        print("dump %s -> %s (%d 字符)" % (key, p, len(text)))


if __name__ == "__main__":
    main()
