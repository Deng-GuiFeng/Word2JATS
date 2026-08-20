"""输出包与 JATS 合法性检查。"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict

from lxml import etree

from .artifacts import CandidatePackage, local_name, local_path
from .models import IssueCollector
from .policy import JATS_PUBLIC_ID, JATS_SYSTEM_ID, MATHML_NS, XLINK_NS
from .samples import ROOT


DTD_PATH = (
    ROOT / "src" / "word2jats" / "resources" / "dtd"
    / "JATS-Publishing-1-3-MathML3-DTD" / "JATS-journalpublishing1-3-mathml3.dtd"
)
ROOT_TAG = "article"
XLINK_HREF = "{%s}href" % XLINK_NS
ORCID_RE = re.compile(r"^https://orcid\.org/(\d{4})-(\d{4})-(\d{4})-(\d{3}[\dX])$")
XML_DECL_RE = re.compile(br"^\s*<\?xml\s+[^?]*encoding\s*=\s*(['\"])([^'\"]+)\1", re.I)

_DTD = None
_DTD_LOCK = threading.RLock()


def dtd():
    global _DTD
    with _DTD_LOCK:
        if _DTD is None:
            try:
                _DTD = etree.DTD(str(DTD_PATH))
            except etree.DTDParseError:
                # 部分 libxml2 版本不能从含非 ASCII 字符的普通绝对路径继续
                # 解析 .ent/.mod；百分号编码的 file URI 不改变进程工作目录。
                _DTD = etree.DTD(DTD_PATH.as_uri())
        return _DTD


def _orcid_checksum_valid(url: str) -> bool:
    match = ORCID_RE.match(url)
    if not match:
        return False
    digits = "".join(match.groups())
    total = 0
    for char in digits[:-1]:
        total = (total + int(char)) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    expected = "X" if result == 10 else str(result)
    return digits[-1] == expected


def _has_internal_doctype_subset(raw: bytes) -> bool:
    """只扫描 XML 序言中的真实 DOCTYPE，跳过注释和处理指令。"""

    index = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
    length = len(raw)
    while index < length:
        if raw[index:index + 1] in b" \t\r\n":
            index += 1
            continue
        if raw.startswith(b"<!--", index):
            end = raw.find(b"-->", index + 4)
            if end < 0:
                return False
            index = end + 3
            continue
        if raw.startswith(b"<?", index):
            end = raw.find(b"?>", index + 2)
            if end < 0:
                return False
            index = end + 2
            continue
        if raw.startswith(b"<!DOCTYPE", index):
            quote: int | None = None
            cursor = index + len(b"<!DOCTYPE")
            while cursor < length:
                char = raw[cursor]
                if quote is not None:
                    if char == quote:
                        quote = None
                elif char in {ord("'"), ord('"')}:
                    quote = char
                elif char == ord("["):
                    return True
                elif char == ord(">"):
                    return False
                cursor += 1
            return False
        if raw.startswith(b"<", index):
            return False
        index += 1
    return False


def validate_xml(package: CandidatePackage, tree, issues: IssueCollector) -> Dict[str, object]:
    """对已经安全解析的候选树执行完整合法性检查。"""
    if package.xml_path is None:
        return {"wellformed": False, "dtd_ok": False, "doctype_ok": False}

    root = tree.getroot()
    info = tree.docinfo
    result: Dict[str, object] = {
        "wellformed": True,
        "dtd_ok": False,
        "doctype_ok": False,
        "encoding": info.encoding,
    }

    public_ok = info.public_id == JATS_PUBLIC_ID
    system_ok = info.system_url == JATS_SYSTEM_ID
    result["doctype_ok"] = public_ok and system_ok
    if not public_ok:
        issues.add(
            "DOCTYPE_PUBLIC_INVALID", "validity", "DOCTYPE public id 不符合金标准契约",
            severity="critical", expected=JATS_PUBLIC_ID, actual=info.public_id,
        )
    if not system_ok:
        issues.add(
            "DOCTYPE_SYSTEM_INVALID", "validity", "DOCTYPE system id 不符合金标准契约",
            severity="critical", expected=JATS_SYSTEM_ID, actual=info.system_url,
        )

    raw_document = package.xml_path.read_bytes()
    raw = raw_document[:512]
    declaration = XML_DECL_RE.search(raw)
    if not declaration:
        issues.add(
            "XML_DECLARATION_MISSING", "validity", "XML 缺少带编码的 XML 声明",
            severity="error",
        )
    else:
        encoding = declaration.group(2).decode("ascii", "replace")
        if encoding.upper().replace("-", "") != "UTF8":
            issues.add(
                "XML_ENCODING_INVALID", "validity", "XML 声明编码必须是 UTF-8",
                severity="error", expected="UTF-8", actual=encoding,
            )

    if _has_internal_doctype_subset(raw_document):
        issues.add(
            "DOCTYPE_INTERNAL_SUBSET_FORBIDDEN", "validity",
            "候选 XML 不得声明内部 DTD 子集或自定义实体",
            severity="critical",
        )

    nsmap = root.nsmap or {}
    if nsmap.get("xlink") != XLINK_NS:
        issues.add(
            "XLINK_NAMESPACE_INVALID", "validity", "xlink 前缀必须绑定标准 URI",
            severity="error", expected=XLINK_NS, actual=nsmap.get("xlink"),
        )
    math_prefixes = [prefix for prefix, uri in nsmap.items() if uri == MATHML_NS]
    has_math = any(etree.QName(element).namespace == MATHML_NS for element in root.iter() if isinstance(element.tag, str))
    if has_math and not math_prefixes:
        issues.add(
            "MATHML_NAMESPACE_MISSING", "validity", "文档含 MathML，但根元素没有声明 MathML 命名空间",
            severity="error",
        )

    # DTD.error_log 是共享可变状态；validate 与读取错误必须在同一把锁内。
    try:
        with _DTD_LOCK:
            validator = dtd()
            result["dtd_ok"] = bool(validator.validate(tree))
            dtd_errors = list(validator.error_log)
    except (OSError, etree.DTDParseError, etree.DTDValidateError) as exc:
        issues.add(
            "DTD_VALIDATOR_UNAVAILABLE", "infrastructure", "本地 JATS DTD 无法加载或执行",
            severity="critical", actual=str(DTD_PATH), evidence={"error": str(exc)},
        )
        dtd_errors = []
    if not result["dtd_ok"]:
        for error in dtd_errors:
            issues.add(
                "DTD_INVALID", "validity", error.message,
                severity="critical", candidate_path="line %s" % error.line,
                evidence={"column": error.column, "level": error.level_name},
            )
    # 根元素名核对。DTD.validate() 走的 libxml2 xmlValidateDtd 会跳过 XML 1.0 §2.8 的
    # Root Element Type 比对（它把 intSubset 置空后才校验，而根名比对以 intSubset 非空
    # 为前提），根元素错配会被判成合法。上面的 public_ok/system_ok 也挡不住——根元素是
    # sec 时 public id 照样匹配。lxml 取不到 DOCTYPE 原文声明的名字（docinfo.root_name
    # 返回的是实际根元素名），故用等价判据：DOCTYPE 恒为 <!DOCTYPE article ...>。
    if info.doctype and root.tag != ROOT_TAG:
        result["dtd_ok"] = False
        issues.add(
            "ROOT_ELEMENT_INVALID", "validity",
            "根元素必须是 %s（XML 1.0 §2.8 Root Element Type）" % ROOT_TAG,
            severity="critical", expected=ROOT_TAG, actual=root.tag,
        )

    ids: Dict[str, object] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        value = element.get("id")
        if value:
            if value in ids:
                issues.add(
                    "ID_DUPLICATE", "validity", "同一 id 在文档中出现多次",
                    severity="critical", candidate_path=local_path(element), actual=value,
                    evidence={"first": local_path(ids[value])},
                )
            else:
                ids[value] = element

    for element in root.iter("{*}xref"):
        rid = (element.get("rid") or "").strip()
        if not rid:
            issues.add(
                "XREF_RID_MISSING", "validity", "xref 缺少 rid，无法形成有效关系",
                severity="error", candidate_path=local_path(element),
            )
            continue
        for token in rid.split():
            if token not in ids:
                issues.add(
                    "XREF_TARGET_MISSING", "validity", "xref 指向不存在的 id",
                    severity="critical", candidate_path=local_path(element), actual=token,
                )

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        name = local_name(element.tag)
        if name in ("inline-formula", "disp-formula"):
            representation = any(
                isinstance(child.tag, str)
                and (
                    etree.QName(child).namespace == MATHML_NS
                    or local_name(child.tag) in {"tex-math", "graphic", "inline-graphic"}
                )
                for child in element.iter()
            )
            if not representation:
                issues.add(
                    "FORMULA_REPRESENTATION_MISSING", "validity",
                    "公式缺少 MathML、TeX 或原稿图像表达",
                    severity="error", candidate_path=local_path(element),
                )

        if name in ("fig", "table-wrap", "disp-formula") and not element.get("id"):
            issues.add(
                "DISPLAY_ID_MISSING", "validity", "%s 缺少 id" % name,
                severity="error", candidate_path=local_path(element),
            )
        if name in ("graphic", "inline-graphic", "media") and not element.get(XLINK_HREF):
            issues.add(
                "MEDIA_HREF_MISSING", "validity", "%s 缺少 xlink:href" % name,
                severity="error", candidate_path=local_path(element),
            )

    for element in root.iter("{*}contrib-id"):
        if element.get("contrib-id-type") != "orcid":
            continue
        value = "".join(element.itertext()).strip()
        if not ORCID_RE.match(value):
            issues.add(
                "ORCID_FORMAT_INVALID", "validity", "ORCID 必须使用完整 https://orcid.org/ URL",
                severity="error", candidate_path=local_path(element), actual=value,
            )
        elif not _orcid_checksum_valid(value):
            issues.add(
                "ORCID_CHECKSUM_INVALID", "validity", "ORCID 校验位不正确",
                severity="error", candidate_path=local_path(element), actual=value,
            )

    for element in root.iter("{*}license"):
        href = element.get(XLINK_HREF)
        if not href and element.find(".//{*}ext-link") is None:
            issues.add(
                "LICENSE_LINK_MISSING", "validity", "license 缺少许可链接",
                severity="error", candidate_path=local_path(element),
            )

    return result
