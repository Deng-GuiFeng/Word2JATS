"""用 LLM 把纯文本参考文献结构化为 element-citation 字段（可选增强）。

混合策略：规则法已抽取 year/doi/volume；本模块把整条引用交给 LLM 拆分
authors/title/source/卷期页，置信字段齐全则升级为 element-citation，否则保持
mixed-citation 兜底。批量请求 + 缓存控成本，全部失败时静默降级。
"""

from __future__ import annotations

from typing import List

from ..model.structured import Reference

_SYSTEM = (
    "You are a bibliographic reference parser for academic journal production. "
    "Given numbered raw citation strings (Vancouver/AMA style), extract structured "
    "fields for each. Output STRICT JSON only."
)

_INSTRUCT = """Parse each reference into fields. Return JSON:
{"refs":[{"i":<index>,"type":"journal|book|confproc|web|other",
"authors":[["Surname","Initials/GivenNames"],...],"etal":true|false,
"article_title":"","source":"<journal or book title>","year":"YYYY",
"volume":"","issue":"","fpage":"","lpage":"","doi":""}]}
Rules: keep author order; "Surname AB" -> ["Surname","AB"]; if "et al." present set etal=true
and include only listed authors; source = journal/book name (expand only if obvious, else keep as-is);
omit a field by using empty string; never invent data. Input references:
"""

_BATCH = 20


def _do_batch(llm, chunk):
    """处理一批参考文献,返回 {批内序号: 字段dict}。"""
    lines = ["%d. %s" % (j + 1, r.raw_text) for j, r in enumerate(chunk)]
    data = llm.extract_json(_SYSTEM, _INSTRUCT + "\n".join(lines), max_tokens=4096)
    items = data.get("refs") if isinstance(data, dict) else data
    by_idx = {}
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and "i" in it:
                by_idx[it["i"]] = it
    return by_idx


def structure_with_llm(refs: List[Reference], llm, max_workers: int = 8) -> int:
    """就地结构化 refs。多批并发提交本地服务(榨干吞吐),返回成功结构化的条数。"""
    if not refs or not getattr(llm, "enabled", False):
        return 0
    from concurrent.futures import ThreadPoolExecutor
    chunks = [refs[s:s + _BATCH] for s in range(0, len(refs), _BATCH)]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(lambda c: _do_batch(llm, c), chunks))
    n_ok = 0
    for chunk, by_idx in zip(chunks, results):
        for j, r in enumerate(chunk):
            it = by_idx.get(j + 1)
            if it and _apply(r, it):
                n_ok += 1
    return n_ok


def _apply(ref: Reference, it: dict) -> bool:
    """把 LLM 字段写入 Reference；字段足够则标记 structured。"""
    authors = []
    for a in (it.get("authors") or []):
        if isinstance(a, (list, tuple)) and a:
            surname = str(a[0]).strip()
            given = str(a[1]).strip() if len(a) > 1 else ""
            if surname:
                authors.append((surname, given))
    title = (it.get("article_title") or "").strip()
    source = (it.get("source") or "").strip()
    # 仅在关键字段齐全时升级为 element-citation，否则保持 mixed
    if not (source and (title or authors)):
        return False
    ref.authors = authors
    ref.etal = bool(it.get("etal"))
    ref.article_title = title or None
    ref.source = source
    ref.year = (it.get("year") or ref.year or "").strip() or None
    ref.volume = (it.get("volume") or ref.volume or "").strip() or None
    ref.issue = (it.get("issue") or "").strip() or None
    ref.fpage = (it.get("fpage") or ref.fpage or "").strip() or None
    ref.lpage = (it.get("lpage") or ref.lpage or "").strip() or None
    ref.doi = (it.get("doi") or ref.doi or "").strip() or None
    ref.pub_type = (it.get("type") or "journal").strip() or "journal"
    ref.structured = True
    return True
