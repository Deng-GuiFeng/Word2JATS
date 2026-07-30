"""表述层解析助手：从题注块剥"Fig. N."前缀、定位 References 标题、规整 ORCID。

这些是**表示层解析**（把已有文本切成 label + 正文、认出 "References" 这个词），
不是"这段内容是什么角色"的语义判定——语义判定已全部交给 LLM。故保留、复用。
"""

from __future__ import annotations

import re

# 剥"标签部分"（Fig. N. / Table N |），不吃标题首字
FIG_LABEL_STRIP = re.compile(r"^\s*fig(?:ure)?s?\.?\s*\d+\s*[\.\:\|│]?\s*", re.I)
TABLE_LABEL_STRIP = re.compile(r"^\s*tables?\.?\s*\d+\s*[\.\:\|│]?\s*", re.I)

# References / Bibliography 标题（整行）
REFERENCES_HEAD = re.compile(
    r"^\s*(references?|bibliography|参考文献|literature\s+cited)\s*:?\s*$", re.I)
# 参考条目编号 "[1]" 开头
REF_LABEL_BRACKET = re.compile(r"^\s*\[(\d+)\]")

ORCID = re.compile(r"(\d{4}[\- ]?\d{4}[\- ]?\d{4}[\- ]?\d{3}[\dxX])")
ORCID_URL = re.compile(r"orcid\.org/(\S+)")


def normalize_orcid(s):
    """把任意 ORCID 串规整为 0000-0000-0000-000X。先精确定位 ORCID 记号再规整，
    避免把人名里的 X（如 Xiaoze）污染进来。"""
    if not s:
        return None
    m = ORCID_URL.search(s)
    if m:
        s = m.group(1)
    om = ORCID.search(s.replace(" ", ""))
    if not om:
        return None
    digits = re.sub(r"[^\dxX]", "", om.group(1)).upper()
    if len(digits) != 16:
        return None
    return "-".join([digits[0:4], digits[4:8], digits[8:12], digits[12:16]])


def strip_fig_label(text):
    """返回 (label, prefix_len)：从题注文本剥 'Fig. N' 前缀。"""
    m = FIG_LABEL_STRIP.match(text)
    if m:
        return text[:m.end()].strip(), m.end()
    return None, 0


def strip_table_label(text):
    m = TABLE_LABEL_STRIP.match(text)
    if m:
        return text[:m.end()].strip(), m.end()
    return None, 0


# 无显式标题的裸声明 → IMR house-style 规范标题（B 档结构性标签）。
# 取值来自 10 例结构参考的观测形态（含 <ack>/<glossary> 容器由标题关键词路由）。
CANON_DECL_TITLE = {
    "funding": "Funding",
    "conflict": "Conflicts of Interest",
    "ethics": "Ethics Approval and Consent to Participate",
    "consent": "Ethics Approval and Consent to Participate",
    "acknowledgments": "Acknowledgment",
    "author-contributions": "Author Contributions",
    "data-availability": "Availability of Data and Materials",
    "abbreviations": "Abbreviations",
    "supplementary": "Supplementary Material",
    "ai-declaration": "Declaration of AI and AI-Assisted Technologies in the Writing Process",
}


def strip_title_prefix_len(text, title):
    """若 text 以 title 开头（忽略首尾空白/大小写），返回应剥离的字符数（含其后分隔符）；否则 0。"""
    if not title:
        return 0
    t = text.lstrip()
    lead = len(text) - len(t)
    if t[:len(title)].casefold() != title.casefold():
        return 0
    j = lead + len(title)
    # 跳过标签后的分隔符与空白（": " / ". " / " " …）
    while j < len(text) and text[j] in " :：.\t-—|":
        j += 1
    return j
