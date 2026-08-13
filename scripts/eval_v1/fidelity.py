"""L1 忠实层(设计 §6.2):对源 docx 守"只加结构、不改内容"。

## 取词口径:两侧都按"块内拼合"

Word 常把一个词拆进多个 run(首字母单独成 run、拼写检查痕迹、局部改格式),逐 `<w:t>` 切词
会把 `Age` 切成 `A`+`ge`、`Keywords` 切成 `Key`+`w`+`ords`,虚报一堆丢失/编造。JATS 侧同样
会拆:`stat<italic>ins</italic>` 逐元素切就是 `stat`+`ins`。所以**两侧都拼合、且必须对称**:
  docx 侧 按 `<w:p>` 段落拼(制表符/换行当空格,保住词边界);
  XML  侧 按块级边界拼(内联元素 bold/italic/sup/xref… 并入当前块,`<break/>` 当空格)。
只拼一侧会造出新的假差——2026-08 实测:只拼 docx 侧,`Ca<sup>2+</sup>` 一类立刻虚报。

## 三个桶,各归各管

- **主内容**:正文文字。这是守恒口径唯一计缺陷的部分。
- **公式**(MathML 子树 / docx 的 `m:t` / tex-math):数学符号的正确性是**运算树**的事,
  由 L2 的公式类目比;塞进词多重集只会因表示差异虚报(`<mi>f</mi><mi>i</mi>` vs docx 的
  `fi`)。两侧同时分离,保持对称。
- **B 档网络**(ext-link/pub-id/contrib-id/uri):DOI 链接、ORCID URL 等允许补全,不入编造。

纯数字**单列但计缺陷**:它们过去被静默丢弃,理由是角标/编号是结构化天然产物。代价是
表格里 `281/343` 被改成 `999/999`、作者电话邮编整块丢失都查不出来——而数字恰恰是论文
最要命的内容。故改为单列 numeric 桶并照常计缺陷,结构性数字的噪声由参考仲裁口径吸收。

## 两个口径,都出,不混淆

- **gold_free**:只用 docx 与输出,不看金标准。任何新样例可跑,是真正的"安全不变量"口径。
- **gold_ref**(主口径,计入缺陷数):以冻结参考为"应保留内容"的仲裁——
    真丢失 = (docx有·输出无) − (docx有·参考也无)
    真编造 = (输出有·docx无) − (参考有·docx无)
  它扣掉了金标准同样不承载的部分(C 档编辑加工剔除项),噪声低;但**它用到了参考,
  就不是 gold-free**,消融实验若要宣称"不依赖金标准",必须引 gold_free 那一栏。

图片:L1 只验"外部化文件真实存在且可解码";图片**身份**(是不是这一张)由 L2 的媒体字节
类目按语义槽位比 SHA-256,不在此重复计缺陷。
100% 确定性。
"""
import os
import re
import zipfile
from collections import Counter
from difflib import get_close_matches

from lxml import etree

from .normalize import tokens

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W_T = _W_NS + "t"
W_P = _W_NS + "p"
M_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
M_T = M_NS + "t"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_HDRFTR = re.compile(r"word/(header|footer)\d*\.xml$")
_PARSER = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)

# 段内这些元素代表词边界(制表符/换行/软回车):拼合时当空格,否则 tab 分隔的词会粘连
_W_SEP = {_W_NS + "tab", _W_NS + "br", _W_NS + "cr"}

# JATS 内联元素:文本属于所在块,拼合时并入当前块(与 docx 的 run 拆分对称)
_INLINE = {"bold", "italic", "sup", "sub", "underline", "sc", "monospace", "overline", "strike",
           "roman", "sans-serif", "styled-content", "named-content", "xref", "ext-link", "uri",
           "email", "inline-formula", "inline-graphic", "target", "milestone-start",
           "milestone-end", "private-char", "abbrev", "x"}
# `<break/>` 是换行,是**词边界**不是粘合点:当空格处理,否则表格里 <break/> 分隔的多行会粘成
# `smokingldl` 这类怪词(2026-08 实测,一度虚报 30 条)
_XML_SEP = {"break"}
# B 档网络/标识子树(DOI 链接、ORCID URL 等),允许补全,不入"编造"口径
_BNET_TAGS = {"ext-link", "pub-id", "contrib-id", "uri"}


def _ln(el):
    return etree.QName(el).localname


def _para_text(p):
    """一个 <w:p> 内按文档顺序拼合 run 文本;制表符/换行按空格处理,保住词边界。"""
    parts = []
    for el in p.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        if tag == W_T:
            parts.append(el.text or "")
        elif tag in _W_SEP:
            parts.append(" ")
    return "".join(parts)


def _para_math_text(p):
    """段内 OMML 公式的文字(`m:t`),单独成桶,不与正文混。"""
    return "".join(t.text or "" for t in p.iter(M_T))


def _docx_tokens(docx_path):
    """(正文, 页眉页脚, 公式) 三个词多重集。页眉页脚只用于豁免"编造",不用于判"丢失"。"""
    z = zipfile.ZipFile(docx_path)
    main, aux, math = Counter(), Counter(), Counter()

    def feed(name, into):
        try:
            root = etree.fromstring(z.read(name))
        except KeyError:
            return
        for p in root.iter(W_P):
            into.update(tokens(_para_text(p)))
            math.update(tokens(_para_math_text(p)))

    feed("word/document.xml", main)
    feed("word/footnotes.xml", main)
    feed("word/endnotes.xml", main)
    for n in z.namelist():
        if _HDRFTR.match(n):
            feed(n, aux)
    return main, aux, math


def _xml_tokens(xml_path_or_root):
    """(主内容, B档网络子树, 公式) 三个词多重集。块内拼合:内联元素并入当前块。"""
    if isinstance(xml_path_or_root, str):
        root = etree.parse(xml_path_or_root, _PARSER).getroot()
    else:
        root = xml_path_or_root
    main, bnet, math = Counter(), Counter(), Counter()

    def subtree_text(el):
        return "".join(el.itertext())

    def walk(el, in_bnet):
        """把 el 这一"块"内的内联文本拼起来切词;遇到块级子元素则先冲刷再递归。"""
        buf = []

        def flush():
            if buf:
                (bnet if in_bnet else main).update(tokens("".join(buf)))
                del buf[:]

        if el.text:
            buf.append(el.text)
        for c in el:
            if not isinstance(c.tag, str):
                if c.tail:
                    buf.append(c.tail)
                continue
            name = _ln(c)
            ns = etree.QName(c).namespace
            if ns == MATHML_NS or name == "tex-math":
                # 公式内容单列:符号的正确性由 L2 比运算树,不进词多重集(见模块头)
                math.update(tokens(subtree_text(c)))
            elif name in _XML_SEP:
                buf.append(" ")
            elif name in _BNET_TAGS:
                bnet.update(tokens(subtree_text(c)))
            elif name in _INLINE:
                walk_inline(c, buf)
            else:
                flush()
                walk(c, in_bnet)
            if c.tail:
                buf.append(c.tail)
        flush()

    def walk_inline(el, buf):
        """内联元素:文本并入外层 buf(与 docx 的 run 拆分对称)。"""
        if el.text:
            buf.append(el.text)
        for c in el:
            if not isinstance(c.tag, str):
                if c.tail:
                    buf.append(c.tail)
                continue
            name = _ln(c)
            ns = etree.QName(c).namespace
            if ns == MATHML_NS or name == "tex-math":
                math.update(tokens(subtree_text(c)))
            elif name in _XML_SEP:
                buf.append(" ")
            elif name in _BNET_TAGS:
                bnet.update(tokens(subtree_text(c)))
            else:
                walk_inline(c, buf)
            if c.tail:
                buf.append(c.tail)

    walk(root, False)
    return main, bnet, math


def _split_kinds(counter):
    """把词多重集拆成 (文字词, 纯数字词)。单字符不入(角标/单位残片,信息量为零)。"""
    words = Counter({t: n for t, n in counter.items() if len(t) >= 2 and not t.isdigit()})
    nums = Counter({t: n for t, n in counter.items() if len(t) >= 2 and t.isdigit()})
    return words, nums


def _items(counter, limit=None):
    out = [{"token": t, "n": n} for t, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out[:limit] if limit else out


# 魔数兜底能认的格式,全部是 PIL 本来就支持的。所以**装了 PIL 就不给兜底**:
# 兜底的正当理由只有"PIL 不认识某种合法格式",而这张表里没有那样的格式;
# 保留 else 分支只为 PIL 未安装的环境。一个 243 字节、只有 \xff\xd8\xff 开头的空壳
# 骗得过魔数,骗不过解码器——过去它被判为真图(2026-08 实测)。
_MAGIC = ((b"\xff\xd8\xff", 3), (b"\x89PNG\r\n\x1a\n", 8), (b"II*\x00", 4), (b"MM\x00*", 4),
          (b"GIF87a", 6), (b"GIF89a", 6), (b"BM", 2))


def _decodable(path):
    """真图判定:能被真正解码。PIL 可用时以 PIL 为准,不可用才退到魔数 + 体积。"""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        try:
            with Image.open(path) as im:
                im.verify()
            return True
        except Exception:
            return False
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        size = os.path.getsize(path)
    except OSError:
        return False
    return any(head[:n] == m for m, n in _MAGIC) and size > 64


def _graphic_hrefs(root):
    s = {(g.get(XLINK_HREF) or "") for g in root.iter("{*}graphic")}
    s |= {(g.get(XLINK_HREF) or "") for g in root.iter("{*}inline-graphic")}
    return s - {""}


def _check_images(out_xml_root, out_dir):
    """逐个 graphic/inline-graphic 的 xlink:href:文件在 out_dir 存在 + 可被解码为真图。
    图片**是不是这一张**由 L2 媒体字节类目按语义槽位比 SHA-256,不在此判。"""
    seen, out = set(), []
    for g in out_xml_root.iter():
        if not isinstance(g.tag, str) or _ln(g) not in ("graphic", "inline-graphic"):
            continue
        href = g.get(XLINK_HREF) or ""
        if not href or href in seen:
            continue
        seen.add(href)
        p = os.path.join(out_dir, href) if out_dir else href
        exists = os.path.exists(p)
        out.append({"href": href, "exists": exists,
                    "decodable": _decodable(p) if exists else False})
    return out


def run(sample, out_xml, out_dir):
    """返回 L1 结果。主缺陷数取 gold_ref 口径;gold_free 同时给出,供"不依赖金标准"的论证用。"""
    D, D_aux, D_math = _docx_tokens(sample.docx)
    X, X_bnet, X_math = _xml_tokens(out_xml)
    R, R_bnet, R_math = _xml_tokens(sample.ref_xml)

    lost_raw = D - X
    fab_raw = X - (D + D_aux)
    lost_adj = lost_raw - (D - R)            # 参考也没保留的,不算丢
    fab_adj = fab_raw - (R - (D + D_aux))    # 参考也补的(模板/label/B档),不算编造

    lost_w, lost_num = _split_kinds(lost_adj)
    fab_w, fab_num = _split_kinds(fab_adj)
    free_lost_w, free_lost_num = _split_kinds(lost_raw)
    free_fab_w, free_fab_num = _split_kinds(fab_raw)
    # 公式桶:两侧都已从主内容分离,这里只作信息量提示(公式对错由 L2 比运算树)
    math_lost, _ = _split_kinds(D_math - X_math)
    math_fab, _ = _split_kinds(X_math - D_math)

    # 疑似"改字"配对:真丢失词与真编造词词形相近 → 提示为改写对(信息,不另计缺陷)
    fab_keys = list(fab_w)
    altered = []
    for w in sorted(lost_w):
        near = get_close_matches(w, fab_keys, n=1, cutoff=0.78)
        if near:
            altered.append({"docx": w, "out": near[0]})

    out_root = etree.parse(out_xml, _PARSER).getroot()
    ref_root = etree.parse(sample.ref_xml, _PARSER).getroot()
    images = _check_images(out_root, out_dir)
    n_img_bad = sum(1 for i in images if (not i["exists"]) or (not i["decodable"]))
    n_out_g, n_ref_g = len(_graphic_hrefs(out_root)), len(_graphic_hrefs(ref_root))

    n_lost = len(lost_w) + len(lost_num)
    n_fab = len(fab_w) + len(fab_num)
    return {
        "lost": _items(lost_w), "fabricated": _items(fab_w),
        "lost_numeric": _items(lost_num), "fabricated_numeric": _items(fab_num),
        "altered_pairs": altered,
        # gold_free:只用 docx 与输出。消融若要宣称"不依赖金标准",引这一栏
        "gold_free": {
            "n_lost": len(free_lost_w) + len(free_lost_num),
            "n_fabricated": len(free_fab_w) + len(free_fab_num),
            "lost": _items(free_lost_w, limit=40), "fabricated": _items(free_fab_w, limit=40),
            "lost_numeric": _items(free_lost_num, limit=20),
            "fabricated_numeric": _items(free_fab_num, limit=20),
        },
        "formula_tokens": {"lost": _items(math_lost, limit=20), "fabricated": _items(math_fab, limit=20),
                           "n_lost": len(math_lost), "n_fabricated": len(math_fab)},
        "b_additions": _items(X_bnet - R_bnet - D, limit=30),
        "images": images,
        "image_count": {"out": n_out_g, "ref": n_ref_g, "match": n_out_g == n_ref_g},
        "n_lost": n_lost, "n_fabricated": n_fab, "n_img_bad": n_img_bad,
        "defect_n": n_lost + n_fab + n_img_bad,
    }
