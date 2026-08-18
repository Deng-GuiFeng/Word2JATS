"""把无损源对象图铺成模型可见、可回指的全文清单。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..model.source import OBJECT_REPLACEMENT, SourceDocument, SourceNode


@dataclass(frozen=True)
class DisplayRecord:
    """清单中的一行；``source_nodes`` 是该行所指的原节点。"""

    key: str
    text: str
    source_nodes: tuple[str, ...]
    kind: str = "paragraph"  # paragraph | table | table-row
    part: str = "document"

    def render(self) -> str:
        if self.kind == "table":
            return f"[{self.key}|表]"
        return f"[{self.key}] {self.text}"


@dataclass(frozen=True)
class SerializedDocument:
    source: SourceDocument
    records: tuple[DisplayRecord, ...]

    def render(self, indices: Optional[Iterable[int]] = None) -> str:
        selected = self.records if indices is None else tuple(
            self.records[index] for index in indices
        )
        return "\n".join(item.render() for item in selected)

    def by_key(self, key: str) -> Optional[DisplayRecord]:
        return next((item for item in self.records if item.key == key), None)

    @property
    def document_node_ids(self) -> tuple[str, ...]:
        seen = []
        for record in self.records:
            if record.part != "document":
                continue
            for node_id in record.source_nodes:
                if node_id not in seen:
                    seen.append(node_id)
        return tuple(seen)


def _object_marker(source: SourceDocument, occ_id: str) -> str:
    occurrence = source.occurrence(occ_id)
    if occurrence.kind == "omml":
        kind = "公式"
    elif occurrence.kind in {"image", "vml"}:
        kind = "图"
    else:
        kind = "对象"
    return f"⟦{kind}#{occ_id}⟧"


def visible_text(source: SourceDocument, node: SourceNode) -> str:
    """只改写对象占位的展示形式，普通字符不归一。"""
    anchors = {item.pos: item.occ_id for item in node.objects}
    out = []
    for index, char in enumerate(node.text):
        if char == OBJECT_REPLACEMENT:
            occurrence = anchors.get(index)
            out.append(_object_marker(source, occurrence) if occurrence else "⟦对象#?⟧")
        elif char == "\t":
            out.append("⇥")
        else:
            out.append(char)
    return "".join(out)


def _paragraph_records(source: SourceDocument, node: SourceNode):
    lines = visible_text(source, node).split("\n")
    if not lines:
        lines = [""]
    for index, line in enumerate(lines):
        key = node.node_id if index == 0 else f"{node.node_id}.{index + 1}"
        yield DisplayRecord(key, line, (node.node_id,), "paragraph", node.part)


def _children(source: SourceDocument, parent: str, kind: Optional[str] = None):
    values = [item for item in source.nodes
              if item.parent == parent and (kind is None or item.kind == kind)]
    return sorted(values, key=lambda item: (item.order, item.node_id))


def _descendant_paragraphs(source: SourceDocument, root: str):
    result = []

    def walk(parent):
        for child in _children(source, parent):
            if child.kind == "para":
                result.append(child)
            elif child.kind != "table":
                walk(child.node_id)

    walk(root)
    return result


def _table_records(source: SourceDocument, table: SourceNode):
    yield DisplayRecord(table.node_id, "", (table.node_id,), "table", table.part)
    for row_no, row in enumerate(_children(source, table.node_id, "row"), 1):
        values = []
        nodes = []
        for cell in _children(source, row.node_id, "cell"):
            paras = _descendant_paragraphs(source, cell.node_id)
            nodes.extend(item.node_id for item in paras)
            values.append(" ↵ ".join(visible_text(source, item) for item in paras))
        yield DisplayRecord(
            f"{table.node_id}.r{row_no}", " ⇥ ".join(values), tuple(nodes),
            "table-row", table.part,
        )


def _inside_table(source: SourceDocument, node: SourceNode) -> bool:
    parent = node.parent
    while parent:
        owner = source.node(parent)
        if owner.kind == "table":
            return True
        parent = owner.parent
    return False


def serialize(source: SourceDocument) -> SerializedDocument:
    """
    主文、文本框、脚注/尾注均展示；页眉页脚作为已声明政策项显式标记。
    表格内段落只在表行中展示一次，不重复摊平。
    """
    source.validate()
    records: list[DisplayRecord] = []
    ordered = sorted(source.nodes, key=lambda item: (item.order, item.node_id))
    for node in ordered:
        if node.part.startswith(("header", "footer")):
            continue
        if node.kind == "table" and not _inside_table(source, node):
            records.extend(_table_records(source, node))
        elif node.kind == "para" and not _inside_table(source, node):
            records.extend(_paragraph_records(source, node))
    return SerializedDocument(source, tuple(records))
