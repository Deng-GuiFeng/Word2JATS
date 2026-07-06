"""用本地多模态模型把"图片表格"重建为 JATS table-wrap。

适用:整张表是一张图片(如样例2),或渲染成图片的制表符表(样例5)。
让 VLM 看图输出 {headers, rows} JSON,再构建标准 JATS 表格。

这是**泛化**手段:任何"表是图片"的情况都能处理,不针对某个样例写死。
失败(模型不可用/解析失败)则返回 None,上层回退(不影响出 XML)。
"""

from __future__ import annotations

import json
import re

from .jats import E, sub

_PROMPT = (
    "这是学术论文里的一张表格图片。把它**完整、忠实**地转录成 JSON,不要编造或省略。\n"
    "要求:\n"
    "1. 格式严格为 {\"headers\": [列标题...], \"rows\": [[单元格...], ...]}。\n"
    "2. **先按数据行确定真实列数**;headers 的个数与每一行的单元格个数都必须等于这个列数,三者严格一致。\n"
    "3. 表头若是多行/堆叠的,要拆成对应的**多列**(不要把相邻列的表头合并成一个)。\n"
    "4. 跨行/跨列的合并单元格,按其覆盖范围在每个位置重复其文本。\n"
    "5. 单元格若是图片(如器械示意图),填 \"[image]\"。\n"
    "6. 只输出 JSON,不要解释、不要 markdown 代码块标记。"
)


def _parse_json(text: str):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


_TEXT_PROMPT = (
    "下面是从学术论文 Word 里用制表符(Tab)对齐拼出来的一张表格的若干行文本。"
    "请把它**忠实**还原成 JSON,不要编造或省略。\n"
    "要求:\n"
    "1. 格式严格为 {\"headers\": [列标题...], \"rows\": [[单元格...], ...]}。\n"
    "2. 制表符大体是列分隔,但可能为对齐用了多个;请按语义判断真实列数,每行单元格数等于 headers 数。\n"
    "3. 跨多列的分组小标题行(如某行只有一个分类名),在该行用该名占第一格、其余留空。\n"
    "4. 只输出 JSON,不要解释、不要代码块标记。\n\n表格文本:\n"
)


def _table_wrap_from_json(data, number, caption_label, caption_runs, inline_math):
    headers = data.get("headers") or []
    rows = data.get("rows") or []
    if not headers or not rows:
        return None
    ncol = len(headers)
    from .jats import append_inline
    wrap = E("table-wrap", id="T%03d" % number)
    if caption_label:
        sub(wrap, "label", caption_label)
    if caption_runs:
        cap = sub(wrap, "caption")
        p = sub(cap, "p")
        append_inline(p, caption_runs, inline_math)
    table = sub(wrap, "table", frame="hsides", rules="groups")
    cg = sub(table, "colgroup")
    for _ in range(ncol):
        sub(cg, "col")
    thead = sub(table, "thead")
    tr = sub(thead, "tr")
    for h in headers:
        sub(tr, "th", str(h), scope="col")
    tbody = sub(table, "tbody")
    for row in rows:
        if not isinstance(row, list):
            continue
        tr = sub(tbody, "tr")
        cells = list(row)[:ncol] + [""] * max(0, ncol - len(row))
        for c in cells:
            txt = "" if (c is None or str(c) == "[image]") else str(c)
            sub(tr, "td", txt)
    return wrap


def build_table_from_text_deterministic(lines, number, caption_label=None,
                                        caption_runs=None, inline_math=None):
    """**确定性**把制表符表还原成 table-wrap(不依赖 LLM,不编造):每行按 Tab 切、strip、
    丢掉空单元(连续 Tab 是对齐填充)、左对齐,列数取各行最大值,首行为表头。文字全部来自
    docx,只加结构。列切分对不齐时结构可能与金标准不完全一致,但保证不丢表、不丢字。"""
    rows = [[c.strip() for c in ln.split("\t") if c.strip()] for ln in lines]
    rows = [r for r in rows if r]
    if len(rows) < 2:
        return None
    ncol = max(len(r) for r in rows)
    headers = rows[0] + [""] * (ncol - len(rows[0]))
    return _table_wrap_from_json({"headers": headers, "rows": rows[1:]},
                                 number, caption_label, caption_runs, inline_math)


def build_table_from_image(llm, image_path, number, caption_label=None,
                           caption_runs=None, inline_math=None, target_hint=None):
    """看图重建一张表 → table-wrap 元素;失败返回 None。

    :param target_hint: 当一页有多张表时,用它**定向**指明只转录哪一张(标签+大致行列),
        避免整页多表混转或漏转。
    """
    if llm is None or not getattr(llm, "enabled", False):
        return None
    prompt = _PROMPT
    if target_hint:
        prompt = _PROMPT + "\n**重要**:本页可能有多张表格," + target_hint + \
            " 请**只**转录这一张,忽略页面上的其它表格与正文。"
    data = _parse_json(llm.chat_vision(prompt, image_path, max_tokens=4096))
    if not isinstance(data, dict):
        return None
    return _table_wrap_from_json(data, number, caption_label, caption_runs, inline_math)


def build_table_from_text(llm, lines, number, caption_label=None,
                          caption_runs=None, inline_math=None):
    """把制表符表的若干行文本交给文本模型结构化 → table-wrap;失败返回 None。"""
    if llm is None or not getattr(llm, "enabled", False) or not lines:
        return None
    user = _TEXT_PROMPT + "\n".join(lines)
    data = llm.extract_json("你是表格结构化助手,只输出 JSON。", user, max_tokens=4096)
    if not isinstance(data, dict):
        return None
    return _table_wrap_from_json(data, number, caption_label, caption_runs, inline_math)
