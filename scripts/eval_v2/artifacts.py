"""候选输出包与金标准媒体包的只读访问。"""

from __future__ import annotations

import hashlib
import re
import struct
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote, urlsplit

from lxml import etree

from .models import IssueCollector
from .policy import LOCAL_MEDIA_ELEMENTS, XLINK_NS


XLINK_HREF = "{%s}href" % XLINK_NS
MAX_MEDIA_BYTES = 256 * 1024 * 1024
MAX_TOTAL_MEDIA_BYTES = 512 * 1024 * 1024
MAX_XML_BYTES = 256 * 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def local_name(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def local_path(element) -> str:
    """生成不依赖命名空间前缀的稳定可读路径。"""
    parts: List[str] = []
    current = element
    while current is not None and isinstance(current.tag, str):
        name = local_name(current.tag)
        parent = current.getparent()
        if parent is None:
            parts.append(name)
            break
        siblings = [child for child in parent if isinstance(child.tag, str) and local_name(child.tag) == name]
        if len(siblings) > 1:
            parts.append("%s[%d]" % (name, siblings.index(current) + 1))
        else:
            parts.append(name)
        current = parent
    return "/" + "/".join(reversed(parts))


def parse_xml(path: Path):
    # XMLParser 具有可变 error_log，不跨调用共享，保证并发调用相互独立。
    parser = etree.XMLParser(
        load_dtd=False,
        no_network=True,
        resolve_entities=False,
        remove_blank_text=False,
        huge_tree=False,
    )
    return etree.parse(str(path), parser)


@dataclass(frozen=True)
class MediaBlob:
    logical_name: str
    sha256: str
    size: int
    data: bytes = field(repr=False, compare=False)


class GoldMediaStore:
    """读取 figures.zip；同时支持完整成员名和唯一 basename 查找。"""

    def __init__(self, zip_path: Path):
        self.path = zip_path
        self.by_member: Dict[str, MediaBlob] = {}
        self.by_basename: Dict[str, List[MediaBlob]] = {}
        self.error: Optional[str] = None
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    data = archive.read(info)
                    name = info.filename.replace("\\", "/")
                    if name in self.by_member:
                        self.error = f"figures.zip 含重复成员名：{name}"
                    blob = MediaBlob(name, sha256_bytes(data), len(data), data)
                    self.by_member[name] = blob
                    self.by_basename.setdefault(PurePosixPath(name).name, []).append(blob)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            self.error = str(exc)

    def resolve(self, href: str) -> Optional[MediaBlob]:
        normalized = href.replace("\\", "/").lstrip("./")
        if normalized in self.by_member:
            return self.by_member[normalized]
        basename = PurePosixPath(normalized).name
        matches = self.by_basename.get(basename, [])
        return matches[0] if len(matches) == 1 else None

    @property
    def hashes(self) -> Set[str]:
        return {blob.sha256 for blob in self.by_member.values()}


class CandidatePackage:
    """候选输出目录。

    候选可以直接指向 XML 文件，也可以指向包含唯一顶层 XML 的目录。
    """

    def __init__(self, candidate: Path, issues: IssueCollector):
        original = candidate
        input_is_symlink = original.is_symlink()
        candidate = candidate.resolve()
        self.argument = candidate
        self.direct_file = candidate.is_file()
        self.root: Path
        self.xml_path: Optional[Path] = None
        self._referenced: Set[Path] = set()
        self._media_bytes_read = 0

        if candidate.is_file():
            self.root = candidate.parent
            if candidate.suffix.lower() == ".xml":
                self.xml_path = candidate
                if input_is_symlink:
                    issues.add(
                        "CANDIDATE_XML_SYMLINK", "package", "候选主 XML 不得是符号链接",
                        severity="error", actual=str(original),
                    )
            else:
                issues.add(
                    "CANDIDATE_NOT_XML", "package", "候选文件不是 XML",
                    severity="critical", actual=str(candidate),
                )
            return

        self.root = candidate
        if not candidate.exists():
            issues.add(
                "CANDIDATE_NOT_FOUND", "package", "候选路径不存在",
                severity="critical", actual=str(candidate),
            )
            return
        if not candidate.is_dir():
            issues.add(
                "CANDIDATE_NOT_DIRECTORY", "package", "候选路径既不是 XML 文件也不是目录",
                severity="critical", actual=str(candidate),
            )
            return

        xmls = sorted(path for path in candidate.glob("*.xml") if path.is_file())
        if len(xmls) == 1:
            self.xml_path = xmls[0]
            if xmls[0].is_symlink():
                issues.add(
                    "CANDIDATE_XML_SYMLINK", "package", "候选主 XML 不得是符号链接",
                    severity="error", actual=str(xmls[0]),
                )
        elif not xmls:
            issues.add(
                "CANDIDATE_XML_MISSING", "package", "候选目录顶层没有 XML 文件",
                severity="critical", candidate_path=str(candidate),
            )
        else:
            issues.add(
                "CANDIDATE_XML_AMBIGUOUS", "package", "候选目录顶层有多份 XML，无法确定主文件",
                severity="critical", candidate_path=str(candidate),
                actual=[path.name for path in xmls],
            )

    def resolve_media(self, href: str, issues: Optional[IssueCollector] = None, path: Optional[str] = None) -> Optional[Path]:
        """安全解析本地媒体 href；任何越界或 URL 都拒绝。"""
        if not href:
            if issues is not None:
                issues.add(
                    "MEDIA_HREF_EMPTY", "package", "媒体 href 为空",
                    candidate_path=path,
                )
            return None
        if "\\" in href:
            if issues is not None:
                issues.add(
                    "MEDIA_PATH_BACKSLASH", "package", "媒体 href 使用反斜杠，不是规范 URI 路径",
                    candidate_path=path, actual=href,
                )
            return None
        if any(character.isspace() or ord(character) < 32 for character in href) or re.search(
            r"%(?![0-9A-Fa-f]{2})", href
        ):
            if issues is not None:
                issues.add(
                    "MEDIA_PATH_INVALID_URI", "package", "媒体 href 含未转义空白、控制字符或错误百分号编码",
                    candidate_path=path, actual=href,
                )
            return None
        split = urlsplit(href)
        if split.scheme or split.netloc or split.query or split.fragment:
            if issues is not None:
                issues.add(
                    "MEDIA_PATH_NOT_LOCAL", "package", "媒体 href 必须是输出包内相对路径",
                    candidate_path=path, actual=href,
                )
            return None
        decoded = unquote(split.path)
        segments = decoded.split("/")
        if "\x00" in decoded or "\\" in decoded or any(
            segment in {"", ".", ".."} for segment in segments
        ):
            if issues is not None:
                issues.add(
                    "MEDIA_PATH_UNSAFE", "package", "媒体 href 含空段、点段、反斜杠或空字符",
                    candidate_path=path, actual=href,
                )
            return None
        pure = PurePosixPath(decoded)
        if pure.is_absolute():
            if issues is not None:
                issues.add(
                    "MEDIA_PATH_UNSAFE", "package", "媒体 href 含绝对路径、空段或越界段",
                    candidate_path=path, actual=href,
                )
            return None
        unresolved = self.root / Path(*pure.parts)
        current = self.root
        for part in pure.parts:
            current = current / part
            if current.is_symlink():
                if issues is not None:
                    issues.add(
                        "MEDIA_PATH_SYMLINK", "package", "媒体路径不得经过符号链接",
                        candidate_path=path, actual=href,
                    )
                return None
        target = unresolved.resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            if issues is not None:
                issues.add(
                    "MEDIA_PATH_ESCAPE", "package", "媒体 href 解析后越出候选目录",
                    candidate_path=path, actual=href,
                )
            return None
        self._referenced.add(target)
        return target

    def media_blob(self, href: str, issues: Optional[IssueCollector] = None, path: Optional[str] = None) -> Optional[MediaBlob]:
        target = self.resolve_media(href, issues, path)
        if target is None:
            return None
        if not target.exists() or not target.is_file():
            if issues is not None:
                issues.add(
                    "MEDIA_FILE_MISSING", "package", "XML 引用的媒体文件不存在",
                    candidate_path=path, actual=href,
                )
            return None
        try:
            size = target.stat().st_size
            if size > MAX_MEDIA_BYTES:
                if issues is not None:
                    issues.add(
                        "MEDIA_FILE_TOO_LARGE", "package", "单个媒体文件超过 256 MiB 安全上限",
                        candidate_path=path, actual=href, evidence={"size": size},
                    )
                return None
            if self._media_bytes_read + size > MAX_TOTAL_MEDIA_BYTES:
                if issues is not None:
                    issues.add(
                        "MEDIA_PACKAGE_TOO_LARGE", "package", "候选媒体总量超过 512 MiB 安全上限",
                        candidate_path=path, actual=href,
                        evidence={"accumulated": self._media_bytes_read, "next_size": size},
                    )
                return None
            data = target.read_bytes()
            self._media_bytes_read += size
        except OSError as exc:
            if issues is not None:
                issues.add(
                    "MEDIA_FILE_UNREADABLE", "package", "XML 引用的媒体文件无法读取",
                    candidate_path=path, actual=href, evidence={"error": str(exc)},
                )
            return None
        return MediaBlob(href, sha256_bytes(data), len(data), data)

    def extra_files(self) -> List[Path]:
        # 直接传 XML 文件表示调用者只声明这一文件及其显式引用为评测包，
        # 同目录的其他文件不属于本次候选；传目录时才检查目录闭包。
        if self.direct_file:
            return []
        if not self.root.exists() or not self.root.is_dir():
            return []
        allowed = set(self._referenced)
        if self.xml_path is not None:
            allowed.add(self.xml_path.resolve())
        return sorted(
            path for path in self.root.rglob("*")
            if (path.is_file() or path.is_symlink())
            and (path.is_symlink() or path.resolve() not in allowed)
        )


def media_elements(root) -> Iterable:
    for element in root.iter():
        if isinstance(element.tag, str) and local_name(element.tag) in LOCAL_MEDIA_ELEMENTS:
            if element.get(XLINK_HREF) is not None:
                yield element


def _valid_emf(data: bytes) -> bool:
    if len(data) < 88 or data[:4] != b"\x01\x00\x00\x00" or data[40:44] != b" EMF":
        return False
    header_size = struct.unpack_from("<I", data, 4)[0]
    declared_bytes = struct.unpack_from("<I", data, 48)[0]
    declared_records = struct.unpack_from("<I", data, 52)[0]
    if header_size < 88 or header_size > len(data) or declared_bytes != len(data):
        return False
    offset = 0
    records = 0
    last_type = None
    while offset < len(data):
        if offset + 8 > len(data):
            return False
        record_type, record_size = struct.unpack_from("<II", data, offset)
        if record_size < 8 or record_size % 4 or offset + record_size > len(data):
            return False
        if offset == 0 and record_size != header_size:
            return False
        last_type = record_type
        records += 1
        offset += record_size
    return offset == len(data) and records == declared_records and last_type == 14


def _valid_wmf(data: bytes) -> bool:
    placeable = data.startswith(b"\xd7\xcd\xc6\x9a")
    offset = 22 if placeable else 0
    if placeable:
        if len(data) < 40:
            return False
        words = struct.unpack_from("<10H", data, 0)
        checksum = struct.unpack_from("<H", data, 20)[0]
        calculated = 0
        for word in words:
            calculated ^= word
        if calculated != checksum:
            return False
    if len(data) < offset + 18:
        return False
    file_type, header_words, _version, file_words, _objects, max_record, _params = struct.unpack_from(
        "<HHHIHIH", data, offset
    )
    if file_type not in {1, 2} or header_words != 9:
        return False
    end = offset + file_words * 2
    if end != len(data):
        return False
    position = offset + 18
    largest = 0
    saw_eof = False
    while position < end:
        if position + 6 > end:
            return False
        record_words = struct.unpack_from("<I", data, position)[0]
        function = struct.unpack_from("<H", data, position + 4)[0]
        if record_words < 3 or position + record_words * 2 > end:
            return False
        largest = max(largest, record_words)
        position += record_words * 2
        if function == 0:
            saw_eof = record_words == 3 and position == end
            break
    return saw_eof and position == end and largest == max_record


def strict_media_decodable(blob: MediaBlob) -> Tuple[bool, str]:
    """严格验证常见位图；对 Pillow 不支持的 WMF/EMF 验证完整格式签名。

    魔数兜底只用于 WMF/EMF，不再把任意带 JPEG/PNG 开头的损坏文件当成真图。
    """
    data = blob.data
    has_wmf_header = len(data) >= 4 and (
        data.startswith(b"\xd7\xcd\xc6\x9a") or data.startswith(b"\x01\x00\x09\x00")
    )
    has_emf_header = (
        len(data) >= 44 and data[:4] == b"\x01\x00\x00\x00" and data[40:44] == b" EMF"
    )
    is_wmf = has_wmf_header and _valid_wmf(data)
    is_emf = has_emf_header and _valid_emf(data)
    if has_wmf_header and not is_wmf:
        return False, "WMF 容器记录或长度不完整"
    if has_emf_header and not is_emf:
        return False, "EMF 容器记录或长度不完整"
    try:
        from io import BytesIO
        from PIL import Image, ImageSequence

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(blob.data)) as image:
                detected = (image.format or "Pillow-unknown").upper()
                image.verify()
            if not (is_emf or is_wmf):
                # verify() 对部分格式只核对容器；重新打开并解码每一帧，才能
                # 发现截断的 JPEG/TIFF/GIF 像素流。
                with Image.open(BytesIO(blob.data)) as decoded:
                    for frame in ImageSequence.Iterator(decoded):
                        frame.load()
        # Pillow 的 WmfImagePlugin 会把 EMF 也统称为 WMF；用格式头细分。
        return True, "EMF" if is_emf else ("WMF" if is_wmf else detected)
    except Exception as pil_error:
        # Placeable WMF 或标准 WMF METAHEADER。
        if is_wmf:
            return True, "WMF"
        # EMF：ENHMETAHEADER iType=1，偏移 40 处签名为 ' EMF'。
        if is_emf:
            return True, "EMF"
        return False, str(pil_error)


def inspect_candidate_media(package: CandidatePackage, root, issues: IssueCollector) -> Dict[str, MediaBlob]:
    """检查所有媒体引用并返回 ``本地路径 -> blob``。"""
    blobs: Dict[str, MediaBlob] = {}
    for element in media_elements(root):
        href = element.get(XLINK_HREF) or ""
        path = local_path(element)
        blob = blobs.get(href)
        if blob is None:
            blob = package.media_blob(href, issues, path)
        if blob is None:
            continue
        blobs[href] = blob
        valid, detail = strict_media_decodable(blob)
        if not valid:
            issues.add(
                "MEDIA_DECODE_FAILED", "package", "媒体文件不能被严格解码",
                candidate_path=path, actual=href,
                evidence={"sha256": blob.sha256, "size": blob.size, "detail": detail},
            )
            continue
        expected_extensions = {
            "JPEG": {".jpg", ".jpeg"},
            "PNG": {".png"},
            "TIFF": {".tif", ".tiff"},
            "GIF": {".gif"},
            "BMP": {".bmp"},
            "WMF": {".wmf"},
            "EMF": {".emf"},
        }.get(detail)
        extension = PurePosixPath(href).suffix.lower()
        if expected_extensions is not None and extension not in expected_extensions:
            issues.add(
                "MEDIA_EXTENSION_MISMATCH", "package", "媒体扩展名与实际文件格式不一致",
                candidate_path=path, actual=href,
                expected=sorted(expected_extensions), evidence={"detected_format": detail},
            )
    return blobs


def check_extra_files(package: CandidatePackage, issues: IssueCollector) -> List[str]:
    extras = package.extra_files()
    for path in extras:
        relative = str(path.relative_to(package.root))
        if path.is_symlink():
            issues.add(
                "UNREFERENCED_OUTPUT_SYMLINK", "package", "候选目录含未引用的符号链接",
                candidate_path=relative, severity="error",
            )
        else:
            issues.add(
                "UNREFERENCED_OUTPUT_FILE", "package", "候选输出目录含未被 XML 引用的文件",
                candidate_path=relative, severity="error",
                evidence={"sha256": sha256_file(path), "size": path.stat().st_size},
            )
    return [str(path.relative_to(package.root)) for path in extras]
