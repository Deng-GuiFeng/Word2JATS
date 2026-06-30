"""智能升级层:启发式跑完后做自检,只有当某部分**确实失败**时才让 LLM 补救。

这正是"启发式先跑 → 检查 → 不过就上模型"的循环:
- 确定性分类已经把绝大多数文档处理对了(检查通过即收敛,模型一次都不调,零成本零风险);
- 只在自检发现明确缺口(如一位作者都没认出来)时,才把对应原文交给 LLM 重新抽取。

设计上**绝不覆盖已成功的确定性结果**——只填补空缺,因此对正常文档是纯增益、无回退风险。
"""

from __future__ import annotations

import re

from ..classify import frontmatter as FM
from ..model.blocks import Paragraph
from ..model.structured import Author

_AUTHOR_SYS = "你从学术论文前置文字里抽取作者信息。只输出 JSON。"
_AUTHOR_USER = """下面是一篇论文开头的若干行文字。请抽取作者列表(不要把单位、邮箱、编辑当作者)。
返回 JSON: {"authors":[{"surname":"姓","given":"名","aff":["1","2"],"corresponding":false,"equal":false}]}
- surname/given 按英文习惯拆分(名在前姓在后时,姓取最后一个词);
- aff 是作者上标里的单位编号(数字),没有就空数组;
- corresponding: 是否带 * 通讯标记;equal: 是否带 †/# 共同贡献标记。
只输出 JSON。前置文字:
"""


def _front_text(doc, limit=25):
    lines = []
    for b in doc.blocks:
        if isinstance(b, Paragraph):
            t = b.text.strip()
            if t:
                lines.append(t)
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def refine(sd, doc, llm) -> dict:
    """对失败的抽取部分做 LLM 补救。返回触发了哪些升级。"""
    escalated = []
    if llm is None or not getattr(llm, "enabled", False):
        return {"escalated": escalated}

    # 失败信号:一位作者都没认出来(确定性失败)→ 交给 LLM 重抽
    if not sd.authors:
        data = llm.extract_json(_AUTHOR_SYS, _AUTHOR_USER + _front_text(doc))
        items = data.get("authors") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            for it in items:
                surname = str(it.get("surname", "")).strip()
                given = str(it.get("given", "")).strip()
                if not (surname or given):
                    continue
                aff = [str(x) for x in (it.get("aff") or []) if str(x).strip()]
                sd.authors.append(Author(
                    surname=surname, given_names=given, aff_labels=aff,
                    is_corresponding=bool(it.get("corresponding")),
                    equal_contrib=bool(it.get("equal")), raw=surname + given))
            escalated.append("authors")

    return {"escalated": escalated}
