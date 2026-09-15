"""产品收尾：结构清单与出版设置继承；均使用隔离副本，不调用模型。"""
from pathlib import Path
import time

import pytest
from lxml import etree, html

from tests.test_web_editor import api, xml, frozen, KEYS  # noqa: F401
from webapp import editor, presentation
from webapp.render import render_html


def wait_result(client, tid):
    for _ in range(300):
        status = client.get('/api/status/' + tid).json()
        if status['status'] in {'done', 'error'}:
            assert status['status'] == 'done', status
            return client.get('/api/result/' + tid).json()
        time.sleep(.01)
    pytest.fail('new conversion did not finish')


@pytest.mark.parametrize('provider', ['qwen', 'deepseek'])
@pytest.mark.parametrize('key', KEYS)
def test_structure_groups_match_xml_and_all_objects_locate(provider, key):
    path = frozen(key, provider).candidate_xml
    data = path.read_bytes()
    root = editor.parse(data)
    prepared, blocks = presentation.prepare(data)
    preview = html.fromstring(render_html(prepared, 'fixture'))
    for row in blocks:
        if row['kind'] in {'ref', 'inline-formula', 'disp-formula', 'abstract', 'trans-abstract', 'fig', 'table-wrap'}:
            assert preview.xpath('//*[@id=$id]', id=row['id']), row
    assert sum(b['kind'] == 'ref' for b in blocks) == len(root.findall('.//ref'))
    summary = presentation.structure(data)
    assert summary['keywords'] == [editor.text(n) for n in root.findall('./front/article-meta/kwd-group/kwd')]
    assert summary['authors'] == len(editor.extract(data)['authors'])
    assert path.read_bytes() == data
    assert editor.text(editor.parse(prepared)) == editor.text(root)


def test_structure_carriers_are_not_quality_grades():
    data = b'''<article xmlns:m="http://www.w3.org/1998/Math/MathML"><front><article-meta><title-group><article-title>A</article-title></title-group></article-meta></front><body><sec><title>T</title><p><inline-formula><m:math><m:mi>x</m:mi></m:math></inline-formula><inline-formula><inline-graphic/></inline-formula><inline-formula><tex-math>x</tex-math></inline-formula></p><table-wrap><graphic/></table-wrap><table-wrap><table><tbody><tr><td>a</td></tr></tbody></table></table-wrap></sec></body><back><ref-list><ref><element-citation><year>2020</year></element-citation></ref><ref><mixed-citation>Text <year>2021</year></mixed-citation></ref></ref-list></back></article>'''
    # 确切比较载体，不用 JATS 合法性替代内容正确性。
    _, blocks = presentation.prepare(data)
    descriptions = {b['description'] for b in blocks}
    assert {'MathML', '图像', 'TeX', '图像表格', '单元格结构', '字段著录', '混合著录'} <= descriptions
    assert not {'正确', '错误', '已核对'} & descriptions


def test_reconvert_carries_current_publication_not_content_edits(api):
    client, tid, module = api
    before = client.get('/api/result/' + tid).json()
    work = client.get('/api/workbench/' + tid).json()
    publication = {**work['fields']['publication'], 'title':'Current Journal', 'journal_id':'CUSTOM',
                   'issn_print':'1530-6550', 'issn_electronic':'', 'publisher':'Current Publisher', 'doi':'10.1234/current'}
    work['fields']['publication'] = publication
    work['fields']['title'] = 'Do not carry this manual title'
    work['fields']['authors'][0]['surname'] += ' manual'
    assert client.post('/api/edit/' + tid, json={'version':work['version'], 'fields':work['fields']}).status_code == 200
    saved = client.get('/api/result/' + tid).json()
    response = client.post('/api/reconvert/' + tid, json={'provider':'deepseek', 'version':saved['version']})
    assert response.status_code == 200
    new = response.json()['task_id']
    result = wait_result(client, new)
    fields = editor.extract(result['xml'].encode())
    assert fields['publication'] == publication
    assert fields['title'] == editor.extract(before['xml'].encode())['title']
    assert fields['authors'] == editor.extract(before['xml'].encode())['authors']
    assert result['validation']['dtd_valid']
    assert not result['edited']  # 出版设置是新任务输入，不是未说明的正文修订。
    assert client.get('/api/result/' + tid).json()['xml'] == saved['xml']
    assert client.get('/api/xml/' + new).text == result['xml']
    assert module.TASKS[new]['options']['publication'] == publication
    assert Path(module.TASKS[new]['result']['candidate_xml']).read_text() == before['xml']
    # 多次重新转换之后仍能回到最初上传的设置，而非上一次继承值。
    original = client.post('/api/reconvert/' + new, json={'provider':'dashscope', 'publication_source':'original'}).json()['task_id']
    wait_result(client, original)
    assert module.TASKS[original]['options']['doi'] == '10.31083/JIN49347'
    assert module.TASKS[original]['options']['journal'] == 'JIN'
    assert 'publication' not in module.TASKS[original]['options']


def test_reconvert_stale_snapshot_rejected_before_creation(api):
    client, tid, module = api
    initial = set(module.TASKS)
    assert client.post('/api/reconvert/' + tid, json={'provider':'deepseek', 'version':'outdated'}).status_code == 409
    assert set(module.TASKS) == initial
    assert client.post('/api/reconvert/' + tid, json={'provider':'deepseek', 'publication_source':'unknown'}).status_code == 422


def test_reconvert_missing_publication_does_not_overwrite_identified_journal(api):
    client, tid, module = api
    path = Path(module.TASKS[tid]['result']['xml_path'])
    root = editor.parse(path.read_bytes()); root.find('front/journal-meta').clear()
    path.write_bytes(etree.tostring(root))
    response = client.post('/api/reconvert/' + tid, json={'provider':'dashscope'})
    new = response.json()['task_id']
    result = wait_result(client, new)
    assert set(module.TASKS[new]['options']['publication']) == {'doi'}
    assert editor.extract(result['xml'].encode())['publication']['title']


def test_failed_reconvert_retry_preserves_settings(api):
    client, tid, module = api
    pub = editor.extract(client.get('/api/result/' + tid).json()['xml'].encode())['publication']
    pub.update(doi='10.1234/retry', title='Retained Journal')
    module.TASKS[tid]['status'] = 'error'
    module.TASKS[tid]['options']['publication'] = pub
    response = client.post('/api/reconvert/' + tid, json={'provider':'deepseek'})
    result = wait_result(client, response.json()['task_id'])
    assert editor.extract(result['xml'].encode())['publication'] == pub
