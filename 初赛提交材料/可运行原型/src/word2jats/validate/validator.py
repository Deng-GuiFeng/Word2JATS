"""JATS 校验：基于本地 JATS 1.3 (MathML3) DTD 做结构合法性校验。

DTD 校验非命名空间感知，按字面元素名匹配（故 MathML 必须用 mml: 前缀）。
已实测：本地 DTD 可校验通过 10 例结构参考（见 docs/06-评测与成绩.md）。
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field

from lxml import etree

_DTD_REL = ("dtd/JATS-Publishing-1-3-MathML3-DTD/"
            "JATS-journalpublishing1-3-mathml3.dtd")
_DTD_PATH = os.path.join(os.path.dirname(__file__), "..", "resources", _DTD_REL)

# DTD 加载 + 校验的进程级锁与缓存。**线程安全的关键**：
# ① 老实现每次 new Validator 都 os.chdir 切目录加载 DTD——chdir 改的是进程全局 cwd，
#    Web 应用并发跑 convert() 时会互相踩：一个线程切走目录，别的线程的相对路径(开 docx、
#    加载 DTD 的 .ent/.mod 子模块)全失败 → 表格模块没加载 → 假报"table 未声明"DTD 不通过。
# ② 现代 libxml2(≥2.12)可直接吃含非 ASCII 的绝对路径，无需 chdir；只有老版才切目录兜底，
#    且此时在锁内、只发生一次（DTD 缓存复用），不再有 cwd 竞争。
# ③ etree.DTD 对象的 error_log 是共享可变状态，并发 validate() 会串错误日志——故 validate
#    也在锁内做（校验仅几十毫秒、是转换尾巴，串行化开销可忽略，换来结果正确）。
_LOCK = threading.Lock()
_DTD_CACHE: dict = {}


def _load_dtd(dtd_path: str):
    """加载 DTD（返回 (dtd, err)）。优先绝对路径直载；失败再切目录用相对名兜底。"""
    try:
        return etree.DTD(dtd_path), None
    except Exception:
        d, fn = os.path.split(dtd_path)
        cwd = os.getcwd()
        try:
            os.chdir(d)
            return etree.DTD(fn), None
        except Exception as e:  # noqa
            return None, str(e)
        finally:
            os.chdir(cwd)


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
        if os.path.exists(self.dtd_path):
            with _LOCK:  # 加载一次、缓存复用；切目录兜底也被序列化，不踩别的线程 cwd
                if self.dtd_path not in _DTD_CACHE:
                    _DTD_CACHE[self.dtd_path] = _load_dtd(self.dtd_path)
                self._dtd, _ = _DTD_CACHE[self.dtd_path]

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
        # 共享 DTD 的 validate()+error_log 非线程安全，加锁保原子
        with _LOCK:
            res.dtd_valid = self._dtd.validate(root)
            if not res.dtd_valid:
                res.errors = [e.message for e in self._dtd.error_log]
        return res
