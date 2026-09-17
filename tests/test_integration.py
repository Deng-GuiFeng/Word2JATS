"""用最终缓存离线重放 14 例 × 两种大模型配置，禁止测试发起外部请求。"""
from pathlib import Path
import socket

import pytest

from scripts.eval_v2.samples import ALL_SAMPLES
from word2jats.pipeline import ConvertOptions, convert

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'runtime/cache'


@pytest.mark.parametrize('sample', ALL_SAMPLES, ids=lambda sample: sample.key)
@pytest.mark.parametrize('provider', ['deepseek', 'dashscope'])
def test_cached_sample_conversion(tmp_path, monkeypatch, sample, provider):
    if not CACHE.is_dir():
        pytest.skip('离线集成测试需要本地 runtime/cache；源码分发不含运行缓存')

    def forbidden(*args, **kwargs):
        raise AssertionError('离线重放不得访问网络')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    result = convert(ConvertOptions(
        docx_path=str(sample.docx), out_dir=str(tmp_path),
        journal_id=sample.journal, doi=sample.doi,
        llm=provider, llm_cache_dir=str(CACHE),
    ))
    assert result.validation.well_formed
    assert result.validation.dtd_valid, result.validation.errors
    assert Path(result.candidate_xml).is_file()
    assert result.stats['llm']['calls'] == 0
    assert result.stats['llm']['cache_misses'] == 0
    assert result.stats['references'] >= 1
