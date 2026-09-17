"""JATS 校验：基于本地 JATS 1.3 (MathML3) DTD 做结构合法性校验。

DTD 校验非命名空间感知，按字面元素名匹配（故 MathML 必须用 mml: 前缀）。
校验范围与内容评价的区别见 docs/数据与评测.md。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

from lxml import etree

_DTD_REL = ("dtd/JATS-Publishing-1-3-MathML3-DTD/"
            "JATS-journalpublishing1-3-mathml3.dtd")
_DTD_PATH = os.path.join(os.path.dirname(__file__), "..", "resources", _DTD_REL)
_ROOT_TAG = "article"

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


def _root_tag_ok(root) -> bool:
    """带 DOCTYPE 的完整文档，根元素必须是 article。

    XML 1.0 §2.8 有效性约束 Root Element Type 要求 DOCTYPE 里的名字与根元素类型一致，
    但 DTD.validate() 走的 libxml2 xmlValidateDtd 会跳过这条比对——它把 intSubset 置空
    后才校验，而根名比对以 intSubset 非空为前提。于是根元素错配（例如渲染坍塌成只剩
    front）会被判成合法。这里补上。

    lxml 取不到 DOCTYPE 原文声明的名字：docinfo.root_name 返回的是实际根元素名，
    docinfo.doctype 也被按实际根元素名重写，比对会恒真。改用等价判据——本项目输出的
    DOCTYPE 是硬编码常量 <!DOCTYPE article ...>（见 build/jats.py），故根元素必须是
    article。

    不带 DOCTYPE 的输入是片段校验（如单独校验一个 <address>），跳过此项。
    """
    if not root.getroottree().docinfo.doctype:
        return True
    return root.tag == _ROOT_TAG


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
        # 直接吃 bytes、保留 DOCTYPE：lxml 默认 parser 本就 load_dtd=False、no_network=True，
        # 带 DOCTYPE 解析既不联网也不慢（实测 0.0009s）；而保留它才能分辨这是完整文档还是
        # 片段（见 _root_tag_ok）。旧写法用正则剥 DOCTYPE，遇到内部子集会截出 "]>" 残留，
        # 把合法文档误判成非良构。
        try:
            root = etree.fromstring(xml_bytes)
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
        if not _root_tag_ok(root):
            res.dtd_valid = False
            res.errors.append("根元素必须是 %s，实际是 %s（XML 1.0 §2.8 Root Element Type）"
                              % (_ROOT_TAG, root.tag))
        return res
