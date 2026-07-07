"""渲染 + 出口校验（+ 确定性修复兜底）。

方法的"出错兜底"由**确定性上游守卫**承担，而非事后向 LLM 重问——这是有意的取舍：
守卫确定、即时、无抖动，且本架构下正文按源块索引取回原文、内容守恒结构性成立，
出口几乎无编造可修，LLM 重问既慢又引入非确定性，得不偿失。守卫分布在：
- 组装层：参考字段须为原文子串/词重叠，否则置空、必要时退回 mixed-citation（防编造）；
  无标签参考按位置补号；结构化摘要块内切分（防重复）。
- 渲染层：交叉引用只在目标存在时生成、ref id 强制唯一（防悬空/重复）。
- 渲染末：``validate.repair`` 机械修掉残留悬空引用/空表行。
本函数据此渲染一次并跑出口校验（内容守恒 + DTD + 结构自洽），产出校验报告。
"""

from __future__ import annotations

from ..render.render import render_document


def render_verify_repair(sd, meta, llm, registry, doi, journal_id, fig_src,
                         article_id, out_dir, default_year=None,
                         docx_path=None, max_rounds=2, do_validate=True):
    xml_bytes, ctx = render_document(
        sd, registry, doi, journal_id, fig_src, article_id, out_dir,
        default_year=default_year)

    vreport = None
    if do_validate and docx_path:
        from .verify import verify
        vreport = verify(xml_bytes, docx_path)
    return xml_bytes, ctx, vreport
