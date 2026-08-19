"""把无损源对象图铺成模型可见、可回指的全文清单。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable, Optional

from ..model.source import (
    OBJECT_REPLACEMENT, SourceDocument, SourceNode, TextRange,
)


@dataclass(frozen=True)
class DisplayRecord:
    """清单中的一行；``source_nodes`` 是该行所指的原节点。"""

    key: str
    text: str
    source_nodes: tuple[str, ...]
    kind: str = "paragraph"  # paragraph | table | table-row
    part: str = "document"
    # 清单文字到 Word 源字符的逐字映射。None 表示为了展示表格
    # 而加入的分隔符，它们不是稿件字符。
    source_map: tuple[Optional[TextRange], ...] = ()

    def __post_init__(self):
        if self.source_map and len(self.source_map) != len(self.text):
            raise ValueError("清单文字与源映射长度不一致")

    def render(self) -> str:
        if self.kind == "table":
            return f"[{self.key}|表]"
        return f"[{self.key}] {self.text}"


@dataclass(frozen=True)
class SerializedDocument:
    source: SourceDocument
    records: tuple[DisplayRecord, ...]

    @staticmethod
    def _format_values(run) -> tuple[str, ...]:
        return tuple(
            key
            for key in (
                "bold", "italic", "superscript", "subscript",
                "underline", "strike", "small_caps",
            )
            if getattr(run, key)
        )

    def _structural_facts(self, record: DisplayRecord) -> dict:
        """Return only source facts useful to block-structure interpretation."""
        if record.kind != "paragraph" or len(record.source_nodes) != 1:
            return {}
        node = self.source.node(record.source_nodes[0])
        # 正文结构判断只需要显式段落样式、提纲/编号和有效字符
        # 格式。每段都重复的默认样式名和对齐方式仍留在事实层，
        # 但不扩张本次章节判断的模型输入。
        evidence = {}
        for key in ("outline_level", "numbering"):
            if node.properties.get(key) is not None:
                evidence["outline" if key == "outline_level" else key] = (
                    node.properties[key]
                )

        mapped = [item for item in record.source_map if item is not None]
        if mapped:
            left = min(item[1] for item in mapped)
            right = max(item[2] for item in mapped)
        else:
            left = right = 0
        spans = []
        for span in node.run_spans:
            start, end = max(left, span.start), min(right, span.end)
            values = self._format_values(span.run)
            if start >= end or not values:
                continue
            current = [start, end, "+".join(values)]
            if spans and spans[-1][1] == start and spans[-1][2] == current[2]:
                spans[-1][1] = end
            else:
                spans.append(current)
        if spans:
            evidence["f"] = spans
        facts = {}
        style_id = node.properties.get("style_id")
        # outline_level 已是样式链解析后的实际结构属性；同时
        # 重复输出其任意样式名不增加事实。仅对没有提纲级别、
        # 但有编号属性的段落附带样式身份作为补充证据。
        if (style_id is not None and "numbering" in evidence
                and "outline" not in evidence):
            facts["s"] = [style_id, node.properties.get("style_name")]
        evidence = {
            {"outline": "o", "numbering": "n"}.get(key, key): value
            for key, value in evidence.items()
        }
        facts.update(evidence)
        return facts

    def render(self, indices: Optional[Iterable[int]] = None, *,
               structural_facts: bool = False) -> str:
        selected = self.records if indices is None else tuple(
            self.records[index] for index in indices
        )
        lines = []
        for item in selected:
            lines.append(item.render())
            facts = self._structural_facts(item) if structural_facts else {}
            if facts:
                lines.append(
                    f"  WORD_FACTS({item.key}) "
                    + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
                )
        return "\n".join(lines)

    def _head_segments(self, record: DisplayRecord) -> list[dict]:
        """把一条显示记录写成带 Word 行内格式的连续文字片段。

        头部模型既需要看到原始文字，也需要区分紧邻姓名的上标单位标记。
        因此这里不把格式猜成语义，只逐字投影 OOXML 已解析出的事实。
        ``text`` 片段按顺序拼接后严格等于 ``record.text``。
        """
        if not record.text:
            return []

        def formats_at(index: int) -> tuple[str, ...]:
            if index >= len(record.source_map):
                return ()
            mapped = record.source_map[index]
            if mapped is None:
                return ()
            node_id, start, _ = mapped
            node = self.source.node(node_id)
            for span in node.run_spans:
                if span.start <= start < span.end:
                    return self._format_values(span.run)
            return ()

        values = []
        start = 0
        current = formats_at(0)
        for index in range(1, len(record.text)):
            found = formats_at(index)
            if found == current:
                continue
            values.append({"text": record.text[start:index], "styles": list(current)})
            start = index
            current = found
        values.append({"text": record.text[start:], "styles": list(current)})
        return values

    def render_head_record(self, index: int) -> str:
        """返回一条紧凑、可寻址且保留行内格式事实的头部输入记录。"""
        record = self.records[index]
        suffix = "|table" if record.kind == "table" else ""
        return (
            f"[{record.key}{suffix}] "
            + json.dumps(
                self._head_segments(record), ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    def head_prefix(self, byte_budget: int) -> tuple[tuple[int, ...], str]:
        """只返回从文档开头起的首个输入窗口，不生成后续窗口。

        预算沿用理解层已有的 UTF-8 字节上界口径。切分只发生在显示
        记录之间；首条记录即使单独超过预算也会完整保留。
        """
        if isinstance(byte_budget, bool) or not isinstance(byte_budget, int) \
                or byte_budget < 1:
            raise ValueError("头部输入预算必须是正整数")
        indices = []
        lines = []
        used = 0
        for index in range(len(self.records)):
            line = self.render_head_record(index)
            cost = len(line.encode("utf-8")) + (1 if lines else 0)
            if lines and used + cost > byte_budget:
                break
            indices.append(index)
            lines.append(line)
            used += cost
        return tuple(indices), "\n".join(lines)

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


def _visible_projection(source: SourceDocument, node: SourceNode):
    """返回模型可见文字及其逐字源地址。"""
    anchors = {item.pos: item.occ_id for item in node.objects}
    out = []
    source_map: list[Optional[TextRange]] = []
    for index, char in enumerate(node.text):
        if char == OBJECT_REPLACEMENT:
            occurrence = anchors.get(index)
            visible = _object_marker(source, occurrence) if occurrence else "⟦对象#?⟧"
        elif char == "\t":
            visible = "⇥"
        else:
            visible = char
        out.append(visible)
        source_map.extend(((node.node_id, index, index + 1),) * len(visible))
    return "".join(out), tuple(source_map)


def visible_text(source: SourceDocument, node: SourceNode) -> str:
    """只改写对象占位的展示形式，普通字符不归一。"""
    return _visible_projection(source, node)[0]


def _paragraph_records(source: SourceDocument, node: SourceNode):
    visible, source_map = _visible_projection(source, node)
    lines = visible.split("\n")
    if not lines:
        lines = [""]
    offset = 0
    for index, line in enumerate(lines):
        key = node.node_id if index == 0 else f"{node.node_id}.{index + 1}"
        yield DisplayRecord(
            key, line, (node.node_id,), "paragraph", node.part,
            source_map[offset:offset + len(line)],
        )
        offset += len(line) + 1


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
        value_maps = []
        nodes = []
        for cell in _children(source, row.node_id, "cell"):
            paras = _descendant_paragraphs(source, cell.node_id)
            nodes.extend(item.node_id for item in paras)
            projected = [_visible_projection(source, item) for item in paras]
            values.append(" ↵ ".join(item[0] for item in projected))
            joined_map = []
            for index, (_, mapping) in enumerate(projected):
                if index:
                    joined_map.extend((None,) * len(" ↵ "))
                joined_map.extend(mapping)
            value_maps.append(tuple(joined_map))
        row_map = []
        for index, mapping in enumerate(value_maps):
            if index:
                row_map.extend((None,) * len(" ⇥ "))
            row_map.extend(mapping)
        yield DisplayRecord(
            f"{table.node_id}.r{row_no}", " ⇥ ".join(values), tuple(nodes),
            "table-row", table.part, tuple(row_map),
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
