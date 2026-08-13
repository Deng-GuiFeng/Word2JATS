"""评测器的**灵敏度与特异性**回归:证明它能发现错误、且不误伤等价形。

`test_eval.py` 锁的是自洽(参考与自身比零缺陷、参考本身合法、两跑一致)。自洽是必要条件、
不是充分条件——一个永远返回"零缺陷"的比对器同样满足它。2026-08 的外部审查正是这样打穿的:
当时 17 个"保持 XML 合法、尽量保持词多重集不变"的单点破坏,评测器只报出 5 个;而一次纯
id 重命名(引用关系与可见内容全不变)却被误判 97 条。

所以这里做两件事:
  **变异测试**  对金标准做单点破坏,断言"必须有人报出来"。破坏都挑那种能骗过粗粒度检查的:
                换位不换内容(词多重集不变)、改属性不改文字、字节换图不换文件名。
  **等价测试**  对金标准做语义等价的改写,断言"一条都不许报"。id 名字、缩进这些是转换器的
                自由,拿它扣分等于逼转换器去猜委员会的内部命名。
新增比较器时,配一条变异用例;放宽归一化时,配一条等价用例。
"""
import os
import shutil
import zipfile

import pytest
from lxml import etree

from eval import samples as S
from eval import fidelity, structure, validity

P = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
DOCTYPE = ('<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.3 '
           '20210610//EN" "https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd">')


def _ln(e):
    return etree.QName(e).localname


# ============================ 破坏性变异 ============================
def m_author_swap(r):
    """调换前两位作者:词多重集完全不变,只有比顺序才看得见。"""
    cg = r.find(".//{*}contrib-group")
    cs = [c for c in cg if isinstance(c.tag, str) and _ln(c) == "contrib"]
    i = list(cg).index(cs[0])
    cg.remove(cs[1])
    cg.insert(i, cs[1])
    return True


def m_addr_move(r):
    """把地址整块改挂到另一位作者名下:词多重集不变,只有比归属才看得见。"""
    cs = [c for c in r.iter("{*}contrib") if c.get("contrib-type") == "author"]
    for c in cs:
        ad = c.find("{*}address")
        if ad is not None:
            c.remove(ad)
            for o in cs:
                if o is not c:
                    o.append(ad)
                    return True
    return False


def m_sec_swap(r):
    """调换 body 下前两个同级 sec。"""
    body = r.find("{*}body")
    secs = [s for s in body if isinstance(s.tag, str) and _ln(s) == "sec"]
    if len(secs) < 2:
        return False
    i = list(body).index(secs[0])
    body.remove(secs[1])
    body.insert(i, secs[1])
    return True


def m_sec_reparent(r):
    """把一个子节从它的父节提到 body 顶层:层级归属变了,标题集合没变。"""
    body = r.find("{*}body")
    for sec in body.findall("{*}sec"):
        sub = sec.findall("{*}sec")
        if sub:
            sec.remove(sub[0])
            body.append(sub[0])
            return True
    return False


def m_xref_drop(r):
    """删掉一处 xref 外壳但保留可见文字(目标仍被别处引用):按去重集合比就发现不了。"""
    from collections import Counter
    c = Counter(x.get("rid") for x in r.iter("{*}xref"))
    dup = [k for k, n in c.items() if n >= 2]
    if not dup:
        return False
    for x in r.iter("{*}xref"):
        if x.get("rid") == dup[0]:
            p = x.getparent()
            i = list(p).index(x)
            txt = "".join(x.itertext())
            if i == 0:
                p.text = (p.text or "") + txt + (x.tail or "")
            else:
                p[i - 1].tail = (p[i - 1].tail or "") + txt + (x.tail or "")
            p.remove(x)
            return True
    return False


def m_bold2italic(r):
    """把一处 bold 改成 italic:文字一个不差。"""
    for b in r.iter("{*}bold"):
        b.tag = "{%s}italic" % etree.QName(b).namespace if etree.QName(b).namespace else "italic"
        return True
    return False


def m_issn_swap(r):
    """对调两种 ISSN 的 pub-type:只比 ISSN 文本集合就发现不了。"""
    iss = list(r.iter("{*}issn"))
    if len(iss) < 2 or iss[0].get("pub-type") == iss[1].get("pub-type"):
        return False
    a, b = iss[0].get("pub-type"), iss[1].get("pub-type")
    if a is None or b is None:
        return False
    iss[0].set("pub-type", b)
    iss[1].set("pub-type", a)
    return True


def m_mfrac2mrow(r):
    """把 mfrac 换成 mrow:字符序列一模一样,运算树变了。"""
    for f in r.iter("{http://www.w3.org/1998/Math/MathML}mfrac"):
        f.tag = "{http://www.w3.org/1998/Math/MathML}mrow"
        return True
    return False


def m_cell_swap(r):
    """调换同一表格里两个单元格的内容。"""
    for t in r.iter("{*}table"):
        tds = [x for x in t.iter("{*}td") if "".join(x.itertext()).strip()]
        if len(tds) >= 2:
            a, b = tds[0], tds[1]
            at, ac = a.text, list(a)
            a.text, b.text = b.text, at
            bc = list(b)
            for x in ac:
                a.remove(x)
            for x in bc:
                b.remove(x)
            for x in bc:
                a.append(x)
            for x in ac:
                b.append(x)
            return True
    return False


def m_cell_number(r):
    """把一个表格单元格的数值改掉:纯数字过去被排除出忠实口径,压根不查。"""
    import re
    for td in r.iter("{*}td"):
        s = (td.text or "").strip()
        if s and re.fullmatch(r"[\d.]+", s):
            td.text = "999.9"
            return True
    return False


def m_rowspan(r):
    """把 rowspan 改错:合并关系变了,单元格个数没变。"""
    for c in r.iter():
        if isinstance(c.tag, str) and _ln(c) in ("td", "th") and c.get("rowspan"):
            c.set("rowspan", "99")
            return True
    return False


def m_ref_doi(r):
    """把一条文献的 DOI 改错:ext-link/pub-id 子树不入忠实口径,只能靠字段比。"""
    for p in r.iter("{*}pub-id"):
        if p.get("pub-id-type") == "doi":
            p.text = "10.9999/BOGUS.0000"
            return True
    return False


def m_ref_swap(r):
    """调换两条参考文献的内容(各自保留 label)。"""
    rl = r.find(".//{*}ref-list")
    refs = [x for x in rl if isinstance(x.tag, str) and _ln(x) == "ref"]
    if len(refs) < 2:
        return False

    def cit(x):
        for c in x:
            if isinstance(c.tag, str) and _ln(c) in ("element-citation", "mixed-citation"):
                return c
        return None
    a, b = cit(refs[0]), cit(refs[1])
    if a is None or b is None:
        return False
    ia, ib = list(refs[0]).index(a), list(refs[1]).index(b)
    refs[0].remove(a)
    refs[1].remove(b)
    refs[0].insert(ia, b)
    refs[1].insert(ib, a)
    return True


def m_ref_author_swap(r):
    """调换一条文献里前两位作者的次序:姓氏集合不变。"""
    for pg in r.iter("{*}person-group"):
        names = [n for n in pg if isinstance(n.tag, str) and _ln(n) == "name"]
        if len(names) >= 2:
            i = list(pg).index(names[0])
            pg.remove(names[1])
            pg.insert(i, names[1])
            return True
    return False


def m_abstract_type(r):
    """改第二份摘要的 abstract-type:只读第一份摘要就发现不了。"""
    abs_ = list(r.iter("{*}abstract"))
    if len(abs_) < 2:
        return False
    abs_[1].set("abstract-type", "teaser")
    return True


def m_abstract_drop(r):
    """删掉图文摘要(它往往没有文字,词多重集几乎不变)。"""
    for ab in r.iter("{*}abstract"):
        if ab.get("abstract-type") == "graphical":
            ab.getparent().remove(ab)
            return True
    return False


def m_graphic_swap(r):
    """两张图的 href 对调:文件都在、都能解码,只有比字节才发现张冠李戴。"""
    gs = list(r.iter("{*}graphic"))
    if len(gs) < 2:
        return False
    k = "{http://www.w3.org/1999/xlink}href"
    a, b = gs[0].get(k), gs[1].get(k)
    if a == b:
        return False
    gs[0].set(k, b)
    gs[1].set(k, a)
    return True


def m_aff_rebind(r):
    """把作者的单位关联改指向另一个 aff。"""
    affs = [a.get("id") for a in r.iter("{*}aff") if a.get("id")]
    if len(affs) < 2:
        return False
    for c in r.iter("{*}contrib"):
        for x in c.iter("{*}xref"):
            if x.get("ref-type") == "aff":
                other = [a for a in affs if a != x.get("rid")]
                if other:
                    x.set("rid", other[0])
                    return True
    return False


def m_xref_rebind(r):
    """把一处正文引用改指向另一条文献:引用仍闭合,指错了对象。"""
    refs = [x.get("id") for x in r.iter("{*}ref") if x.get("id")]
    if len(refs) < 2:
        return False
    for x in r.iter("{*}xref"):
        if x.get("ref-type") == "bibr":
            other = [a for a in refs if a != x.get("rid")]
            if other:
                x.set("rid", other[-1])
                return True
    return False


MUTATIONS = [
    ("01", m_author_swap), ("01", m_addr_move), ("01", m_sec_swap), ("01", m_sec_reparent),
    ("01", m_xref_drop), ("01", m_bold2italic), ("01", m_issn_swap), ("01", m_rowspan),
    ("01", m_aff_rebind), ("01", m_graphic_swap), ("01", m_xref_rebind),
    ("02", m_sec_swap), ("02", m_ref_author_swap),
    ("03", m_mfrac2mrow), ("03", m_cell_swap),
    ("05", m_cell_swap), ("05", m_cell_number), ("05", m_ref_doi),
    ("S03", m_ref_swap), ("S03", m_abstract_type),
    ("S05", m_abstract_drop), ("S05", m_graphic_swap),
    ("X02", m_abstract_drop), ("X03", m_ref_swap), ("X04", m_ref_swap),
]

# ============================ 等价变换 ============================


def e_id_rename(r):
    """全量重命名 id 并同步 rid:引用关系与可见内容一字不变。
    id 是文档内部标识,委员会取 aff1、转换器取 A1 都对,拿它扣分等于逼转换器猜命名。"""
    ids, n = {}, 0
    for el in r.iter():
        if not isinstance(el.tag, str):
            continue
        i = el.get("id")
        if i:
            n += 1
            ids[i] = "zz%04d" % n
            el.set("id", ids[i])
    for el in r.iter():
        if not isinstance(el.tag, str):
            continue
        rid = el.get("rid")
        if rid:
            el.set("rid", " ".join(ids.get(t, t) for t in rid.split()))
    return True


def e_split_bold(r):
    """把一处 <bold>A B</bold> 拆成 <bold>A</bold> <bold>B</bold>:渲染与语义一致。"""
    for b in r.iter("{*}bold"):
        t = (b.text or "").strip()
        if " " in t and len(list(b)) == 0:
            head, _, tail = t.partition(" ")
            b.text = head
            sib = etree.SubElement(b.getparent(), b.tag)
            sib.text = tail
            b.getparent().remove(sib)
            b.getparent().insert(list(b.getparent()).index(b) + 1, sib)
            sib.tail = b.tail
            b.tail = " "
            return True
    return False


def e_mrow_wrap(r):
    """给一个 MathML 子式套一层多余的 <mrow>:W3C 规范里 mrow 只作分组,套一层是等价形。"""
    MM = "http://www.w3.org/1998/Math/MathML"
    for msup in r.iter("{%s}msup" % MM):
        kids = list(msup)
        if len(kids) == 2 and _ln(kids[0]) != "mrow":
            wrap = etree.Element("{%s}mrow" % MM)
            msup.replace(kids[0], wrap)
            wrap.append(kids[0])
            return True
    return False


def e_reindent(r):
    """改动缩进空白:XML 的可忽略空白不是内容。"""
    n = 0
    for el in r.iter():
        if isinstance(el.tag, str) and el.tail and not el.tail.strip():
            el.tail = "\n" + " " * ((n % 5) + 1)
            n += 1
    return n > 0


EQUIVALENCES = [
    ("01", e_id_rename), ("01", e_reindent), ("01", e_mrow_wrap),
    ("03", e_mrow_wrap), ("04", e_split_bold), ("S03", e_id_rename), ("X03", e_id_rename),
]


# ============================ 执行 ============================
def _apply(key, fn, tmp_path):
    """对金标准施加一次改动,写到临时文件;返回路径(改动不适用则 None)。"""
    tree = etree.parse(S.get(key).ref_xml, P)
    if not fn(tree.getroot()):
        return None
    p = str(tmp_path / ("%s_%s.xml" % (key, fn.__name__)))
    tree.write(p, encoding="utf-8", xml_declaration=True, doctype=DOCTYPE)
    return p


def _figures_dir(key, tmp_path):
    """把金标准的 figures.zip 摊成目录,当作"转换输出的媒体目录"喂给评测器。"""
    smp = S.get(key)
    d = tmp_path / "media"
    d.mkdir(exist_ok=True)
    if os.path.exists(smp.figures_zip):
        with zipfile.ZipFile(smp.figures_zip) as z:
            for n in z.namelist():
                if not n.endswith("/"):
                    with open(str(d / os.path.basename(n)), "wb") as f:
                        f.write(z.read(n))
    return str(d)


def _total(key, xml_path, out_dir=None):
    smp = S.get(key)
    l0 = validity.check(xml_path)
    n0 = sum(1 for v in l0["violations"] if v["severity"] == "error")
    return n0 + structure.run(smp, xml_path, out_dir)["defect_n"]


@pytest.mark.parametrize("key,fn", MUTATIONS, ids=[f"{k}-{f.__name__}" for k, f in MUTATIONS])
def test_mutation_is_caught(key, fn, tmp_path):
    """单点破坏必须被报出来。漏检 = 评测器在这个维度上是瞎的。"""
    p = _apply(key, fn, tmp_path)
    if p is None:
        pytest.skip("该变异对 %s 不适用" % key)
    out_dir = _figures_dir(key, tmp_path)
    assert _total(key, p, out_dir) > 0, \
        "%s 上的破坏 %s 未被任何一层报出——评测器在该维度失明" % (key, fn.__name__)


@pytest.mark.parametrize("key,fn", EQUIVALENCES, ids=[f"{k}-{f.__name__}" for k, f in EQUIVALENCES])
def test_equivalence_is_not_flagged(key, fn, tmp_path):
    """语义等价的改写一条都不许报。误报 = 逼转换器去迎合无关紧要的表示细节。"""
    p = _apply(key, fn, tmp_path)
    if p is None:
        pytest.skip("该等价变换对 %s 不适用" % key)
    out_dir = _figures_dir(key, tmp_path)
    n = _total(key, p, out_dir)
    assert n == 0, "%s 上的等价变换 %s 被误报 %d 条" % (key, fn.__name__, n)


def test_media_bytes_swapped_is_caught(tmp_path):
    """href 不变、字节换成另一张图:文件在、能解码、XML 一字不差,只有比字节才发现。"""
    key = "01"
    smp = S.get(key)
    d = _figures_dir(key, tmp_path)
    names = sorted(os.listdir(d))
    if len(names) < 2:
        pytest.skip("%s 图少于 2 张" % key)
    shutil.copyfile(os.path.join(d, names[1]), os.path.join(d, names[0]))
    assert structure.run(smp, smp.ref_xml, d)["defect_n"] > 0, \
        "图片字节被掉包却零缺陷——媒体只查了存在性,没查身份"


def test_fake_image_is_not_decodable(tmp_path):
    """只带 JPEG 魔数的空壳文件不算真图:魔数兜底不能给假货放行。"""
    p = tmp_path / "fake.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 240)
    assert not fidelity._decodable(str(p))


def test_l1_catches_dropped_text(tmp_path):
    """删掉一段正文,L1 必须报丢失。"""
    smp = S.get("01")
    tree = etree.parse(smp.ref_xml, P)
    for p in tree.getroot().iter("{*}p"):
        if len("".join(p.itertext())) > 200:
            p.getparent().remove(p)
            break
    x = str(tmp_path / "dropped.xml")
    tree.write(x, encoding="utf-8", xml_declaration=True, doctype=DOCTYPE)
    base = fidelity.run(smp, smp.ref_xml, _figures_dir("01", tmp_path))
    hurt = fidelity.run(smp, x, _figures_dir("01", tmp_path))
    assert hurt["n_lost"] > base["n_lost"], "删掉整段正文,L1 丢失数没涨"


def test_gold_free_does_not_use_reference(tmp_path):
    """gold_free 口径必须真的不看金标准:换一份参考,这一栏的数字不能变。
    消融把 n_fab 称作"gold-free 安全不变量",而主口径 gold_ref 是拿参考做仲裁的——
    两者混用,结论就站不住。这条测试把界限钉死。"""
    smp = S.get("02")
    out = os.path.join("reports", "eval", "latest", "02")
    if not os.path.isdir(out):
        pytest.skip("无现成输出可评")
    xmls = [f for f in os.listdir(out) if f.endswith(".xml")]
    if not xmls:
        pytest.skip("无现成输出可评")
    x = os.path.join(out, xmls[0])

    class Swapped:                      # 同一个 docx,换成别的样例的参考
        key, group, journal, doi = smp.key, smp.group, smp.journal, smp.doi
        docx, dir = smp.docx, smp.dir
        ref_xml = S.get("03").ref_xml
        figures_zip = smp.figures_zip

    a = fidelity.run(smp, x, out)["gold_free"]
    b = fidelity.run(Swapped(), x, out)["gold_free"]
    assert a == b, "换了参考,gold_free 口径跟着变了——它并不是 gold-free"
