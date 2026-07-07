"""机械定位参考文献区边界（分块，非内容判定）。

只做一件事：找出"References/Bibliography 标题"或首条"[1]"参考条目的位置，把文档切成
front+body 区与 references 区。找的是明显的文本记号（"References" 这个词、"[1]" 编号），
属表示层分块；"这一段扮演什么角色"仍全交给 LLM。标记缺失时退化为把更多内容交给 LLM 判。
"""

from __future__ import annotations

from .patterns import REF_LABEL_BRACKET, REFERENCES_HEAD

# front 区上限：前置区（标题/作者/单位/通讯/日期/摘要/关键词）总在文档最前，
# 给 front pass 一个足够宽的窗口即可，无需喂整篇。
FRONT_CAP = 130


def find_refs_boundary(stream):
    """返回 (refs_head_idx, refs_start)：
    - refs_head_idx: "References" 标题块的 idx（无则 None）
    - refs_start: 参考条目区的起始 idx（用于切分；front+body = [0, refs_start)）
    """
    n = len(stream.lines)
    head_idx = None
    # 从后向前找"References"整行标题（正文里也可能出现该词，取靠后的更稳）
    for ln in reversed(stream.lines):
        if ln.kind == "para" and REFERENCES_HEAD.match(ln.text.strip()):
            head_idx = ln.idx
            break
    if head_idx is not None:
        # 参考条目从标题后第一个非空块起
        for ln in stream.lines:
            if ln.idx > head_idx and ln.text.strip():
                return head_idx, ln.idx
        return head_idx, head_idx + 1

    # 无标题：在后 45% 里找首条 "[1]" 参考条目
    start_scan = int(n * 0.55)
    for ln in stream.lines:
        if ln.idx >= start_scan and ln.kind == "para":
            m = REF_LABEL_BRACKET.match(ln.text.strip())
            if m and m.group(1) == "1":
                return None, ln.idx
    # 实在找不到：整篇当 front+body，无独立参考区
    return None, n


def front_window(stream, refs_start):
    """front pass 的输入窗口上界。"""
    return min(refs_start, FRONT_CAP)
