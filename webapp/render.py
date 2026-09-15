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
        # 上游样式表的摘要容器未复制原 id，补入属性才能由网页目录定位。
        # 仅修改内存中的预览样式，不改 XML 或第三方源文件。
        namespace = {'xsl': 'http://www.w3.org/1999/XSL/Transform'}
        for container in xsl.xpath('//xsl:for-each[@select="abstract | trans-abstract"]/div', namespaces=namespace):
            copy_id = etree.Element('{http://www.w3.org/1999/XSL/Transform}copy-of', select='@id')
            container.insert(0, copy_id)
        for container in xsl.xpath('//xsl:template[@match="inline-formula | chem-struct"]/span', namespaces=namespace):
            container.insert(0, etree.Element('{http://www.w3.org/1999/XSL/Transform}copy-of', select='@id'))
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


def _expand_multi_xrefs(doc) -> None:
    """预览中将多目标引用展开为独立链接；交付 XML 的 rid 列表不变。"""
    targets = {node.get("id"): node for node in doc.xpath('//*[@id]')}
    for node in list(doc.xpath('//xref[@rid]')):
        ids = (node.get("rid") or "").split()
        if len(ids) < 2:
            continue
        labels = []
        for target_id in ids:
            target = targets.get(target_id)
            label = target.find("label") if target is not None else None
            labels.append("".join(label.itertext()).strip() if label is not None else "")
        # 不凭 ID 编造文献编号，也不掩盖源 XML 的缺失目标。
        if not all(labels):
            continue
        # 文献标签可能自带方括号；沿用正文引用的括号位置，避免 [[1], [2]]。
        # 外置于 xref 的括号由父段落保留，只有引用自身的括号需要重新加回。
        numbers = [re.fullmatch(r"\[?(\d+)\]?", label) for label in labels]
        if node.get("ref-type") == "bibr" and all(numbers):
            labels = [number.group(1) for number in numbers]
            citation = "".join(node.itertext()).strip()
            if citation.startswith("[") and citation.endswith("]"):
                labels[0] = "[" + labels[0]
                labels[-1] += "]"
        parent, index, tail = node.getparent(), node.getparent().index(node), node.tail
        for i, (target_id, label) in enumerate(zip(ids, labels)):
            link = etree.Element("xref", {k:v for k,v in node.attrib.items() if k != "id"})
            link.set("rid", target_id)
            if i == 0 and node.get("id"):
                link.set("id", node.get("id"))
            link.text = label
            link.tail = tail if i == len(ids)-1 else ", "
            parent.insert(index+i, link)
        parent.remove(node)


# element-citation 里各子元素(题名/刊名/年/卷/期/页…)按 JATS 规范本就不带字面标点,
# 分隔标点应由渲染系统生成。NLM 预览样式表偏偏不补,把它们拍平成裸文本相邻输出,于是
# 年/卷/页糊成一串数字("2023"+"24"+"11939"→"20232411939"),读者无从辨读。结构参考同为
# element-citation、交付 XML 与之逐字一致(标点本就不该进 XML),故只在预览渲染前按子元素
# 类型注入常规温哥华式分隔,让参考文献读起来像真实期刊条目。只碰 element-citation,不动
# mixed-citation(后者自带字面标点,再注入会重复)。
def _cite_sep(prev_tag: str, cur_tag: str) -> str:
    """返回排在 prev 与 cur 两个引用子元素之间的分隔符(温哥华式)。"""
    close = ")" if prev_tag == "issue" and cur_tag != "issue" else ""
    if cur_tag in ("article-title", "chapter-title", "part-title", "trans-title", "data-title"):
        base = ". "
    elif cur_tag in ("source", "conf-name"):
        base = ". "
    elif cur_tag in ("year", "date-in-citation", "string-date"):
        base = ". "
    elif cur_tag == "volume":
        base = "; "
    elif cur_tag == "issue":
        return "("  # 卷后紧跟"(期)",开括号;闭括号由下一元素的 close 补
    elif cur_tag in ("fpage", "elocation-id", "page-range"):
        base = ": "
    elif cur_tag == "lpage":
        return "–"  # 起止页用连接号,前面不会是 issue
    elif cur_tag in ("pub-id", "ext-link", "publisher-name", "publisher-loc",
                     "edition", "series", "isbn"):
        base = ". "
    elif cur_tag in ("comment", "annotation", "supplement"):
        # 尾注(PMID、(In Chinese) 等)前用句点分隔——既是 NLM/温哥华惯例(PMID 跟在句点后),
        # 又能穿过样式表变换。纯空格分隔会被 XSLT 的 strip-space 吞掉,导致"44PMID"这种粘连。
        base = ". "
    else:
        # 兜底分隔用不换行空格  :普通空格会被样式表 strip-space 吞掉致相邻子元素粘连,
        #   不属 XML 空白、能留住,渲染出来仍是一个空格。
        base = " "
    return close + base if close else base


def _strip_trailing_ws(el) -> None:
    """去掉元素子树里"最后输出的那段文本"的尾部空白,免得注入分隔符时冒出" ."" ;"这种空格+标点。"""
    while len(el):
        last = el[-1]
        if last.tail is None or last.tail.strip() == "":
            if last.tail:
                last.tail = ""
            el = last  # 纯空白 tail,继续往最后一个子元素里钻
        else:
            last.tail = last.tail.rstrip()
            return
    if el.text and el.text.strip() and el.text != el.text.rstrip():
        el.text = el.text.rstrip()


def _separate_element_citations(doc) -> None:
    """给每条 element-citation 的相邻子元素之间注入分隔标点(改写 tail,穿过 XSLT 保留)。"""
    for ec in doc.xpath("//*[local-name()='element-citation']"):
        # JATS 中每个作者独立标记；预览时补分隔符，避免相邻姓名粘连。
        for group in ec.findall("person-group"):
            names = [n for n in group if isinstance(n.tag, str) and n.tag in {"name", "string-name", "collab"}]
            for name in names[:-1]:
                if not (name.tail or "").strip():
                    name.tail = ", "
        children = [c for c in ec if isinstance(c.tag, str)]
        prev = None
        prev_tag = None
        for c in children:
            cur_tag = etree.QName(c).localname
            if prev is not None:
                sep = _cite_sep(prev_tag, cur_tag)
                tail = prev.tail or ""
                if tail.strip() == "":
                    _strip_trailing_ws(prev)  # 先抹掉前一元素尾部空白,再接分隔符
                    prev.tail = sep
                else:
                    prev.tail = tail.rstrip() + sep  # tail 夹了实义文本则保留
            prev, prev_tag = c, cur_tag


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


def _link_unsupported_images(result) -> None:
    """EMF 无浏览器原生支持：明确给出原文件入口，不显示无解释的破图。"""
    for img in result.xpath("//*[local-name()='img']"):
        source = img.get("src", "")
        if not source.lower().endswith(".emf"):
            continue
        img.tag = "a"
        img.attrib.clear()
        img.set("href", source)
        img.set("download", "")
        img.set("class", "w2j-media-fallback")
        img.text = "EMF 图像暂不支持网页预览，请下载原文件查看（%s）" % source.rsplit("/", 1)[-1]


_CITATION_P_RE = re.compile(r'(<p class="citation">)(.*?)(</p>)', re.DOTALL)
_WS_BEFORE_PUNCT_RE = re.compile(r"\s+([.;,])")
_DOUBLE_DOT_RE = re.compile(r"(?<!\.)\.\.(?!\.)")  # 只并两点,放过省略号"..."
_QBANG_DOT_RE = re.compile(r"([?!])\.(?=\s)")       # "effective?. " → "effective? "


def _tidy_citation_spacing(html: str) -> str:
    """收尾:参考文献里若个别子元素渲染为空(如空的 <etal/>),会在注入的句点/分号前留下空白,
    形成" ."" ;";若某子元素文本本就以句末标点收尾(如 <edition>"2 ed."),再叠我注入的". "又会
    成"..""?."。只在 citation 段内收紧:空白+标点→标点、冗余双句点→单句点(省略号"..."原样保留)、
    "?."/"!."→"?""!"。标点前无空白且非叠加的正常情形(DOI、标题冒号、"2019; 26"等)一律不受影响。"""
    def fix(m):
        body = m.group(2)
        body = _WS_BEFORE_PUNCT_RE.sub(r"\1", body)
        body = _DOUBLE_DOT_RE.sub(".", body)
        body = _QBANG_DOT_RE.sub(r"\1", body)
        return m.group(1) + body + m.group(3)
    return _CITATION_P_RE.sub(fix, html)


def _demote_mathml(html: str) -> str:
    """去掉 MathML 的 mml: 前缀并设默认命名空间，让浏览器原生渲染。"""
    html = html.replace("<mml:", "<").replace("</mml:", "</")
    html = html.replace('xmlns:mml="http://www.w3.org/1998/Math/MathML"',
                        'xmlns="http://www.w3.org/1998/Math/MathML"')
    return html


def _strip_stylesheet_warnings(html: str) -> str:
    """去掉 NCBI 样式表的诊断提示 span（class="warning"，如
    "{ label (or @symbol) needed for fn[@id='fn1'] }"）——这类提示是样式表对某些 JATS
    模式的挑剔（我方作者注脚注与结构参考逐字一致、并非缺陷），不应泄露进读者可见的预览。"""
    return re.sub(r'<span class="warning">[^<]*</span>', "", html)


# 预览样式补丁：NCBI 样式表缺 img 限宽（大图会撑爆版式）、ORCID 以裸 URL 呈现（观感糙）。
_PREVIEW_CSS = (
    "<style>"
    "img,svg{max-width:100%;height:auto;}"
    "table{max-width:100%;}"
    "body{overflow-x:auto;}"
    ".w2j-media-fallback{display:block;margin:8px 0;padding:10px;border:1px solid #c6d4ce;color:#164d50;}"
    ".w2j-orcid{display:inline-block;font-size:.7em;font-weight:700;vertical-align:super;"
    "color:#fff;background:#A6CE39;text-decoration:none;border-radius:3px;padding:0 3px;"
    "margin-right:3px;line-height:1.5;}"
    "</style>"
)
_ORCID_RE = re.compile(
    r'<span class="generated">\[</span>'
    r'((?:https?://orcid\.org/)?[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9Xx])'
    r'<span class="generated">\] </span>'
)


def _polish_preview(html: str) -> str:
    """给预览补两处样式：① 图片/表格限宽防大图撑爆版式；② ORCID 裸 URL → 小号绿色 iD 链接。"""
    def link(match):
        value = match.group(1)
        href = value if value.startswith('http') else 'https://orcid.org/' + value
        return f'<a class="w2j-orcid" href="{href}" target="_blank" rel="noopener" title="ORCID iD">iD</a> '
    html = _ORCID_RE.sub(link, html)
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
    _expand_multi_xrefs(doc)
    _separate_element_citations(doc)
    transform = _get_transform()
    result = transform(doc, css=etree.XSLT.strparam(css_href))
    for body in result.xpath('//*[local-name()="body"]'):
        body.set("data-w2j-preview", "true")
    _strip_diagnostic_front(result)
    _link_unsupported_images(result)
    return _polish_preview(_tidy_citation_spacing(_strip_stylesheet_warnings(_demote_mathml(str(result)))))
