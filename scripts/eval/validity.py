"""L0 合法层(设计 §6.1 / 附录C):不看参考,判"结构本身是否成立"。

产物 = 通过/不通过 + 违规点清单(节点定位 + 严重度 error/warning + 可读消息)。
100% 确定性。规则来源:JATS DTD、JATS4R 推荐(math/permissions 等)、IMR 实测惯例(负向核对)。
"""
import os
import re

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DTD_PATH = os.path.join(
    ROOT, "src", "word2jats", "resources", "dtd",
    "JATS-Publishing-1-3-MathML3-DTD", "JATS-journalpublishing1-3-mathml3.dtd")
DOCTYPE_PUBLIC = "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.3 20210610//EN"
ORCID_RE = re.compile(r"^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

_DTD = None


def _dtd():
    global _DTD
    if _DTD is None:
        _DTD = etree.DTD(DTD_PATH)
    return _DTD


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

    # DOCTYPE public id 逐字核对
    res["doctype_ok"] = tree.docinfo.public_id == DOCTYPE_PUBLIC
    if not res["doctype_ok"]:
        v("doctype", "error", "/", "DOCTYPE public id 非标准 JATS v1.3: %r" % tree.docinfo.public_id)

    # XML 声明编码(§6.1 步1):须 UTF-8
    enc = (tree.docinfo.encoding or "").upper().replace("-", "")
    if enc and enc != "UTF8":
        v("encoding", "warning", "/", "XML 声明编码非 UTF-8: %r" % tree.docinfo.encoding)

    # 命名空间 URI 精确核对(§6.1 步1):xlink / MathML 前缀若声明,必须绑定标准 URI
    XLINK_NS = "http://www.w3.org/1999/xlink"
    for pfx, uri in (root.nsmap or {}).items():
        if pfx == "xlink" and uri != XLINK_NS:
            v("namespace", "error", "/", "xlink 命名空间 URI 错误: %r" % uri)
        if pfx in ("mml", "m", "mathml") and uri != MATHML_NS:
            v("namespace", "error", "/", "MathML 命名空间 URI 错误: %r" % uri)

    # DTD 校验
    dtd = _dtd()
    res["dtd_ok"] = bool(dtd.validate(tree))
    if not res["dtd_ok"]:
        for e in list(dtd.error_log)[:20]:
            v("dtd", "error", "line %s" % e.line, e.message)

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
            v("xref-closed", "warning", where(x), "xref 无 rid")
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
            elif (c.get("authenticated") or "").lower() != "true":
                v("orcid-authenticated", "info", where(c), "ORCID 建议标 authenticated=\"true\"(JATS4R)")

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
