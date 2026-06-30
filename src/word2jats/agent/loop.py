"""Agent 视觉闭环编排器——本作品的质量保证核心。

定位(对齐项目根本架构):热启动只产出"快但可能错"的草稿;质量主要来自这个闭环。
两条**硬约束**贯穿所有修复器:
  ① 最终必须 DTD 合法(赛题硬要求);
  ② 不编造源文档没有的内容(正确性底线);改动即时做 DTD 校验 + 内容守恒校验。
R15 去掉了早期"结构/层级问题查出却丢弃"的白名单行为——章节层级(A)、作者归属(B)、
后置声明(C)改由对应修复器**实际修复**(各按字段选最可靠方法:LLM 语义判断 / 确定性解析 /
热启动标记检测)。内容补全阶段(D)按"已实现哪些补全器"取舍(见下 REPAIRABLE),
漏图/漏式当前仅记录到 trace、不自动修(尚无对应补全器),这是诚实的能力边界。

流程:
    热启动草稿 ──▶ 渲染输入 Word 为页面图(人眼真值,一次) + VLM 逐页视觉清点(一次)
                                          │
        A. 正文章节结构修正(LLM 据样式重排层级,移动已有内容块)
        B. 作者归属修正(LLM 仅定单位;ORCID 确定性解析;通讯/共同贡献/邮箱用热启动标记)
        C. 后置声明识别与拆分(LLM 把裸声明句识别并归入命名 back 小节)
        D. 内容核对与补全(看图重建漏表 / 补抽漏认作者),仅 REPAIRABLE 两类,多轮至收敛
                                          │
                                 返回最终 XML + 完整 trace(透明报告)

要点:视觉真值独立于被测解析器(渲染图 + VLM),非循环论证;全过程留痕(每阶段做了什么、
是否生效、为何不生效),既是质量门控也是透明交付依据。
"""

from __future__ import annotations

import os

from . import authors_fix as _authors_fix
from . import declarations as _decl
from . import inspect as _inspect
from . import outline as _outline
from . import reconcile as _reconcile
from . import render as _render
from . import repair as _repair
from . import structure as _structure

# 内容补全阶段(D)目前**已实现的两类补全器**。诚实说明:它仍是一个集合门控——成员之外的
# 内容类 finding(漏图 missing_figure、漏式 missing_equation,action=review)在阶段 D 被跳过、
# 仅记录到 trace(尚无对应补全器,实测多为视觉噪声/解析问题),这是诚实的能力边界,列入优化清单。
# 注:R15 真正去掉的是"把**结构/层级/声明**类问题查出却丢弃"的旧行为——它们已改由 A/B/C 实修。
REPAIRABLE = {"extract_authors", "rebuild_table"}


def _visual_summary(visual: dict) -> dict:
    return {
        "pages": len(visual.get("per_page", [])),
        "tables": [t.get("label") or "(无号)" for t in visual.get("tables", [])],
        "figures": [f.get("label") or "(无号)" for f in visual.get("figures", [])],
        "display_equations": visual.get("display_equations", 0),
        "reference_items": visual.get("reference_items", 0),
        "author_block_pages": visual.get("author_block_pages", []),
        "authors_visual": len(visual.get("authors_visual", []) or []),
    }


ALL_PHASES = ("structure", "authors", "declarations", "content")


def run_agent_loop(article, sd, doc, ctx, llm, *, docx_path, out_dir,
                   rebuild_front, max_rounds: int = 3, dpi: int = 120,
                   phases=None) -> dict:
    """在已建好的 article(热启动草稿)上跑 LLM 驱动的视觉闭环,就地修复。返回 trace。

    phases:启用的修复阶段集合(默认全开);消融分析用其逐个关闭某阶段以量化贡献。
    """
    enabled_phases = set(phases) if phases is not None else set(ALL_PHASES)
    trace = {"enabled": False, "reason": "", "rounds": [], "visual": {},
             "structure": {}, "declarations": {}, "authors": {},
             "phases_enabled": sorted(enabled_phases)}
    if llm is None or not getattr(llm, "enabled", False):
        trace["reason"] = "LLM 未启用,跳过视觉闭环(退回热启动草稿)"
        return trace

    work = os.path.join(out_dir, ".agent")
    try:
        pages = _render.render_pages(docx_path, os.path.join(work, "render"), dpi=dpi)
    except Exception as e:
        trace["reason"] = "渲染失败,跳过视觉闭环: %s" % e
        return trace

    visual = _inspect.inspect_pages(llm, pages)
    if visual.get("author_block_pages"):
        ab = visual["author_block_pages"][0]
        if 0 < ab <= len(pages):
            visual["authors_visual"] = _inspect.read_authors(llm, pages[ab - 1]) or []
    trace["enabled"] = True
    trace["visual"] = _visual_summary(visual)

    from ..validate.validator import Validator
    validator = Validator()

    # ===== A. 正文章节结构修正(LLM 主导:据源样式 + 视觉清点 + 当前树重排层级) =====
    if "structure" in enabled_phases:
        try:
            trace["structure"] = _structure.correct_structure(
                llm, article, doc, visual=visual, validator=validator)
        except Exception as e:
            trace["structure"] = {"applied": False, "reason": "结构修正异常: %s" % e}
    else:
        trace["structure"] = {"applied": False, "reason": "阶段被消融关闭"}

    # ===== B. 作者元数据归属修正(混合:LLM 仅定单位;ORCID 确定性解析;通讯/共同贡献/邮箱用热启动标记) =====
    if "authors" in enabled_phases:
        try:
            trace["authors"] = _authors_fix.fix_authors(
                llm, article, sd, doc, rebuild_front, validator=validator)
        except Exception as e:
            trace["authors"] = {"applied": False, "reason": "作者修正异常: %s" % e}
    else:
        trace["authors"] = {"applied": False, "reason": "阶段被消融关闭"}

    # ===== C. 后置声明识别与拆分(LLM 主导:裸声明句 → 命名 back 小节) =====
    if "declarations" in enabled_phases:
        try:
            trace["declarations"] = _decl.split_declarations(llm, article, doc, validator=validator)
        except Exception as e:
            trace["declarations"] = {"applied": False, "reason": "声明拆分异常: %s" % e}
    else:
        trace["declarations"] = {"applied": False, "reason": "阶段被消融关闭"}

    # ===== D. 内容核对与补全(漏表/漏作者…),多轮至收敛。只覆盖已实现的两类补全器(REPAIRABLE);
    #         漏图/漏式(action=review)仅记录到 trace、不自动修——诚实的能力边界,非压制判断。 =====
    if "content" not in enabled_phases:
        return trace
    for rnd in range(1, max_rounds + 1):
        counts_before = _outline.counts(article)
        xml_text = _outline.to_text(_outline.blocks(article))
        findings = _reconcile.reconcile(llm, visual, article, xml_text)
        repairable = [f for f in findings if f.get("action") in REPAIRABLE]

        round_rec = {
            "round": rnd, "counts_before": counts_before,
            "findings": findings, "n_repairable": len(repairable),
            "applied": [], "skipped": [],
        }
        if not repairable:
            round_rec["note"] = "无可补全内容,收敛"
            trace["rounds"].append(round_rec)
            break

        result = _repair.apply(
            findings, root=article, sd=sd, doc=doc, ctx=ctx, llm=llm,
            page_pngs=pages, rebuild_front=rebuild_front, visual=visual)
        round_rec["applied"] = result["applied"]
        round_rec["skipped"] = result["skipped"]
        round_rec["counts_after"] = _outline.counts(article)
        trace["rounds"].append(round_rec)

        if not result["applied"]:
            round_rec["note"] = "本轮无任何修复生效,停止以免空转"
            break

    return trace
