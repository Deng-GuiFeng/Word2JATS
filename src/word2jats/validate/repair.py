"""确定性修复(检查→修复循环的"修复"层中,安全、不需模型的那部分)。

构建阶段已尽量不产生悬空引用/空行,这里作为**最后一道防线**:对未见过的奇怪文档,
即便上游漏了,也把明显的结构问题就地修掉,保证输出稳定合规。纯结构操作,不改语义内容。
"""

from __future__ import annotations

from lxml import etree


def repair(root) -> dict:
    """就地修复 root。返回各类修复计数。"""
    fixed = {"dangling_xref": 0, "empty_tr": 0}

    ids = {el.get("id") for el in root.iter() if el.get("id")}

    # 1) 悬空 xref:rid 指向不存在的 id → 拆掉 xref 外壳,保留其文字
    for xref in list(root.iter("xref")):
        rid = xref.get("rid")
        if rid and any(one not in ids for one in rid.split()):
            _unwrap(xref)
            fixed["dangling_xref"] += 1

    # 2) 空表格行(无 th/td)→ 删除
    for tr in list(root.iter("tr")):
        if not (tr.findall("td") or tr.findall("th")):
            parent = tr.getparent()
            if parent is not None:
                _absorb_tail(tr)
                parent.remove(tr)
                fixed["empty_tr"] += 1

    return fixed


def _unwrap(el):
    """用元素的文本/子节点替换它本身(去掉外壳标签,内容上提到父级)。"""
    parent = el.getparent()
    if parent is None:
        return
    idx = list(parent).index(el)
    text = "".join(el.itertext())
    tail = el.tail or ""
    # 简化:把纯文本接到前一个兄弟的 tail 或父的 text
    if idx == 0:
        parent.text = (parent.text or "") + text + tail
    else:
        prev = parent[idx - 1]
        prev.tail = (prev.tail or "") + text + tail
    parent.remove(el)


def _absorb_tail(el):
    """删除元素前,把它的 tail 文本并到相邻节点,避免丢字。"""
    tail = el.tail
    if not tail:
        return
    parent = el.getparent()
    idx = list(parent).index(el)
    if idx == 0:
        parent.text = (parent.text or "") + tail
    else:
        prev = parent[idx - 1]
        prev.tail = (prev.tail or "") + tail
