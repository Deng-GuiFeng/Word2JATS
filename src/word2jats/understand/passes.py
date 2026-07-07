"""运行三个理解 pass：调用 LLM，产出结构 JSON。

- front / body 输出紧凑（前置字段 / 大纲 + 特殊块），单次调用足够。
- references 可能很多条，按 "[N]" 条目边界分块调用再合并，规避单次输出上限。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .patterns import REF_LABEL_BRACKET
from .prompts import BODY_SYS, BOOK_FIELDS_SYS, FRONT_SYS, REFS_SYS, build_user

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
    # 各参考分块彼此独立 → 全量并发调用 LLM（DashScope 支持高并发；串行是纯浪费）。
    # ThreadPoolExecutor.map 保持提交顺序，结果拼接后参考编号顺序不变、确定复现。
    spans = [(i, min(i + _REFS_PER_CHUNK, len(bset)))
             for i in range(0, len(bset), _REFS_PER_CHUNK)]
    if len(spans) <= 1:
        chunks = [_refs_chunk(stream, llm, bounds, i, j) for i, j in spans]
    else:
        with ThreadPoolExecutor(max_workers=len(spans)) as ex:
            chunks = list(ex.map(lambda s: _refs_chunk(stream, llm, bounds, s[0], s[1]), spans))
    references = []
    for c in chunks:
        references.extend(c)
    _augment_book_fields(stream, llm, references)
    return {"references": references}


def _norm_key_frag(s):
    """粗归一（小写 + 去空白/标点），用于判断 comment 是否已含 publisher 串。"""
    import re as _re
    return _re.sub(r"[\s\-.,;:()]", "", (s or "").casefold())


def _augment_book_fields(stream, llm, references):
    """二级 pass：对 book 类参考单独抽 editors/publisher_name/publisher_loc。
    主 refs 提示词不含这些字段（保持期刊 ref 提取零漂移、缓存有效），只对少数书籍条目
    补一次针对性调用。各书籍条目独立 → 全量并发。"""
    books = [r for r in references
             if isinstance(r, dict) and (r.get("pub_type") == "book") and not r.get("editors")]
    if not books:
        return

    def _one(r):
        idxs = r.get("block_idxs") or []
        raw = " ".join(stream.text_of(i).strip() for i in idxs).strip()
        if not raw:
            return
        out = llm.extract_json(BOOK_FIELDS_SYS, build_user(raw, 0, 0), max_tokens=1024)
        if not isinstance(out, dict):
            return
        eds = [e for e in (out.get("editors") or []) if e]
        if eds:
            r["editors"] = eds
        pub = out.get("publisher_name") or None
        loc = out.get("publisher_loc") or None
        if pub:
            r["publisher_name"] = pub
        if loc:
            r["publisher_loc"] = loc
        if out.get("edition"):
            r["edition"] = out["edition"]
        # 主 refs pass 常把书末尾 "N ed., Publisher, Loc" 整段塞进 comment；这些信息现已进入
        # edition/publisher-name/publisher-loc 结构化字段，若 comment 仍含 publisher 就会重复
        # 输出该串（L1 编造，实测 01 三条书籍）。判为冗余则清空 comment。
        cm = r.get("comment")
        if cm and pub and _norm_key_frag(pub) in _norm_key_frag(cm):
            r["comment"] = None

    if len(books) == 1:
        _one(books[0])
    else:
        with ThreadPoolExecutor(max_workers=len(books)) as ex:
            list(ex.map(_one, books))


def _refs_chunk(stream, llm, bounds, i, j):
    """处理参考条目 [i, j)。若返回条数少于预期（多因输出超上限被截断），二分重试到补齐。"""
    lo, hi = bounds[i], bounds[j]
    expected = j - i
    out = llm.extract_json(REFS_SYS, build_user(stream.render(lo, hi), lo, hi), max_tokens=_MAX)
    refs = out.get("references", []) if isinstance(out, dict) else []
    if len(refs) >= expected or expected <= 1:
        return refs
    # 不完整 → 二分递归（各半块更小、更易在输出上限内完成）；两半独立 → 并发。
    mid = (i + j) // 2
    with ThreadPoolExecutor(max_workers=2) as ex:
        left = ex.submit(_refs_chunk, stream, llm, bounds, i, mid)
        right = ex.submit(_refs_chunk, stream, llm, bounds, mid, j)
        return left.result() + right.result()


def _front_cap(stream):
    from .segment import FRONT_CAP
    return FRONT_CAP
