"""正文章节树构建。

把 body 区的 IR 块（段落/表格）按标题切分为嵌套 :class:`Section`。
标题识别综合：编号前缀、样式名、加粗短行 + 常见小节名（兼容无样式文档）。
"""

from __future__ import annotations

import re
from typing import Optional

from ..model.blocks import Paragraph, Table, TextRun
from ..model.structured import Section
from . import patterns as P

# 行首列表项符号(项目符号)。识别"被降级成加粗/斜体段的子标题"时需先剥离;
# 否则前导 "● " 既让 is_bold 失效(项目符号 run 非粗体),又让首字符不是大写字母。
# 仅收录无歧义的项目符号字形,**刻意不含** '-' '*' '–' '—' ——它们也常作连字符/
# 强调/破折号出现在正文,纳入会扩大误判面。
_LIST_MARKERS = "●•▪◦‣·∙○◌■□◆◇▶►"
_MARKER_RE = re.compile(r"^[\s" + re.escape(_LIST_MARKERS) + r"]+")


def _strip_list_marker(text: str) -> str:
    """剥离行首的项目符号/列表标记(及其后空白)。"""
    return _MARKER_RE.sub("", text, count=1).strip()


def _emphasis_style(para: Paragraph) -> tuple:
    """返回 (eff_bold, eff_italic):忽略"纯项目符号 run"后,其余有文字的
    run 是否**全部加粗** / **全部斜体**。

    与 Paragraph.is_bold 的区别:这里跳过仅由项目符号组成的 run(如 "● "),
    从而把"前缀带项目符号、其余整段加粗"的子标题(样例2 '● Key Points')判为加粗。"""
    runs = []
    for r in para.runs:
        if not isinstance(r, TextRun):
            continue
        s = r.text.strip()
        if not s or all(ch in _LIST_MARKERS for ch in s):
            continue
        runs.append(r)
    if not runs:
        return (False, False)
    return (all(r.bold for r in runs), all(r.italic for r in runs))


def _is_emphasis_subheading(para: Paragraph, next_is_body: bool) -> bool:
    """保守识别"被降级成普通加粗/斜体段"的(子)节标题。

    根因:某些子节标题在 docx 里既无编号、又无标题样式,仅靠"整段加粗或斜体"
    呈现,旧 _heading_level 漏判,被当成普通正文段,丢失 <sec><title> 结构。实测:
      - 样例3 'Limitations and Future Directions':整段**斜体**(旧逻辑无斜体分支);
      - 样例2 'Key Updates Compared with…' / 9 处 '● Key Points':整段**加粗**,
        但前缀 '● ' 项目符号 run 非粗体,致 is_bold=False 而漏判。
    (金标准里这些确为 <sec><title>,'Key Points' 提示框亦逐个成节,故应收回。)

    保守边界(须**同时**满足,严防误吃正文强调/表内分组标签/图表注):
      1) 剥离行首项目符号后,整段文字全部加粗或全部斜体;
      2) 短(<=14 词)、不以句号结尾、首字母大写(标题式);
      3) 本段不含制表符、且非图/表题注;
      4) **其后紧跟正文段**(由调用方判定:下一块为有文字、不含制表符的普通段)。
         这条最关键——借此排除样例5 表格里 '\t' 分隔的分组标签(Demographic /
         Lab values / Adverse events…),它们整段斜体且短,但后面是制表符数据行而非
         正文,绝不能误判成一堆假小节。"""
    if not next_is_body:
        return False
    text = para.text.strip()
    if "\t" in para.text:                       # 制表符行=表格数据行,非标题
        return False
    if P.FIG_CAPTION.match(text) or P.TABLE_CAPTION.match(text):
        return False
    core = _strip_list_marker(text)
    if not core or len(core) > 200 or core.endswith("."):
        return False
    if len(core.split()) > 14 or not core[0].isupper():
        return False
    eff_bold, eff_ital = _emphasis_style(para)
    return eff_bold or eff_ital


def _heading_level(para: Paragraph) -> Optional[int]:
    """返回标题级别（1/2/3…），非标题返回 None。"""
    text = para.text.strip()
    if not text or len(text) > 200:
        return None
    # 制表符行 = 表格/数据行（tab 模拟表），不是标题
    if "\t" in para.text:
        return None
    # 图/表题注不是标题（交由图表处理）
    if P.FIG_CAPTION.match(text) or P.TABLE_CAPTION.match(text):
        return None

    # 1) 编号前缀：必须是真正的小节号（含内部点 "2.1" 或带尾点 "2."），
    #    以排除 "1 month ..." / "2 or more ..." 这类数据行
    m = re.match(r"^\s*(\d+(?:\.\d+)*)(\.)?\s+(\S.*)$", text)
    if m:
        num, trailing_dot, rest = m.group(1), m.group(2), m.group(3)
        # 编号小节标题:正常短标题(<=120 字符)即可判定。若整段加粗,则放宽长度上限
        # —— 加粗的编号行几乎必为标题(正文/数据行极少整段加粗),长标题不应漏判。
        # (实测样例4 第6节标题超 120 字符被漏判,致整节并入上一节、正文节号 5→7 跳号。)
        if ("." in num or trailing_dot) and (len(rest) <= 120 or para.is_bold):
            return num.count(".") + 1

    # 2) 样式名暗示标题
    sname = (para.style_name or "").lower()
    sid = (para.style_id or "")
    if "heading" in sname or sname in ("title", "subtitle"):
        mlvl = re.search(r"(\d)", sname)
        return int(mlvl.group(1)) if mlvl else 1
    if sid in ("1", "2", "3", "4"):
        return int(sid)

    # 3) 加粗短行 + 常见小节名 / 标题式（兼容无样式）
    words = text.split()
    if para.is_bold and len(words) <= 14 and not text.endswith("."):
        if text.lower().strip(": ") in P.COMMON_SECTION_NAMES:
            return 1
        # 标题式：首字母大写、无句末标点
        if text[0].isupper():
            return 1
    # 非加粗但完全匹配常见小节名（如未加粗的 "Introduction"）
    if text.lower().strip(": ") in P.COMMON_SECTION_NAMES and len(words) <= 6:
        return 1
    return None


def build_sections(blocks: list, id_prefix: str = "S") -> list:
    """把 body 块序列构建为 Section 树。"""
    root_children: list = []
    # 栈：(level, Section, is_emph)  —— is_emph 标记"强调式子标题"(无编号/样式,
    # 仅靠整段加粗或斜体识别),用于让相邻强调式子标题互为兄弟而非彼此嵌套。
    stack: list = []

    def _next_is_body(idx: int) -> bool:
        """下一块是否为"正文段":有文字、不含制表符的普通段(非表格/空段)。
        强调式子标题须"其后紧跟正文段"方成立(见 _is_emphasis_subheading)。"""
        nb = blocks[idx + 1] if idx + 1 < len(blocks) else None
        if not isinstance(nb, Paragraph):
            return False
        return bool(nb.text.strip()) and "\t" not in nb.text

    for i, blk in enumerate(blocks):
        lvl = _heading_level(blk) if isinstance(blk, Paragraph) else None
        title = None
        is_emph = False
        # 编号/样式/常见名均未命中时,再保守地判定"强调式子标题"
        if lvl is None and isinstance(blk, Paragraph) and \
                _is_emphasis_subheading(blk, _next_is_body(i)):
            # 层级 = 最近"结构性"小节 + 1(成为其子节);先弹出尾部的强调式兄弟,
            # 使相邻强调式子标题(如 'Key Updates…' 与 'Key Points')互为兄弟。
            while stack and stack[-1][2]:
                stack.pop()
            lvl = (stack[-1][0] + 1) if stack else 1
            title = _strip_list_marker(blk.text.strip())  # 标题去掉项目符号前缀
            is_emph = True

        if lvl is not None:
            if title is None:
                title = blk.text.strip()
            # 退栈到父级
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            sec = Section(title=title)
            if stack:
                stack[-1][1].subsections.append(sec)
            else:
                root_children.append(sec)
            stack.append((lvl, sec, is_emph))
        else:
            if stack:
                stack[-1][1].blocks.append(blk)
            else:
                # 标题之前的零散块：放入一个隐式首节
                if not root_children:
                    implicit = Section(title="")
                    root_children.append(implicit)
                    stack.append((1, implicit, False))
                    stack[-1][1].blocks.append(blk)
                else:
                    root_children[-1].blocks.append(blk)

    _assign_ids(root_children, id_prefix)
    return root_children


def _assign_ids(sections: list, prefix: str):
    """递归分配 sec id：S1 / S1.SS1 / S1.SS1.SSS1。"""
    for i, sec in enumerate(sections, 1):
        depth = prefix.count(".")
        if prefix == "S":
            sec.sec_id = "S%d" % i
        else:
            # 子节
            tier = ["SS", "SSS", "SSSS"][min(depth, 2)]
            sec.sec_id = "%s.%s%d" % (prefix, tier, i)
        if sec.subsections:
            _assign_ids(sec.subsections, sec.sec_id)
