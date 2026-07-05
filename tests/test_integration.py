"""集成测试:10 个样例端到端转换并通过 JATS 1.3 DTD 校验。

这是最强的回归保障——任何破坏结构合法性的改动都会被立即捕获。
样例清单直接取自评测的样例登记表(scripts/eval/samples.py),与评测同一数据源,
不再各自硬编路径(旧 `样例/样例N/第一组/` 布局已改为 `样例数据/<key>/`)。
"""
import os

import pytest

from eval import samples as S  # conftest 已把 scripts/ 加入 sys.path
from word2jats.pipeline import ConvertOptions, convert

ALL = S.SAMPLES


@pytest.mark.parametrize("smp", ALL, ids=[s.key for s in ALL])
def test_sample_converts_and_validates(tmp_path, smp):
    if not os.path.exists(smp.docx):
        pytest.skip("样例缺失:%s" % smp.docx)
    r = convert(ConvertOptions(docx_path=smp.docx, out_dir=str(tmp_path),
                               journal_id=smp.journal, doi=smp.doi,
                               figures_path=smp.figures_zip if os.path.exists(smp.figures_zip) else None))
    assert r.validation is not None
    assert r.validation.well_formed, "XML 非良构"
    assert r.validation.dtd_valid, "DTD 校验未通过: %s" % r.validation.errors[:3]
    # 基本完整性:作者、参考文献应被提取
    assert r.stats["authors"] >= 1
    assert r.stats["references"] >= 1
    assert os.path.exists(r.xml_path)
    # 出版规范(JATS4R)与结构一致性都不应有 high 级问题
    assert r.stats.get("jats4r", {}).get("high", 0) == 0
    assert r.stats.get("checks", {}).get("high", 0) == 0


def test_runs_without_doi_or_figures(tmp_path):
    """泛化:不提供 DOI/图片包也应产出良构 XML(图片回退到 docx 内嵌)。"""
    smp = S.get("03")
    if not os.path.exists(smp.docx):
        pytest.skip("样例缺失")
    r = convert(ConvertOptions(docx_path=smp.docx, out_dir=str(tmp_path)))
    assert r.validation.well_formed
