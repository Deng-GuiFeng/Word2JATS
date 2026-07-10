"""服务端把 JATS XML 渲染成期刊样式 HTML。

用 NCBI 公有领域 JATS 预览样式表（vendor/jats/jats-html.xsl，XSLT 1.0，libxslt 可跑）。
浏览器原生 XSLT 正被移除，故渲染放服务端。三件事：
  1. 变换前改写 <graphic>/<inline-graphic> 的本地 xlink:href → 指向本服务的图片接口；
  2. XSLT 变换（禁网，样式表本身不读外部资源）；
  3. 变换后把 MathML 的 mml: 前缀去掉、设为默认命名空间，浏览器才认（HTML5 只渲染无前缀 <math>）。
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

_XSL_PATH = Path(__file__).resolve().parent / "vendor" / "jats" / "jats-html.xsl"
_XLINK = "http://www.w3.org/1999/xlink"

_transform = None


def _get_transform():
    global _transform
    if _transform is None:
        xsl = etree.parse(str(_XSL_PATH))
        _transform = etree.XSLT(xsl, access_control=etree.XSLTAccessControl.DENY_ALL)
    return _transform


def _rewrite_figure_hrefs(doc, task_id: str) -> None:
    """把本地图片引用改写到 /api/figure/<task_id>/<name>；外链(http/https)保持不动。"""
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname in ("graphic", "inline-graphic"):
            href = el.get("{%s}href" % _XLINK)
            if href and not href.startswith(("http://", "https://", "/")):
                el.set("{%s}href" % _XLINK, "/api/figure/%s/%s" % (task_id, href))


def _demote_mathml(html: str) -> str:
    """去掉 MathML 的 mml: 前缀并设默认命名空间，让浏览器原生渲染。"""
    html = html.replace("<mml:", "<").replace("</mml:", "</")
    html = html.replace('xmlns:mml="http://www.w3.org/1998/Math/MathML"',
                        'xmlns="http://www.w3.org/1998/Math/MathML"')
    return html


def _strip_stylesheet_warnings(html: str) -> str:
    """去掉 NCBI 样式表的诊断提示 span（class="warning"，如
    "{ label (or @symbol) needed for fn[@id='fn1'] }"）——这类提示是样式表对某些 JATS
    模式的挑剔（我方作者注脚注与金标准逐字一致、并非缺陷），不应泄露进读者可见的预览。"""
    return re.sub(r'<span class="warning">[^<]*</span>', "", html)


def render_html(xml_bytes: bytes, task_id: str, css_href: str = "/assets/jats-preview.css") -> str:
    """JATS XML(bytes) → 期刊样式 HTML(str)。失败时抛异常，由调用方兜底。"""
    text = xml_bytes.decode("utf-8")
    text = re.sub(r"<!DOCTYPE.*?>", "", text, count=1, flags=re.DOTALL)
    doc = etree.fromstring(text.encode("utf-8"))
    _rewrite_figure_hrefs(doc, task_id)
    transform = _get_transform()
    result = transform(doc, css=etree.XSLT.strparam(css_href))
    return _strip_stylesheet_warnings(_demote_mathml(str(result)))
