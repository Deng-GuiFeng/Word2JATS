"""阶段 P3：用 X02 与 01 验证“出现—表示组—组成组—资源”对象图。"""

from __future__ import annotations

import json
import posixpath
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from lxml import etree


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}
RID = f"{{{NS['r']}}}id"
REMBED = f"{{{NS['r']}}}embed"
RLINK = f"{{{NS['r']}}}link"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


@dataclass(frozen=True)
class Resource:
    resource_id: str
    package_uri: str
    content_type: str
    sha256: str
    size: int
    kind: str  # media | ole-payload


@dataclass
class Representation:
    role: str
    resource_id: str
    physical_path: str
    selected_for_gold_account: bool = False
    superseded_reason: str | None = None


@dataclass
class ObjectOccurrence:
    occurrence_id: str
    kind: str
    source_part: str
    node_path: str
    representation_group_id: str | None = None
    composition_id: str | None = None
    composition_index: int | None = None
    representations: list[Representation] = field(default_factory=list)


def lname(element) -> str:
    return etree.QName(element).localname


def stable_path(element) -> str:
    parts: list[str] = []
    current = element
    while current is not None and isinstance(current.tag, str):
        name = lname(current)
        parent = current.getparent()
        if parent is None:
            parts.append(name)
            break
        siblings = [c for c in parent if isinstance(c.tag, str) and lname(c) == name]
        parts.append(f"{name}[{siblings.index(current) + 1}]")
        current = parent
    return "/" + "/".join(reversed(parts))


def ancestor(element, namespace: str, local: str):
    tag = f"{{{namespace}}}{local}"
    return next((item for item in element.iterancestors() if item.tag == tag), None)


def content_types(archive: ZipFile) -> tuple[dict[str, str], dict[str, str]]:
    root = etree.fromstring(archive.read("[Content_Types].xml"))
    defaults = {
        e.get("Extension", "").lower(): e.get("ContentType", "")
        for e in root.findall(f"{{{CT_NS}}}Default")
    }
    overrides = {
        e.get("PartName", "").lstrip("/"): e.get("ContentType", "")
        for e in root.findall(f"{{{CT_NS}}}Override")
    }
    return defaults, overrides


def relationship_map(archive: ZipFile, source_part: str) -> dict[str, str]:
    source = PurePosixPath(source_part)
    rels_name = str(source.parent / "_rels" / (source.name + ".rels"))
    root = etree.fromstring(archive.read(rels_name))
    result = {}
    for rel in root.findall(f"{{{REL_NS}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = posixpath.normpath(posixpath.join(str(source.parent), rel.get("Target", "")))
        result[rel.get("Id", "")] = target
    return result


def binary_resources(archive: ZipFile) -> tuple[dict[str, Resource], dict[str, Resource]]:
    defaults, overrides = content_types(archive)
    by_uri = {}
    by_hash = {}
    for uri in sorted(archive.namelist()):
        if uri.endswith("/") or not (uri.startswith("word/media/") or uri.startswith("word/embeddings/")):
            continue
        blob = archive.read(uri)
        digest = sha256(blob).hexdigest()
        content_type = overrides.get(uri) or defaults.get(PurePosixPath(uri).suffix.lstrip(".").lower(), "")
        kind = "media" if uri.startswith("word/media/") else "ole-payload"
        resource = Resource(
            resource_id=f"res-{len(by_uri) + 1}", package_uri=uri,
            content_type=content_type, sha256=digest, size=len(blob), kind=kind,
        )
        by_uri[uri] = resource
        by_hash.setdefault(digest, resource)
    return by_uri, by_hash


def gold_hashes(zip_path: Path) -> dict[str, str]:
    with ZipFile(zip_path) as archive:
        return {
            sha256(archive.read(name)).hexdigest(): name
            for name in archive.namelist() if not name.endswith("/")
        }


def media_ref(element) -> str | None:
    if lname(element) == "blip":
        return element.get(REMBED) or element.get(RLINK)
    if lname(element) == "imagedata":
        return element.get(RID)
    return None


def build_graph(docx: Path, figures_zip: Path) -> dict:
    gold = gold_hashes(figures_zip)
    with ZipFile(docx) as archive:
        source_part = "word/document.xml"
        root = etree.fromstring(archive.read(source_part))
        rels = relationship_map(archive, source_part)
        resources, _ = binary_resources(archive)

        def res_for_rid(rid: str | None) -> Resource | None:
            return resources.get(rels.get(rid or "", ""))

        # 先建 AlternateContent 的 Choice/Fallback 配对。配对依据是同一
        # AlternateContent 内的内容哈希，不依赖历史文件名。
        alt_choice: dict[str, tuple[str, int, Resource]] = {}
        alt_fallback_by_hash: dict[int, dict[str, list[Resource]]] = {}
        alt_counter = 0
        for alternate in root.xpath(".//mc:AlternateContent", namespaces=NS):
            choices = alternate.xpath("./mc:Choice//a:blip | ./mc:Choice//v:imagedata", namespaces=NS)
            fallbacks = alternate.xpath("./mc:Fallback//a:blip | ./mc:Fallback//v:imagedata", namespaces=NS)
            if not choices and not fallbacks:
                continue
            alt_counter += 1
            fallback_map: dict[str, list[Resource]] = defaultdict(list)
            for ref in fallbacks:
                resource = res_for_rid(media_ref(ref))
                if resource:
                    fallback_map[resource.sha256].append(resource)
            alt_fallback_by_hash[alt_counter] = fallback_map
            for index, ref in enumerate(choices, 1):
                resource = res_for_rid(media_ref(ref))
                if resource:
                    alt_choice[stable_path(ref)] = (f"composition-{alt_counter}", index, resource)

        occurrences: list[ObjectOccurrence] = []
        handled_paths: set[str] = set()
        physical_media_refs: list[dict] = []
        for ref in root.xpath(".//a:blip | .//v:imagedata", namespaces=NS):
            resource = res_for_rid(media_ref(ref))
            physical_media_refs.append({
                "path": stable_path(ref),
                "rid": media_ref(ref),
                "resource_id": resource.resource_id if resource else None,
                "package_uri": resource.package_uri if resource else None,
            })

        # w:object 是一次逻辑对象出现；OLE 载荷与 VML 预览是它的两种表示。
        for obj in root.xpath(".//w:object", namespaces=NS):
            preview_ref = next(iter(obj.xpath(".//v:imagedata", namespaces=NS)), None)
            ole = next(iter(obj.xpath(".//o:OLEObject", namespaces=NS)), None)
            preview = res_for_rid(media_ref(preview_ref)) if preview_ref is not None else None
            payload = res_for_rid(ole.get(RID) if ole is not None else None)
            reps = []
            if payload:
                reps.append(Representation(
                    role="object", resource_id=payload.resource_id,
                    physical_path=stable_path(ole),
                    selected_for_gold_account=False,
                    superseded_reason="当前金标准只外部化可呈现表示，OLE 载荷保留供公式解码",
                ))
            if preview:
                selected = preview.sha256 in gold
                reps.append(Representation(
                    role="preview", resource_id=preview.resource_id,
                    physical_path=stable_path(preview_ref),
                    selected_for_gold_account=selected,
                    superseded_reason=None if selected else "对象已以结构化公式/文本交付，预览图被替代",
                ))
                handled_paths.add(stable_path(preview_ref))
            occurrences.append(ObjectOccurrence(
                occurrence_id=f"o{len(occurrences) + 1}", kind="ole",
                source_part=source_part, node_path=stable_path(obj),
                representation_group_id=f"representation-{len(occurrences) + 1}",
                representations=reps,
            ))

        # 其余媒体引用逐次建出现。Fallback 不另建逻辑出现，而是挂到
        # 同一 Choice 出现的表示组。同一资源在两处引用仍是两次出现。
        for ref in root.xpath(".//a:blip | .//v:imagedata", namespaces=NS):
            ref_path = stable_path(ref)
            if ref_path in handled_paths:
                continue
            if ancestor(ref, NS["mc"], "Fallback") is not None:
                continue
            resource = res_for_rid(media_ref(ref))
            if not resource:
                continue
            comp_id = comp_index = None
            reps = []
            rep_group = None
            choice_info = alt_choice.get(ref_path)
            if choice_info:
                comp_id, comp_index, _ = choice_info
                rep_group = f"representation-alt-{comp_id}-{comp_index}"
                reps.append(Representation(
                    role="choice", resource_id=resource.resource_id,
                    physical_path=stable_path(ref), selected_for_gold_account=resource.sha256 in gold,
                ))
                alt_number = int(comp_id.rsplit("-", 1)[1])
                fallbacks = alt_fallback_by_hash[alt_number].get(resource.sha256, [])
                if not fallbacks:
                    raise AssertionError(f"Choice 无同字节 Fallback：{stable_path(ref)}")
                fallback = fallbacks.pop(0)
                reps.append(Representation(
                    role="fallback", resource_id=fallback.resource_id,
                    physical_path=f"{stable_path(ancestor(ref, NS['mc'], 'AlternateContent'))}/Fallback",
                    selected_for_gold_account=False,
                    superseded_reason="AlternateContent 的 Choice 可用，Fallback 仅作替代表示",
                ))
            else:
                reps.append(Representation(
                    role="image", resource_id=resource.resource_id,
                    physical_path=stable_path(ref), selected_for_gold_account=resource.sha256 in gold,
                    superseded_reason=None if resource.sha256 in gold else "媒体出现未进入目标 JATS 子集",
                ))
            occurrences.append(ObjectOccurrence(
                occurrence_id=f"o{len(occurrences) + 1}", kind="image",
                source_part=source_part, node_path=stable_path(ref),
                representation_group_id=rep_group,
                composition_id=comp_id, composition_index=comp_index,
                representations=reps,
            ))

        selected_hashes = {
            resource.sha256
            for occurrence in occurrences for rep in occurrence.representations
            for resource in resources.values()
            if rep.resource_id == resource.resource_id and rep.selected_for_gold_account
            and resource.kind == "media"
        }
        media_resources = [r for r in resources.values() if r.kind == "media"]
        ole_resources = [r for r in resources.values() if r.kind == "ole-payload"]
        ref_counts = Counter(item["resource_id"] for item in physical_media_refs)
        reused = {rid: count for rid, count in sorted(ref_counts.items()) if count > 1}
        compositions = defaultdict(list)
        for occurrence in occurrences:
            if occurrence.composition_id:
                compositions[occurrence.composition_id].append(occurrence.occurrence_id)

        return {
            "source_part": source_part,
            "relationships_part": "word/_rels/document.xml.rels",
            "counts": {
                "logical_occurrences": len(occurrences),
                "physical_media_references": len(physical_media_refs),
                "distinct_media_parts": len(media_resources),
                "distinct_media_hashes": len({r.sha256 for r in media_resources}),
                "ole_payload_resources": len(ole_resources),
                "all_binary_resources": len(resources),
                "gold_files": len(gold),
                "gold_hashes_matched": len(set(gold) & {r.sha256 for r in media_resources}),
                "selected_media_hashes": len(selected_hashes),
                "composition_groups": len(compositions),
            },
            "reused_media_resource_references": reused,
            "composition_groups": dict(compositions),
            "resources": [asdict(r) for r in resources.values()],
            "occurrences": [asdict(o) for o in occurrences],
            "physical_media_references": physical_media_refs,
            "gold_unmatched_hashes": sorted(set(gold) - {r.sha256 for r in media_resources}),
            "selected_not_in_gold": sorted(selected_hashes - set(gold)),
        }


def main() -> int:
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    results = {}
    for key in ("X02", "01"):
        sample = root / "样例数据" / key
        results[key] = build_graph(sample / "初始文件.docx", sample / "figures.zip")

    x02 = results["X02"]["counts"]
    s01 = results["01"]["counts"]
    checks = {
        "X02_41_logical_occurrences": x02["logical_occurrences"] == 41,
        "X02_41_physical_media_references": x02["physical_media_references"] == 41,
        "X02_39_media_parts": x02["distinct_media_parts"] == 39,
        "X02_26_ole_payloads": x02["ole_payload_resources"] == 26,
        "X02_gold_31_all_matched": x02["gold_files"] == x02["gold_hashes_matched"] == 31,
        "X02_reuse_not_deduplicated": sorted(results["X02"]["reused_media_resource_references"].values()) == [2, 2],
        "01_five_member_composition": sorted(len(v) for v in results["01"]["composition_groups"].values()) == [5],
        "01_gold_five_all_matched": s01["gold_files"] == s01["gold_hashes_matched"] == 5,
        "01_choice_fallback_five_groups": sum(
            bool(o["representation_group_id"]) and len(o["representations"]) == 2
            for o in results["01"]["occurrences"]
        ) == 5,
        "no_gold_hash_unmatched": all(not data["gold_unmatched_hashes"] for data in results.values()),
        "selection_account_exact": all(not data["selected_not_in_gold"] for data in results.values()),
    }
    payload = {"checks": checks, "samples": results}
    (here / "p3_object_graph.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# P3 对象图原型",
        "",
        "## 结论",
        "",
        "原型直接读取 OOXML 部件和该部件自己的 `.rels`，没有把 `rId` 当成跨部件全局键。"
        "OLE 载荷与预览图是同一逻辑对象的替代表示；01 的五联图是组成关系，五个成员一个不少。",
        "",
        "## 数量对账",
        "",
        "| 样例 | 逻辑出现 | 物理媒体引用 | 媒体部件 | 媒体字节身份 | OLE载荷 | 金标准文件/字节命中 | 组成组 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("X02", "01"):
        c = results[key]["counts"]
        lines.append(
            f"| {key} | {c['logical_occurrences']} | {c['physical_media_references']} | "
            f"{c['distinct_media_parts']} | {c['distinct_media_hashes']} | {c['ole_payload_resources']} | "
            f"{c['gold_files']}/{c['gold_hashes_matched']} | {c['composition_groups']} |"
        )
    lines += [
        "",
        "X02 中两个媒体资源各被引用两次。因此是 41 次出现、39 个媒体部件；"
        "原型保留了四次出现，没有按资源去重成两次。",
        "",
        "## 门禁",
        "",
    ]
    for name, passed in checks.items():
        lines.append(f"- {'通过' if passed else '**失败**'}：`{name}`")
    lines += [
        "",
        "细到每个出现、表示和资源的路径、哈希、选择/弃置理由见 `p3_object_graph.json`。",
        "",
    ]
    (here / "P3-对象图原型.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({key: value["counts"] for key, value in results.items()}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
