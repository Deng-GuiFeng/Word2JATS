"""理解层编排：Document → SemanticDoc。

parse 出的 IR → 序列化内容流 → 机械定位参考区 → 三个 LLM pass（front/body/refs）
→ 组装成 SemanticDoc（索引解析回原始 runs）。这是"LLM 做理解"的全部入口；
下游渲染层与出口校验只消费 SemanticDoc，不再回看 docx。
"""

from __future__ import annotations

from .assemble import assemble
from .passes import body_pass, front_pass, refs_pass
from .segment import find_refs_boundary
from .serialize import serialize


def understand(doc, llm):
    """返回 (SemanticDoc, meta)。meta 记录分块位置与各 pass 原始 JSON，供调试/校验。"""
    stream = serialize(doc)
    refs_head, refs_start = find_refs_boundary(stream)

    front_json = front_pass(stream, llm, refs_start)
    body_start = int(front_json.get("body_start_idx") or 0)
    # front 区结束点：body_start 若异常（0 或越界），退化为 refs_start（整段当 body 让 body pass 判）
    if not (0 < body_start < refs_start):
        body_start = _fallback_body_start(stream, refs_start)

    body_json = body_pass(stream, llm, body_start, refs_start)
    refs_json = refs_pass(stream, llm, refs_start)

    sd = assemble(stream, front_json, body_json, refs_json,
                  body_start=body_start, refs_start=refs_start)

    meta = {
        "n_blocks": len(stream.lines),
        "refs_head_idx": refs_head,
        "refs_start": refs_start,
        "body_start": body_start,
        "front_json": front_json,
        "body_json": body_json,
        "refs_json": refs_json,
        "stream": stream,
    }
    return sd, meta


def _fallback_body_start(stream, refs_start):
    """front pass 没给出可用 body_start 时的机械兜底：找首个明显正文小节标题。"""
    from ..understand.patterns import REFERENCES_HEAD
    import re
    COMMON = re.compile(
        r"^\s*(?:\d+\.?\s+)?(introduction|background|materials?\s+and\s+methods|methods?|"
        r"results?|discussion|conclusion)s?\s*$", re.I)
    for ln in stream.lines:
        if ln.kind == "para" and COMMON.match(ln.text.strip()):
            return ln.idx
    return min(40, refs_start)
