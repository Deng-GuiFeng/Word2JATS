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


# NLM 预览样式表是"诊断预览"(给 JATS 开发者查标记用),会在正文最前面自动生成
# Journal/Article Information 两块后台字段转储(刊号/ISSN/缩写刊名/publisher-id/日期…)。
# 这不是文章内容,对"看排版稿核对图表公式"的用户是噪声,故在渲染后按诊断标题精确移除,
# 让预览像真实排版稿那样从标题起。全部结构化字段仍完整保留在「XML 源文件」页供技术核对。
_DIAG_HEADINGS = {"Journal Information", "Article Information", "Article Information (continued)"}


def _strip_diagnostic_front(result) -> None:
    # 诊断元数据块出现在两处:正文最前的 div.front[0](Journal/Article Information),
    # 和页面底部 div.footer(Article Information continued 运行页脚)。凡含诊断标题的块整块清除。
    for h in list(result.xpath("//*[local-name()='h4'][@class='generated']")):
        if (h.text or "").strip() not in _DIAG_HEADINGS:
            continue
        block = None  # 优先删整个 footer;否则删承载它的 div.metadata 块
        for anc in h.iterancestors():
            cls = (anc.get("class") or "").split()
            if "footer" in cls:
                block = anc
                break
            if "metadata" in cls and block is None:
                block = anc
        if block is not None and block.getparent() is not None:
            block.getparent().remove(block)
    # 清掉因移除而空悬在最前的分隔线
    for front in result.xpath("//*[local-name()='div'][@class='front']"):
        while len(front) and isinstance(front[0].tag, str) and front[0].tag.split("}")[-1] == "hr":
            front.remove(front[0])


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


# 预览样式补丁：NCBI 样式表缺 img 限宽（大图会撑爆版式）、ORCID 以裸 URL 呈现（观感糙）。
_PREVIEW_CSS = (
    "<style>"
    "img,svg{max-width:100%;height:auto;}"
    "table{max-width:100%;}"
    "body{overflow-x:auto;}"
    ".w2j-orcid{display:inline-block;font-size:.7em;font-weight:700;vertical-align:super;"
    "color:#fff;background:#A6CE39;text-decoration:none;border-radius:3px;padding:0 3px;"
    "margin-right:3px;line-height:1.5;}"
    "</style>"
)
_ORCID_RE = re.compile(
    r'<span class="generated">\[</span>'
    r'(https?://orcid\.org/[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9Xx])'
    r'<span class="generated">\] </span>'
)


def _polish_preview(html: str) -> str:
    """给预览补两处样式：① 图片/表格限宽防大图撑爆版式；② ORCID 裸 URL → 小号绿色 iD 链接。"""
    html = _ORCID_RE.sub(
        r'<a class="w2j-orcid" href="\1" target="_blank" rel="noopener" title="ORCID iD">iD</a> ',
        html,
    )
    if "</head>" in html:
        html = html.replace("</head>", _PREVIEW_CSS + "</head>", 1)
    else:
        html = _PREVIEW_CSS + html
    return html


def render_html(xml_bytes: bytes, task_id: str, css_href: str = "/assets/jats-preview.css") -> str:
    """JATS XML(bytes) → 期刊样式 HTML(str)。失败时抛异常，由调用方兜底。"""
    text = xml_bytes.decode("utf-8")
    text = re.sub(r"<!DOCTYPE.*?>", "", text, count=1, flags=re.DOTALL)
    doc = etree.fromstring(text.encode("utf-8"))
    _rewrite_figure_hrefs(doc, task_id)
    transform = _get_transform()
    result = transform(doc, css=etree.XSLT.strparam(css_href))
    _strip_diagnostic_front(result)
    return _polish_preview(_strip_stylesheet_warnings(_demote_mathml(str(result))))
