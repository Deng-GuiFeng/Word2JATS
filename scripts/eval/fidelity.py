"""L1 忠实层(设计 §6.2):对源 docx 守"只加结构、不改内容"。

词多重集守恒,两个口径:
  raw      口径:输出 vs docx 的原始差集(不依赖参考,任何新样例可跑)。
  adjusted 口径(主口径):以冻结的 结构参考.xml 为"应保留内容"的仲裁——
    真丢失 = (docx有·输出无) − (docx有·参考也无)   参考同样不承载的(通讯地址块/页眉),不算丢
    真编造 = (输出有·docx无) − (参考有·docx无)     参考同样补的(B 档模板/label),不算编造
  这把 §6.2 的"允许清单"从手工维护数据升级为"由冻结参考确定性推出",无需逐样例手编。
B-档网络补全内容(ext-link/pub-id/contrib-id 子树,如 CrossRef DOI)不入编造,单列 b_additions。
图片:输出外部化文件须存在;与 figures.zip 同名文件逐字节 md5 比对。
100% 确定性。
"""
import hashlib
import os
import re
import zipfile
from collections import Counter
from difflib import get_close_matches

from lxml import etree

from .normalize import tokens

W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
M_T = "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_HDRFTR = re.compile(r"word/(header|footer)\d*\.xml$")
_PARSER = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)

# 这些子树是 B-档网络/标识内容(DOI 链接、ORCID URL 等),允许补全,不入"编造"口径
_BNET_TAGS = {"ext-link", "pub-id", "contrib-id", "uri"}


def docx_text(docx_path):
    """docx 全文(document+footnotes+endnotes 的 w:t 与公式 m:t),供 L2 判 DOI 是否 docx 自带。"""
    z = zipfile.ZipFile(docx_path)
    parts = []
    for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
        try:
            root = etree.fromstring(z.read(name))
        except KeyError:
            continue
        parts.extend(el.text or "" for el in root.iter(W_T, M_T))
    return " ".join(parts)


def _docx_tokens(docx_path):
    """(正文词多重集, 页眉页脚词多重集)。页眉页脚只用于豁免"编造",不用于判"丢失"。"""
    z = zipfile.ZipFile(docx_path)
    main, aux = Counter(), Counter()

    def feed(name, into):
        try:
            root = etree.fromstring(z.read(name))
        except KeyError:
            return
        for el in root.iter(W_T, M_T):
            into.update(tokens(el.text or ""))

    feed("word/document.xml", main)
    feed("word/footnotes.xml", main)
    feed("word/endnotes.xml", main)
    for n in z.namelist():
        if _HDRFTR.match(n):
            feed(n, aux)
    return main, aux


def _xml_tokens(xml_path):
    """(主内容词多重集, B-档网络子树词多重集)。"""
    root = etree.parse(xml_path, _PARSER).getroot()
    main, bnet = Counter(), Counter()

    def walk(el, in_bnet):
        if not isinstance(el.tag, str):
            return
        b = in_bnet or etree.QName(el).localname in _BNET_TAGS
        if el.text:
            (bnet if b else main).update(tokens(el.text))
        for c in el:
            walk(c, b)
            if c.tail:  # tail 属于父上下文
                (bnet if in_bnet else main).update(tokens(c.tail))

    walk(root, False)
    return main, bnet


def _is_content(tok):
    """内容词判定:忠实口径只看'内容词'。纯数字(角标/年份/表格数/区间展开)与单字符是
    结构化天然产物、噪声极大,不入丢失/编造(它们的完整性由 L2 计数/xref 结构性把关)。"""
    return len(tok) >= 2 and not tok.isdigit()


def _content_only(counter):
    return Counter({t: n for t, n in counter.items() if _is_content(t)})


def _items(counter, limit=None):
    out = [{"token": t, "n": n} for t, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out[:limit] if limit else out


def _check_images(sample, out_xml_root, out_dir):
    zmd5 = {}
    if sample.figures_zip:
        z = zipfile.ZipFile(sample.figures_zip)
        zmd5 = {os.path.basename(n): hashlib.md5(z.read(n)).hexdigest()
                for n in z.namelist() if not n.endswith("/")}
    seen, out = set(), []
    for g in out_xml_root.iter("{*}graphic"):
        href = g.get(XLINK_HREF) or ""
        if not href or href in seen:
            continue
        seen.add(href)
        p = os.path.join(out_dir, href)
        exists = os.path.exists(p)
        m = None  # None = figures.zip 无同名文件,不可比
        base = os.path.basename(href)
        if exists and base in zmd5:
            with open(p, "rb") as f:
                m = hashlib.md5(f.read()).hexdigest() == zmd5[base]
        out.append({"href": href, "exists": exists, "md5_match": m})
    return out


def run(sample, out_xml, out_dir):
    """返回 L1 结果:真丢失/真编造/疑似改写对/图片核对/缺陷数(adjusted 主口径)。"""
    D, D_aux = _docx_tokens(sample.docx)
    X, _X_bnet = _xml_tokens(out_xml)
    R, _R_bnet = _xml_tokens(sample.ref_xml)

    lost_raw = D - X
    fab_raw = X - (D + D_aux)
    lost_true = _content_only(lost_raw - (D - R))          # 参考也没保留的,不算丢
    fab_true = _content_only(fab_raw - (R - (D + D_aux)))  # 参考也补的(模板/label/B档),不算编造

    # 疑似"改字"配对:真丢失词与真编造词词形相近 → 提示为改写对(信息,不另计缺陷)
    fab_keys = list(fab_true)
    altered = []
    for w in sorted(lost_true):
        near = get_close_matches(w, fab_keys, n=1, cutoff=0.78)
        if near:
            altered.append({"docx": w, "out": near[0]})

    out_root = etree.parse(out_xml, _PARSER).getroot()
    images = _check_images(sample, out_root, out_dir)
    n_img_bad = sum(1 for i in images if (not i["exists"]) or i["md5_match"] is False)

    return {
        "lost": _items(lost_true),
        "fabricated": _items(fab_true),
        "altered_pairs": altered,
        "raw": {"lost_kinds": len(lost_raw), "fabricated_kinds": len(fab_raw)},
        "b_additions": _items(_X_bnet - _R_bnet - D, limit=30),
        "images": images,
        "defect_n": len(lost_true) + len(fab_true) + n_img_bad,
    }
