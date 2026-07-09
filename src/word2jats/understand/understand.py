"""理解层编排：Document → SemanticDoc。

parse 出的 IR → 序列化内容流 → 机械定位参考区 → 三个 LLM pass（front/body/refs）
→ 组装成 SemanticDoc（索引解析回原始 runs）。这是"LLM 做理解"的全部入口；
下游渲染层与出口校验只消费 SemanticDoc，不再回看 docx。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .assemble import assemble, _sanitize_llm_indices
from .passes import body_pass, front_pass, refs_pass
from .segment import find_refs_boundary
from .serialize import serialize


def understand(doc, llm):
    """返回 (SemanticDoc, meta)。meta 记录分块位置与各 pass 原始 JSON，供调试/校验。"""
    stream = serialize(doc)
    refs_head, refs_start = find_refs_boundary(stream)

    # refs pass 只依赖 refs_start，与 front/body 完全独立 → 与前置区并发跑（各自内部再并发
    # 分块）。front→body 有依赖（body_start 取自 front），保持串行。全量并发、无GPU、云端高并发。
    with ThreadPoolExecutor(max_workers=2) as ex:
        refs_future = ex.submit(refs_pass, stream, llm, refs_start)

        # 每个 pass 的 JSON 进下游前先归一索引/计数字段:高温/弱模型畸形输出安全降级、绝不搞崩管线
        front_json = _sanitize_llm_indices(front_pass(stream, llm, refs_start))
        body_start = int(front_json.get("body_start_idx") or 0)
        # front 区结束点：body_start 若异常（0 或越界），退化为 refs_start（整段当 body 让 body pass 判）
        if not (0 < body_start < refs_start):
            body_start = _fallback_body_start(stream, refs_start)
        body_json = _sanitize_llm_indices(body_pass(stream, llm, body_start, refs_start))

        refs_json = _sanitize_llm_indices(refs_future.result())

    # 正文内容上界：有"References"标题时止于该标题块（refs_head），把标题块排除在 body 之外——
    # 否则它落进 [body_start, refs_start) 会作为尾随段落漏进最后一个声明块（ref-list 标题是
    # 模板生成，标题块无需保留），造成 "References" 一词被重复输出（L1 编造，实测 8 样例）。
    # 注意：仅在 assemble 组装时按 body_end 过滤；body_pass 仍看到 [body_start, refs_start) 全文，
    # 以保持 LLM 缓存稳定、判定不因区间变化而漂移（"References" 标题块由 assemble 层丢弃）。
    body_end = refs_head if (refs_head is not None and refs_head >= body_start) else refs_start

    sd = assemble(stream, front_json, body_json, refs_json,
                  body_start=body_start, refs_start=refs_start, body_end=body_end)

    meta = {
        "n_blocks": len(stream.lines),
        "refs_head_idx": refs_head,
        "refs_start": refs_start,
        "body_start": body_start,
        "body_end": body_end,
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
