"""Word 事实层的无损、可寻址对象图。

这一层只记录“源文档里有什么、在哪里”，不判断标题、作者、
参考文献等语义角色。语义层只保存 :class:`SourceText` 地址，最终文字
和格式在渲染时由这一层机械取回。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable, Optional, TypeAlias


OBJECT_REPLACEMENT = "\ufffc"
SOURCE_SCHEMA = "word2jats.source-document"
SOURCE_SCHEMA_VERSION = 1

TextRange: TypeAlias = tuple[str, int, int]


@dataclass(frozen=True)
class SourcePart:
    """docx 包内一个可读部件。"""

    part_id: str
    name: str
    uri: str
    content_type: str = ""
    node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunRef:
    """一段字符的源 run 身份与实际生效格式。"""

    run_id: str
    part: str
    node_path: str
    style_id: Optional[str] = None
    bold: bool = False
    italic: bool = False
    superscript: bool = False
    subscript: bool = False
    underline: bool = False
    strike: bool = False
    small_caps: bool = False
    language: Optional[str] = None


@dataclass(frozen=True)
class RunSpan:
    start: int
    end: int
    run: RunRef


@dataclass(frozen=True)
class LinkSpan:
    start: int
    end: int
    target: str
    source: str = "hyperlink"


@dataclass(frozen=True)
class ObjectAnchor:
    pos: int
    occ_id: str


@dataclass
class SourceNode:
    """有稳定路径的源节点：段落、表、行、格或其他容器。"""

    node_id: str
    part: str
    kind: str
    parent: Optional[str]
    order: int
    text: str = ""
    run_spans: list[RunSpan] = field(default_factory=list)
    links: list[LinkSpan] = field(default_factory=list)
    objects: list[ObjectAnchor] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectRelation:
    kind: str
    target: str


@dataclass
class ObjectOccurrence:
    """对象的一次出现；出现不等于物理资源。"""

    occ_id: str
    kind: str
    node_id: str
    char_pos: int
    representation_group_id: Optional[str] = None
    representation_role: Optional[str] = None
    composition_id: Optional[str] = None
    composition_index: Optional[int] = None
    resource_id: Optional[str] = None
    relations: list[ObjectRelation] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class BinaryResource:
    res_id: str
    rel_target: str
    content_type: str
    blob: bytes
    fmt: str
    part: str = "document"

    @property
    def digest(self) -> str:
        return sha256(self.blob).hexdigest()


@dataclass
class OmmlResource:
    res_id: str
    part: str
    node_path: str
    omml_xml: str


@dataclass
class ChartResource:
    res_id: str
    part: str
    node_path: str
    xml: str


@dataclass
class SmartArtResource:
    res_id: str
    part: str
    node_path: str
    xml: str


Resource: TypeAlias = BinaryResource | OmmlResource | ChartResource | SmartArtResource


@dataclass(frozen=True)
class UnsupportedSource:
    part: str
    node_path: str
    kind: str
    detail: str
    visible: bool = True


@dataclass(frozen=True)
class ResolvedRun:
    """SourceText 切片后可直接供渲染层消费的最小片段。"""

    text: str
    source_range: TextRange
    run: Optional[RunRef]
    hyperlink: Optional[str] = None


@dataclass(frozen=True)
class SourceText:
    """语义层内容字段的统一类型：只存源字符区间。"""

    ranges: tuple[TextRange, ...] = ()

    def text(self, doc: "SourceDocument") -> str:
        return "".join(doc.slice_text(item) for item in self.ranges)

    def runs(self, doc: "SourceDocument") -> list[ResolvedRun]:
        out: list[ResolvedRun] = []
        for item in self.ranges:
            out.extend(doc.slice_runs(item))
        return out


@dataclass
class SourceDocument:
    """一份 docx 的事实层快照。"""

    parts: list[SourcePart] = field(default_factory=list)
    nodes: list[SourceNode] = field(default_factory=list)
    occurrences: list[ObjectOccurrence] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    unsupported: list[UnsupportedSource] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self._reindex()

    def _reindex(self):
        self._nodes = {node.node_id: node for node in self.nodes}
        self._occurrences = {occ.occ_id: occ for occ in self.occurrences}
        self._resources = {res.res_id: res for res in self.resources}

    def node(self, node_id: str) -> SourceNode:
        return self._nodes[node_id]

    def occurrence(self, occ_id: str) -> ObjectOccurrence:
        return self._occurrences[occ_id]

    def resource(self, res_id: str) -> Resource:
        return self._resources[res_id]

    def slice_text(self, text_range: TextRange) -> str:
        node_id, start, end = text_range
        node = self.node(node_id)
        _check_bounds(node, start, end)
        return node.text[start:end]

    def slice_runs(self, text_range: TextRange) -> list[ResolvedRun]:
        """按 run 和链接边界切片；部分覆盖 run 时不扩大到原 run。"""
        node_id, start, end = text_range
        node = self.node(node_id)
        _check_bounds(node, start, end)
        if start == end:
            return []

        cuts = {start, end}
        for span in node.run_spans:
            if span.end > start and span.start < end:
                cuts.update((max(start, span.start), min(end, span.end)))
        for link in node.links:
            if link.end > start and link.start < end:
                cuts.update((max(start, link.start), min(end, link.end)))
        points = sorted(cuts)
        out: list[ResolvedRun] = []
        for left, right in zip(points, points[1:]):
            if left == right:
                continue
            run = next((span.run for span in node.run_spans
                        if span.start <= left and right <= span.end), None)
            hyperlink = next((link.target for link in node.links
                              if link.start <= left and right <= link.end), None)
            out.append(ResolvedRun(
                text=node.text[left:right],
                source_range=(node_id, left, right),
                run=run,
                hyperlink=hyperlink,
            ))
        return _merge_resolved(out)

    def validate(self) -> None:
        """验证快照的身份、范围与对象引用闭合。"""
        self._reindex()
        _unique("part_id", [part.part_id for part in self.parts])
        _unique("node_id", [node.node_id for node in self.nodes])
        _unique("occ_id", [occ.occ_id for occ in self.occurrences])
        _unique("res_id", [res.res_id for res in self.resources])
        part_ids = {part.part_id for part in self.parts}
        for node in self.nodes:
            if node.part not in part_ids:
                raise ValueError(f"{node.node_id}: 未知部件 {node.part}")
            if node.parent is not None and node.parent not in self._nodes:
                raise ValueError(f"{node.node_id}: 未知父节点 {node.parent}")
            last = 0
            for span in sorted(node.run_spans, key=lambda item: (item.start, item.end)):
                _check_bounds(node, span.start, span.end)
                if span.start < last:
                    raise ValueError(f"{node.node_id}: run 区间重叠")
                last = span.end
            for link in node.links:
                _check_bounds(node, link.start, link.end)
            for anchor in node.objects:
                if not 0 <= anchor.pos < len(node.text):
                    raise ValueError(f"{node.node_id}: 对象位置越界 {anchor.pos}")
                if node.text[anchor.pos] != OBJECT_REPLACEMENT:
                    raise ValueError(f"{node.node_id}: 对象位置没有 U+FFFC")
                occ = self._occurrences.get(anchor.occ_id)
                if occ is None or occ.node_id != node.node_id or occ.char_pos != anchor.pos:
                    raise ValueError(f"{node.node_id}: 对象锚点 {anchor.occ_id} 不闭合")
        anchored = {anchor.occ_id for node in self.nodes for anchor in node.objects}
        if anchored != set(self._occurrences):
            missing = sorted(set(self._occurrences) - anchored)
            raise ValueError(f"出现记录没有锚点: {missing}")
        for occ in self.occurrences:
            if occ.resource_id is not None and occ.resource_id not in self._resources:
                raise ValueError(f"{occ.occ_id}: 未知资源 {occ.resource_id}")
        for part in self.parts:
            unknown = set(part.node_ids) - set(self._nodes)
            if unknown:
                raise ValueError(f"{part.part_id}: 未知节点 {sorted(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": SOURCE_SCHEMA,
            "version": SOURCE_SCHEMA_VERSION,
            "parts": [_part_dict(item) for item in self.parts],
            "nodes": [_node_dict(item) for item in self.nodes],
            "occurrences": [_occurrence_dict(item) for item in self.occurrences],
            "resources": [_resource_dict(item) for item in self.resources],
            "unsupported": [_unsupported_dict(item) for item in self.unsupported],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceDocument":
        if raw.get("schema") != SOURCE_SCHEMA:
            raise ValueError("不是 word2jats 源对象图快照")
        if raw.get("version") != SOURCE_SCHEMA_VERSION:
            raise ValueError(f"不支持的源快照版本: {raw.get('version')}")
        doc = cls(
            parts=[_part_from(item) for item in raw.get("parts", [])],
            nodes=[_node_from(item) for item in raw.get("nodes", [])],
            occurrences=[_occurrence_from(item) for item in raw.get("occurrences", [])],
            resources=[_resource_from(item) for item in raw.get("resources", [])],
            unsupported=[UnsupportedSource(**item) for item in raw.get("unsupported", [])],
            metadata=dict(raw.get("metadata") or {}),
        )
        doc.validate()
        return doc


def _check_bounds(node: SourceNode, start: int, end: int):
    if not (0 <= start <= end <= len(node.text)):
        raise ValueError(f"{node.node_id}: 字符区间越界 ({start}, {end})/{len(node.text)}")


def _unique(name: str, values: Iterable[str]):
    seen = set()
    for value in values:
        if value in seen:
            raise ValueError(f"重复 {name}: {value}")
        seen.add(value)


def _merge_resolved(items: list[ResolvedRun]) -> list[ResolvedRun]:
    out: list[ResolvedRun] = []
    for item in items:
        if (out and out[-1].run == item.run and out[-1].hyperlink == item.hyperlink
                and out[-1].source_range[0] == item.source_range[0]
                and out[-1].source_range[2] == item.source_range[1]):
            old = out[-1]
            out[-1] = ResolvedRun(
                text=old.text + item.text,
                source_range=(old.source_range[0], old.source_range[1], item.source_range[2]),
                run=old.run,
                hyperlink=old.hyperlink,
            )
        else:
            out.append(item)
    return out


def _part_dict(item: SourcePart) -> dict[str, Any]:
    return {"part_id": item.part_id, "name": item.name, "uri": item.uri,
            "content_type": item.content_type, "node_ids": list(item.node_ids)}


def _part_from(raw: dict[str, Any]) -> SourcePart:
    return SourcePart(part_id=raw["part_id"], name=raw["name"], uri=raw["uri"],
                      content_type=raw.get("content_type", ""),
                      node_ids=tuple(raw.get("node_ids", [])))


def _run_dict(item: RunRef) -> dict[str, Any]:
    return {key: getattr(item, key) for key in RunRef.__dataclass_fields__}


def _node_dict(item: SourceNode) -> dict[str, Any]:
    return {
        "node_id": item.node_id, "part": item.part, "kind": item.kind,
        "parent": item.parent, "order": item.order, "text": item.text,
        "run_spans": [{"start": span.start, "end": span.end,
                       "run": _run_dict(span.run)} for span in item.run_spans],
        "links": [vars(link) for link in item.links],
        "objects": [vars(anchor) for anchor in item.objects],
        "properties": item.properties,
    }


def _node_from(raw: dict[str, Any]) -> SourceNode:
    return SourceNode(
        node_id=raw["node_id"], part=raw["part"], kind=raw["kind"],
        parent=raw.get("parent"), order=int(raw["order"]), text=raw.get("text", ""),
        run_spans=[RunSpan(start=int(span["start"]), end=int(span["end"]),
                           run=RunRef(**span["run"]))
                   for span in raw.get("run_spans", [])],
        links=[LinkSpan(**item) for item in raw.get("links", [])],
        objects=[ObjectAnchor(**item) for item in raw.get("objects", [])],
        properties=dict(raw.get("properties") or {}),
    )


def _occurrence_dict(item: ObjectOccurrence) -> dict[str, Any]:
    return {
        "occ_id": item.occ_id, "kind": item.kind, "node_id": item.node_id,
        "char_pos": item.char_pos,
        "representation_group_id": item.representation_group_id,
        "representation_role": item.representation_role,
        "composition_id": item.composition_id,
        "composition_index": item.composition_index,
        "resource_id": item.resource_id,
        "relations": [vars(rel) for rel in item.relations],
        "properties": item.properties,
    }


def _occurrence_from(raw: dict[str, Any]) -> ObjectOccurrence:
    return ObjectOccurrence(
        occ_id=raw["occ_id"], kind=raw["kind"], node_id=raw["node_id"],
        char_pos=int(raw["char_pos"]),
        representation_group_id=raw.get("representation_group_id"),
        representation_role=raw.get("representation_role"),
        composition_id=raw.get("composition_id"),
        composition_index=raw.get("composition_index"),
        resource_id=raw.get("resource_id"),
        relations=[ObjectRelation(**item) for item in raw.get("relations", [])],
        properties=dict(raw.get("properties") or {}),
    )


def _resource_dict(item: Resource) -> dict[str, Any]:
    if isinstance(item, BinaryResource):
        return {
            "type": "binary", "res_id": item.res_id, "rel_target": item.rel_target,
            "content_type": item.content_type,
            "blob_b64": base64.b64encode(item.blob).decode("ascii"),
            "sha256": item.digest, "fmt": item.fmt, "part": item.part,
        }
    if isinstance(item, OmmlResource):
        return {"type": "omml", **vars(item)}
    if isinstance(item, ChartResource):
        return {"type": "chart", **vars(item)}
    if isinstance(item, SmartArtResource):
        return {"type": "smartart", **vars(item)}
    raise TypeError(f"不支持的资源类型: {type(item)!r}")


def _resource_from(raw: dict[str, Any]) -> Resource:
    kind = raw.get("type")
    if kind == "binary":
        blob = base64.b64decode(raw["blob_b64"], validate=True)
        if sha256(blob).hexdigest() != raw.get("sha256"):
            raise ValueError(f"{raw.get('res_id')}: 二进制资源摘要不符")
        return BinaryResource(
            res_id=raw["res_id"], rel_target=raw["rel_target"],
            content_type=raw.get("content_type", ""), blob=blob,
            fmt=raw.get("fmt", ""), part=raw.get("part", "document"),
        )
    fields = {key: value for key, value in raw.items() if key != "type"}
    if kind == "omml":
        return OmmlResource(**fields)
    if kind == "chart":
        return ChartResource(**fields)
    if kind == "smartart":
        return SmartArtResource(**fields)
    raise ValueError(f"未知资源类型: {kind}")


def _unsupported_dict(item: UnsupportedSource) -> dict[str, Any]:
    return {key: getattr(item, key) for key in UnsupportedSource.__dataclass_fields__}
