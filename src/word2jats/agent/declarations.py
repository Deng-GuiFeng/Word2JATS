"""Agent 闭环——LLM 主导的后置声明识别与拆分。

为什么需要它(第一性原理):
  期刊论文末尾的声明(资助 Funding、利益冲突 Conflicts of Interest、伦理 Ethics、
  致谢 Acknowledgments、作者贡献 Author Contributions、数据可得性 Data Availability 等)
  在源 docx 里**常以"裸句子"出现、不带任何标题**(如 "There was no funding to perform
  this study."、"There are no conflicts of interest to declare")。启发式只能认出带
  "标签:"前缀的那种,于是实测把多条裸声明全部塞进同一个标题(样例5:资助/冲突/致谢/伦理
  四条声明都挂在 "Author Contributions" 之下)。判断"这句话属于哪类声明"是纯语义任务,
  正是该交给 LLM 的部分。

本模块把 <back> 里声明区的段落交给 LLM,按标准声明类别重新切分、命名,再据此重建
  <back> 的声明小节(ref-list / fn-group 等非声明节原样保留)。只移动已有段落、不重写,
  保证内容守恒;重建后即时 DTD 校验。仅依据源已有内容,不凭空新增声明(源没有就不造)。
"""
from __future__ import annotations

import copy
import re

from lxml import etree

from ..build.jats import E, sub


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _decl_nodes(back):
    """back 下的声明节(sec/ack/glossary),排除 ref-list / fn-group / app-group 等。"""
    out = []
    for ch in back:
        if etree.QName(ch).localname in ("sec", "ack", "glossary"):
            out.append(ch)
    return out


def _collect_paras(nodes):
    """从声明节里按顺序收集 (title提示, <p>元素) 单元。返回 (units, titles_seen)。"""
    units = []
    titles = []
    for node in nodes:
        t = node.findtext("title")
        if t:
            titles.append(t.strip())
        for p in node.findall("p"):
            units.append(p)
    return units, titles


_SYS = "你是严谨的学术编辑,只依据给定段落判断其所属声明类别,只输出 JSON。"


def _prompt(para_texts, titles_seen):
    lines = ["[%d] %s" % (i, t[:300]) for i, t in enumerate(para_texts)]
    return (
        "下面是一篇论文【后置声明区】的若干段落(按原文顺序编号)。它们可能被错误地全部"
        "堆在一个标题下。请按**标准声明类别**把每个段落归类,并给出该类别的规范标题。\n"
        "常见类别(用其一,按语义判断;没有的类别不要出现):\n"
        "  Author Contributions / Funding / Conflicts of Interest / "
        "Ethics Approval and Consent to Participate / Acknowledgments / "
        "Availability of Data and Materials / Consent for Publication 等。\n"
        "线索:'no funding'/'supported by' → Funding;'no conflicts of interest' → "
        "Conflicts of Interest;'approved by ... ethic(s) committee'/'protocol' → "
        "Ethics Approval and Consent to Participate;'no acknowledgements'/'we thank' → "
        "Acknowledgments;'Conceptualization, ...; methodology, ...' → Author Contributions;"
        "'data ... available' → Availability of Data and Materials。\n"
        "原文里已出现的标题(供参考):" + ("、".join(titles_seen) if titles_seen else "无") + "\n\n"
        "段落:\n" + "\n".join(lines) + "\n\n"
        "要求:每个段落**恰好**归入一个类别;按段落出现顺序组织;保持段落原文不变(只分组)。\n"
        '严格输出 JSON:{"sections":[{"title":"规范标题","paras":[段落编号,...]}]}'
    )


def split_declarations(llm, article, doc=None, validator=None) -> dict:
    rec = {"applied": False, "reason": ""}
    back = article.find("back")
    if back is None:
        rec["reason"] = "无 back"
        return rec
    nodes = _decl_nodes(back)
    if not nodes:
        rec["reason"] = "无声明节"
        return rec
    units, titles_seen = _collect_paras(nodes)
    if len(units) <= 1:
        rec["reason"] = "声明段落≤1,无需拆分"
        return rec
    para_texts = ["".join(p.itertext()).strip() for p in units]

    if llm is None or not getattr(llm, "enabled", False):
        rec["reason"] = "LLM 未启用"
        return rec
    data = llm.extract_json(_SYS, _prompt(para_texts, titles_seen), max_tokens=2048)
    segs = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(segs, list) or not segs:
        rec["reason"] = "LLM 未给出有效切分"
        return rec

    # 校验切分:每个段落恰好用一次,且覆盖全部
    seen, clean = set(), []
    for s in segs:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip()
        idxs = [i for i in (s.get("paras") or []) if isinstance(i, int) and 0 <= i < len(units)]
        idxs = [i for i in idxs if i not in seen]
        if not title or not idxs:
            continue
        seen.update(idxs)
        clean.append({"title": title, "idxs": idxs})
    if seen != set(range(len(units))):
        rec["reason"] = "切分未恰好覆盖全部段落(%d/%d),放弃" % (len(seen), len(units))
        return rec
    # 仅当切分确实把"挤压在一起的声明"拆出**更多**命名小节时才应用。若切分数不多于
    # 现有节数,说明 back 已被(热启动)妥善拆分,再处理只会无谓改写已正确的标题
    # (如把源 'Acknowledgment' 改成 'Acknowledgments'),故跳过,保留现有标题忠实于源。
    if len(clean) <= len(nodes):
        rec["reason"] = "back 已妥善拆分(%d 节 → %d),无需再拆" % (len(nodes), len(clean))
        return rec

    # 记录 ref-list / fn-group / app-group 等非声明节的位置(保持其相对 back 末尾的位置)
    tail_nodes = [ch for ch in back if etree.QName(ch).localname not in ("sec", "ack", "glossary")]

    # 重建:先把旧声明节从 back 摘掉,按切分插到 back 最前(声明在 ref-list 之前)
    for node in nodes:
        back.remove(node)

    def make_node(title):
        low = title.lower()
        if "acknowledg" in low:
            n = E("ack")
        elif "abbreviation" in low:
            n = E("glossary")
        else:
            n = E("sec")
        sub(n, "title", title)
        return n

    new_nodes = []
    for seg in clean:
        node = make_node(seg["title"])
        for i in seg["idxs"]:
            node.append(copy.deepcopy(units[i]))
        new_nodes.append(node)

    # 声明节插到第一个 tail 节(ref-list/fn-group)之前;没有 tail 则直接追加
    anchor = tail_nodes[0] if tail_nodes else None
    for node in new_nodes:
        if anchor is not None:
            anchor.addprevious(node)
        else:
            back.append(node)

    # DTD 校验,失败回滚
    if validator is not None:
        from ..build.jats import serialize
        res = validator.validate_bytes(serialize(article))
        if not (res and res.ok):
            # 回滚:移除新节,放回旧节到最前
            for node in new_nodes:
                back.remove(node)
            ref_anchor = tail_nodes[0] if tail_nodes else None
            for node in nodes:
                if ref_anchor is not None:
                    ref_anchor.addprevious(node)
                else:
                    back.append(node)
            rec["reason"] = "拆分后 DTD 不通过,回滚"
            return rec

    rec["applied"] = True
    rec["sections"] = [{"title": s["title"], "n_paras": len(s["idxs"])} for s in clean]
    rec["before_titles"] = titles_seen
    return rec
