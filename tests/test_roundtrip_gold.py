"""JATS 金标准 -> SemanticDoc v2 -> JATS 的严格往返测试。

唯一豁免：XML 缩进空白和属性排列次序。内部 ID 按全局双射比较；
媒体 href 按文件字节的 SHA-256 身份比较。可见文字、混合内容次序、
标签、属性值以及 ID/rid 关系均不豁免。
"""

from collections import Counter
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
import pytest

from tests.goldload import load_gold
from word2jats.render.v2 import render_v2
from word2jats.validate.validator import Validator


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "样例数据"
XLINK = "http://www.w3.org/1999/xlink"
HREF = f"{{{XLINK}}}href"


def _sample_keys():
    return [path.parent.name for path in sorted(SAMPLES.glob("*/结构参考.xml"))]


def _zip_media(path: Path) -> dict[str, bytes]:
    result = {}
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            blob = archive.read(name)
            result[name] = blob
    return result


def _normal_form(xml_bytes: bytes, media: dict[str, bytes]):
    root = etree.fromstring(xml_bytes)
    identities = [element.get("id") for element in root.iter() if element.get("id")]
    assert len(identities) == len(set(identities)), "XML ID 必须全局唯一"
    id_map = {value: f"id-{index}" for index, value in enumerate(identities, 1)}

    def normalize(element):
        attributes = []
        for name, value in element.attrib.items():
            if name == "id":
                value = id_map[value]
            elif name == "rid":
                targets = value.split()
                assert all(target in id_map for target in targets), (
                    f"存在悬空 rid: {value}"
                )
                value = " ".join(id_map[target] for target in targets)
            elif name == HREF:
                blob = media.get(value)
                if blob is None:
                    matches = [item for path, item in media.items()
                               if Path(path).name == Path(value).name]
                    blob = matches[0] if len(matches) == 1 else None
                if blob is not None:
                    value = "media:" + sha256(blob).hexdigest()
            attributes.append((name, value))
        text = element.text if element.text and element.text.strip() else None
        children = tuple(
            (
                normalize(child),
                child.tail if child.tail and child.tail.strip() else None,
            )
            for child in element
        )
        return element.tag, tuple(sorted(attributes)), text, children

    return normalize(root)


def _assert_visible_text_has_provenance(xml_bytes: bytes, entries) -> None:
    root = etree.fromstring(xml_bytes)
    tree = root.getroottree()
    grouped = {}
    for item in entries:
        if item.target_kind in {"text", "tail"}:
            grouped.setdefault((item.output_path, item.target_kind), []).append(item)
    for element in root.iter():
        path = tree.getpath(element)
        for slot, value in (("text", element.text), ("tail", element.tail)):
            if not value:
                continue
            covered = [False] * len(value)
            for item in grouped.get((path, slot), ()):
                assert item.start is not None and item.end is not None
                assert value[item.start:item.end] == item.value
                for index in range(item.start, item.end):
                    covered[index] = True
            assert all(covered), f"输出文字没有来源映射: {path}/{slot}={value!r}"


@pytest.mark.parametrize("key", _sample_keys())
def test_gold_semantic_roundtrip(key):
    directory = SAMPLES / key
    gold_media = _zip_media(directory / "figures.zip")
    document = load_gold(directory / "结构参考.xml", directory / "figures.zip")
    rendered = render_v2(document)

    assert _normal_form(
        (directory / "结构参考.xml").read_bytes(), gold_media
    ) == _normal_form(rendered.xml_bytes, rendered.media)
    assert Counter(sha256(blob).hexdigest() for blob in rendered.media.values()) == Counter(
        sha256(blob).hexdigest() for blob in gold_media.values()
    )

    # 来源映射是可序列化的最终 XML 路径，不得留 Python 对象地址。
    assert rendered.provenance
    assert all(item.output_path.startswith("/article") for item in rendered.provenance)
    _assert_visible_text_has_provenance(rendered.xml_bytes, rendered.provenance)
    validation = Validator().validate_bytes(rendered.xml_bytes)
    assert validation.ok, validation.errors
