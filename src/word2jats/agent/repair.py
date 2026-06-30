"""修复路由:把闭环发现的、可靠可修的 findings 实际改进到 XML 上。

设计原则:
- **只修能可靠修的**,绝不为了"看起来在修"而瞎改(那是另一种偷懒)。当前可靠修复:
  · extract_authors:页面有作者块但 XML 没抽出作者 → LLM 重抽并重建 <front>;
  · rebuild_table:页面可见、XML 缺失的表 → VLM 看页面图重建,插到其题注段之后。
- 其余 findings(漏图/漏式/文献数差/标题层级)记录为**已验证差异**,进入透明报告,
  不静默乱改——把判断权与证据如实交出,而不是假装修好。
- 每次修复都核对"是否真的改了 XML",没改就不计入,避免空转。
"""

from __future__ import annotations

import re

from ..build.vision_tables import build_table_from_image


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def repair_authors(root, sd, doc, llm, rebuild_front, visual_authors=None) -> bool:
    """作者漏认(0 位或少于视觉所见)→ 补抽 + 重建 <front>。返回是否生效。

    优先用视觉提取的完整作者列表重建,并**按姓合并**保留启发式已关联的 ORCID/邮箱/单位,
    只有"更完整"(数量增加)才替换,避免把已正确的结果改差。视觉不可用时退回文本重抽。
    """
    from ..model.structured import Author
    before = len(sd.authors)

    if visual_authors and len(visual_authors) > before:
        old_by_sur = {}
        for a in sd.authors:
            old_by_sur.setdefault((a.surname or "").strip().lower(), a)
        # 安全校验:视觉作者必须**涵盖现有全部作者**(超集)才替换——若视觉与现有不一致
        # (可能漏读/错读),宁可不动,避免把已正确的结果改差。
        vis_sur = {str(it.get("surname", "")).strip().lower()
                   for it in visual_authors if str(it.get("surname", "")).strip()}
        if not all(s in vis_sur for s in old_by_sur if s):
            return False
        new = []
        for it in visual_authors:
            surname = str(it.get("surname", "")).strip()
            given = str(it.get("given", "")).strip()
            if not (surname or given):
                continue
            prev = old_by_sur.get(surname.lower())
            new.append(Author(
                surname=surname, given_names=given,
                aff_labels=([str(x) for x in (it.get("aff") or []) if str(x).strip()]
                            or (prev.aff_labels if prev else [])),
                is_corresponding=bool(it.get("corresponding")) or bool(prev and prev.is_corresponding),
                equal_contrib=bool(it.get("equal")) or bool(prev and prev.equal_contrib),
                orcid=(prev.orcid if prev else None),
                orcid_authenticated=bool(prev and prev.orcid_authenticated),
                email=(prev.email if prev else None),
                raw=(surname + given)))
        if len(new) <= before:
            return False
        sd.authors = new
    else:
        from .refine import refine as _refine
        _refine(sd, doc, llm)
        if len(sd.authors) <= before:
            return False

    old_front = root.find("front")
    new_front = rebuild_front()
    if old_front is None or new_front is None:
        return False
    root.replace(old_front, new_front)
    return True


def _caption_paragraph_for_table(body, number: int):
    """找以 "Table N" **开头**的题注段(文本表常有);只认严格题注,不认正文行内提及。

    图片表的题注常嵌在图片里、正文无文本题注,此时返回 None,由上层改用"按页定位小节"。
    """
    head = re.compile(r"^\s*table\s*0*%d\b" % number, re.I)
    for p in body.iter("p"):
        if head.match("".join(p.itertext())):
            return p
    return None


def _find_sec_by_heading(body, heading_text: str):
    h = _norm(heading_text)
    if not h:
        return None
    for sec in body.iter("sec"):
        t = _norm(sec.findtext("title") or "")
        if t and (t == h or t in h or h in t):
            return sec
    return None


def _build_table_wrap(llm, page_png, number, hint=None):
    """看页面图重建一张表(慢,VLM 调用)→ table-wrap 元素;失败 None。可并发调用。"""
    if page_png is None:
        return None
    label = "Table %d." % number if number and number > 0 else None
    return build_table_from_image(llm, page_png, number if number and number > 0 else 1,
                                  caption_label=label, target_hint=hint)


def _table_hint(payload) -> str:
    """据视觉证据生成定向提示,指明只转录哪一张表(同页多表时关键)。"""
    n = payload.get("number")
    parts = []
    if n and n > 0:
        parts.append("标签为 Table %d" % n)
    r, c = payload.get("rows"), payload.get("cols")
    if r and c and r > 0 and c > 0:
        parts.append("大约 %d 行 × %d 列" % (r, c))
    return ("只转录" + "、".join(parts) + " 的那一张。") if parts else ""


def _safe_append(container, wrap) -> bool:
    """把 wrap 放进 container 的 prose 区(子 <sec> 之前)——满足 JATS 内容模型
    `(p|table-wrap|…)* , sec*`:table-wrap 不能出现在子 sec 之后,否则 DTD 不过。"""
    if container is None:
        return False
    first_sec = container.find("sec")
    if first_sec is not None:
        first_sec.addprevious(wrap)   # 插到第一个子 sec 之前(prose 区末尾)
    else:
        container.append(wrap)
    return True


def _insert_table(body, wrap, number, page_headings=None) -> bool:
    """把建好的 table-wrap 插到 DTD 合规的合适位置(快,树操作,串行)。"""
    if body is None or wrap is None:
        return False
    # 1) 优先插到题注段之后(最忠实)——但仅当不会落到子 sec 之后(否则违反内容模型)
    cap_p = _caption_paragraph_for_table(body, number) if number and number > 0 else None
    if cap_p is not None:
        parent = cap_p.getparent()
        kids = list(parent)
        idx = kids.index(cap_p)
        sec_idx = next((i for i, ch in enumerate(kids) if ch.tag == "sec"), None)
        if sec_idx is None or idx < sec_idx:
            parent.insert(idx + 1, wrap)
            return True
        # 题注段处于子 sec 之后(罕见)→ 退回安全放置到其所在 sec 的 prose 区
        return _safe_append(parent if parent.tag == "sec" else body, wrap)
    # 2) 退而求其次:放到该页可见标题对应小节的 prose 区
    if page_headings:
        for ht in page_headings:
            sec = _find_sec_by_heading(body, ht)
            if sec is not None:
                return _safe_append(sec, wrap)
    # 3) 兜底:放到正文最后一个顶层小节的 prose 区(保证不丢内容且 DTD 合规)
    top_secs = body.findall("sec")
    if top_secs:
        return _safe_append(top_secs[-1], wrap)
    return _safe_append(body, wrap)


def _page_heading_map(visual) -> dict:
    """按页"有效标题":取该页或其之前最近一页出现的标题(续页表也能归入正确小节)。"""
    out = {}
    if not visual:
        return out
    per_page = sorted(visual.get("per_page", []), key=lambda x: x.get("page", 0))
    carried = []
    for pp in per_page:
        hs = pp.get("headings", [])
        if hs:
            carried = hs
        out[pp.get("page")] = list(reversed(carried))  # 最近的优先
    return out


def apply(findings, *, root, sd, doc, ctx, llm, page_pngs, rebuild_front,
          visual=None) -> dict:
    """路由并执行可修复的 findings。返回 {applied:[...], skipped:[...]}。

    优化:漏表重建的 VLM 调用(慢)先**并发**完成,树插入(快)再串行,避免 8 张表串行等待。
    """
    from concurrent.futures import ThreadPoolExecutor

    applied, skipped = [], []
    body = root.find("body")
    page_headings_map = _page_heading_map(visual)

    # 漏表重建:同页多表也能处理——用定向提示(标签+行列)让 VLM 只转录目标那一张。
    buildable = []
    for f in findings:
        if f.get("action") != "rebuild_table":
            continue
        page = f["payload"].get("page")
        png = page_pngs[page - 1] if (page and 0 < page <= len(page_pngs)) else None
        if png is not None:
            buildable.append(f)
        else:
            f["skip_reason"] = "无对应页面图,无法重建"

    wraps = {}
    if buildable:
        with ThreadPoolExecutor(max_workers=min(6, len(buildable))) as ex:
            futs = {id(f): ex.submit(
                _build_table_wrap, llm, page_pngs[f["payload"]["page"] - 1],
                f["payload"].get("number"), _table_hint(f["payload"]))
                for f in buildable}
        for f in buildable:
            wraps[id(f)] = futs[id(f)].result()

    for f in findings:
        act = f.get("action")
        if act == "extract_authors":
            va = f.get("payload", {}).get("visual_authors")
            ok = repair_authors(root, sd, doc, llm, rebuild_front, visual_authors=va)
            (applied if ok else skipped).append(f)
        elif act == "rebuild_table":
            if f.get("skip_reason"):
                skipped.append(f)
                continue
            wrap = wraps.get(id(f))
            page = f["payload"].get("page")
            ok = _insert_table(body, wrap, f["payload"].get("number"),
                               page_headings_map.get(page))
            (applied if ok else skipped).append(f)
        else:
            skipped.append(f)  # review-only:记录,不改
    return {"applied": applied, "skipped": skipped}
