"""生产转换管线 v2：无损事实层 -> 指针式理解 -> 类型化语义 -> 确定渲染。"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path, PurePosixPath
import shutil
import time
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Optional

from lxml import etree

from .config import PubConfig, decide_publication_year
from .enrich.journals import JournalRegistry
from .llm.client import LLMClient
from .parse.docx_reader import read_source_docx
from .render.v2 import render_v2
from .semantic import model as sm
from .semantic.enrich import apply_publication_config
from .understand.understand import understand
from .understand.passes import UnderstandConfig
from .verify import conservation
from .verify.audit import (
    audit_provenance, audit_source_coverage, audit_structure,
)


@dataclass
class ConvertOptions:
    docx_path: str
    out_dir: str = "output"
    journal_id: Optional[str] = None
    doi: Optional[str] = None
    publication_year: Optional[str] = None
    include_publisher_note: Optional[bool] = None
    do_validate: bool = True
    llm: str = "dashscope"
    model: Optional[str] = None
    temperature: float = 0
    top_p: Optional[float] = None
    seed: Optional[int] = None
    llm_cache_dir: Optional[str] = None
    max_workers: int = 32
    input_token_budget: int = 90_000
    boundary_token_budget: int = 4_000
    output_token_budget: Optional[int] = 128_000
    llm_timeout: Optional[float] = None
    llm_transport_retries: int = 2
    llm_retry_backoff: float = 1.0
    llm_retry_backoff_max: float = 8.0
    progress: object = None


@dataclass
class ConvertResult:
    xml_path: str = ""
    article_id: str = ""
    delivered: bool = False
    candidate_dir: str = ""
    candidate_xml: str = ""
    stats: dict = field(default_factory=dict)
    validation: object = None


def _emit(callback, key, label):
    if callback:
        try:
            callback(key, label)
        except Exception:  # 进度 UI 不得改变转换结果
            pass


def _safe_write_media(staging: Path, media: dict[str, bytes]) -> None:
    root = staging.resolve()
    for href, blob in media.items():
        pure = PurePosixPath(href)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError(f"不安全的媒体路径: {href}")
        target = staging.joinpath(*pure.parts).resolve()
        target.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)


def _walk(value):
    yield value
    if isinstance(value, sm.SemanticDoc):
        for item in fields(value):
            if item.name != "source":
                yield from _walk(getattr(value, item.name))
    elif is_dataclass(value):
        for item in fields(value):
            yield from _walk(getattr(value, item.name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk(item)


def _stats(document: sm.SemanticDoc):
    values = tuple(_walk(document))
    references = document.reference_list.references if document.reference_list else ()
    return {
        "authors": sum(len(group.contributors) for group in document.contributor_groups),
        "affiliations": len(document.affiliations),
        "keywords": sum(len(group.keywords) for group in document.keyword_groups),
        "abstract_sections": sum(len(item.sections) for item in document.abstracts),
        "body_sections": sum(isinstance(item, sm.Section) for item in values),
        "references": len(references),
        "refs_structured": sum(isinstance(item.citation, sm.StructuredCitation)
                               for item in references),
        "figures_exported": sum(isinstance(item, sm.Figure) for item in values),
        "tables": sum(isinstance(item, sm.TableBlock) for item in values),
        "formulas": sum(isinstance(item, sm.Formula) for item in values),
        "xrefs": sum(isinstance(item, sm.CrossReference) for item in values),
    }


def _configured_tokens(document: sm.SemanticDoc) -> Counter:
    """从类型化配置对象取允许的模板词，不读来源账或豁免词表。"""
    result = Counter()
    for value in _walk(document):
        if isinstance(value, sm.ConfigText):
            result.update(conservation.tokens(value.value))
    return result


def _independent_conservation(docx_path, root, document: sm.SemanticDoc) -> dict:
    """不读两本账，独立核对输出是否出现源文和配置都没有的词。

    同一源地址合法用于多位作者时，词的出现次数会增加，但这不是
    无中生有。因此第九门阻断“源文中根本不存在的词”；仅次数超出的
    原有词单列报告，再由逐字符来源账判定每次复用是否合法。
    """
    raw = conservation.check(docx_path, root)
    configured = _configured_tokens(document)
    excess = Counter(raw["fabricated"]) - configured
    if excess:
        source_main, source_aux = conservation.docx_tokens(docx_path)
        source_words = set(source_main) | set(source_aux)
    else:
        source_words = set()
    unseen = Counter({word: count for word, count in excess.items()
                      if word not in source_words})
    repeated = Counter({word: count for word, count in excess.items()
                        if word in source_words})
    return {
        **raw,
        "fabricated": dict(unseen),
        "n_fab": len(unseen),
        "n_fab_occurrences": sum(unseen.values()),
        "overproduced_source_tokens": dict(repeated),
        "n_overproduced_source_tokens": len(repeated),
        "allowed_config_tokens": sum(configured.values()),
    }


def convert(opts: ConvertOptions) -> ConvertResult:
    from .validate.validator import Validator
    from .verify.delivery import (
        archive_candidate, create_staging, deliver_candidate,
        reclassify_failed, write_report,
    )
    from .verify.media import verify_package

    started = time.time()
    _emit(opts.progress, "parse", "解析 Word 文档")
    source = read_source_docx(opts.docx_path)

    registry = JournalRegistry()
    journal_id = opts.journal_id or registry.guess_from_doi(opts.doi)
    article_id = registry.article_id_from_doi(opts.doi) or (journal_id or "article")
    llm = LLMClient(
        provider=opts.llm, model=opts.model, temperature=opts.temperature,
        top_p=opts.top_p, seed=opts.seed, cache_dir=opts.llm_cache_dir,
        max_inflight=opts.max_workers,
        request_timeout=opts.llm_timeout,
        transport_retries=opts.llm_transport_retries,
        retry_backoff=opts.llm_retry_backoff,
        retry_backoff_max=opts.llm_retry_backoff_max,
    )

    _emit(opts.progress, "understand", "大模型判断结构")
    try:
        document, understanding = understand(
            source, llm, UnderstandConfig(
                max_workers=opts.max_workers,
                input_token_budget=opts.input_token_budget,
                boundary_token_budget=opts.boundary_token_budget,
                output_token_budget=opts.output_token_budget,
            )
        )
    finally:
        close_llm = getattr(llm, "close", None)
        if close_llm:
            close_llm()
    year = decide_publication_year(opts.publication_year, document.dates, source)
    journal_info = registry.get(journal_id) or {}
    include_publisher_note = (
        opts.include_publisher_note if opts.include_publisher_note is not None
        else bool(journal_info.get("include-publisher-note", False))
    )
    publication = PubConfig(
        journal_id, opts.doi, year.year, include_publisher_note
    )
    apply_publication_config(document, registry, publication)

    _emit(opts.progress, "render", "渲染 JATS 并回填图片")
    run_id, staging = create_staging(opts.out_dir, article_id)
    try:
        rendered = render_v2(document, media_prefix=article_id)
        xml_path = staging / f"{article_id}.xml"
        xml_path.write_bytes(rendered.xml_bytes)
        _safe_write_media(staging, rendered.media)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    expected_hashes = {
        href: hashlib.sha256(blob).hexdigest() for href, blob in rendered.media.items()
    }
    media_report = verify_package(rendered.xml_bytes, staging, expected_hashes)
    structure_report = audit_structure(rendered.xml_bytes)
    provenance_report = audit_provenance(
        rendered.xml_bytes, rendered.provenance, source
    )
    coverage_report = audit_source_coverage(
        source, rendered.provenance, understanding.get("assignments", ()),
        understanding.get("source_uses", ()),
    )
    conservation_report = None
    validation = None
    if opts.do_validate:
        _emit(opts.progress, "validate", "DTD 校验与内容守恒")
        validation = Validator().validate_bytes(rendered.xml_bytes)
        root = etree.fromstring(
            rendered.xml_bytes,
            etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True),
        )
        conservation_report = _independent_conservation(
            opts.docx_path, root, document
        )

    unsupported = [
        {"part": item.part, "node_path": item.node_path, "kind": item.kind,
         "detail": item.detail}
        for item in source.unsupported if item.visible
    ]
    media_codes = {item["code"] for item in media_report.issues}
    structure_codes = {item.code for item in structure_report.issues}
    preliminary_gates = {
        "understanding": not understanding.get("blocking", False),
        "supported_ooxml": not unsupported,
        "well_formed": bool(validation and validation.well_formed),
        "dtd": bool(validation and validation.dtd_valid),
        "id_unique": "DUPLICATE_ID" not in structure_codes,
        "rid_closed": "DANGLING_RID" not in structure_codes,
        "media_bytes": not media_codes.intersection({
            "media_xml_unreadable", "media_path_unsafe", "media_missing",
            "media_source_unregistered", "media_bytes_changed",
        }),
        "media_format": not media_codes.intersection({
            "media_unknown_format", "media_extension_mismatch",
            "media_invalid_structure",
        }),
        "no_redundant_files": not media_codes.intersection({
            "media_unreferenced", "media_export_unreferenced",
        }),
        "source_coverage": coverage_report.ok,
        # 第九门同时要求来源映射闭合和独立逐词守恒不报编造。
        # 两者用不同的实现路径核对，不得用其中一个代替另一个。
        "output_provenance": bool(
            provenance_report.ok
            and conservation_report
            and conservation_report["n_fab"] == 0
        ),
    }
    gate_ok = bool(opts.do_validate and all(preliminary_gates.values()))
    run = archive_candidate(staging, opts.out_dir, article_id, run_id, failed=not gate_ok)
    delivered = False
    delivery_error = None
    final_xml = None
    if gate_ok:
        delivery = deliver_candidate(run.package, opts.out_dir, article_id, run_id)
        delivered = delivery.delivered
        final_xml = delivery.xml
        delivery_error = delivery.error
        if not delivered:
            run = reclassify_failed(run, opts.out_dir, article_id)

    result = ConvertResult(
        xml_path=str(final_xml if delivered else run.xml), article_id=article_id,
        delivered=delivered, candidate_dir=str(run.package),
        candidate_xml=str(run.xml), validation=validation,
    )
    result.stats = {
        **_stats(document), "llm": llm.stats,
        "understanding": {
            "blocking": understanding.get("blocking"),
            "issues": understanding.get("issues", []),
            "reference_count": understanding.get("reference_count", 0),
        },
        "verify": {
            "dtd_ok": bool(validation and validation.dtd_valid),
            "gates": preliminary_gates,
            "structure": structure_report.to_dict(),
            "source_coverage": coverage_report.to_dict(),
            "output_provenance": provenance_report.to_dict(),
            "conservation": conservation_report,
            "conservation_alarm": bool(
                conservation_report and conservation_report["n_fab"]
            ),
        },
        "checks": {
            "high": sum(item.get("severity") == "high"
                        for item in understanding.get("issues", [])),
            "review_blocking": sum(item.get("severity") == "review_blocking"
                                   for item in understanding.get("issues", [])),
        },
        "delivery": {
            "run_id": run_id, "gate_ok": gate_ok, "delivered": delivered,
            "reason": (
                "validation_disabled" if not opts.do_validate
                else "verification_failed" if not gate_ok else delivery_error
            ),
        },
        "media_gate": media_report.as_dict(),
        "publication_year": year.as_dict(),
        "elapsed_sec": round(time.time() - started, 2),
    }
    write_report(run, {
        "schema": "word2jats.conversion-report", "version": 2,
        "article_id": article_id, "candidate_dir": str(run.package),
        "candidate_xml": str(run.xml), "delivered": delivered,
        "delivery": result.stats["delivery"], "gates": preliminary_gates,
        "understanding": understanding, "unsupported": unsupported,
        "media": media_report.as_dict(),
        "structure": structure_report.to_dict(),
        "source_coverage": coverage_report.to_dict(),
        "output_provenance": provenance_report.to_dict(),
        "conservation": conservation_report,
        "publication_year": year.as_dict(),
        "provenance": [item.__dict__ for item in rendered.provenance],
        "validation": ({
            "well_formed": validation.well_formed,
            "dtd_valid": validation.dtd_valid, "errors": validation.errors,
        } if validation is not None else None),
    })
    return result
