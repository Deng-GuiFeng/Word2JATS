"""正文交叉引用解析：把纯文本的 "Fig. 1" / "Table 2" / "[5]" / "[8-15]"
转成 JATS ``<xref>``。

挂牌保留（2026-08-21）：本模块已脱离生产路径（v2 渲染未接线，见检查点 B 清单 B-14），
作为图/表交叉引用能力的参考件保留，去向待 B-14 裁决后定。

依据调研：docx 无 REF 域/书签，交叉引用全靠文本识别。这是结构参考中数量最大的元素
（每篇数十至数百个），对"问题解决完整性"贡献显著。
"""

from __future__ import annotations

import re

from .jats import E

# 文献引用： [5] / [8-15] / [1,2,5] / [8–15]
_BIB = re.compile(r"\[\s*([0-9]+(?:\s*[,，–\-]\s*[0-9]+)*)\s*\]")
# 图引用： Fig. 1 / Figure 2 / Figs 1 and 2 / Figures 1–3 / Figs 1, 2（含全角逗号，中文排版）
_FIG = re.compile(r"\b(Figs?\.?|Figures?)\s+(\d+(?:\s*(?:[,，]|&|and|–|to)\s*\d+)*)", re.I)
# 表引用： Table 1 / Tables 2 and 3 / Tables 1–3
_TAB = re.compile(r"\b(Tables?)\s+(\d+(?:\s*(?:[,，]|&|and|–|to)\s*\d+)*)", re.I)
# 公式引用： Eqn 1 / Equation (2)
_EQN = re.compile(r"\b(Eqs?\.?|Equations?)\s+\(?(\d+)\)?", re.I)

# 拼写数字的图/表引用（如 "Table one" / "Figure three"，部分期刊惯用）。
_NUMWORD = {w: i for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
     "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
     "sixteen", "seventeen", "eighteen", "nineteen", "twenty"])}
_WORDS_RE = "|".join(sorted(_NUMWORD, key=len, reverse=True))
_FIG_WORD = re.compile(r"\b(Figs?\.?|Figures?)\s+(%s)\b" % _WORDS_RE, re.I)
_TAB_WORD = re.compile(r"\b(Tables?)\s+(%s)\b" % _WORDS_RE, re.I)


_NUMBER_OR_RANGE = re.compile(r"\d+\s*[–\-]\s*\d+|\d+")


class XrefResolver:
    def __init__(self, ref_nums=None, fig_nums=None, table_nums=None, eqn_nums=None,
                 max_ref=0, ref_targets=None, fig_targets=None,
                 table_targets=None, eqn_targets=None):
        # ref_nums:存在的参考文献显示号集合(处理跳号);兼容旧 max_ref(1..N)
        old_ref_nums = set(ref_nums) if ref_nums else set(range(1, max_ref + 1))
        self.ref_targets = dict(ref_targets) if ref_targets is not None else {
            n: "b%d" % n for n in old_ref_nums
        }
        self.fig_targets = dict(fig_targets) if fig_targets is not None else {
            n: "F%03d" % n for n in set(fig_nums or [])
        }
        self.table_targets = dict(table_targets) if table_targets is not None else {
            n: "T%03d" % n for n in set(table_nums or [])
        }
        self.eqn_targets = dict(eqn_targets) if eqn_targets is not None else {
            n: "E%03d" % n for n in set(eqn_nums or [])
        }
        self.ref_nums = set(self.ref_targets)
        self.fig_nums = set(self.fig_targets)
        self.table_nums = set(self.table_targets)
        self.eqn_nums = set(self.eqn_targets)
        self.count = 0

    # ---- 单串 → token 序列（str / xref 元素） ---------------------- #
    def _linkify(self, text: str):
        if not text:
            return [text] if text else []
        tokens = [text]
        tokens = self._apply(tokens, _BIB, self._bib_repl)
        tokens = self._apply(tokens, _FIG, self._make_repl("fig", self.fig_targets))
        tokens = self._apply(tokens, _TAB, self._make_repl("table", self.table_targets))
        tokens = self._apply(tokens, _FIG_WORD, self._make_word_repl("fig", self.fig_targets))
        tokens = self._apply(tokens, _TAB_WORD, self._make_word_repl("table", self.table_targets))
        tokens = self._apply(tokens, _EQN, self._make_repl("disp-formula", self.eqn_targets))
        return tokens

    def _make_word_repl(self, ref_type, targets):
        def repl(m):
            before = m.string[:m.start()].rstrip()
            if re.search(r"(?i)\b(supp(?:l|lementary|lemental|lement)?)\.?$", before):
                return None
            if m.string[m.end():m.end() + 1].isalpha():
                return None
            word = m.group(2).lower()
            num = _NUMWORD.get(word)
            if num is None or num not in targets:
                return None
            whole = m.group(0)
            start = m.start(2) - m.start(0)
            end = m.end(2) - m.start(0)
            return [whole[:start], self._xref(ref_type, targets[num], whole[start:end]), whole[end:]]
        return repl

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
        # 只给原字符区间套标签；括号、空格、逗号、连接号一个字都不改。
        whole = m.group(0)
        start = m.start(1) - m.start(0)
        end = m.end(1) - m.start(0)
        linked, any_link = self._link_number_text(
            whole[start:end], "bibr", self.ref_targets
        )
        return [whole[:start], *linked, whole[end:]] if any_link else None

    def _make_repl(self, ref_type, targets):
        def repl(m):
            # "Supplementary Fig./Table N" 指补充材料,不应链接到正文同号图表
            before = m.string[:m.start()].rstrip()
            if re.search(r"(?i)\b(supp(?:l|lementary|lemental|lement)?)\.?$", before):
                return None
            # 数字后紧跟字母 = 分图标记(panel letter，如 "Fig. 2A" / "Fig. 4A-C")：
            # 结构参考一律保留为纯文本、不 linkify。若强行 linkify，会把连续的 "2A" 拆成
            # <xref>2</xref>+尾字母 "A"，破坏结构参考/docx 里连续的 "2a" token（L1 丢失），
            # 且凭空多出 fig/table xref（L2 多标）。实测 S03 图引用全是此形态。
            if m.string[m.end():m.end() + 1].isalpha():
                return None
            whole = m.group(0)
            start = m.start(2) - m.start(0)
            end = m.end(2) - m.start(0)
            pieces, any_link = self._link_number_text(whole[start:end], ref_type, targets)
            return [whole[:start], *pieces, whole[end:]] if any_link else None
        return repl

    def _link_number_text(self, text, ref_type, targets):
        """把数字/区间指向真实 ID，返回的可见文字与输入逐字相同。"""
        pieces, cursor, any_link = [], 0, False
        for match in _NUMBER_OR_RANGE.finditer(text):
            pieces.append(text[cursor:match.start()])
            visible = match.group(0)
            range_match = re.fullmatch(r"(\d+)\s*[–\-]\s*(\d+)", visible)
            if range_match:
                first, last = int(range_match.group(1)), int(range_match.group(2))
                numbers = list(range(first, last + 1)) if 0 < last - first <= 40 else []
            else:
                numbers = [int(visible)]
            if numbers and all(number in targets for number in numbers):
                rid = " ".join(targets[number] for number in numbers)
                pieces.append(self._xref(ref_type, rid, visible))
                any_link = True
            else:
                pieces.append(visible)
            cursor = match.end()
        pieces.append(text[cursor:])
        return pieces, any_link

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
