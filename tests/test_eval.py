"""评测器自身的回归测试(此前评测无 pytest 覆盖)。

锁定四条不变量,任何改动破坏其一即报:
1. 参考 vs 自身 → L2 零缺陷(比对器自洽,无假阳)。
2. 覆盖守门 → 零未覆盖(参考每种元素都有明确处置,无静默漏检)。
3. 参考自身 → L0 零 error(参考是合法 JATS 1.3)。
4. L2 确定性:同输入两跑结果完全一致(无 AI/无随机进回路)。
5. 图片包闭合:每个 graphic 指向的文件都在 figures.zip 里,且字节与 docx 内嵌媒体一致。
"""
import json
import os

import pytest

from eval import samples as S
from eval import structure, validity

ALL = S.SAMPLES
IDS = [s.key for s in ALL]


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_reference_self_compare_zero(smp):
    """参考与自身对位应零缺陷(含覆盖守门零未覆盖)。"""
    r = structure.run(smp, smp.ref_xml)
    assert r["defect_n"] == 0, "自比出现缺陷: %s" % [
        (c["cat"], c["defects"][:2]) for c in r["by_category"] if c["defects"]]
    cov = [c for c in r["by_category"] if c["cat"] == "覆盖守门"][0]
    assert not cov["defects"], "覆盖守门发现未覆盖元素: %s" % [d["key"] for d in cov["defects"]]


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_reference_L0_no_error(smp):
    """冻结的参考必须过 L0(0 error;warning/info 允许)。"""
    res = validity.check(smp.ref_xml)
    errs = [v for v in res["violations"] if v["severity"] == "error"]
    assert res["ok"] and not errs, "参考 L0 出错: %s" % errs[:3]


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_L2_deterministic(smp):
    """L2 两跑逐字节一致(确定性,无随机/无 AI)。"""
    a = structure.run(smp, smp.ref_xml)
    b = structure.run(smp, smp.ref_xml)
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == \
        json.dumps(b, sort_keys=True, ensure_ascii=False)


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_figures_closed_and_byte_identical(smp):
    """金标准的图片包闭合:参考里每个 graphic 指向的文件都在 figures.zip 里,
    且字节与 docx 内嵌媒体逐字节相同(不重编码、不换图);包内无冗余成员。"""
    import hashlib
    import zipfile

    from lxml import etree

    zpath = os.path.join(smp.dir, "figures.zip")
    root = etree.parse(smp.ref_xml).getroot()
    hrefs = [g.get("{http://www.w3.org/1999/xlink}href")
             for g in root.iter("{*}graphic")]
    hrefs += [g.get("{http://www.w3.org/1999/xlink}href")
              for g in root.iter("{*}inline-graphic")]
    hrefs = [h for h in hrefs if h]
    if not hrefs:
        return

    assert os.path.exists(zpath), "参考引用了图片但缺 figures.zip"
    with zipfile.ZipFile(smp.docx) as dz:
        docx_md5 = {hashlib.md5(dz.read(n)).hexdigest()
                    for n in dz.namelist() if n.startswith("word/media/")}
    with zipfile.ZipFile(zpath) as fz:
        members = {n.rsplit("/", 1)[-1]: fz.read(n)
                   for n in fz.namelist() if not n.endswith("/")}

    for h in hrefs:
        name = os.path.basename(h)
        assert name in members, "%s 引用的 %s 不在 figures.zip 里" % (smp.key, h)
        assert hashlib.md5(members[name]).hexdigest() in docx_md5, \
            "%s 的 %s 不是 docx 内嵌媒体的原始字节" % (smp.key, h)

    referenced = {os.path.basename(h) for h in hrefs}
    extra = sorted(set(members) - referenced)
    assert not extra, "%s 的 figures.zip 有未被引用的冗余成员:%s" % (smp.key, extra)
