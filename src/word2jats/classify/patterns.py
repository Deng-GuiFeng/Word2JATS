"""分类用的正则与词典（集中管理，便于维护与泛化）。

注意：依据调研，docx 样式名不可靠（样例5 全是 Normal），因此角色判定以**内容特征**
为主。这里的模式力求通用，避免过拟合 5 个样例。
"""

from __future__ import annotations

import re

# 邮箱
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# ORCID：16 位数字，可带连字符，末位可为 X
ORCID = re.compile(r"(\d{4}[\- ]?\d{4}[\- ]?\d{4}[\- ]?\d{3}[\dxX])")
ORCID_URL = re.compile(r"orcid\.org/(\S+)")

# 日期：2025/9/21 或 2025-09-21 或 21 September 2025 或 September 21, 2025
DATE_SLASH = re.compile(r"\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b")
DATE_DMY = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{4})\b", re.I)
DATE_MDY = re.compile(
    r"\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b", re.I)

MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

# 稿件类型（article-categories/subject）。键为识别用小写，值为标准写法。
ARTICLE_TYPES = {
    "original research": ("Original Research", "research-article"),
    "research article": ("Original Research", "research-article"),
    "research": ("Original Research", "research-article"),
    "review": ("Review", "review-article"),
    "review article": ("Review", "review-article"),
    "systematic review": ("Systematic Review", "review-article"),
    "article": ("Article", "research-article"),
    "case report": ("Case Report", "case-report"),
    "editorial": ("Editorial", "editorial"),
    "commentary": ("Commentary", "article-commentary"),
    "meta-analysis": ("Meta-Analysis", "review-article"),
    "brief report": ("Brief Report", "brief-report"),
    "short communication": ("Short Communication", "research-article"),
}

# 摘要 / 关键词 / 参考文献 等区段标签
ABSTRACT_LABEL = re.compile(r"^\s*abstract\s*:?\s*$", re.I)
# "Abstract: 正文……" 行内标签(标签后直接跟正文,如 MDPI)
ABSTRACT_INLINE = re.compile(r"^\s*abstract\s*[:：]\s*\S", re.I)
ABSTRACT_PREFIX = re.compile(r"^\s*abstract\s*[:：]\s*", re.I)
KEYWORDS_LABEL = re.compile(r"^\s*(key\s*words?|index\s*terms)\s*:?", re.I)
AFFIL_LABEL = re.compile(r"^\s*affiliations?\s*:?\s*$", re.I)
AUTHOR_LABEL = re.compile(r"^\s*authors?(\s+(information|names?|list))?\s*[:：]\s*$", re.I)
CORRESP_LABEL = re.compile(r"correspond", re.I)
REFERENCES_HEAD = re.compile(
    r"^\s*(references?|bibliography|参考文献|literature\s+cited)\s*:?\s*$", re.I)
EDITOR_LABEL = re.compile(r"(学编|academic\s+editor|handling\s+editor)", re.I)

# 结构化摘要小标题：Background: / Methods: / Results: / Conclusion(s): ...
ABSTRACT_SUBHEAD = re.compile(
    r"^\s*(background|objectives?|aims?|introduction|methods?|materials? and methods|"
    r"results?|conclusions?|discussion|significance|purpose|design|setting)\s*:",
    re.I)

# 图 / 表题注： "Fig. 1." / "Figure 1:" / "Table 1." / "Table 1 |"
# 图/表题注:编号后须紧跟 分隔符(./:/|) 或 空格+大写字母/括号(标题开头),
# 以排除正文交叉引用("Fig. 1 shows…" 这种编号后接小写动词的句子)。
# 注意:关键词大小写不敏感用 (?i:...) 局部标志,但"标题首字母须大写"的 [A-Z(]
# 必须**大小写敏感**——不能给整条正则加 re.I,否则 [A-Z] 会匹配小写,
# 使 "Fig. 1 shows…"(编号后小写)被误判为题注(实测 bug)。
FIG_CAPTION = re.compile(r"^\s*((?i:fig(?:ure)?s?)\.?)\s*(\d+)\s*(?:[\.\:\|│]|\s+[A-Z(])")
TABLE_CAPTION = re.compile(r"^\s*((?i:tables?))\s*(\d+)\s*(?:[\.\:\|│]|\s+[A-Z(])")
# 仅取编号(给 xref 用,宽松匹配)
FIG_NUM = re.compile(r"^\s*(fig(?:ure)?s?\.?)\s*(\d+)", re.I)
TABLE_NUM = re.compile(r"^\s*(tables?)\s*(\d+)", re.I)
# 仅匹配"标签部分"(Fig. N. / Figure N: / Table N |),用于剥离前缀而**不吃掉标题首字**。
# 末尾的 \s* 顺带吞掉标签与标题之间的空白,避免 caption 残留前导空格。
FIG_LABEL_STRIP = re.compile(r"^\s*fig(?:ure)?s?\.?\s*\d+\s*[\.\:\|│]?\s*", re.I)
TABLE_LABEL_STRIP = re.compile(r"^\s*tables?\.?\s*\d+\s*[\.\:\|│]?\s*", re.I)

# 表脚注/缩写释义(紧跟表格之后):Note:/Abbreviations:/Model N:/符号开头,
# 或"缩写, 释义; 缩写, 释义"式列表。用于收入 <table-wrap-foot>。
TABLE_FOOTNOTE = re.compile(
    r"^\s*(note[s]?\s*[:：]|abbreviations?\s*[:：]|"
    r"(?:model|tertile|quartile|group|cohort|stage|grade|type)\s*\w*\s*\d*\s*[:：]|"
    r"[*†‡§¶#]|data (?:are|were)\b|"
    r"[A-Za-z][\w/\-]{0,14}\s+indicates\b|"          # "CHD indicates …" 缩写释义(医学表脚注常式)
    r"[A-Za-z][\w/\-]{0,14}\s*\d*\s*[:：].+[;:].+|"   # "Label N: …; …" 定义式
    r"[A-Za-z0-9/]{1,8},\s+\S.*?;\s*\S)", re.I)

# 参考文献条目编号： "[1]" / "1." 开头
REF_LABEL_BRACKET = re.compile(r"^\s*\[(\d+)\]\s*")
REF_LABEL_DOT = re.compile(r"^\s*(\d+)\.\s+\S")

# 正文小节编号： "2", "2.1", "2.1.1" 后跟标题文本
SECTION_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+(\S.*)$")

# 声明类小节关键词(覆盖各刊常见类型)。同一套关键词派生两种匹配:
#  - DECLARATION_INLINE:前置区"标签: 内容"行(带冒号)
#  - DECLARATION_HEADING:后置区独立标题行(无冒号)
# 标题统一保留 docx 原文(更忠实),不强行归一化。
# 注:Capsule(一句话研究摘要)既非声明小节也非正文,曾误列于此,导致声明区把它当成
# 一节、并越界吞并其后的"Author information:"作者元数据块(实测补充样例3 过抽成 6 节)。
# 已移出关键词,改由下方 CAPSULE_LABEL 作为"声明区终止边界"处理。
_DECL_KW = (
    r"author\s+contributions?|funding(?:\s+statement)?|conflicts?\s+of\s+interest|"
    r"competing\s+interests?|disclosure(?:\s+statement)?|declarations?(?:\s+of\s+[\w\s\-/&]+?)?|"
    r"attestation\s+statement|data\s+(?:availability|sharing)(?:\s+statement)?|"
    r"availability\s+of\s+data(?:\s+and\s+materials?)?|ethics?\s+(?:approval|statement)[\w\s]*?|"
    r"ethical\s+approval[\w\s]*?|consent\s+to\s+participate[\w\s]*?|trial\s+registration|"
    r"acknowledge?ments?|supplementary\s+(?:material|materials|information|data)|"
    r"abbreviations?")
DECLARATION_INLINE = re.compile(r"^\s*(" + _DECL_KW + r")\s*[:：]", re.I)
DECLARATION_HEADING = re.compile(r"^\s*(" + _DECL_KW + r")\s*$", re.I)

# 后置区"起点"判定专用的行内标签子集。
# 这些标签出现在行首 + 冒号时几乎必为后置声明区开端;**刻意排除** abbreviations/
# supplementary/note —— 它们也常作"表脚注/缩写释义"出现在正文段首(如
# "Abbreviations: VDAC, …"),若用于切分会把正文里的表脚注误判为后置区起点(过早截断
# body)。abbreviations 等仍由 DECLARATION_HEADING / ABBREV_HEADING(整行=标题,无内容)
# 安全识别。
_BACK_START_KW = (
    r"author\s+contributions?|funding(?:\s+statement)?|conflicts?\s+of\s+interest|"
    r"competing\s+interests?|disclosure(?:\s+statement)?|"
    r"declarations?\s+of\s+[\w\s\-/&]+?|"
    r"data\s+(?:availability|sharing)(?:\s+statement)?|"
    r"availability\s+of\s+data(?:\s+and\s+materials?)?|ethics?\s+(?:approval|statement)[\w\s]*?|"
    r"ethical\s+approval[\w\s]*?|consent\s+to\s+participate[\w\s]*?|trial\s+registration|"
    r"acknowledge?ments?")
DECLARATION_START_INLINE = re.compile(r"^\s*(" + _BACK_START_KW + r")\s*[:：]", re.I)

# 缩写表标题(独立标题行,允许 "List of " 前缀);整行须正好是标题,故不会误吃
# "Abbreviations: VDAC, …" 这类带内容的表脚注。
ABBREV_HEADING = re.compile(r"^\s*(?:list\s+of\s+)?abbreviations?\s*[:：]?\s*$", re.I)

# Capsule:一句话研究摘要(非声明小节、非正文)。作为"声明区终止边界":本身不成节,
# 也阻止前一声明小节继续吞并其后内容(配合 _is_decl_boundary,见 document.py)。
CAPSULE_LABEL = re.compile(r"^\s*capsule\s*[:：]", re.I)

# 后置区声明类小节标题（IMR 固定，顺序与措辞见 02-数据与映射规格.md §10）
BACK_SECTION_TITLES = [
    "Availability of Data and Materials",
    "Author Contributions",
    "Ethics Approval and Consent to Participate",
    "Acknowledgment",
    "Acknowledgments",
    "Acknowledgement",
    "Funding",
    "Conflict of Interest",
    "Conflicts of Interest",
    "Supplementary Material",
    "Abbreviations",
    "Data Availability Statement",
]

# 常见正文一级小节名（用于无样式时的标题识别兜底，不区分大小写）
COMMON_SECTION_NAMES = {
    "introduction", "background", "related work", "materials and methods",
    "methods", "materials", "methodology", "results", "results and discussion",
    "discussion", "conclusion", "conclusions", "limitations", "future work",
    "experiments", "evaluation", "analysis", "findings", "case presentation",
    "literature review",
}

DOI_IN_TEXT = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)

# 机构关键词（用于无样式时识别 affiliation 行）
INSTITUTION = re.compile(
    r"\b(department|universit|hospital|institut|school|colleg|cent(?:er|re)|"
    r"laborator|clinic|faculty|division|academy|ministry|college|"
    r"research|medic|science|technolog)", re.I)

# affiliation 行：可选前导（上标）数字 + 机构内容
AFFIL_LINE = re.compile(r"^\s*(\d{1,2})\s*[\.\)]?\s*(.+)$")

# 样式名暗示 affiliation（中英文）
AFF_STYLE = re.compile(r"(机构|单位|affiliation|author\s*info)", re.I)
KEYWORD_STYLE = re.compile(r"(关键词|keyword)", re.I)
ABSTRACT_STYLE = re.compile(r"(摘要|abstract)", re.I)

# 结构化摘要的"行内"子标题（可能出现在同一段中部，常加粗）
ABSTRACT_SUBHEAD_INLINE = re.compile(
    r"(Background|Objectives?|Aims?|Methods?|Materials and Methods|Results?|"
    r"Conclusions?|Discussion|Significance|Purpose|Design|Setting|"
    r"Findings(?:\s+in\s+Brief)?|Mechanism|Mechanisms|Introduction|Importance|"
    r"Patients?|Interventions?|Main Outcome Measures?|Limitations?|"
    r"Rationale|Hypothesis|Context|Measurements?|Participants?|Outcomes?)\s*:",
    re.I)


def normalize_orcid(s: str) -> str | None:
    """把任意 ORCID 串规整为 0000-0000-0000-0000 形式。

    必须先用 ORCID 正则**精确定位 ORCID 记号**再规整,不能对整段裸抽数字/字母——
    否则同段里人名含 'X'(如 Xiaoze)会污染抽取(实测 bug)。
    """
    m = ORCID_URL.search(s)
    if m:
        s = m.group(1)
    om = ORCID.search(s.replace(" ", ""))
    if not om:
        return None
    digits = re.sub(r"[^\dxX]", "", om.group(1)).upper()  # 末位校验位 X 统一大写
    if len(digits) != 16:
        return None
    return "-".join([digits[0:4], digits[4:8], digits[8:12], digits[12:16]])
