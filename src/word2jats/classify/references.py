"""参考文献切分与解析。

docx 中参考文献为无结构纯文本。本模块先按编号切条，再做轻量字段解析。
解析置信度高 → 标记 structured（构建为 element-citation）；否则保留原文走 mixed-citation。
"""

from __future__ import annotations

import re
from typing import Optional

from ..model.blocks import Paragraph
from ..model.structured import Reference
from . import patterns as P


# "姓 缩写,"作者列表起始样式(姓可含连字符/重音;后接 1–4 个大写缩写 + 逗号)。
# 用于在编号列表中辨认"缺了编号前缀、却确为新条目"的参考文献(见 is_continuation)。
_REF_START = re.compile(r"^[A-Z][A-Za-z'’À-ſ\-]+\s+[A-Z]{1,4},")


def split_reference_blocks(blocks: list) -> list:
    """把参考文献区的段落切成一条条引用（label, raw_text）。

    两种排版都要支持:
    (1) 有编号前缀(`[n]` 或 `n.`):按编号切条,无编号的段视为上一条的续行(悬挂缩进);
    (2) 无编号:每个段落即一条参考文献(IMR 这批新样例如此)。
    先扫一遍判断是否带编号,再据此切分——避免把无编号列表误并成一条。
    """
    paras = [b.text.strip() for b in blocks
             if isinstance(b, Paragraph) and b.text.strip()]
    if not paras:
        return []

    refs = []          # list[[label, text]]
    seen_label = False  # 是否已遇到第一个带编号的条目

    def is_continuation(t: str) -> bool:
        """t 是否为上一条参考文献的续行(折行),而非新条目。

        规则(兼顾两种排版):
        - 带编号([n]/n.) → 一定是新条目。
        - 以小写字母开头 → 续行(承接上句)。
        - **已进入带编号列表后**(seen_label)出现的无编号段 → 视为上一条的尾部续行
          (如折行的"期刊名. 年;卷:页. doi");此前(首个编号之前)的无编号段则各自成条
          (应对编号从中途开始、前几条丢了编号的情形)。
        """
        if not refs:
            return False
        if P.REF_LABEL_BRACKET.match(t) or P.REF_LABEL_DOT.match(t):
            return False
        if t.lstrip("([")[:1].islower():
            return True
        # 即便已处于编号列表中,若本段以"姓 缩写,"的作者列表样式开头,它是**新条目**——
        # docx 自动编号有时未落入 run 文本,导致某条参考文献缺失 "[n]" 前缀;此时不能把它
        # 误并为上一条的续行(实测样例2:无编号的第 69 条 Freitas-Ferraz 被并入第 68 条并污染页码)。
        # 续行通常是"期刊名. 年;卷:页. doi"片段,不会匹配此作者列表样式。
        if _REF_START.match(t):
            return False
        return seen_label

    for t in paras:
        mb = P.REF_LABEL_BRACKET.match(t)
        md = P.REF_LABEL_DOT.match(t)
        if is_continuation(t):
            refs[-1][1] = (refs[-1][1] + " " + t).strip()
            continue
        if mb:
            label, body = mb.group(1), t[mb.end():].strip()
            seen_label = True
        elif md:
            label, body = md.group(1), t[md.end() - 1:].strip()
            seen_label = True
        else:
            label, body = str(len(refs) + 1), t
        refs.append([label, body])
    return [(lbl, txt) for lbl, txt in refs if txt]


def _parse_one(label: str, text: str) -> Reference:
    ref = Reference(label="[%s]" % label if label else "", raw_text=text)
    # DOI
    md = P.DOI_IN_TEXT.search(text)
    if md:
        ref.doi = md.group(0).rstrip(".")
    # 年份（19xx/20xx）
    my = re.search(r"\b(19|20)\d{2}\b", text)
    if my:
        ref.year = my.group(0)
    # 卷:页  形如 "265: 85–90" 或 "2020; 265: 85-90"
    mvp = re.search(r"(\d+)\s*:\s*(\d+)\s*[–\-]\s*(\d+)", text)
    if mvp:
        ref.volume, ref.fpage, ref.lpage = mvp.group(1), mvp.group(2), mvp.group(3)
    return ref


def parse_references(blocks: list) -> list:
    raw = split_reference_blocks(blocks)
    return [_parse_one(lbl, txt) for lbl, txt in raw if txt]
