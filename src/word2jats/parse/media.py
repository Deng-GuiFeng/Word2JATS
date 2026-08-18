"""OOXML 对象出现、替代表示组、组成组与物理资源解析。"""

from __future__ import annotations

from dataclasses import dataclass
import posixpath
from pathlib import PurePosixPath
from typing import Optional
from zipfile import ZipFile

from lxml import etree

from ..build.figures import media_format
from ..model.source import (
    BinaryResource,
    ChartResource,
    ObjectRelation,
    OmmlResource,
    SmartArtResource,
)
from .ooxml import NS, qn


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


@dataclass(frozen=True)
class Relationship:
    rel_id: str
    target: str
    rel_type: str
    external: bool = False


@dataclass(frozen=True)
class ObjectSpec:
    kind: str
    resource_id: Optional[str]
    representation_group_id: Optional[str] = None
    representation_role: Optional[str] = None
    composition_id: Optional[str] = None
    composition_index: Optional[int] = None
    relations: tuple[ObjectRelation, ...] = ()
    properties: Optional[dict] = None


def stable_xml_path(element) -> str:
    parts = []
    current = element
    while current is not None and isinstance(current.tag, str):
        name = etree.QName(current).localname
        parent = current.getparent()
        if parent is None:
            parts.append(name)
            break
        siblings = [child for child in parent
                    if isinstance(child.tag, str)
                    and etree.QName(child).localname == name]
        parts.append(f"{name}[{siblings.index(current) + 1}]")
        current = parent
    return "/" + "/".join(reversed(parts))


class PackageRelationships:
    def __init__(self, archive: ZipFile):
        self.archive = archive
        self._cache: dict[str, dict[str, Relationship]] = {}

    @staticmethod
    def _rels_name(source_part: str) -> str:
        source = PurePosixPath(source_part)
        return str(source.parent / "_rels" / (source.name + ".rels"))

    def for_part(self, source_part: str) -> dict[str, Relationship]:
        if source_part in self._cache:
            return self._cache[source_part]
        name = self._rels_name(source_part)
        if name not in self.archive.namelist():
            self._cache[source_part] = {}
            return {}
        root = etree.fromstring(self.archive.read(name))
        source = PurePosixPath(source_part)
        result = {}
        for item in root.findall(f"{{{REL_NS}}}Relationship"):
            rel_id = item.get("Id") or ""
            external = item.get("TargetMode") == "External"
            raw_target = item.get("Target") or ""
            target = raw_target if external else posixpath.normpath(
                posixpath.join(str(source.parent), raw_target)
            )
            result[rel_id] = Relationship(
                rel_id, target, item.get("Type") or "", external
            )
        self._cache[source_part] = result
        return result

    def get(self, source_part: str, rel_id: Optional[str]) -> Optional[Relationship]:
        return self.for_part(source_part).get(rel_id or "")


class MediaRegistry:
    """媒体关系按所属部件解析；rId 从不当作包级全局键。"""

    def __init__(self, archive: ZipFile):
        self.archive = archive
        self.relationships = PackageRelationships(archive)
        self.resources = []
        self._by_uri: dict[tuple[str, str], str] = {}
        self._counter = 0
        self._group_counter = 0
        self._composition_counter = 0
        self._defaults, self._overrides = self._content_types()

    def _content_types(self):
        root = etree.fromstring(self.archive.read("[Content_Types].xml"))
        defaults = {
            item.get("Extension", "").lower(): item.get("ContentType", "")
            for item in root.findall(f"{{{CT_NS}}}Default")
        }
        overrides = {
            item.get("PartName", "").lstrip("/"): item.get("ContentType", "")
            for item in root.findall(f"{{{CT_NS}}}Override")
        }
        return defaults, overrides

    def _content_type(self, uri: str) -> str:
        return self._overrides.get(uri) or self._defaults.get(
            PurePosixPath(uri).suffix.lstrip(".").lower(), ""
        )

    def _next_resource(self) -> str:
        self._counter += 1
        return f"res{self._counter}"

    def add_omml(self, source_part: str, node_path: str, element) -> str:
        """OMML 本体是 XML 资源，与二进制媒体共用全局资源号。"""
        res_id = self._next_resource()
        self.resources.append(OmmlResource(
            res_id=res_id, part=source_part, node_path=node_path,
            omml_xml=etree.tostring(element, encoding="unicode"),
        ))
        return res_id

    def _next_group(self, prefix: str) -> str:
        self._group_counter += 1
        return f"{prefix}{self._group_counter}"

    def _binary(self, uri: str, source_part: str) -> Optional[str]:
        key = ("binary", uri)
        if key in self._by_uri:
            return self._by_uri[key]
        if uri not in self.archive.namelist():
            return None
        blob = self.archive.read(uri)
        fmt = media_format(blob)
        if not fmt:
            fmt = PurePosixPath(uri).suffix.lstrip(".").lower() or "unknown"
        res_id = self._next_resource()
        self.resources.append(BinaryResource(
            res_id=res_id, rel_target=uri,
            content_type=self._content_type(uri), blob=blob, fmt=fmt,
            part=source_part,
        ))
        self._by_uri[key] = res_id
        return res_id

    def _xml_resource(self, uri: str, source_part: str, kind: str) -> Optional[str]:
        key = (kind, uri)
        if key in self._by_uri:
            return self._by_uri[key]
        if uri not in self.archive.namelist():
            return None
        xml = self.archive.read(uri).decode("utf-8", errors="replace")
        res_id = self._next_resource()
        if kind == "chart":
            value = ChartResource(res_id, source_part, uri, xml)
        else:
            value = SmartArtResource(res_id, source_part, uri, xml)
        self.resources.append(value)
        self._by_uri[key] = res_id
        return res_id

    @staticmethod
    def _image_rid(element) -> Optional[str]:
        name = etree.QName(element).localname
        if name == "blip":
            return element.get(qn("r:embed")) or element.get(qn("r:link"))
        return element.get(qn("r:id"))

    def _image_resource(self, part: str, element) -> tuple[Optional[str], Optional[str]]:
        relationship = self.relationships.get(part, self._image_rid(element))
        if relationship is None:
            return None, None
        if relationship.external:
            return None, relationship.target
        return self._binary(relationship.target, part), None

    def _resource_digest(self, resource_id: Optional[str]) -> Optional[str]:
        for resource in self.resources:
            if resource.res_id == resource_id and isinstance(resource, BinaryResource):
                return resource.digest
        return None

    def _image_specs(self, elements, part: str, *, role: str = "image",
                     fallback_by_digest=None) -> list[ObjectSpec]:
        elements = list(elements)
        composition = self._next_group("composition") if len(elements) > 1 else None
        result = []
        for index, element in enumerate(elements, 1):
            resource_id, external = self._image_resource(part, element)
            relations = []
            group = None
            actual_role = role
            if fallback_by_digest is not None:
                group = self._next_group("representation")
                actual_role = "choice"
                digest = self._resource_digest(resource_id)
                fallbacks = fallback_by_digest.get(digest or "", [])
                if fallbacks:
                    fallback_id = fallbacks.pop(0)
                    relations.append(ObjectRelation("fallback_of", fallback_id))
            result.append(ObjectSpec(
                kind="image", resource_id=resource_id,
                representation_group_id=group,
                representation_role=actual_role,
                composition_id=composition,
                composition_index=index if composition else None,
                relations=tuple(relations),
                properties={
                    "xml_path": stable_xml_path(element),
                    "rel_id": self._image_rid(element),
                    "external_target": external,
                },
            ))
        return result

    def scan(self, container, part: str) -> list[ObjectSpec]:
        """解析一个 run 内对象容器，返回按可见顺序排列的逻辑出现。"""
        name = etree.QName(container).localname
        ns = {**NS, "mc": NS["mc"], "o": NS["o"], "c": NS["c"], "dgm": NS["dgm"]}
        if name == "object":
            ole = next(iter(container.xpath(".//o:OLEObject", namespaces=ns)), None)
            preview = next(iter(container.xpath(".//v:imagedata", namespaces=ns)), None)
            payload_id = preview_id = None
            payload_target = preview_target = None
            if ole is not None:
                relationship = self.relationships.get(part, ole.get(qn("r:id")))
                if relationship and not relationship.external:
                    payload_target = relationship.target
                    payload_id = self._binary(relationship.target, part)
            if preview is not None:
                preview_id, preview_target = self._image_resource(part, preview)
            group = self._next_group("representation")
            relations = []
            if payload_id:
                relations.append(ObjectRelation("object_payload", payload_id))
            if preview_id and payload_id:
                relations.append(ObjectRelation("preview_of", payload_id))
            return [ObjectSpec(
                kind="ole", resource_id=preview_id or payload_id,
                representation_group_id=group,
                representation_role="preview" if preview_id else "object",
                relations=tuple(relations),
                properties={
                    "xml_path": stable_xml_path(container),
                    "payload_resource_id": payload_id,
                    "preview_resource_id": preview_id,
                    "payload_target": payload_target,
                    "external_preview": preview_target,
                    "prog_id": ole.get("ProgID") if ole is not None else None,
                },
            )]

        if name == "AlternateContent":
            choices = container.xpath(
                "./mc:Choice//a:blip | ./mc:Choice//v:imagedata", namespaces=ns
            )
            fallbacks = container.xpath(
                "./mc:Fallback//a:blip | ./mc:Fallback//v:imagedata", namespaces=ns
            )
            fallback_by_digest: dict[str, list[str]] = {}
            for element in fallbacks:
                resource_id, _ = self._image_resource(part, element)
                digest = self._resource_digest(resource_id)
                if digest and resource_id:
                    fallback_by_digest.setdefault(digest, []).append(resource_id)
            if choices:
                return self._image_specs(
                    choices, part, fallback_by_digest=fallback_by_digest
                )
            # 没有图片 Choice 的 AlternateContent 通常是文本框或其他形状，
            # Fallback 只是替代表示，不另造一次逻辑出现。
            return []

        chart = next(iter(container.xpath(".//c:chart", namespaces=ns)), None)
        if chart is not None:
            relationship = self.relationships.get(part, chart.get(qn("r:id")))
            resource_id = (
                self._xml_resource(relationship.target, part, "chart")
                if relationship and not relationship.external else None
            )
            return [ObjectSpec(
                "chart", resource_id,
                properties={"xml_path": stable_xml_path(chart)},
            )]

        diagram = next(iter(container.xpath(".//dgm:relIds", namespaces=ns)), None)
        if diagram is not None:
            rel_id = diagram.get(qn("r:dm"))
            relationship = self.relationships.get(part, rel_id)
            resource_id = (
                self._xml_resource(relationship.target, part, "smartart")
                if relationship and not relationship.external else None
            )
            return [ObjectSpec(
                "smartart", resource_id,
                properties={"xml_path": stable_xml_path(diagram)},
            )]

        images = container.xpath(".//a:blip | .//v:imagedata", namespaces=ns)
        # w:object 由上面的单一逻辑出现处理，不把预览图重复计数。
        images = [item for item in images
                  if not any(etree.QName(ancestor).localname == "object"
                             for ancestor in item.iterancestors())]
        return self._image_specs(images, part)
