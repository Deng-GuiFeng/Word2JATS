"""集成测试:样例端到端转换并通过 JATS 1.3 DTD 校验 + 出口校验无 high 级问题。

本方法的理解层由 LLM 承担，故集成测试需要模型。为快速、离线、可复现，测试复用评测
已预热的磁盘缓存（reports/_llm_cache/dashscope/<key>，temp=0 下确定复现）；
模型不可用或缓存缺失时优雅跳过（不打真 API、不使 CI 变慢/不稳）。
样例清单取自评测样例登记表（scripts/eval_v1/samples.py，读 样例数据/样例登记.json），与评测同一数据源。
"""
import os

import pytest

from scripts.eval_v1 import samples as S
from word2jats.llm.client import LLMClient
from word2jats.pipeline import ConvertOptions, convert

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_ROOT = os.path.join(ROOT, "reports", "_llm_cache", "dashscope")

# 主样例各取一，覆盖不同期刊/结构（图片表、公式、综述、纯文本），控制单测时长
CASES = ["05", "03", "S03"]


def _cache_dir(key):
    return os.path.join(CACHE_ROOT, key)


def _llm_ready():
    return LLMClient(provider="dashscope").enabled


@pytest.mark.parametrize("key", CASES)
def test_sample_converts_and_validates(tmp_path, key):
    smp = S.get(key)
    if not os.path.exists(smp.docx):
        pytest.skip("样例缺失:%s" % smp.docx)
    if not _llm_ready():
        pytest.skip("LLM 不可用（缺 DASHSCOPE_API_KEY），理解层需模型")
    if not os.path.isdir(_cache_dir(key)):
        pytest.skip("LLM 缓存缺失，先跑一次评测预热:python -m scripts.eval_v1 --llm dashscope")
    r = convert(ConvertOptions(
        docx_path=smp.docx, out_dir=str(tmp_path), journal_id=smp.journal, doi=smp.doi,
        llm="dashscope", llm_cache_dir=_cache_dir(key)))
    assert r.validation is not None
    assert r.validation.well_formed, "XML 非良构"
    assert r.validation.dtd_valid, "DTD 校验未通过: %s" % r.validation.errors[:3]
    assert r.stats["authors"] >= 1
    assert r.stats["references"] >= 1
    assert os.path.exists(r.xml_path)
    # 结构一致性不应有 high 级问题（悬空 xref / 重复 id 等）
    assert r.stats.get("checks", {}).get("high", 0) == 0
    # 出口校验：DTD 通过
    assert r.stats.get("verify", {}).get("dtd_ok", True)
