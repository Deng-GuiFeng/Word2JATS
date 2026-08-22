"""JATS DTD 校验与结构化报错。

**判定与提示分离**是本模块的基本设计：

- *判定* 一律由 libxml2 的校验解析给出。它是 XML 1.0 有效性约束的权威实现,
  覆盖根元素名、元素内容模型、属性类型/枚举/必需/固定值、ID 唯一性与 IDREF
  解析。自己重写判定必然造出比它更松或更严的口径,还要独自趟属性值规范化、
  XML 空白严格判定、实体穿透这些坑。
- *提示* 由本模块补齐。libxml2 的消息有两处不够用:期望模型在约 5000 字节处
  被截断,且从不指出"第几个子元素开始不匹配"。这两样都能从 DTD 的内容模型树
  重建出来。**提示层即使算错也只影响措辞,不影响判定。**

为什么用校验解析而不是 ``etree.DTD().validate()``:后者不核对根元素名(它把树
放进一个没有 DOCTYPE 的临时文档再校验),且文档若未经 DTD 解析,属性值不会被
规范化,会把 ``k=" a "`` 这类合法写法误报成枚举越界。
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

from lxml import etree

_DTD_REL = ("dtd/JATS-Publishing-1-3-MathML3-DTD/"
            "JATS-journalpublishing1-3-mathml3.dtd")
DTD_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "resources", _DTD_REL))

JATS_PUBLIC_ID = "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.3 20210610//EN"
JATS_SYSTEM_ID = "https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd"
ROOT_TAG = "article"

# 这份 DTD 由 64 个文件拼成。缺文件时 etree.DTD() 只给 WARNING 不抛异常,残缺的
# DTD 会把正常文档判成满屏"No declaration for element X"。加载后立刻核对规模,
# 数字对不上就直接失败,不让残缺 DTD 进入判定。
_EXPECTED_ELEMENTS = 498

_LOCK = threading.Lock()
_DECL_CACHE: dict = {}


class DtdUnavailable(RuntimeError):
    """DTD 无法加载或加载得不完整。"""


# --------------------------------------------------------------------------
# DTD 声明表:内容模型必须先编译成纯 Python 树
# --------------------------------------------------------------------------

class _Node:
    """内容模型节点。

    lxml 的内容模型节点是临时代理对象——``d.content is d.content`` 为 False,
    且 ``id()`` 会被复用。拿它做字典键或记忆化键会让不同节点的结果串味,产生
    静默错判。所以一次性编译成本模块自己的节点。
    """

    __slots__ = ("type", "occur", "name", "left", "right")

    def __init__(self, type, occur, name, left, right):
        self.type, self.occur, self.name = type, occur, name
        self.left, self.right = left, right


def _compile(node):
    if node is None:
        return None
    return _Node(node.type, node.occur, node.name,
                 _compile(node.left), _compile(node.right))


def _qname(prefix, name):
    return "%s:%s" % (prefix, name) if prefix else name


@dataclass
class _Attr:
    qname: str
    type: str                 # cdata / id / idref(s) / nmtoken(s) / enumeration / notation
    default: str              # none(字面默认值) / required / implied / fixed
    default_value: Optional[str]
    values: tuple = ()        # 枚举/记号的允许值


@dataclass
class _Decl:
    qname: str
    type: str                 # empty / any / mixed / element
    content: Optional[_Node]
    attrs: dict = field(default_factory=dict)


def _build_decls(dtd) -> dict:
    """{qname: _Decl}。

    键必须是 qname 而不是局部名:这份 DTD 里 sec/fn/list/product/annotation
    五个名字同时存在无前缀版与 mml: 版,按局部名建索引会让 mml:sec(EMPTY)
    覆盖 JATS 的 sec(element-only),后果是所有 sec 的子元素被判非法。
    """
    out = {}
    for el in dtd.iterelements():
        q = _qname(el.prefix, el.name)
        attrs = {}
        for at in el.iterattributes():
            aq = _qname(at.prefix, at.name)
            attrs[aq] = _Attr(aq, at.type, at.default, at.default_value,
                              tuple(at.itervalues() or ()))
        out[q] = _Decl(q, el.type, _compile(el.content), attrs)
    return out


def _load_decls():
    with _LOCK:
        if DTD_PATH not in _DECL_CACHE:
            try:
                dtd = etree.DTD(DTD_PATH)
            except etree.DTDParseError as exc:
                raise DtdUnavailable("DTD 解析失败: %s" % exc) from exc
            decls = _build_decls(dtd)
            if len(decls) != _EXPECTED_ELEMENTS:
                raise DtdUnavailable(
                    "DTD 不完整: 元素声明 %d 条,应为 %d 条。"
                    "多半是某个 .ent 模块缺失——etree.DTD() 对此只给 WARNING。"
                    % (len(decls), _EXPECTED_ELEMENTS))
            _DECL_CACHE[DTD_PATH] = decls
        return _DECL_CACHE[DTD_PATH]


class _LocalResolver(etree.Resolver):
    """把文档声明的 JATS 公共/系统标识符映射到随包内置的 DTD,其余一概不解析。"""

    def resolve(self, system_url, public_id, context):
        if public_id == JATS_PUBLIC_ID or system_url == JATS_SYSTEM_ID:
            return self.resolve_filename(DTD_PATH, context)
        return None


# --------------------------------------------------------------------------
# 内容模型:串行化与失配定位（仅用于提示）
# --------------------------------------------------------------------------

_OCCUR_SUFFIX = {"once": "", "opt": "?", "mult": "*", "plus": "+"}


def render_model(node) -> str:
    """把内容模型树还原成 DTD 写法。libxml2 的消息会截断,这里不会。"""
    if node is None:
        return ""
    if node.type == "pcdata":
        body = "#PCDATA"
    elif node.type == "element":
        body = node.name or "?"
    elif node.type in ("seq", "or"):
        sep = ", " if node.type == "seq" else " | "
        parts = []
        _flatten(node, node.type, parts)
        body = "(" + sep.join(render_model(p) for p in parts) + ")"
    else:
        body = "?"
    return body + _OCCUR_SUFFIX.get(node.occur, "")


def _flatten(node, kind, out):
    """把同类连缀的右倾二叉树摊平,(a,(b,c)) 还原成 a, b, c。"""
    for side in (node.left, node.right):
        if side is None:
            continue
        if side.type == kind and side.occur == "once":
            _flatten(side, kind, out)
        else:
            out.append(side)


def allowed_children(node, acc=None) -> set:
    if acc is None:
        acc = set()
    if node is None:
        return acc
    if node.type == "element" and node.name:
        acc.add(node.name)
    allowed_children(node.left, acc)
    allowed_children(node.right, acc)
    return acc


def _reachable(node, seq, pos, memo):
    """从 pos 出发匹配 node 后所有可能的结束位置。"""
    if node is None:
        return {pos}
    key = (id(node), pos)
    if key in memo:
        return memo[key]
    memo[key] = set()
    kind, occur = node.type, node.occur

    def once(at):
        if kind == "element":
            return {at + 1} if at < len(seq) and seq[at] == node.name else set()
        if kind == "pcdata":
            return {at}
        if kind == "seq":
            ends = set()
            for mid in _reachable(node.left, seq, at, memo):
                ends |= _reachable(node.right, seq, mid, memo)
            return ends
        if kind == "or":
            return (_reachable(node.left, seq, at, memo)
                    | _reachable(node.right, seq, at, memo))
        return set()

    if occur == "opt":
        res = {pos} | once(pos)
    elif occur in ("mult", "plus"):
        reached, frontier = set(), {pos}
        while frontier:
            nxt = set()
            for at in frontier:
                for end in once(at):
                    if end not in reached:
                        reached.add(end)
                        nxt.add(end)
            frontier = nxt
        res = (reached | {pos}) if occur == "mult" else set(reached)
    else:
        res = once(pos)
    memo[key] = res
    return res


def first_mismatch(model, kids) -> Optional[int]:
    """最长可匹配前缀的长度,即第一个走不通的位置;整体可匹配时返回 None。"""
    if model is None:
        return 0 if kids else None
    if len(kids) in _reachable(model, kids, 0, {}):
        return None
    for cut in range(len(kids), -1, -1):
        # 判据是"能否完整消耗前 cut 个"。只看返回集合非空是不够的:内容模型
        # 各部分多半可选,匹配零个子元素也会返回 {0},那样任何前缀都算通过。
        if cut in _reachable(model, kids[:cut], 0, {}):
            return cut
    return 0


# --------------------------------------------------------------------------
# 违反记录
# --------------------------------------------------------------------------

@dataclass
class Violation:
    """一条违反。字段够模型定位并修复,不依赖 libxml2 消息里的名字。"""

    code: str                       # libxml2 的错误类型名,如 DTD_CONTENT_MODEL
    path: str                       # 带同名兄弟序号的 XPath,唯一可靠定位
    line: int
    message: str                    # libxml2 原始消息
    qname: str = ""                 # 出问题的元素 qname
    expected: str = ""              # 从 DTD 重建的期望,不截断
    actual: str = ""                # 实得的子元素序列
    hint: str = ""                  # 首个失配点的人话说明

    def render(self) -> str:
        lines = ["[%s] %s" % (self.code, self.path or "/")]
        if self.hint:
            lines.append("  问题: %s" % self.hint)
        else:
            lines.append("  问题: %s" % self.message)
        if self.actual:
            lines.append("  实际子元素: %s" % self.actual)
        if self.expected:
            lines.append("  DTD 要求: %s" % self.expected)
        return "\n".join(lines)


@dataclass
class Report:
    well_formed: bool = False
    valid: bool = False
    violations: list = field(default_factory=list)
    parse_error: str = ""

    @property
    def ok(self) -> bool:
        return self.well_formed and self.valid

    def render(self) -> str:
        if not self.well_formed:
            return "XML 非良构: %s" % self.parse_error
        if self.valid:
            return ""
        return "\n".join(v.render() for v in self.violations)


# --------------------------------------------------------------------------
# 校验入口
# --------------------------------------------------------------------------

# 只看单个元素及其直接子元素就能判定的类别。片段校验时只采信这些——跨文档
# 类别(ID 唯一性、IDREF 解析)在片段上判不准:被引用的目标可能在文档其他部分。
LOCAL_CODES = frozenset({
    "DTD_CONTENT_MODEL", "DTD_INVALID_CHILD", "DTD_NOT_PCDATA",
    "DTD_NOT_EMPTY", "DTD_UNKNOWN_ELEM", "DTD_UNKNOWN_ATTRIBUTE",
    "DTD_ATTRIBUTE_VALUE", "DTD_ATTRIBUTE_DEFAULT", "DTD_MISSING_ATTRIBUTE",
    "DTD_NOTATION_VALUE", "DTD_ROOT_NAME",
})

_CHILD_RE = re.compile(r"^\s*$")


def _child_qnames(el) -> list:
    out = []
    for child in el:
        tag = child.tag
        if not isinstance(tag, str):
            continue                       # 注释与 PI 不参与内容模型匹配
        if tag.startswith("{"):
            local = tag.split("}", 1)[1]
            out.append("%s:%s" % (child.prefix, local) if child.prefix else local)
        else:
            out.append(tag)
    return out


def _element_qname(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        local = tag.split("}", 1)[1]
        return "%s:%s" % (el.prefix, local) if el.prefix else local
    return tag


_ENRICHABLE = frozenset({"DTD_CONTENT_MODEL", "DTD_INVALID_CHILD",
                         "DTD_NOT_PCDATA", "DTD_NOT_EMPTY",
                         "DTD_ATTRIBUTE_VALUE", "DTD_MISSING_ATTRIBUTE",
                         "DTD_ATTRIBUTE_DEFAULT", "DTD_UNKNOWN_ATTRIBUTE"})

_ATTR_CODES = frozenset({"DTD_ATTRIBUTE_VALUE", "DTD_MISSING_ATTRIBUTE",
                         "DTD_ATTRIBUTE_DEFAULT", "DTD_UNKNOWN_ATTRIBUTE"})


def _attr_qname(key: str, el) -> str:
    """把 lxml 的属性键还原成 DTD 里的字面名(xlink:href 这样)。"""
    if not key.startswith("{"):
        return key
    uri, local = key[1:].split("}", 1)
    for prefix, ns in (el.nsmap or {}).items():
        if ns == uri and prefix:
            return "%s:%s" % (prefix, local)
    if uri == "http://www.w3.org/XML/1998/namespace":
        return "xml:%s" % local
    return local


def _attr_hint(el, decl) -> str:
    """不解析 libxml2 消息,直接拿实际属性与声明比对定位问题。

    消息里的 qname 被剥掉了前缀(xlink:href 会显示成 href),从消息正则抽名字
    并不可靠。
    """
    problems = []
    for key, value in el.attrib.items():
        aq = _attr_qname(key, el)
        spec = decl.attrs.get(aq)
        if spec is None:
            problems.append("属性 @%s 未声明" % aq)
            continue
        if spec.values and value not in spec.values:
            problems.append(
                "@%s 的取值 %r 不在允许值里；允许: %s"
                % (aq, value, " | ".join(spec.values)))
        elif spec.default == "fixed" and spec.default_value is not None \
                and value != spec.default_value:
            problems.append("@%s 是固定值属性,必须写成 %r,实际是 %r"
                            % (aq, spec.default_value, value))
    # 命名空间绑定不在 el.attrib 里,只能从 nsmap 取,但 DTD 把它们声明成了
    # 普通属性(xmlns:mml 是 CDATA #FIXED)。只看本元素新声明的那些,继承来的
    # 已经在祖先上查过了。
    parent = el.getparent()
    inherited = dict(parent.nsmap) if parent is not None else {}
    for prefix, uri in (el.nsmap or {}).items():
        if not prefix or inherited.get(prefix) == uri:
            continue
        spec = decl.attrs.get("xmlns:%s" % prefix)
        if spec is None:
            problems.append("命名空间绑定 xmlns:%s 未声明" % prefix)
        elif spec.default == "fixed" and spec.default_value is not None \
                and uri != spec.default_value:
            problems.append("xmlns:%s 是固定值,必须绑定 %r,实际是 %r"
                            % (prefix, spec.default_value, uri))

    present = {_attr_qname(k, el) for k in el.attrib}
    for aq, spec in decl.attrs.items():
        if spec.default == "required" and aq not in present:
            problems.append("缺少必需属性 @%s" % aq)
    return "；".join(problems)


def _enrich(v: Violation, tree, decls) -> Violation:
    """给内容模型类的违反补上期望、实得与失配点。定位失败就原样返回。"""
    if v.code not in _ENRICHABLE or tree is None or not v.path:
        return v
    try:
        found = tree.xpath(v.path)
        if not found:
            # libxml2 在部分错误类型上给的不是绝对路径:实测 DTD_UNKNOWN_ATTRIBUTE
            # 报的是 "/sec" 而元素其实在 /article/body/sec。退回按标签名全树搜索,
            # 唯一命中才采用——多个同名元素时无从判断是哪一个,宁可不给提示。
            tail = v.path.rsplit("/", 1)[-1]
            if not tail:
                return v
            found = tree.xpath("//" + tail)
            if len(found) != 1:
                return v
            v.path = tree.getpath(found[0])
    except Exception:
        return v
    if not hasattr(found[0], "tag"):
        return v
    el = found[0]
    v.qname = _element_qname(el)
    decl = decls.get(v.qname)
    if decl is None:
        return v
    if v.code in _ATTR_CODES:
        hint = _attr_hint(el, decl)
        if hint:
            v.hint = "<%s> %s" % (v.qname, hint)
        return v

    kids = _child_qnames(el)
    v.actual = " → ".join(kids) if kids else "（空）"
    v.expected = render_model(decl.content)

    if decl.type == "empty":
        v.hint = ("<%s> 声明为 EMPTY,不能有任何内容——子元素、文字、空白、"
                  "注释、处理指令都不行" % v.qname)
        return v
    if decl.type == "mixed":
        # 混合内容不讲顺序也不讲次数,唯一的违反方式是出现名单外的子元素。
        # DTD_INVALID_CHILD 与 DTD_NOT_PCDATA 都只落在这一类元素上——实测
        # element-only 元素出现名单外子元素时,libxml2 报的是 DTD_CONTENT_MODEL。
        allowed = allowed_children(decl.content)
        bad = [k for k in kids if k not in allowed]
        if bad:
            v.hint = "<%s> 不允许子元素 %s" % (
                v.qname, "、".join("<%s>" % b for b in dict.fromkeys(bad)))
        return v
    # 走到这里 decl.type 必为 element:empty 与 mixed 已在上面返回,而这份 DTD
    # 里 ANY 类型的元素数为 0。
    at = first_mismatch(decl.content, kids)
    if at is None:
        return v
    if at >= len(kids):
        v.hint = "<%s> 的子元素在 %s 之后就结束了,但 DTD 要求还有内容" % (
            v.qname, kids[-1] if kids else "开头")
    elif at == 0:
        v.hint = "<%s> 的第 1 个子元素 <%s> 就不该出现在这个位置" % (
            v.qname, kids[0])
    else:
        v.hint = "<%s> 的第 %d 个子元素 <%s> 不应出现在 <%s> 之后" % (
            v.qname, at + 1, kids[at], kids[at - 1])
    return v


def validate_bytes(xml_bytes: bytes, *, scope: str = "document") -> Report:
    """对整篇文档做 DTD 校验。

    scope="document"  采信全部违反。
    scope="fragment"  只采信 LOCAL_CODES,忽略跨文档类别。用于文档尚未拼装
                      完整时的中途校验——那时 xref 的目标可能还没生成。
    """
    decls = _load_decls()
    report = Report()
    parser = etree.XMLParser(dtd_validation=True, load_dtd=True,
                             no_network=True, resolve_entities=True)
    # 每次新建 parser。复用会让根元素名检查静默失效(同一 parser 成功解析过一次
    # 之后,后续文档的 DTD_ROOT_NAME 不再报出),且 error_log 是可变共享状态。
    parser.resolvers.add(_LocalResolver())
    tree = None
    try:
        root = etree.fromstring(xml_bytes, parser)
        tree = root.getroottree()
        report.well_formed = True
    except etree.XMLSyntaxError as exc:
        entries = list(parser.error_log)
        fatal = [e for e in entries if e.domain_name != "VALID"]
        if fatal:
            report.parse_error = str(exc)
            return report
        report.well_formed = True
        try:
            tree = etree.fromstring(
                xml_bytes,
                etree.XMLParser(load_dtd=False, no_network=True,
                                resolve_entities=False)).getroottree()
        except etree.XMLSyntaxError:
            tree = None

    seen = set()
    for entry in parser.error_log:
        if entry.domain_name != "VALID":
            continue
        code = entry.type_name
        if scope == "fragment" and code not in LOCAL_CODES:
            continue
        key = (code, entry.path, entry.message)
        if key in seen:
            continue
        seen.add(key)
        report.violations.append(_enrich(
            Violation(code=code, path=entry.path or "", line=entry.line or 0,
                      message=entry.message.strip()),
            tree, decls))
    report.valid = not report.violations
    return report
