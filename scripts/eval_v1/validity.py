"""L0 合法层(设计 §6.1 / 附录C):不看参考,判"结构本身是否成立"。

产物 = 通过/不通过 + 违规点清单(节点定位 + 严重度 error/warning + 可读消息)。
100% 确定性。规则来源:JATS DTD、JATS4R 推荐(math/permissions 等)、IMR 实测惯例(负向核对)。
"""
import os
import re
import threading

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DTD_PATH = os.path.join(
    ROOT, "src", "word2jats", "resources", "dtd",
    "JATS-Publishing-1-3-MathML3-DTD", "JATS-journalpublishing1-3-mathml3.dtd")
DOCTYPE_PUBLIC = "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.3 20210610//EN"
DOCTYPE_SYSTEM = "https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd"
ROOT_TAG = "article"
ORCID_RE = re.compile(r"^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

_DTD_LOCK = threading.Lock()
_DTD = None


def _dtd():
    global _DTD
    with _DTD_LOCK:
        if _DTD is None:
            _DTD = etree.DTD(DTD_PATH)
        return _DTD


def _orcid_checkdigit_ok(text):
    """ORCID 末位是 ISO 7064 MOD 11-2 校验位。抄错一位数字靠正则看不出来,靠校验位能。
    不合规不必然是转换器的错(docx 本身可能就写错),故记 warning 不记 error。"""
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\s*$", (text or "").strip())
    if not m:
        return None
    digits = m.group(1).replace("-", "")
    total = 0
    for c in digits[:-1]:
        total = (total + int(c)) * 2
    expect = (12 - total % 11) % 11
    return ("X" if expect == 10 else str(expect)) == digits[-1]


def check(xml_path):
    """返回 {ok, wellformed, dtd_ok, doctype_ok, violations:[{rule,severity,where,msg}]}。"""
    res = {"ok": False, "wellformed": False, "dtd_ok": False, "doctype_ok": False, "violations": []}
    V = res["violations"]

    def v(rule, severity, where, msg):
        V.append({"rule": rule, "severity": severity, "where": where, "msg": msg})

    parser = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
    try:
        tree = etree.parse(xml_path, parser)
    except (etree.XMLSyntaxError, OSError) as e:
        v("wellformed", "error", "/", str(e))
        return res
    res["wellformed"] = True
    root = tree.getroot()

    def where(el):
        try:
            return tree.getpath(el)
        except Exception:
            return "?"

    # DOCTYPE public id **与 system id** 逐字核对:只查 public id 会放过指向别处(甚至本地
    # 路径、错版本)的 system id,而下游解析器按 system id 取 DTD
    pub_ok = tree.docinfo.public_id == DOCTYPE_PUBLIC
    sys_ok = (tree.docinfo.system_url or "") == DOCTYPE_SYSTEM
    res["doctype_ok"] = pub_ok and sys_ok
    if not pub_ok:
        v("doctype", "error", "/", "DOCTYPE public id 非标准 JATS v1.3: %r" % tree.docinfo.public_id)
    if not sys_ok:
        v("doctype", "error", "/", "DOCTYPE system id 非标准 JATS v1.3: %r" % tree.docinfo.system_url)

    # XML 声明编码(§6.1 步1):须 UTF-8。JATS 交付是硬要求,非 UTF-8 下游取字必错,记 error
    enc = (tree.docinfo.encoding or "").upper().replace("-", "")
    if enc and enc != "UTF8":
        v("encoding", "error", "/", "XML 声明编码非 UTF-8: %r" % tree.docinfo.encoding)

    # 命名空间 URI 精确核对(§6.1 步1):xlink / MathML 前缀若声明,必须绑定标准 URI
    XLINK_NS = "http://www.w3.org/1999/xlink"
    for pfx, uri in (root.nsmap or {}).items():
        if pfx == "xlink" and uri != XLINK_NS:
            v("namespace", "error", "/", "xlink 命名空间 URI 错误: %r" % uri)
        if pfx in ("mml", "m", "mathml") and uri != MATHML_NS:
            v("namespace", "error", "/", "MathML 命名空间 URI 错误: %r" % uri)

    # DTD 校验。**整个 validate+读 error_log 必须在锁内**:lxml 的 DTD 对象把校验错误写进
    # 自身的 error_log,run.py 是多样例并发跑的,不加锁会串样例(A 的错误出现在 B 的报告里)。
    # DTD 校验是毫秒级、转换是分钟级,串行化它对总耗时无影响,换来结果确定可复现。
    dtd = _dtd()
    with _DTD_LOCK:
        res["dtd_ok"] = bool(dtd.validate(tree))
        errs = [(e.line, e.message) for e in list(dtd.error_log)[:20]]
    if not res["dtd_ok"]:
        for line, msg in errs:
            v("dtd", "error", "line %s" % line, msg)
    # 根元素名核对。DTD.validate() 走的 libxml2 xmlValidateDtd 会跳过 XML 1.0 §2.8 的
    # Root Element Type 比对(它把 intSubset 置空后才校验,而根名比对以 intSubset 非空为
    # 前提),于是根元素错配(如只剩 front)会被判成合法。lxml 又取不到 DOCTYPE 原文声明的
    # 名字(docinfo.root_name 返回的是实际根元素名),故用等价判据:金标准与本项目输出的
    # DOCTYPE 恒为 <!DOCTYPE article ...>,根元素必须是 article。
    if tree.docinfo.doctype and root.tag != ROOT_TAG:
        res["dtd_ok"] = False
        v("dtd", "error", "/", "根元素必须是 %s,实际是 %s(XML 1.0 §2.8 Root Element Type)"
          % (ROOT_TAG, root.tag))

    # id 全局唯一 + xref 闭合(@rid 为 IDREFS,可含多个空格分隔 id)
    ids = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        i = el.get("id")
        if i:
            if i in ids:
                v("id-unique", "error", where(el), "id 重复: %s" % i)
            ids.add(i)
    for x in root.iter("{*}xref"):
        rid = (x.get("rid") or "").strip()
        if not rid:
            # DTD 把 @rid 定为 #IMPLIED,但一个不指向任何东西的交叉引用是坏输出:
            # 渲染成死链、检索工具解析不到目标。14 例金标准实测 0 处,记 error 安全
            v("xref-closed", "error", where(x), "xref 无 rid(交叉引用不指向任何目标)")
            continue
        for tok in rid.split():
            if tok not in ids:
                v("xref-closed", "error", where(x), "悬空 xref rid=%s" % tok)

    # 公式须含机读表示(JATS4R Math)
    for tag in ("inline-formula", "disp-formula"):
        for f in root.iter("{*}" + tag):
            has_math = any(
                isinstance(c.tag, str) and (etree.QName(c).namespace == MATHML_NS
                                            or etree.QName(c).localname == "tex-math")
                for c in f.iter())
            if not has_math:
                # 设计 §6.1/附录C:公式缺机读表示为 warning(非 error)——退化为图片仍合法,但丢了可检索公式
                v("formula-math", "warning", where(f), "公式无 mml:math/tex-math 机读表示")

    # ORCID 须完整 URL 规范形;并建议标 authenticated(JATS4R,info 级)
    for c in root.iter("{*}contrib-id"):
        if c.get("contrib-id-type") == "orcid":
            if not ORCID_RE.match((c.text or "").strip()):
                v("orcid-url", "error", where(c), "ORCID 非完整 URL 规范形: %r" % (c.text or ""))
            else:
                if _orcid_checkdigit_ok(c.text) is False:
                    v("orcid-checkdigit", "warning", where(c),
                      "ORCID 校验位(ISO 7064)不符,疑似抄错一位: %r" % (c.text or ""))
                if (c.get("authenticated") or "").lower() != "true":
                    v("orcid-authenticated", "info", where(c),
                      "ORCID 建议标 authenticated=\"true\"(JATS4R)")

    # 展示对象须有 id;graphic 须有 xlink:href
    for tag in ("fig", "table-wrap", "disp-formula"):
        for el in root.iter("{*}" + tag):
            if not el.get("id"):
                v("display-id", "error", where(el), "%s 缺 id" % tag)
    for g in root.iter("{*}graphic"):
        if not g.get(XLINK_HREF):
            v("graphic-href", "error", where(g), "graphic 缺 xlink:href")

    # license 须带链接(JATS4R Permissions)
    for lic in root.iter("{*}license"):
        if lic.find(".//{*}ext-link") is None and not lic.get(XLINK_HREF):
            v("license-link", "error", where(lic), "license 无许可链接")

    # 负向核对:table 不应携 frame/rules(IMR 实测惯例,警告级)
    for t in root.iter("{*}table"):
        for a in ("frame", "rules"):
            if t.get(a):
                v("bare-attrs", "warning", where(t), "table 携多余属性 %s=%r" % (a, t.get(a)))

    res["ok"] = res["wellformed"] and res["dtd_ok"] and res["doctype_ok"] \
        and not any(x["severity"] == "error" for x in V)
    return res
