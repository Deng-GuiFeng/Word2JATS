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


def _compile(node, owner_prefix=None, resolve=None):
    if node is None:
        return None
    name = node.name
    if node.type == "element" and name and resolve is not None:
        name = resolve(name, owner_prefix)
    return _Node(node.type, node.occur, name,
                 _compile(node.left, owner_prefix, resolve),
                 _compile(node.right, owner_prefix, resolve))


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
    # 先收齐所有 qname,才能给内容模型里的名字补前缀。lxml 的内容模型节点只
    # 暴露局部名、不暴露 prefix,而 DTD 里写的是 "tex-math | mml:math"。不补
    # 前缀的话,渲染出的期望模型会写出 DTD 里根本不存在的 <math>,allowed_children
    # 也会把合法的 mml:math 判成名单外。
    locals_to_qnames = {}
    for el in dtd.iterelements():
        q = _qname(el.prefix, el.name)
        locals_to_qnames.setdefault(el.name, []).append(q)

    def resolve(local, owner_prefix):
        cands = locals_to_qnames.get(local)
        if not cands:
            return local
        if len(cands) == 1:
            return cands[0]
        # 局部名有歧义(sec/mml:sec 这五组)。与所属元素同前缀的优先——MathML
        # 元素的子元素也是 MathML 元素。
        for cand in cands:
            cand_prefix = cand.split(":", 1)[0] if ":" in cand else None
            if cand_prefix == owner_prefix:
                return cand
        return local

    out = {}
    for el in dtd.iterelements():
        q = _qname(el.prefix, el.name)
        attrs = {}
        for at in el.iterattributes():
            aq = _qname(at.prefix, at.name)
            attrs[aq] = _Attr(aq, at.type, at.default, at.default_value,
                              tuple(at.itervalues() or ()))
        out[q] = _Decl(q, el.type, _compile(el.content, el.prefix, resolve), attrs)
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

# 期望模型/原始消息超过这个长度就不整份贴出——贴了也只是噪音。
_MAX_MODEL_CHARS = 400
_MAX_MESSAGE_CHARS = 220


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


# 内容模型的匹配用 Brzozowski 导数。正则表示成不可变元组,便于缓存:
#   ('nil',)          空集,不匹配任何串
#   ('eps',)          只匹配空串
#   ('el', name)      匹配单个子元素
#   ('seq', L, R) / ('or', L, R) / ('star', X)
_NIL = ("nil",)
_EPS = ("eps",)


def _to_regex(node):
    """把内容模型树转成正则元组。量词在这里展开。"""
    if node is None:
        return _EPS
    kind = node.type
    if kind == "element":
        base = ("el", node.name)
    elif kind == "pcdata":
        base = _EPS                       # 文本不参与子元素序列匹配
    elif kind == "seq":
        base = _cat(_to_regex(node.left), _to_regex(node.right))
    elif kind == "or":
        base = _alt(_to_regex(node.left), _to_regex(node.right))
    else:
        base = _NIL
    occur = node.occur
    if occur == "opt":
        return _alt(base, _EPS)
    if occur == "mult":
        return _star(base)
    if occur == "plus":
        return _cat(base, _star(base))
    return base


def _cat(a, b):
    if a is _NIL or b is _NIL or a == _NIL or b == _NIL:
        return _NIL
    if a == _EPS:
        return b
    if b == _EPS:
        return a
    return ("seq", a, b)


def _alt(a, b):
    if a == _NIL:
        return b
    if b == _NIL:
        return a
    if a == b:
        return a
    return ("or", a, b)


def _star(a):
    if a == _NIL or a == _EPS:
        return _EPS
    return ("star", a)


def _nullable(r) -> bool:
    """能否匹配空串。"""
    kind = r[0]
    if kind in ("eps", "star"):
        return True
    if kind in ("nil", "el"):
        return False
    if kind == "seq":
        return _nullable(r[1]) and _nullable(r[2])
    return _nullable(r[1]) or _nullable(r[2])


def _derive(r, name, memo):
    """对 name 求导:消耗掉一个 name 之后剩下的正则。"""
    key = (r, name)
    hit = memo.get(key)
    if hit is not None:
        return hit
    kind = r[0]
    if kind in ("nil", "eps"):
        out = _NIL
    elif kind == "el":
        out = _EPS if r[1] == name else _NIL
    elif kind == "seq":
        left = _cat(_derive(r[1], name, memo), r[2])
        out = _alt(left, _derive(r[2], name, memo)) if _nullable(r[1]) else left
    elif kind == "or":
        out = _alt(_derive(r[1], name, memo), _derive(r[2], name, memo))
    else:                                  # star
        out = _cat(_derive(r[1], name, memo), r)
    memo[key] = out
    return out


@dataclass
class Mismatch:
    """失配点。position 是最长可行前缀的长度,即第一个走不通的下标。"""

    position: int
    kind: str                 # not-allowed / wrong-place / missing-tail
    child: str = ""


def analyze(model, kids) -> Optional[Mismatch]:
    """整体可匹配时返回 None,否则给出第一个走不通的位置与性质。

    判据是**最长可行前缀**——存在某个后缀能把它补成合法串的最长前缀,而不是
    "能被完整匹配的最长前缀"。两者不同:``(label?, citation+)`` 遇到 ``[label]``
    时,label 无法被完整匹配(后面还欠一个 citation),但它处在合法位置,真正的
    问题是末尾缺内容。按"完整匹配"判会一律怪罪第 1 个子元素。
    """
    regex = _to_regex(model)
    memo = {}
    cur = regex
    for index, name in enumerate(kids):
        nxt = _derive(cur, name, memo)
        if nxt == _NIL:
            allowed = allowed_children(model)
            kind = "wrong-place" if name in allowed else "not-allowed"
            return Mismatch(index, kind, name)
        cur = nxt
    if _nullable(cur):
        return None
    return Mismatch(len(kids), "missing-tail")


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
        head = "[%s] %s" % (self.code, self.path or "/")
        if self.line:
            head += "  第 %d 行" % self.line
        lines = [head]
        if self.hint:
            lines.append("  问题: %s" % self.hint)
        if self.actual:
            lines.append("  实际子元素: %s" % self.actual)
        if self.expected:
            # 内容模型可以极长(mml:mmultiscripts 7535 字符、p 允许 72 种子元素)。
            # 整份贴出去会把上面那句结论淹掉,对修复毫无帮助。超长就只给规模。
            if len(self.expected) > _MAX_MODEL_CHARS:
                count = self.expected.count("|") + 1
                lines.append("  DTD 要求: 内容模型过长(允许约 %d 种子元素),"
                             "请按上面的问题定位" % count)
            else:
                lines.append("  DTD 要求: %s" % self.expected)
        # 提示可能算错,原文是唯一能纠正它的东西,始终保留;但原文自己也可能
        # 有几千字符(libxml2 会把整份内容模型贴进消息),同样要截。
        raw = self.message
        if len(raw) > _MAX_MESSAGE_CHARS:
            raw = raw[:_MAX_MESSAGE_CHARS] + " …（原文过长已截断）"
        lines.append("  校验器原文: %s" % raw)
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
        return "\n\n".join(v.render() for v in self.violations)


# --------------------------------------------------------------------------
# 校验入口
# --------------------------------------------------------------------------

# 片段校验时要忽略的类别——只有这两个真正需要看整篇文档:被引用的目标可能
# 还没生成。这里用**黑名单**而不是白名单:白名单一旦漏掉某个类别,该类违反在
# fragment 口径下会被静默放行,而黑名单最坏只是多报。
CROSS_DOCUMENT_CODES = frozenset({"DTD_ID_REDEFINED", "DTD_UNKNOWN_ID"})


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
    # 带前缀的 path(/article/.../mml:math)必须给 namespaces,否则 lxml 抛
    # XPathEvalError: Undefined namespace prefix——而 MathML 恰恰是最需要重建
    # 期望模型的地方(mml:mmultiscripts 的模型 7535 字符,libxml2 在 5056 处截断)。
    nsmap = {k: v2 for k, v2 in (tree.getroot().nsmap or {}).items() if k}
    try:
        found = tree.xpath(v.path, namespaces=nsmap)
        if not found:
            # libxml2 在部分错误类型上给的不是绝对路径:实测 DTD_UNKNOWN_ATTRIBUTE
            # 报的是 "/sec" 而元素其实在 /article/body/sec。退回按标签名全树搜索,
            # 唯一命中才采用——多个同名元素时无从判断是哪一个,宁可不给提示。
            tail = v.path.rsplit("/", 1)[-1]
            if not tail:
                return v
            found = tree.xpath("//" + tail, namespaces=nsmap)
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
    miss = analyze(decl.content, kids)
    if miss is None:
        return v
    if miss.kind == "missing-tail":
        tail_need = sorted(allowed_children(decl.content) - set(kids))
        need = "、".join("<%s>" % n for n in tail_need[:5]) or "更多内容"
        v.hint = ("<%s> 的子元素到 %s 为止,但 DTD 还要求后面有内容(可选的有 %s)"
                  % (v.qname, "<%s>" % kids[-1] if kids else "空", need))
    elif miss.kind == "not-allowed":
        v.hint = "<%s> 不允许子元素 <%s>,把它挪到别处或删掉" % (v.qname, miss.child)
    elif miss.position == 0:
        v.hint = "<%s> 的第 1 个子元素不能是 <%s>" % (v.qname, miss.child)
    else:
        v.hint = ("<%s> 的第 %d 个子元素 <%s> 位置不对:按 DTD 它不能排在 <%s> 之后"
                  % (v.qname, miss.position + 1, miss.child, kids[miss.position - 1]))
    return v


def _plain_parser():
    return etree.XMLParser(load_dtd=False, no_network=True,
                           resolve_entities=False, strip_cdata=False)


def _validating_parser():
    # strip_cdata 必须显式关掉。lxml 默认 True(等于 libxml2 的 XML_PARSE_NOCDATA),
    # CDATA 段会被并进文本再按可忽略空白处理,于是 element-only 内容里只含空白的
    # CDATA 段被漏判——而 XML 1.0 §3 明确它不匹配 S、不能出现在那些位置。不写这
    # 个参数就等于默默替 libxml2 改了口径。
    parser = etree.XMLParser(dtd_validation=True, load_dtd=True, no_network=True,
                             resolve_entities=True, strip_cdata=False)
    # 每次新建。parser 复用会让根元素名检查静默失效,且 error_log 是可变共享状态。
    parser.resolvers.add(_LocalResolver())
    return parser


def _doctype_violations(tree) -> list:
    """核对文档声明的 DTD 确实是 JATS 1.3,且根元素是 article。

    这三条 libxml2 都不会替我们查:
    - 它核对的是"根元素 == DOCTYPE 里写的名字",而那个名字由待检文档自己提供。
      DOCTYPE 写 sec、根也写 sec,它就判通过。
    - 内部子集里的参数实体会覆盖外部子集,``<!ENTITY % body-model "ANY">`` 能让
      <body> 接受任何元素而依旧"有效"——有效的对象已经不是 JATS 1.3 了。
    """
    out = []
    info = tree.docinfo
    root = tree.getroot()
    declared = info.internalDTD.name if info.internalDTD is not None else None
    actual = _element_qname(root)
    if actual != ROOT_TAG:
        out.append(Violation(
            code="ROOT_ELEMENT_INVALID", path=info.URL or "/", line=0,
            message="root element is %r" % actual, qname=actual,
            expected=ROOT_TAG, actual=actual,
            hint="JATS 文档的根元素必须是 <%s>,实际是 <%s>" % (ROOT_TAG, actual)))
    if declared is not None and declared != ROOT_TAG:
        out.append(Violation(
            code="DOCTYPE_NAME_INVALID", path="/", line=0,
            message="doctype declares %r" % declared,
            expected=ROOT_TAG, actual=declared,
            hint="DOCTYPE 声明的元素名必须是 %s,实际是 %s" % (ROOT_TAG, declared)))
    if info.public_id != JATS_PUBLIC_ID:
        out.append(Violation(
            code="DOCTYPE_PUBLIC_INVALID", path="/", line=0,
            message="public id is %r" % info.public_id,
            expected=JATS_PUBLIC_ID, actual=info.public_id or "（无）",
            hint="DOCTYPE 的公共标识符必须是 %r,否则校验的不是 JATS 1.3"
                 % JATS_PUBLIC_ID))
    idtd = info.internalDTD
    if idtd is not None:
        declared_count = (len(list(idtd.iterentities()))
                          + len(list(idtd.iterelements())))
        if declared_count:
            out.append(Violation(
                code="INTERNAL_SUBSET_FORBIDDEN", path="/", line=0,
                message="internal subset declares %d items" % declared_count,
                hint="DOCTYPE 里带了内部子集(%d 条声明)。内部子集会覆盖 JATS DTD "
                     "的定义,校验结果不再代表符合 JATS 1.3,请删除"
                     % declared_count))
    return out


def validate_bytes(xml_bytes: bytes, *, scope: str = "document") -> Report:
    """对整篇文档做 DTD 校验。

    scope="document"  采信全部违反。
    scope="fragment"  忽略 CROSS_DOCUMENT_CODES。用于文档尚未拼装完整时的中途
                      校验——那时 xref 的目标可能还没生成。
    """
    decls = _load_decls()
    report = Report()

    # 良构性先单独判定:能否解析出树就是良构性的定义。不能拿"校验解析是否抛异常"
    # 当良构性判据——文档只要无效它就抛,那样所有有效性错误都会被误报成语法错。
    try:
        tree = etree.fromstring(xml_bytes, _plain_parser()).getroottree()
    except etree.XMLSyntaxError as exc:
        report.parse_error = str(exc)
        return report
    except (ValueError, TypeError) as exc:
        report.parse_error = "输入不是可解析的 XML 字节串: %s" % exc
        return report
    report.well_formed = True

    parser = _validating_parser()
    try:
        etree.fromstring(xml_bytes, parser)
    except etree.XMLSyntaxError:
        pass

    seen = set()
    violations = list(_doctype_violations(tree))
    for entry in parser.error_log:
        if entry.level_name == "WARNING":
            continue
        code = entry.type_name
        key = (code, entry.path, entry.message)
        if key in seen:
            continue
        seen.add(key)
        violations.append(_enrich(
            Violation(code=code, path=entry.path or "", line=entry.line or 0,
                      message=entry.message.strip()),
            tree, decls))

    if scope == "fragment":
        violations = [v for v in violations if v.code not in CROSS_DOCUMENT_CODES]
    report.violations = violations
    report.valid = not violations
    return report
