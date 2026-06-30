#!/usr/bin/env python
"""为"只有输入"的新样例生成**候选伪标签**:两路独立来源 + 一致性标记。

两路来源相互独立、互为校验,是后续多代理严谨核验的基础:
  (1) DET  —— 直接读原始 OOXML/python-docx(非我们的分类器),提取可机械确定的事实
             (作者行原文、单位行、ORCID 串、收发日期、表格网格、参考文献段、图题注)。
  (2) LLM  —— 把 docx 纯文本喂本地多模态大模型(GPU),分模块结构化抽取。
两者**都不依赖被测的 word2jats 输出**,从而可作为优化目标(金标准近似)。

用法: python scripts/gen_label.py <docx> -o <out.json>
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from word2jats.parse.docx_reader import read_docx          # noqa: E402
from word2jats.model.blocks import Paragraph, Table         # noqa: E402
from word2jats.llm.client import LLMClient                  # noqa: E402

ORCID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dxX]")


def det_extract(doc):
    """直接读原始结构,提取可机械确定的事实。"""
    paras = [p for p in doc.paragraphs if p.text.strip()]
    texts = [p.text.strip() for p in paras]
    out = {}
    # ORCID 串(全文)
    out["orcid_strings"] = sorted(set(
        m.group() for t in texts for m in [ORCID_RE.search(t.replace(" ", ""))] if m))
    # 收发日期相关行
    out["date_lines"] = [t[:120] for t in texts
                         if re.search(r"(?i)\b(submitted|received|revised|accepted)\b", t)
                         and re.search(r"\d", t) and len(t) < 160]
    # 表格网格结构(真 w:tbl)
    tbls = []
    for ti, tb in enumerate(doc.tables):
        rows = []
        for row in tb.rows[:4]:
            rows.append([(c.text.strip()[:30], c.grid_span, c.v_merge) for c in row.cells])
        tbls.append({"index": ti, "n_rows": len(tb.rows),
                     "n_grid_cols": len(tb.grid_cols) if tb.grid_cols else None,
                     "first_rows": rows})
    out["tables_raw"] = tbls
    # 图题注候选(Fig. N. ...)
    out["fig_captions"] = [t[:120] for t in texts if re.match(r"(?i)^\s*fig(ure)?s?\.?\s*\d", t)]
    # 参考文献段计数(References 标题之后的非空段)
    ri = next((i for i, t in enumerate(texts)
               if re.match(r"(?i)^\s*(references?|bibliography|参考文献)\s*:?\s*$", t)), None)
    out["references_section_index"] = ri
    out["references_after_count"] = (len(texts) - ri - 1) if ri is not None else 0
    # 作者行(标题后、第一段含逗号/上标的)+ 其上标
    out["author_line_candidates"] = []
    for p in paras[1:6]:
        sup = "".join(r.text for r in p.runs
                      if getattr(r, "superscript", False) and r.text)
        if ("," in p.text or sup) and len(p.text) < 400:
            out["author_line_candidates"].append({"text": p.text.strip()[:300], "superscripts": sup})
    # 前 30 段(供核对前置区)
    out["front_paras"] = [{"i": i, "style": (p.style_name or "-"), "text": p.text.strip()[:160]}
                          for i, p in enumerate(paras[:30])]
    return out


_MODULE_SYS = ("你是严谨的学术论文元数据抽取器。**只依据给定文本**,不编造、不补全外部知识。"
               "拿不准就留空并标注。只输出 JSON。")


def llm_extract(doc, llm):
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    head = "\n".join(paras[:40])           # 前置区
    body_head = "\n".join(paras[:80])
    res = {}
    # 元数据(标题/作者/单位/编辑/日期/关键词/摘要结构)
    prompt = (
        "下面是一篇论文的开头文本。抽取并返回 JSON:\n"
        '{"title":"", "authors":[{"given":"","surname":"","aff_labels":[],'
        '"corresponding":false,"equal_contrib":false,"orcid":""}],'
        '"affiliations":[{"label":"","text":""}], "editors":[{"given":"","surname":""}],'
        '"dates":{"received":"","revised":"","accepted":""},'
        '"keywords":[], "abstract_subheads":[]}\n'
        "规则:作者英文名按'名 姓'拆;aff_labels 是作者上标里的单位编号;"
        "corresponding 看是否带*;equal_contrib 看是否带†/#;orcid 若文中给出则填(否则空);"
        "abstract_subheads 列出摘要里的小标题(如 Background/Methods),无结构化则空数组。\n\n"
        "文本:\n" + head)
    res["meta"] = llm.extract_json(_MODULE_SYS, prompt, max_tokens=3000)
    # 图/表/参考文献计数与要点
    prompt2 = (
        "下面是论文正文前部。返回 JSON:"
        '{"figure_captions":[{"number":1,"caption":""}],'
        '"table_captions":[{"number":1,"caption":""}]}'
        "(只列出现的图/表题注,number 取其编号)。\n\n文本:\n" + body_head)
    res["floats"] = llm.extract_json(_MODULE_SYS, prompt2, max_tokens=2000)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    doc = read_docx(args.docx)
    llm = LLMClient(provider="local")
    if not llm.enabled:
        print("local LLM 未就绪", file=sys.stderr); sys.exit(2)
    label = {"source_docx": args.docx,
             "det": det_extract(doc),
             "llm": llm_extract(doc, llm)}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(label, f, ensure_ascii=False, indent=2)
    print("written", args.out)


if __name__ == "__main__":
    main()
