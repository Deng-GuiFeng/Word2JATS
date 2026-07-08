"""转换管线（唯一方法：LLM 主理解 + 出口校验）。

docx ──parse──▶ IR ──understand(LLM 三 pass)──▶ SemanticDoc ──render(机械)──▶ JATS
       └────────────────────── verify(内容守恒 / DTD / 结构自洽) ◀──────────────┘

理解由 LLM 端到端承担（结构判定 + 文本切分）；规则只做机械变换（OMML→MathML、图字节
外部化、JATS 序列化、交叉引用）与出口校验。没有可选分支、没有降级档——方法唯一。
"""

from __future__ import annotations

import datetime
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from .build.figures import FigureSource
from .enrich.journals import JournalRegistry
from .llm.client import LLMClient
from .model.blocks import ImageRun, Paragraph
from .parse.docx_reader import read_docx
from .render.render import render_document
from .understand.understand import understand
from .verify.verify import verify as verify_output


@dataclass
class ConvertOptions:
    docx_path: str
    out_dir: str = "output"
    journal_id: Optional[str] = None
    doi: Optional[str] = None
    figures_path: Optional[str] = None
    do_validate: bool = True
    llm: str = "dashscope"                # 理解层模型后端（方法必需）
    model: Optional[str] = None           # 覆盖 provider 默认模型（部署/消融用）
    temperature: float = 0                # 采样温度（默认 0=确定复现）
    top_p: Optional[float] = None         # 显式 top_p（控制变量消融用；默认用服务端默认）
    seed: Optional[int] = None            # 采样种子（temp>0 多种子取平均用）
    llm_cache_dir: Optional[str] = None
    # 下列字段仅为兼容旧调用签名（评测驱动 run.py），新方法不再使用
    crossref: bool = False
    refine: bool = False
    agent: bool = False
    agent_rounds: int = 3
    agent_dpi: int = 120
    agent_phases: object = None
    repair_rounds: int = 2               # 出口校验后的定点修复轮数上限


@dataclass
class ConvertResult:
    xml_path: str = ""
    article_id: str = ""
    stats: dict = field(default_factory=dict)
    validation: object = None


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

    # ---- 理解（LLM 三 pass）----
    llm = LLMClient(provider=opts.llm, model=opts.model,
                    temperature=opts.temperature, top_p=opts.top_p,
                    seed=opts.seed, cache_dir=opts.llm_cache_dir)
    sd, meta = understand(doc, llm)

    # ---- 机械回填的图片来源 ----
    if opts.figures_path and os.path.exists(opts.figures_path):
        fig_src = FigureSource.from_package(opts.figures_path)
    else:
        fig_src = FigureSource.from_docx_media(doc, _collect_body_images(doc))

    default_year = str(datetime.date.today().year)

    # ---- 渲染 + 出口自检（内容守恒 / DTD / 结构自洽）----
    from .verify.repair import render_verify_repair
    xml_bytes, ctx, vreport = render_verify_repair(
        sd, meta, llm, registry, opts.doi, journal_id, fig_src,
        article_id, opts.out_dir, default_year=default_year,
        docx_path=opts.docx_path, max_rounds=opts.repair_rounds,
        do_validate=opts.do_validate)

    os.makedirs(opts.out_dir, exist_ok=True)
    xml_path = os.path.join(opts.out_dir, "%s.xml" % article_id)
    with open(xml_path, "wb") as f:
        f.write(xml_bytes)

    result = ConvertResult(xml_path=xml_path, article_id=article_id)
    result.stats = {
        "authors": len(sd.authors),
        "affiliations": len(sd.affiliations),
        "keywords": len(sd.keywords),
        "abstract_sections": len(sd.abstract),
        "body_sections": len(sd.body),
        "references": len(sd.references),
        "refs_structured": sum(1 for r in sd.references if r.structured),
        "figures_exported": len(ctx.figures.exported),
        "tables": len(ctx.table_numbers),
        "formulas": ctx.formula.stats,
        "xrefs": getattr(ctx, "n_xref", 0),
        "llm": llm.stats,
        "verify": {"n_fab": vreport["conservation"]["n_fab"],
                   "n_lost": vreport["conservation"]["n_lost"],
                   "dtd_ok": vreport["dtd_ok"]} if vreport else {},
        "elapsed_sec": round(time.time() - t0, 2),
    }

    if opts.do_validate and vreport is not None:
        from .validate.validator import Validator
        result.validation = Validator().validate_bytes(xml_bytes)
        result.stats["checks"] = vreport.get("checks", {})
    return result
