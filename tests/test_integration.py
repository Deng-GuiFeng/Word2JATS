"""集成测试：5 个官方样例端到端转换并通过 JATS 1.3 DTD 校验。

这是最强的回归保障——任何破坏结构合法性的改动都会被立即捕获。
"""

import os

import pytest

from conftest import SAMPLES
from word2jats.pipeline import ConvertOptions, convert

CASES = [
    ("样例1/第一组/初始word.docx", "样例1/第一组/figure.zip", "RCM", "10.31083/RCM46777"),
    ("样例2/第一组/初始文件.docx", "样例2/第一组/figures.zip", "RCM", "10.31083/RCM46175"),
    ("样例3/第一组/初始文件.docx", "样例3/第一组/figures.zip", "JIN", "10.31083/JIN49347"),
    ("样例4/第一组/初始文件.docx", "样例4/第一组/figures.zip", "JIN", "10.31083/JIN52316"),
    ("样例5/第一组/初始文件.docx", "样例5/第一组/figures.zip", "HSF", "10.31083/HSF49106"),
]


@pytest.mark.parametrize("docx,fig,journal,doi", CASES)
def test_sample_converts_and_validates(tmp_path, docx, fig, journal, doi):
    docx_p = os.path.join(SAMPLES, docx)
    fig_p = os.path.join(SAMPLES, fig)
    if not os.path.exists(docx_p):
        pytest.skip("样例缺失：%s" % docx)
    r = convert(ConvertOptions(docx_path=docx_p, out_dir=str(tmp_path),
                               journal_id=journal, doi=doi, figures_path=fig_p))
    assert r.validation is not None
    assert r.validation.well_formed, "XML 非良构"
    assert r.validation.dtd_valid, "DTD 校验未通过: %s" % r.validation.errors[:3]
    # 基本完整性：标题、作者、参考文献应被提取
    assert r.stats["authors"] >= 1
    assert r.stats["references"] >= 1
    assert os.path.exists(r.xml_path)
    # 出版规范检查(JATS4R 风格)不应有 high 级问题
    assert r.stats.get("jats4r", {}).get("high", 0) == 0
    # 结构一致性检查不应有 high 级问题(无悬空引用/空正文等)
    assert r.stats.get("checks", {}).get("high", 0) == 0


def test_runs_without_doi_or_figures(tmp_path):
    """泛化：不提供 DOI/图片包也应产出良构 XML（图片回退到 docx 内嵌）。"""
    docx_p = os.path.join(SAMPLES, "样例3/第一组/初始文件.docx")
    if not os.path.exists(docx_p):
        pytest.skip("样例缺失")
    r = convert(ConvertOptions(docx_path=docx_p, out_dir=str(tmp_path)))
    assert r.validation.well_formed


# 委员会补充的 5 篇"只有输入"的全新论文(held-out 泛化回归集)
NEW_SAMPLES = ["样例1", "样例2", "样例3", "样例4", "样例5"]


@pytest.mark.parametrize("name", NEW_SAMPLES)
def test_new_sample_generalization(tmp_path, name):
    """新样例(无金标准)端到端:必须 DTD 合规、无 high 级结构/出版规范问题。"""
    docx_p = os.path.join(SAMPLES, "补充案例-仅输入", "选题一", name + ".docx")
    if not os.path.exists(docx_p):
        pytest.skip("新样例缺失：%s" % name)
    r = convert(ConvertOptions(docx_path=docx_p, out_dir=str(tmp_path)))
    assert r.validation.dtd_valid, "DTD 未通过: %s" % r.validation.errors[:3]
    assert r.stats.get("checks", {}).get("high", 0) == 0
    assert r.stats.get("jats4r", {}).get("high", 0) == 0
    assert r.stats["authors"] >= 1 and r.stats["references"] >= 1


def test_new_sample_audit_fixes(tmp_path):
    """锁定两轮审计修复:抽查若干已修的可泛化点,防回归。"""
    import re
    from lxml import etree

    def root_of(name):
        docx = os.path.join(SAMPLES, "补充案例-仅输入", "选题一", name + ".docx")
        if not os.path.exists(docx):
            pytest.skip("新样例缺失")
        r = convert(ConvertOptions(docx_path=docx, out_dir=str(tmp_path / name)))
        t = re.sub(r"<!DOCTYPE.*?>", "", open(r.xml_path).read(), flags=re.S)
        return etree.fromstring(t.encode())

    r2 = root_of("样例2")
    # 交叉引用跳号零错位:每个 bibr xref 的目标 ref label == 其显示号
    id2lab = {x.get("id"): re.sub(r"\D", "", x.findtext("label") or "") for x in r2.findall(".//ref")}
    for x in r2.findall('.//xref[@ref-type="bibr"]'):
        disp = (x.text or "").strip()
        if disp:
            assert id2lab.get(x.get("rid"), "") == disp, "xref 跳号错位"
    assert len(r2.findall('.//contrib[@contrib-type="editor"]')) == 2

    r5 = root_of("样例5")
    # ORCID 按名匹配:Zhao 应有 ORCID(不被 Hao 子串误配)
    zhao = [c for c in r5.findall('.//contrib[@contrib-type="author"]')
            if c.findtext('.//surname') == "Zhao"]
    assert zhao and zhao[0].find(".//contrib-id") is not None
