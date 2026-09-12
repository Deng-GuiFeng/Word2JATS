"""OOXML 自动编号（numbering.xml）的确定性还原。

Word 里 numPr 编号段落印出的 "1." 是渲染物不是源字符，但其取值由
numbering.xml 的封闭定义与段落出现顺序机械决定——这是文档事实
（既定口径⑪：编号可机械推出时补 label），不是语义猜测。
本模块只还原 decimal（十进制）编号；bullet 及其他字型不产出编号值。
"""

from __future__ import annotations

import re

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PLACEHOLDER = re.compile(r"%(\d)")


def _qn(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _val(element):
    return element.get(_qn("val")) if element is not None else None


def load_numbering(archive) -> dict:
    """读 numbering.xml → {(num_id, ilvl): {"fmt","text","start"}}。"""
    uri = "word/numbering.xml"
    if uri not in archive.namelist():
        return {}
    root = etree.fromstring(archive.read(uri))
    abstract = {}
    for item in root.findall(_qn("abstractNum")):
        levels = {}
        for lvl in item.findall(_qn("lvl")):
            start = _val(lvl.find(_qn("start")))
            levels[lvl.get(_qn("ilvl"))] = {
                "fmt": _val(lvl.find(_qn("numFmt"))),
                "text": _val(lvl.find(_qn("lvlText"))),
                "start": int(start) if start and str(start).isdigit() else 1,
            }
        abstract[item.get(_qn("abstractNumId"))] = levels
    table = {}
    for num in root.findall(_qn("num")):
        num_id = num.get(_qn("numId"))
        levels = {
            ilvl: dict(spec)
            for ilvl, spec in (
                abstract.get(_val(num.find(_qn("abstractNumId")))) or {}
            ).items()
        }
        for override in num.findall(_qn("lvlOverride")):
            ilvl = override.get(_qn("ilvl"))
            start = _val(override.find(_qn("startOverride")))
            if ilvl in levels and start and str(start).isdigit():
                levels[ilvl]["start"] = int(start)
        for ilvl, spec in levels.items():
            table[(num_id, ilvl)] = spec
    return table


def assign_rendered_numbers(nodes, table: dict) -> None:
    """按文档顺序为带 numPr 的段落写入 properties["numbering_rendered"]。

    机械计数：同一 num_id 内该层计数递增，更深层在浅层递增后重置；
    lvlText 的 %N 占位只在引用层全部为 decimal 时才渲染。
    """
    if not table:
        return
    counters: dict[str, dict[int, int]] = {}
    for node in nodes:
        if node.kind != "para":
            continue
        identity = (node.properties or {}).get("numbering")
        if not identity:
            continue
        num_id = str(identity[0]) if identity[0] is not None else None
        ilvl = str(identity[1]) if len(identity) > 1 and identity[1] is not None \
            else "0"
        if num_id in (None, "0"):
            continue
        spec = table.get((num_id, ilvl))
        if not spec or spec.get("fmt") != "decimal" or not spec.get("text"):
            continue
        placeholders = [
            int(match.group(1)) - 1
            for match in _PLACEHOLDER.finditer(spec["text"])
        ]
        renderable = True
        for index in placeholders:
            ref = spec if index == int(ilvl) else table.get((num_id, str(index)))
            if not ref or ref.get("fmt") != "decimal":
                renderable = False
        level = int(ilvl)
        state = counters.setdefault(num_id, {})
        state[level] = state.get(level, spec["start"] - 1) + 1
        for deeper in [key for key in state if key > level]:
            del state[deeper]
        if not renderable:
            continue

        def fill(match) -> str:
            index = int(match.group(1)) - 1
            if index not in state:
                ref = table.get((num_id, str(index))) or {}
                state[index] = ref.get("start", 1)
            return str(state[index])

        node.properties["numbering_rendered"] = _PLACEHOLDER.sub(
            fill, spec["text"]
        )


def restored_number(source, node_id: str) -> str | None:
    """节点还原编号中的数字串（如 "12." → "12"）；无编号返回 None。"""
    try:
        node = source.node(node_id)
    except KeyError:
        return None
    rendered = (node.properties or {}).get("numbering_rendered")
    if not rendered:
        return None
    match = re.search(r"\d+", rendered)
    return match.group() if match else None
