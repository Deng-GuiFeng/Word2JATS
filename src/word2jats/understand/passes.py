"""运行三个理解 pass：调用 LLM，产出结构 JSON。

- front / body 输出紧凑（前置字段 / 大纲 + 特殊块），单次调用足够。
- references 可能很多条，按 "[N]" 条目边界分块调用再合并，规避单次输出上限。
"""

from __future__ import annotations

from .patterns import REF_LABEL_BRACKET
from .prompts import BODY_SYS, FRONT_SYS, REFS_SYS, build_user

_MAX = 8192
# 每块参考条数：28 条平衡上下文与输出上限；单次超时由 client 放宽到 240s 兜底，
# 仍超时/截断时自适应二分（见 _refs_chunk），综述类超长文献也能补齐。
_REFS_PER_CHUNK = 28


def front_pass(stream, llm, refs_start):
    hi = min(refs_start, _front_cap(stream))
    text = stream.render(0, hi)
    user = build_user(text, 0, hi)
    out = llm.extract_json(FRONT_SYS, user, max_tokens=_MAX)
    return out if isinstance(out, dict) else {}


def body_pass(stream, llm, body_start, refs_start):
    text = stream.render(body_start, refs_start)
    user = build_user(text, body_start, refs_start)
    out = llm.extract_json(BODY_SYS, user, max_tokens=_MAX)
    return out if isinstance(out, dict) else {}


def refs_pass(stream, llm, refs_start):
    n = len(stream.lines)
    # 定位各参考条目起点（"[N]" 开头的块）
    starts = [ln.idx for ln in stream.lines
              if ln.idx >= refs_start and ln.kind == "para"
              and REF_LABEL_BRACKET.match(ln.text.strip())]
    # 边界始终从 refs_start 起：部分文献前几条无 "[N]" 前缀（直接作者名开头），
    # 若只从首个 "[N]" 切分会漏掉前面的无标签条目（实测 S03 前 6 条）。
    bset = sorted(set([refs_start] + [s for s in starts if s > refs_start]))
    if not starts and len(bset) <= 1:
        # 条目边界完全识别不出：整段一次交给 LLM
        text = stream.render(refs_start, n)
        out = llm.extract_json(REFS_SYS, build_user(text, refs_start, n), max_tokens=_MAX)
        return out if isinstance(out, dict) else {"references": []}
    bounds = bset + [n]
    references = []
    for i in range(0, len(bset), _REFS_PER_CHUNK):
        j = min(i + _REFS_PER_CHUNK, len(bset))
        references.extend(_refs_chunk(stream, llm, bounds, i, j))
    return {"references": references}


def _refs_chunk(stream, llm, bounds, i, j):
    """处理参考条目 [i, j)。若返回条数少于预期（多因输出超上限被截断），二分重试到补齐。"""
    lo, hi = bounds[i], bounds[j]
    expected = j - i
    out = llm.extract_json(REFS_SYS, build_user(stream.render(lo, hi), lo, hi), max_tokens=_MAX)
    refs = out.get("references", []) if isinstance(out, dict) else []
    if len(refs) >= expected or expected <= 1:
        return refs
    # 不完整 → 二分递归（各半块更小、更易在输出上限内完成）
    mid = (i + j) // 2
    return _refs_chunk(stream, llm, bounds, i, mid) + _refs_chunk(stream, llm, bounds, mid, j)


def _front_cap(stream):
    from .segment import FRONT_CAP
    return FRONT_CAP
