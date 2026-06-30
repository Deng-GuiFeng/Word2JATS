"""调和侧:把"视觉看到的"和"XML 声称的"对齐,产出结构化 findings(差异清单)。

两路并用,各取所长:
- **确定性计数差**(本模块 deterministic_findings):表/图/块级公式/参考文献/作者这些
  可数、可定位、可驱动修复的类别,用确定性比对最可靠,是闭环的硬底线,不依赖模型判断。
- **LLM 整体调和**(llm_findings):标题层级、章节遗漏、错类、顺序这些计数覆盖不到、
  需要语义判断的问题,交给 LLM 看两份提纲做整体审查——这是"LLM 决策"在闭环里的体现。

每条 finding 带 action,供 repair.py 路由:
  rebuild_table / extract_authors / review(仅记录,人工或后续处理) / none
"""

from __future__ import annotations

import json
import re


def _num(label) -> int | None:
    if not label:
        return None
    m = re.search(r"(\d+)", str(label))
    return int(m.group(1)) if m else None


def _xml_table_nums(root) -> dict:
    """{表号: label文本};无号表用负序号占位。"""
    out = {}
    neg = -1
    for tw in root.findall(".//table-wrap"):
        lbl = tw.findtext("label") or ""
        cap = ""
        if tw.find("caption") is not None:
            cap = " ".join("".join(tw.find("caption").itertext()).split())
        n = _num(lbl) or _num(cap)
        if n is None:
            n = neg
            neg -= 1
        out[n] = (lbl + " " + cap).strip()
    return out


def _xml_fig_nums(root) -> set:
    nums = set()
    neg = -1
    for fig in root.findall(".//fig"):
        lbl = fig.findtext("label") or ""
        n = _num(lbl)
        if n is None:
            n = neg
            neg -= 1
        nums.add(n)
    return nums


def deterministic_findings(visual: dict, root) -> list:
    findings = []

    # ---- 表格:视觉看到的表号集合 vs XML 里的表号集合 ----
    xml_tabs = _xml_table_nums(root)
    xml_tab_nums = {n for n in xml_tabs if n > 0}
    vis_tabs = {}
    for t in visual.get("tables", []):
        n = _num(t.get("label"))
        if n is not None:
            vis_tabs[n] = t
    vis_tab_nums = set(vis_tabs.keys())
    # 视觉有、XML 无 → 漏表(可修复:看图重建)
    for n in sorted(vis_tab_nums - xml_tab_nums):
        t = vis_tabs[n]
        findings.append({
            "type": "missing_table", "severity": "high", "where": "p%s" % t.get("page"),
            "detail": "页面可见 Table %d(约%s行×%s列,%s),但 XML 中缺失"
                      % (n, t.get("rows"), t.get("cols"),
                         "整表为图片" if t.get("is_image") else "文本表"),
            "action": "rebuild_table",
            "payload": {"number": n, "page": t.get("page"), "is_image": t.get("is_image"),
                        "rows": t.get("rows"), "cols": t.get("cols"),
                        "label": t.get("label")},
        })
    # 数量级核对(仅作低置信提示)。视觉跨页清点会把续表/分块/非表框误计为"无编号表",
    # 故只在"视觉数 明显多于 XML 且不存在已定位的编号缺口"时给一条**低**严重度、带诚实
    # caveat 的提示,供人工复核——不当作硬差异,避免狼来了。
    n_vis_tab = len(visual.get("tables", []))
    n_xml_tab = len(xml_tabs)
    if n_vis_tab - n_xml_tab >= 2 and not (vis_tab_nums - xml_tab_nums):
        findings.append({
            "type": "table_count", "severity": "low", "where": "doc",
            "detail": "视觉清点约 %d 张表(含无编号块,可能多计),XML %d 张;"
                      "无定位到的编号缺口,仅供复核" % (n_vis_tab, n_xml_tab),
            "action": "review", "payload": {"visual": n_vis_tab, "xml": n_xml_tab}})

    # ---- 图 ----
    xml_figs = _xml_fig_nums(root)
    xml_fig_nums = {n for n in xml_figs if n > 0}
    vis_fig_nums = {x for x in (_num(f.get("label")) for f in visual.get("figures", [])) if x}
    for n in sorted(vis_fig_nums - xml_fig_nums):
        findings.append({
            "type": "missing_figure", "severity": "high", "where": "doc",
            "detail": "页面可见 Figure %d,但 XML 中缺失对应 <fig>" % n,
            "action": "review", "payload": {"number": n}})

    # ---- 块级公式 ----
    n_vis_eq = visual.get("display_equations", 0)
    n_xml_eq = len(root.findall(".//disp-formula"))
    if n_vis_eq - n_xml_eq >= 2:   # 容一点视觉噪声
        findings.append({
            "type": "missing_equation", "severity": "med", "where": "doc",
            "detail": "视觉清点到约 %d 个块级公式,XML 仅 %d 个 <disp-formula>"
                      % (n_vis_eq, n_xml_eq),
            "action": "review", "payload": {"visual": n_vis_eq, "xml": n_xml_eq}})

    # 注:不再输出"参考文献条目数"差异 finding——VLM 跨页清点文献条目极不可靠
    # (续行、非条目行、跨页重复都会被计入,实测样例4 视觉 202 vs 实际 145),
    # 报告它只会制造噪声、损害闭环可信度。文献完整性改由确定性的"编号连续性"等手段保障。

    # ---- 作者:对照视觉提取的完整作者列表 ----
    n_authors = len(root.findall('.//contrib[@contrib-type="author"]'))
    av = visual.get("authors_visual") or []
    if visual.get("author_block_pages") and n_authors == 0:
        findings.append({
            "type": "missing_authors", "severity": "high", "where": "front",
            "detail": "页面可见作者姓名列表,但 XML 未抽出任何作者",
            "action": "extract_authors", "payload": {"visual_authors": av}})
    elif av and len(av) > n_authors:
        # 视觉看到的作者比 XML 多 → 启发式漏认(如作者行跨行/分隔符特殊)→ 补抽
        names = "、".join((a.get("given", "") + " " + a.get("surname", "")).strip()
                          for a in av)
        findings.append({
            "type": "authors_incomplete", "severity": "high", "where": "front",
            "detail": "视觉清点到 %d 位作者(%s),XML 仅 %d 位,疑似漏认"
                      % (len(av), names[:120], n_authors),
            "action": "extract_authors", "payload": {"visual_authors": av}})

    return findings


_RECON_SYS = "你是严谨的学术排版核对员,只依据给定材料,客观比对,只输出 JSON。"


def _recon_user(visual: dict, xml_text: str) -> str:
    vis_headings = []
    for h in visual.get("headings", []):
        vis_headings.append("p%s: %s" % (h.get("page"), h.get("text")))
    return (
        "下面是同一篇论文的两份结构提纲。A 是**人看渲染页**清点出的可见章节标题(按页),"
        "B 是**程序生成的 XML**的结构提纲。请对齐二者,**只**报告 B 相对 A 的结构性问题:\n"
        "- 章节标题在 A 中可见、B 中遗漏(missing_heading);\n"
        "- 标题层级明显错误(如 A 中是顶级节,B 中却成了子节,或反之)(heading_level);\n"
        "- B 中出现 A 里根本没有的章节(extra_heading)。\n"
        "**不要**报告以下情况(它们不是问题,报了算错):\n"
        "  · 'References'/'参考文献'——B 用 <reference_list> 表示,规范做法,不算缺失;\n"
        "  · 'Abstract'/'摘要'、'Keywords'/'关键词'——它们是前置元数据,B 里用 <abstract>、<keywords> 表示,不是章节标题,不算缺失;\n"
        "  · 图题/表题(如 'Figure 3: ...'、'Table 1 ...')——那是图表标题,不是章节标题;\n"
        "  · 纯文字措辞差异、单复数差异(如 Acknowledgment vs Acknowledgments)、编号差异、大小写差异;\n"
        "  · B 中以 <back_section> 形式已存在的致谢/声明/缩写表小节(Acknowledgments、Author Contributions、Funding、Abbreviations 等)——只要 B 里有同名(含单复数/缩写表)条目就不算缺失。\n"
        "没有问题就返回空数组。\n"
        '输出严格 JSON: {"findings":[{"type":"missing_heading|heading_level|extra_heading",'
        '"severity":"high|med|low","detail":"中文说明","heading":"相关标题原文"}]}\n\n'
        "=== A. 视觉可见标题 ===\n" + "\n".join(vis_headings) +
        "\n\n=== B. XML 结构提纲 ===\n" + xml_text)


def llm_findings(llm, visual: dict, xml_text: str) -> list:
    if llm is None or not getattr(llm, "enabled", False):
        return []
    data = llm.extract_json(_RECON_SYS, _recon_user(visual, xml_text), max_tokens=2048)
    out = []
    items = data.get("findings") if isinstance(data, dict) else None
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            # 兜底过滤:前置元数据/图表题/参考文献等已用结构化标签表示,非"缺失标题",滤除误报
            h = str(it.get("heading", "")).strip().lower().rstrip(":：")
            if h in ("references", "reference", "参考文献", "bibliography",
                     "abstract", "摘要", "keywords", "key words", "关键词",
                     "acknowledgment", "acknowledgments", "acknowledgement",
                     "acknowledgements", "致谢"):
                continue
            if re.match(r"^(figure|fig\.?|table|表|图)\s*\d", h):  # 图题/表题非章节标题
                continue
            out.append({
                "type": it.get("type", "heading_issue"),
                "severity": it.get("severity", "low"),
                "where": "body",
                "detail": str(it.get("detail", "")).strip(),
                "action": "review",
                "payload": {"heading": it.get("heading")},
            })
    return out


def reconcile(llm, visual: dict, root, xml_text: str) -> list:
    """合并确定性 + LLM findings。"""
    findings = deterministic_findings(visual, root)
    findings += llm_findings(llm, visual, xml_text)
    return findings
