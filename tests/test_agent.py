"""Agent 视觉闭环——确定性核心的单元测试(不需要 GPU/模型)。

闭环里依赖模型的部分(视觉清点、LLM 调和、看图建表)无法在 CI 离线断言,但其**骨架**
——渲染、XML 结构提纲、确定性调和(计数差→findings)、题注定位/插入——都是确定性的,
必须有回归保障。这正是"启发式/确定性打底 + 模型增强"在测试层面的体现。
"""

import os
import shutil

import pytest
from lxml import etree

from conftest import SAMPLES
from word2jats.agent import outline, reconcile, repair
from word2jats.pipeline import ConvertOptions, convert


def _convert_sample3(tmp_path):
    docx = os.path.join(SAMPLES, "样例3/第一组/初始文件.docx")
    fig = os.path.join(SAMPLES, "样例3/第一组/figures.zip")
    if not os.path.exists(docx):
        pytest.skip("样例缺失")
    r = convert(ConvertOptions(docx_path=docx, out_dir=str(tmp_path),
                               journal_id="JIN", doi="10.31083/JIN49347",
                               figures_path=fig))
    with open(r.xml_path, "rb") as f:
        return f.read()


def test_outline_counts_and_text(tmp_path):
    xml = _convert_sample3(tmp_path)
    c, blocks, text = outline.outline_from_xml(xml)
    assert c["title"] == 1
    assert c["authors"] >= 1
    assert c["references"] >= 1
    assert "<title>" in text and "<heading>" in text  # 文本提纲成形
    assert isinstance(c["xref_by_type"], dict)


def test_reconcile_detects_missing_tables(tmp_path):
    xml = _convert_sample3(tmp_path)
    root = etree.fromstring(xml)
    n_xml_tab = len(root.findall(".//table-wrap"))
    # 合成视觉证据:页面看到比 XML 多 3 张带号表 → 必须报 missing_table
    visual = {"tables": [{"label": "Table %d" % i, "rows": 8, "cols": 4,
                          "is_image": True, "page": i + 3}
                         for i in range(1, n_xml_tab + 4)],
              "figures": [], "display_equations": 0, "reference_items": 0,
              "author_block_pages": [], "per_page": []}
    findings = reconcile.deterministic_findings(visual, root)
    missing = [f for f in findings if f["type"] == "missing_table"]
    assert len(missing) >= 3
    assert all(f["action"] == "rebuild_table" for f in missing)


def test_reconcile_missing_authors(tmp_path):
    # XML 无作者 + 页面有作者块 → missing_authors / extract_authors
    root = etree.fromstring(
        b"<article><front/><body><sec><title>X</title><p>t</p></sec></body></article>")
    visual = {"tables": [], "figures": [], "display_equations": 0,
              "reference_items": 0, "author_block_pages": [1], "per_page": []}
    findings = reconcile.deterministic_findings(visual, root)
    assert any(f["action"] == "extract_authors" for f in findings)


def test_caption_paragraph_strict_only():
    body = etree.fromstring(
        "<body><sec><p>As shown in Table 1, results vary.</p>"
        "<p>Table 1. Baseline characteristics of the cohort.</p></sec></body>")
    p = repair._caption_paragraph_for_table(body, 1)
    assert p is not None
    assert "".join(p.itertext()).startswith("Table 1.")  # 命中题注而非行内提及


def test_validator_dtd_loads():
    """DTD 必须能加载(回归:含中文的绝对路径在老 libxml2 上曾导致 DTD 静默加载失败,
    使所有校验退化为'跳过';validator 改用切目录+相对名加载后须在任意路径下都能加载)。"""
    from word2jats.validate.validator import Validator
    v = Validator()
    assert v._dtd is not None, "DTD 未加载: %s" % v.load_error


@pytest.mark.skipif(not shutil.which("soffice"), reason="无 soffice,跳过渲染测试")
def test_render_pages(tmp_path):
    from word2jats.agent.render import render_pages
    docx = os.path.join(SAMPLES, "样例1/第一组/初始word.docx")
    if not os.path.exists(docx):
        pytest.skip("样例缺失")
    pages = render_pages(docx, str(tmp_path), dpi=100)
    assert pages and all(p.endswith(".png") and os.path.exists(p) for p in pages)
