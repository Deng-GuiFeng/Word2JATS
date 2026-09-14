import json
from pathlib import Path

import pytest

from webapp.export import media_files, names, safe_stem
from webapp.usage import public_usage
from word2jats.llm.usage import summarize_usage


RAW = {'prompt_tokens':120, 'completion_tokens':7, 'total_tokens':127,
       'prompt_cache_hit_tokens':100, 'prompt_cache_miss_tokens':20}


def record(identity, **kw):
    return {'provider':'deepseek', 'model':'deepseek-flash', 'request_id':identity,
            'usage':dict(RAW), **kw}


def test_cache_preserves_original_usage_without_new_network_calls(tmp_path, monkeypatch):
    from word2jats.llm.client import LLMClient
    monkeypatch.setattr(LLMClient, '_load_env', lambda *_: None)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    client = LLMClient('deepseek', cache_dir=str(tmp_path))
    payload = client._payload('JSON only', 'question', 'test', 4096)
    client._cache.put(payload, '{"ok":true}', usage=RAW,
                      response_meta={'request_id':'real-response', 'returned_model':'version'})
    for _ in range(2):
        answer, meta = client.request_json('JSON only', 'question', route='test')
        assert answer == {'ok':True} and meta['cache_hit'] and not meta['network_call']
    public = public_usage(client.stats)
    assert public['incremental_usage']['total_tokens'] == 0
    assert public['result_usage']['total_tokens'] == 127
    assert public['result_usage']['requests'] == 1  # 同一原始响应不重复计费
    assert public['result_usage']['complete']
    assert public['result_usage']['returned_models'] == ['version']
    assert public['reused_responses'] == 2


def test_new_and_reused_usage_include_failed_response_cost():
    network = [record('new'), record('failed', status='failed', status_code=500)]
    reused = [record('cached', status='reused'), record('new', status='reused')]
    public = public_usage({'usage_records':network, 'usage':summarize_usage(network),
                          'reused_usage_records':reused, 'cache_hits':2})
    assert public['incremental_usage']['total_tokens'] == 254
    assert public['result_usage']['total_tokens'] == 381
    assert public['result_usage']['complete']


@pytest.mark.parametrize('known', [False, True])
def test_missing_old_cache_never_becomes_complete_zero(known):
    network = [record('new')] if known else []
    public = public_usage({'cache_hits':3, 'usage_records':network,
                          'usage':summarize_usage(network)})['result_usage']
    assert not public['complete']
    assert public['available'] is known
    assert public['missing_fields']['input_tokens'] == 3


def test_legacy_summarized_usage_survives_partial_reuse():
    incremental = summarize_usage([record('new')])
    result = public_usage({'usage':incremental, 'cache_hits':1})['result_usage']
    assert result['input_tokens'] == 120
    assert not result['complete'] and result['available']


def test_disk_cache_reads_legacy_and_invalid_entries(tmp_path):
    from word2jats.llm.cache import DiskCache
    cache = DiskCache(str(tmp_path)); payload = {'model':'fixture'}
    path = tmp_path / (cache._key(payload) + '.json')
    path.write_text(json.dumps({'response':'ok'}))
    assert cache.get(payload) == 'ok'
    assert cache.get_entry(payload) == {'response':'ok'}
    path.write_text('{bad')
    assert cache.get(payload) is None


@pytest.mark.parametrize('name', ['初始文件.docx', 'input.docx', '../初始文件.docx'])
def test_export_identifies_manuscript_not_generic_input_name(tmp_path, name):
    path = tmp_path / 'original.xml'; path.write_text('<article/>')
    result = names({'task_id':'12345678'*2, 'filename':name,
                    'result':{'article_id':'CEOG50327','candidate_xml':str(path)}})
    assert result == {'download_filename':'Word2JATS-CEOG50327.zip', 'xml_filename':'CEOG50327.xml'}


def test_no_doi_uses_meaningful_name_or_original_title(tmp_path):
    path = tmp_path/'article.xml'
    path.write_text('<article><front><article-meta><title-group><article-title>测试：题名 / 研究</article-title></title-group></article-meta></front></article>')
    task = {'task_id':'abcdef12'*2,'filename':'初始文件.docx',
            'result':{'article_id':'article','candidate_xml':str(path)}}
    assert names(task)['xml_filename'] == '测试：题名 _ 研究.xml'
    task['filename'] = '研究论文.docx'
    assert names(task)['xml_filename'] == '研究论文.xml'
    assert '/' not in safe_stem('../../CON')
    assert safe_stem('NUL') == '_NUL'


def test_export_only_referenced_media_and_rejects_missing(tmp_path):
    (tmp_path/'media').mkdir(); (tmp_path/'media'/'f1.png').write_bytes(b'original')
    (tmp_path/'debug.json').write_text('{}')
    xml = b'<article xmlns:xlink="http://www.w3.org/1999/xlink"><graphic xlink:href="media/f1.png"/><ext-link xlink:href="https://example.org"/></article>'
    assert [(p.read_bytes(), n) for p,n in media_files(tmp_path,xml)] == [(b'original','media/f1.png')]
    with pytest.raises(ValueError):
        list(media_files(tmp_path,xml.replace(b'media/f1.png', b'../missing.png')))


def test_public_product_name_and_cli_result_message(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from word2jats import cli
    from webapp.app import app
    assert app.title == 'Word2JATS'
    with pytest.raises(SystemExit) as stopped:
        cli.main(['--help'])
    assert stopped.value.code == 0
    assert 'Word2JATS' in capsys.readouterr().out
    result = SimpleNamespace(delivered=False, candidate_xml=str(tmp_path/'candidate'/'article.xml'),
                             candidate_dir=str(tmp_path/'candidate'), article_id='article',
                             stats={}, validation=None)
    monkeypatch.setattr(cli, 'convert', lambda _: result)
    assert cli.main(['convert','稿件.docx']) == 1
    output = capsys.readouterr().out
    assert '转换结果需要处理' in output and str(tmp_path/'report.json') in output
    assert '交付门' not in output
