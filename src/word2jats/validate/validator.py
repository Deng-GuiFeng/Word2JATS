"""JATS 校验：基于本地 JATS 1.3 (MathML3) DTD 做结构合法性校验。

DTD 校验非命名空间感知，按字面元素名匹配（故 MathML 必须用 mml: 前缀）。
已实测：本地 DTD 可校验通过 10 例结构参考（见 docs/06-评测与成绩.md）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from lxml import etree

_DTD_REL = ("dtd/JATS-Publishing-1-3-MathML3-DTD/"
            "JATS-journalpublishing1-3-mathml3.dtd")
_DTD_PATH = os.path.join(os.path.dirname(__file__), "..", "resources", _DTD_REL)


@dataclass
class ValidationResult:
    well_formed: bool = False
    dtd_valid: bool = False
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.well_formed and self.dtd_valid


class Validator:
    def __init__(self, dtd_path: str = None):
        self.dtd_path = os.path.abspath(dtd_path or _DTD_PATH)
        self._dtd = None
        self.load_error = None
        if os.path.exists(self.dtd_path):
            # 切到 DTD 所在目录、用**相对文件名**加载,而不是把绝对路径直接交给 libxml2:
            # 老版 libxml2(如 2.10.x)无法解析**含非 ASCII 字符的绝对路径**,而本项目目录
            # 含中文(如 .../学术期刊.../),会导致 DTD 静默加载失败、所有校验退化为"跳过"。
            # 用相对名后,DTD 内部 .ent/.mod 子模块也按 cwd 正确解析,新老 libxml2 均可。
            d, fn = os.path.split(self.dtd_path)
            cwd = os.getcwd()
            try:
                os.chdir(d)
                self._dtd = etree.DTD(fn)
            except Exception as e:  # noqa
                self.load_error = str(e)
                self._dtd = None
            finally:
                os.chdir(cwd)

    def validate_bytes(self, xml_bytes: bytes) -> ValidationResult:
        res = ValidationResult()
        # 去掉 DOCTYPE，避免 lxml 解析时去远程拉 DTD
        text = xml_bytes.decode("utf-8")
        text = re.sub(r"<!DOCTYPE.*?>", "", text, flags=re.S)
        try:
            root = etree.fromstring(text.encode("utf-8"))
            res.well_formed = True
        except etree.XMLSyntaxError as e:
            res.errors.append("XML 非良构: %s" % e)
            return res
        if self._dtd is None:
            res.errors.append("DTD 未加载，跳过 DTD 校验")
            return res
        res.dtd_valid = self._dtd.validate(root)
        if not res.dtd_valid:
            for e in self._dtd.error_log:  # type: ignore
                res.errors.append(e.message)
        return res
