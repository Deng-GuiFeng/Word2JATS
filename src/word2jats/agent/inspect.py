"""视觉侧:让本地多模态模型(VLM)逐页"看"渲染图,给出页面上实际有什么。

这是 Agent 视觉闭环的核心质量托底——用"人眼真值"(渲染页)独立审视我们的产出,
而不是再用一遍解析器自证(那是循环论证)。

聚焦于**启发式已知薄弱、且视觉上无歧义**的结构类别(逐条对照见迭代日志):
- 表格:图片表/制表符表是启发式做不了的硬骨头(评测报告 §4 限制),数量与标签最关键;
- 图、块级公式:计数;
- 标题:层级与遗漏;
- 作者块、参考文献条目数:前置/后置区的完整性。

每页一次 VLM 调用,结果由 llm 客户端按图片哈希缓存,闭环多轮复用、零重复成本。
模型不可用 / 解析失败 → 该页返回空记录,闭环据缺失信息保守处理(不误伤已正确的部分)。
"""

from __future__ import annotations

import json
import re

_PAGE_PROMPT = (
    "你在审阅一篇学术论文 PDF 的**一页**渲染图。请**只**依据图中可见内容,客观清点"
    "这一页上的结构元素,输出严格 JSON(不要解释、不要 markdown 代码块标记):\n"
    "{\n"
    '  "tables": [{"label": "如 Table 1,没有编号填 null", "rows": 估计数据行数(整数), '
    '"cols": 估计列数(整数), "is_image": 这张表是否整体是一张图片(true/false)}],\n'
    '  "figures": [{"label": "如 Figure 2,没有编号填 null"}],\n'
    '  "display_equations": 单独成行/带编号的块级公式个数(整数),\n'
    '  "headings": ["本页可见的章节标题原文,按出现顺序,如 3. Results / 2.1 Data Source"],\n'
    '  "has_author_block": 本页是否含作者姓名列表(标题正下方那种)(true/false),\n'
    '  "reference_items": 本页"参考文献/References"列表里的条目个数(整数,没有填 0)\n'
    "}\n"
    "清点规则:\n"
    "- 表格:标题行(表头)不算入 rows;一张表横跨多页时只数本页可见的部分,"
    "并在 label 标出。整张表是一幅图片(无法选中文字的)务必 is_image=true。\n"
    "- 只把**真正的章节标题**放进 headings(加粗/编号的小节名,如 Introduction、3.1 ...),"
    "不要把论文大标题(文章题目)、Original Research/Review 这类栏目名、作者名放进 headings。\n"
    "- 图注/表注文字本身不是 figures/tables,不要重复计数;以图、表实体为准。\n"
    "- 数不确定时给最接近的整数,不要留空。"
)


def _parse(text: str):
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


def _empty():
    return {"tables": [], "figures": [], "display_equations": 0,
            "headings": [], "has_author_block": False, "reference_items": 0}


def read_page(llm, page_png: str) -> dict:
    """单页视觉清点 → 规整记录;失败返回空记录。"""
    if llm is None or not getattr(llm, "enabled", False):
        return _empty()
    raw = llm.chat_vision(_PAGE_PROMPT, page_png, max_tokens=2048)
    data = _parse(raw)
    if not isinstance(data, dict):
        return _empty()
    rec = _empty()
    tabs = data.get("tables")
    if isinstance(tabs, list):
        for t in tabs:
            if isinstance(t, dict):
                rec["tables"].append({
                    "label": t.get("label"),
                    "rows": _int(t.get("rows")),
                    "cols": _int(t.get("cols")),
                    "is_image": bool(t.get("is_image")),
                })
    figs = data.get("figures")
    if isinstance(figs, list):
        for f in figs:
            if isinstance(f, dict):
                rec["figures"].append({"label": f.get("label")})
            elif isinstance(f, str):
                rec["figures"].append({"label": f})
    rec["display_equations"] = _int(data.get("display_equations")) or 0
    hs = data.get("headings")
    if isinstance(hs, list):
        rec["headings"] = [str(h).strip() for h in hs if str(h).strip()]
    rec["has_author_block"] = bool(data.get("has_author_block"))
    rec["reference_items"] = _int(data.get("reference_items")) or 0
    return rec


_AUTHORS_PROMPT = (
    "这是学术论文首页渲染图。请提取**标题正下方的作者署名列表**(只要作者,不要把单位、"
    "邮箱、编辑、通讯地址当作者)。严格输出 JSON(不要解释、不要代码块标记):\n"
    '{"authors":[{"surname":"姓","given":"名","aff":["1","2"],'
    '"corresponding":false,"equal":false}]}\n'
    "- 英文姓名名在前姓在后时,姓取最后一个词,其余为名;\n"
    "- aff = 作者上标里的单位编号(数字),没有就空数组;\n"
    "- corresponding = 是否带 * 通讯标记;equal = 是否带 †/‡/# 共同贡献标记;\n"
    "- 把同一行/跨行的所有作者都列全,注意被逗号、分号、'and' 分隔的多位作者。只输出 JSON。"
)


def read_authors(llm, page_png: str):
    """看首页图提取完整作者列表(供闭环核对作者是否抽全);失败返回 None。"""
    if llm is None or not getattr(llm, "enabled", False):
        return None
    data = _parse(llm.chat_vision(_AUTHORS_PROMPT, page_png, max_tokens=2048))
    if not isinstance(data, dict):
        return None
    items = data.get("authors")
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        surname = str(it.get("surname", "")).strip()
        given = str(it.get("given", "")).strip()
        if not (surname or given):
            continue
        out.append({
            "surname": surname, "given": given,
            "aff": [str(x) for x in (it.get("aff") or []) if str(x).strip()],
            "corresponding": bool(it.get("corresponding")),
            "equal": bool(it.get("equal")),
        })
    return out


def _int(v):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return None


def inspect_pages(llm, page_pngs: list, max_workers: int = 6) -> dict:
    """并发清点所有页,聚合为文档级视觉证据。

    返回:
      {
        "per_page": [{page:int, ...record...}],
        "tables": [{label,rows,cols,is_image,page}],   # 跨页合并同 label 的续表
        "figures": [{label,page}],
        "display_equations": int,
        "headings": [{text,page}],
        "reference_items": int,
        "author_block_pages": [int],
      }
    """
    from concurrent.futures import ThreadPoolExecutor
    recs = [None] * len(page_pngs)

    def work(i_p):
        i, p = i_p
        recs[i] = read_page(llm, p)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(work, list(enumerate(page_pngs))))

    agg = {"per_page": [], "tables": [], "figures": [], "display_equations": 0,
           "headings": [], "reference_items": 0, "author_block_pages": []}
    seen_table_labels = {}
    for i, rec in enumerate(recs):
        rec = rec or _empty()
        pageno = i + 1
        agg["per_page"].append(dict(page=pageno, **rec))
        for t in rec["tables"]:
            lbl = (t.get("label") or "").strip() or None
            if lbl and lbl in seen_table_labels:
                # 续表:合并行数,不新增表实体
                seen_table_labels[lbl]["rows"] = (seen_table_labels[lbl].get("rows") or 0) + (t.get("rows") or 0)
                continue
            ent = dict(t, page=pageno)
            agg["tables"].append(ent)
            if lbl:
                seen_table_labels[lbl] = ent
        for f in rec["figures"]:
            agg["figures"].append(dict(f, page=pageno))
        agg["display_equations"] += rec["display_equations"]
        for h in rec["headings"]:
            agg["headings"].append({"text": h, "page": pageno})
        agg["reference_items"] += rec["reference_items"]
        if rec["has_author_block"]:
            agg["author_block_pages"].append(pageno)
    return agg
