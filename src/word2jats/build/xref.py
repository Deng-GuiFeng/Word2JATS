"""正文交叉引用解析：把纯文本的 "Fig. 1" / "Table 2" / "[5]" / "[8-15]"
转成 JATS ``<xref>``。

依据调研：docx 无 REF 域/书签，交叉引用全靠文本识别。这是金标准中数量最大的元素
（每篇数十至数百个），对"问题解决完整性"贡献显著。
"""

from __future__ import annotations

import re

from lxml import etree

from .jats import E

# 文献引用： [5] / [8-15] / [1,2,5] / [8–15]
_BIB = re.compile(r"\[\s*([0-9]+(?:\s*[,，–\-]\s*[0-9]+)*)\s*\]")
# 图引用： Fig. 1 / Figure 2 / Figs 1 and 2 / Figures 1–3 / Figs 1, 2（含全角逗号，中文排版）
_FIG = re.compile(r"\b(Figs?\.?|Figures?)\s+(\d+(?:\s*(?:[,，]|&|and|–|to)\s*\d+)*)", re.I)
# 表引用： Table 1 / Tables 2 and 3 / Tables 1–3
_TAB = re.compile(r"\b(Tables?)\s+(\d+(?:\s*(?:[,，]|&|and|–|to)\s*\d+)*)", re.I)
# 公式引用： Eqn 1 / Equation (2)
_EQN = re.compile(r"\b(Eqs?\.?|Equations?)\s+\(?(\d+)\)?", re.I)


def _expand_ranges(s: str) -> str:
    """把 "8–15" / "1-3" 这类区间展开为 "8,9,…,15"(仅在间隔合理时,避免误展页码)。"""
    def rep(m):
        a, b = int(m.group(1)), int(m.group(2))
        if 0 < b - a <= 40:
            return ",".join(str(x) for x in range(a, b + 1))
        return m.group(0)
    return re.sub(r"(\d+)\s*[–\-]\s*(\d+)", rep, s)


class XrefResolver:
    def __init__(self, ref_nums=None, fig_nums=None, table_nums=None, eqn_nums=None,
                 max_ref=0):
        # ref_nums:存在的参考文献显示号集合(处理跳号);兼容旧 max_ref(1..N)
        self.ref_nums = set(ref_nums) if ref_nums else set(range(1, max_ref + 1))
        self.fig_nums = set(fig_nums or [])
        self.table_nums = set(table_nums or [])
        self.eqn_nums = set(eqn_nums or [])
        self.count = 0

    # ---- 单串 → token 序列（str / xref 元素） ---------------------- #
    def _linkify(self, text: str):
        if not text:
            return [text] if text else []
        tokens = [text]
        tokens = self._apply(tokens, _BIB, self._bib_repl)
        tokens = self._apply(tokens, _FIG, self._make_repl("fig", "F%03d", self.fig_nums))
        tokens = self._apply(tokens, _TAB, self._make_repl("table", "T%03d", self.table_nums))
        tokens = self._apply(tokens, _EQN, self._make_repl("disp-formula", "E%03d", self.eqn_nums))
        return tokens

    def _apply(self, tokens, regex, repl):
        out = []
        for tok in tokens:
            if not isinstance(tok, str):
                out.append(tok)
                continue
            pos = 0
            for m in regex.finditer(tok):
                pieces = repl(m)
                if pieces is None:
                    continue  # 不匹配（如 rid 不存在），保留原文不切分
                if m.start() > pos:
                    out.append(tok[pos:m.start()])
                out.extend(pieces)
                pos = m.end()
            if pos < len(tok):
                out.append(tok[pos:])
        return _merge_strs(out)

    def _bib_repl(self, m):
        # 展开区间(8–15→8,9,…,15)后,每个编号各自链接;逗号分隔,整体加方括号
        expanded = _expand_ranges(m.group(1))
        pieces, any_link = ["["], False
        first = True
        for part in re.split(r"(\d+)", expanded):
            if part.isdigit():
                num = int(part)
                if not first:
                    pieces.append(", ")
                first = False
                ok = num in self.ref_nums
                pieces.append(self._xref("bibr", "b%d" % num, part, valid=ok))
                any_link = any_link or ok
        pieces.append("]")
        return pieces if any_link else None

    def _make_repl(self, ref_type, id_fmt, valid_set):
        def repl(m):
            # "Supplementary Fig./Table N" 指补充材料,不应链接到正文同号图表
            before = m.string[:m.start()].rstrip()
            if re.search(r"(?i)\b(supp(?:l|lementary|lemental|lement)?)\.?$", before):
                return None
            keyword, numlist = m.group(1), _expand_ranges(m.group(2))
            # 数字列表里每个数字各自链接,分隔符(and/,/–)保留为文本;
            # 仅当至少一个目标存在时才生成,否则保留原文(避免悬空 IDREF)
            pieces, any_link = [keyword + " "], False
            for part in re.split(r"(\d+)", numlist):
                if part.isdigit() and int(part) in valid_set:
                    pieces.append(self._xref(ref_type, id_fmt % int(part), part))
                    any_link = True
                elif part:
                    pieces.append(part)
            return pieces if any_link else None
        return repl

    def _xref(self, ref_type, rid, text, valid=True):
        if not valid:
            return text  # rid 不存在则退化为纯文本
        self.count += 1
        x = E("xref", text, **{"ref-type": ref_type, "rid": rid})
        return x

    # 可递归进入的内联元素（引用常被加粗/斜体/上标包裹）
    _INLINE = {"bold", "italic", "sup", "sub", "sc", "monospace", "underline",
               "overline", "roman", "sans-serif", "styled-content", "named-content"}

    # ---- 遍历树并改写 -------------------------------------------------- #
    def process(self, root):
        for tag in ("p", "td", "th"):
            for el in root.iter(tag):
                self._process_element(el)
        return self.count

    def _process_element(self, el):
        # 收集内容序列：text、子元素（及其 tail）；并递归进入内联子元素的内部文本，
        # 使 <bold>Fig. 1</bold>、<sup>[5]</sup> 等包裹的引用也能被链接。
        seq = []
        if el.text:
            seq.extend(self._linkify(el.text))
        children = list(el)
        for c in children:
            tail = c.tail
            c.tail = None
            el.remove(c)
            ln = c.tag if isinstance(c.tag, str) else ""
            if "}" in ln:
                ln = ln.split("}", 1)[1]
            if ln in self._INLINE:
                self._process_element(c)  # 递归处理内联子元素内部
            seq.append(c)
            if tail:
                seq.extend(self._linkify(tail))
        _rebuild(el, seq)


def _merge_strs(tokens):
    out = []
    for t in tokens:
        if isinstance(t, str) and out and isinstance(out[-1], str):
            out[-1] += t
        else:
            out.append(t)
    return out


def _rebuild(el, seq):
    el.text = None
    last_el = None
    for tok in seq:
        if isinstance(tok, str):
            if last_el is None:
                el.text = (el.text or "") + tok
            else:
                last_el.tail = (last_el.tail or "") + tok
        else:
            el.append(tok)
            last_el = tok
