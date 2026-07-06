"""转换管线编排：docx → JATS XML（+ 外部化图片）。"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from .build.body import build_back, build_body
from .build.context import BuildContext
from .build.figures import FigureBuilder, FigureSource
from .build.formulas import FormulaBuilder
from .build.jats import make_article, serialize
from .build.metadata import build_front
from .build.tables import TableBuilder
from .classify.document import Classifier
from .enrich.journals import JournalRegistry
from .model.blocks import ImageRun, Paragraph
from .parse.docx_reader import read_docx
from .validate.validator import Validator


@dataclass
class ConvertOptions:
    docx_path: str
    out_dir: str = "output"
    journal_id: Optional[str] = None
    doi: Optional[str] = None
    figures_path: Optional[str] = None
    do_validate: bool = True
    llm: str = "off"
    crossref: bool = False
    refine: bool = False
    agent: bool = False          # 开启 Agent 视觉闭环(质量核心,需 --llm)
    agent_rounds: int = 3        # 闭环最大轮数
    agent_dpi: int = 120         # 闭环渲染页 DPI(默认 120 保真优先,实测 OCR 可读)
    agent_phases: Optional[frozenset] = None  # 启用的闭环修复阶段(默认全开);消融分析用
    llm_cache_dir: Optional[str] = None  # 指定 LLM 缓存目录;消融/再生评测给独立空目录,强制真实模型调用、避免命中旧缓存


@dataclass
class ConvertResult:
    xml_path: str = ""
    article_id: str = ""
    stats: dict = field(default_factory=dict)
    validation: object = None


def _agent_trace_summary(trace: dict) -> dict:
    """把闭环 trace 压成简表(放进 stats 给人看;完整 trace 另落盘)。"""
    if not trace.get("enabled"):
        return {"enabled": False, "reason": trace.get("reason", "")}
    rounds = trace.get("rounds", [])
    n_applied = sum(len(r.get("applied", [])) for r in rounds)
    n_findings = sum(len(r.get("findings", [])) for r in rounds)
    return {
        "enabled": True,
        "pages": trace.get("visual", {}).get("pages", 0),
        "rounds": len(rounds),
        "findings_total": n_findings,
        "repairs_applied": n_applied,
    }


def _collect_body_images(doc) -> list:
    imgs = []
    for b in doc.blocks:
        if isinstance(b, Paragraph):
            for r in b.runs:
                if isinstance(r, ImageRun):
                    imgs.append(r)
    return imgs


def convert(opts: ConvertOptions) -> ConvertResult:
    t0 = time.time()
    doc = read_docx(opts.docx_path)

    registry = JournalRegistry()
    journal_id = opts.journal_id or registry.guess_from_doi(opts.doi)
    article_id = registry.article_id_from_doi(opts.doi) or (journal_id or "article")

    sd = Classifier(journal_id, opts.doi).classify(doc)

    # 参考文献增强:先 CrossRef(权威:DOI/刊名全称),再用 LLM 兜底处理 CrossRef 没命中的。
    # 顺序很关键——CrossRef 数据比模型可靠,且能省下大量模型调用。
    llm = None
    llm_stats = {"provider": "off"}
    crossref_stats = {}
    if opts.crossref and sd.references:
        from .enrich.crossref import enrich_references
        crossref_stats = enrich_references(sd.references, enabled=True)
    if opts.llm and opts.llm != "off":
        from .enrich.refs_llm import structure_with_llm
        from .llm.client import LLMClient
        llm = LLMClient(provider=opts.llm, cache_dir=opts.llm_cache_dir)
        if llm.enabled:
            # 智能升级:仅在确定性抽取留有缺口时让 LLM 补救(对正常文档无操作)
            if opts.refine:
                from .agent.refine import refine as refine_sd
                refine_sd(sd, doc, llm)
            # 只让 LLM 处理尚未结构化(CrossRef 没命中)的参考文献
            todo = [r for r in sd.references if not r.structured]
            structure_with_llm(todo, llm)
        llm_stats = llm.stats

    # 图片来源：优先外部图片包，否则 docx 内嵌
    if opts.figures_path and os.path.exists(opts.figures_path):
        fig_src = FigureSource.from_package(opts.figures_path)
    else:
        fig_src = FigureSource.from_docx_media(doc, _collect_body_images(doc))

    formula = FormulaBuilder()
    figures = FigureBuilder(fig_src, article_id, opts.out_dir)
    tables = TableBuilder()
    # 复用上面建好的 LLM(若启用);供看图读表用。
    # 注:热启动即用"逐张表的内嵌图片"做高质量重建(图片裁剪干净,优于整页渲染);
    # Agent 闭环在其上做独立视觉核对——确认忠实、补热启动漏掉的、修结构/作者(质量核心)。
    _ctx_llm = llm if (llm is not None and llm.enabled) else None
    ctx = BuildContext(formula, figures, tables, llm=_ctx_llm,
                       out_dir=opts.out_dir, article_id=article_id)

    # 注:表格已全部走确定性构建(图片表→<graphic>、制表符表→切 tab 重建、原生表→直接建),
    # 不再有"看图/看文本用模型重建表"的调用,故删除原表格预热(空转且徒增 API 调用)。

    # 组装 article
    article = make_article(sd.article_type, "en")
    import datetime
    default_year = str(datetime.date.today().year)  # 无收发日期时版权年的兜底
    article.append(build_front(sd, registry, opts.doi, journal_id,
                               ctx.inline_math, default_year=default_year))
    article.append(build_body(sd, ctx))
    back = build_back(sd, ctx)
    if back is not None:
        article.append(back)

    # ===== Agent 视觉闭环(质量保证核心)=====
    # 在热启动草稿上,用"渲染页 + VLM 视觉核对"独立审视并就地修复(漏表/漏作者…),
    # 循环直到收敛。需 --llm 提供模型;模型不可用时自动跳过、退回草稿(向后兼容)。
    agent_trace = {"enabled": False, "reason": "未开启 --agent"}
    if opts.agent:
        from .agent.loop import run_agent_loop

        def _rebuild_front():
            return build_front(sd, registry, opts.doi, journal_id,
                               ctx.inline_math, default_year=default_year)

        agent_trace = run_agent_loop(
            article, sd, doc, ctx, llm,
            docx_path=opts.docx_path, out_dir=opts.out_dir,
            rebuild_front=_rebuild_front, max_rounds=opts.agent_rounds,
            dpi=opts.agent_dpi, phases=opts.agent_phases)

    # 交叉引用解析（正文 "Fig.N"/"Table N"/"[n]" → <xref>）
    from .build.xref import XrefResolver
    # 文献显示号集合:优先用 build_ref_list 给出的真实映射(处理跳号),否则退化为 1..N
    ref_nums = set(ctx.ref_num_to_id.keys()) or set(range(1, len(sd.references) + 1))
    xresolver = XrefResolver(
        ref_nums=ref_nums,
        fig_nums=figures.numbers,
        table_nums=tables.numbers,
        eqn_nums=list(range(1, formula.stats["disp"] + 1)),
    )
    n_xref = xresolver.process(article)

    # 最后一道防线:就地修掉残留的悬空引用/空表行(确定性,保证稳定合规)
    from .validate.repair import repair
    repair(article)

    xml_bytes = serialize(article)

    os.makedirs(opts.out_dir, exist_ok=True)
    xml_path = os.path.join(opts.out_dir, "%s.xml" % article_id)
    with open(xml_path, "wb") as f:
        f.write(xml_bytes)

    # 用最终 article 树重算关键计数(闭环新增的表/作者直接改树,不经 builder 计数器)
    from .agent.outline import counts as _final_counts
    fc = _final_counts(article)

    result = ConvertResult(xml_path=xml_path, article_id=article_id)
    result.stats = {
        "authors": fc["authors"],
        "affiliations": len(sd.affiliations),
        "keywords": len(sd.keywords),
        "abstract_sections": len(sd.abstract),
        "body_sections": len(sd.body),
        "references": len(sd.references),
        "figures_exported": len(figures.exported),
        "formulas": formula.stats,
        "tables": fc["tables"],
        "xrefs": n_xref,
        "refs_structured": sum(1 for r in sd.references if r.structured),
        "llm": llm_stats,
        "crossref": crossref_stats,
        "agent": _agent_trace_summary(agent_trace),
        "elapsed_sec": round(time.time() - t0, 2),
    }
    # 闭环全过程留痕落盘(透明交付:每轮 findings/applied/skipped/计数)
    if opts.agent and agent_trace.get("enabled"):
        import json as _json
        trace_path = os.path.join(opts.out_dir, "%s.agent-trace.json" % article_id)
        with open(trace_path, "w", encoding="utf-8") as f:
            _json.dump(agent_trace, f, ensure_ascii=False, indent=2)
        result.stats["agent"]["trace_path"] = trace_path
    if opts.do_validate:
        result.validation = Validator().validate_bytes(xml_bytes)
        from .validate.checks import jats4r_checks, run_checks, summarize
        issues = run_checks(xml_bytes)
        result.stats["checks"] = summarize(issues)
        result.stats["jats4r"] = summarize(jats4r_checks(xml_bytes))
        result.issues = issues
    return result
