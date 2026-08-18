"""候选包媒体的引用、字节与真实格式校验。"""

from __future__ import annotations

import hashlib
import io
import struct
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from lxml import etree
from PIL import Image

from ..build.figures import media_format
from ..build.jats import XLINK


@dataclass
class MediaReport:
    issues: list[dict] = field(default_factory=list)
    references: int = 0
    files: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, code: str, detail: str, **data) -> None:
        self.issues.append({"code": code, "detail": detail, **data})

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "references": self.references,
            "files": self.files,
            "issues": self.issues,
        }


_EXTENSIONS = {
    "jpeg": {".jpg", ".jpeg"},
    "png": {".png"},
    "tiff": {".tif", ".tiff"},
    "gif": {".gif"},
    "bmp": {".bmp"},
    "wmf": {".wmf"},
    "emf": {".emf"},
    "svg": {".svg"},
}
_RASTER = {"jpeg", "png", "tiff", "gif", "bmp"}


def _valid_wmf(blob: bytes) -> bool:
    offset = 22 if blob[:4] == b"\xd7\xcd\xc6\x9a" else 0
    if len(blob) < offset + 18:
        return False
    try:
        file_type, header_words, version, file_words, _objects, max_record, params = \
            struct.unpack_from("<HHHIHIH", blob, offset)
    except struct.error:
        return False
    declared_end = offset + file_words * 2
    return (
        file_type in {1, 2} and header_words == 9
        and version in {0x0100, 0x0300}
        and file_words >= 9 and declared_end <= len(blob)
        and max_record >= 3 and params == 0
    )


def _valid_emf(blob: bytes) -> bool:
    if len(blob) < 88 or blob[:4] != b"\x01\x00\x00\x00" or blob[40:44] != b" EMF":
        return False
    try:
        header_size, byte_count, record_count = struct.unpack_from("<III", blob, 4)[0], \
            struct.unpack_from("<I", blob, 48)[0], struct.unpack_from("<I", blob, 52)[0]
    except struct.error:
        return False
    return 88 <= header_size <= len(blob) and byte_count == len(blob) and record_count >= 1


def _valid_svg(blob: bytes) -> bool:
    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
        root = etree.fromstring(blob, parser)
    except (ValueError, etree.XMLSyntaxError):
        return False
    return etree.QName(root).localname.lower() == "svg"


def validate_blob(blob: bytes, fmt: str) -> bool:
    """按格式使用对应验证器，不用通用图库冒充矢量图校验。"""
    if fmt in _RASTER:
        try:
            with Image.open(io.BytesIO(blob)) as image:
                image.verify()
            return True
        except Exception:
            return False
    if fmt == "wmf":
        return _valid_wmf(blob)
    if fmt == "emf":
        return _valid_emf(blob)
    if fmt == "svg":
        return _valid_svg(blob)
    return False


def _safe_media_path(package: Path, href: str) -> Path | None:
    if not href or "\\" in href:
        return None
    split = urlsplit(href)
    if split.scheme or split.netloc or split.query or split.fragment:
        return None
    decoded = unquote(split.path)
    pure = PurePosixPath(decoded)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None
    target = package.joinpath(*pure.parts).resolve()
    try:
        target.relative_to(package.resolve())
    except ValueError:
        return None
    return target


def verify_package(xml_bytes: bytes, package: Path,
                   expected_hashes: dict[str, str]) -> MediaReport:
    """校验 XML 所引媒体与候选包，并与渲染时登记的 docx 源字节对账。"""
    report = MediaReport()
    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
        root = etree.fromstring(xml_bytes, parser)
    except etree.XMLSyntaxError as exc:
        report.add("media_xml_unreadable", "无法从非良构 XML 核对媒体", error=str(exc))
        return report

    hrefs = []
    for element in root.iter():
        if etree.QName(element).localname in {"graphic", "inline-graphic", "media"}:
            href = element.get("{%s}href" % XLINK)
            if href:
                hrefs.append(href)
    report.references = len(hrefs)
    referenced: set[Path] = set()
    expected = {PurePosixPath(key).as_posix(): value for key, value in expected_hashes.items()}

    for href in hrefs:
        path = _safe_media_path(package, href)
        if path is None:
            report.add("media_path_unsafe", "媒体引用不是候选包内安全相对路径", href=href)
            continue
        referenced.add(path)
        if not path.is_file():
            report.add("media_missing", "XML 引用的媒体文件不存在", href=href)
            continue
        blob = path.read_bytes()
        fmt = media_format(blob)
        if fmt is None:
            report.add("media_unknown_format", "媒体字节格式无法识别", href=href)
            continue
        if path.suffix.lower() not in _EXTENSIONS[fmt]:
            report.add(
                "media_extension_mismatch", "扩展名与字节真实格式不一致",
                href=href, extension=path.suffix.lower(), detected=fmt,
            )
        if not validate_blob(blob, fmt):
            report.add("media_invalid_structure", "媒体未通过对应格式的结构校验", href=href, detected=fmt)
        source_hash = expected.get(PurePosixPath(href).as_posix())
        actual_hash = hashlib.sha256(blob).hexdigest()
        if source_hash is None:
            report.add("media_source_unregistered", "媒体没有渲染时的 docx 源字节记录", href=href)
        elif source_hash != actual_hash:
            report.add("media_bytes_changed", "媒体字节与 docx 源字节不同", href=href)

    xml_files = {path.resolve() for path in package.glob("*.xml") if path.is_file()}
    actual_files = {
        path.resolve() for path in package.rglob("*")
        if path.is_file() and path.resolve() not in xml_files
    }
    report.files = len(actual_files)
    for path in sorted(actual_files - referenced):
        report.add(
            "media_unreferenced", "候选包含未被 XML 引用的文件",
            file=path.relative_to(package.resolve()).as_posix(),
        )
    for href in sorted(set(expected) - set(hrefs)):
        report.add("media_export_unreferenced", "已外部化的源媒体没有 XML 引用", href=href)
    return report
