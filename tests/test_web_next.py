"""后续 Web 改进：当前版本、下载、目录和本地记录的独立回归。"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

import pytest
from lxml import etree

from tests.test_web_editor import api, xml  # noqa: F401 — 复用隔离真实产物夹具
from webapp import delivery, editor, presentation


def test_download_manifest_xml_and_zip_are_same_version(api):
    client, tid, module = api
    result = client.get('/api/result/' + tid).json()
    version = result['version']
    manifest = client.get(f'/api/delivery/{tid}?version={version}').json()
    xml = client.get(f'/api/xml/{tid}?version={version}')
    assert xml.status_code == 200
    assert hashlib.sha256(xml.content).hexdigest() == version
    assert xml.content.decode() == result['xml']
    assert "filename*=UTF-8''" in xml.headers['content-disposition']
    assert xml.headers['cache-control'] == 'no-store'
    package = client.get(f'/api/download/{tid}?version={version}')
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    assert set(archive.namelist()) == {file['name'] for file in manifest['files']}
    assert archive.read(manifest['xml_filename']) == xml.content
    assert json.loads(archive.read('检查摘要.json'))['xml_sha256'] == version
    assert not list(Path(module.TASKS[tid]['workdir']).glob('download-*.zip'))
    assert manifest['version'] == version and not manifest['edited']
    assert result['provider'] == 'dashscope'
    assert result['created_at'] > 0


def test_edit_conflict_download_and_restore(api):
    client, tid, _ = api
    result = client.get('/api/result/' + tid).json()
    work = client.get('/api/workbench/' + tid).json()
    work['fields']['title'] += ' 修改后'
    assert client.post('/api/edit/' + tid, json={'version':work['version'], 'fields':work['fields']}).status_code == 200
    for endpoint in ('xml', 'delivery', 'download'):
        response = client.get(f'/api/{endpoint}/{tid}?version={result["version"]}')
        assert response.status_code == 409 and '其他页面' in response.json()['detail']
    after = client.get('/api/result/' + tid).json()
    assert after['edited'] and after['saved_at'] > 0
    assert after['version'] != result['version']
    info = client.get('/api/delivery/' + tid).json()
    assert '修改记录.json' in {file['name'] for file in info['files']}
    assert client.get('/api/xml/' + tid).content.decode() == after['xml']
    assert client.post('/api/restore/' + tid, json={'version':after['version']}).status_code == 200
    assert client.get('/api/xml/' + tid).content.decode() == result['xml']
    assert '修改记录.json' not in {file['name'] for file in client.get('/api/delivery/' + tid).json()['files']}


def test_resource_failure_keeps_xml_downloadable(api):
    client, tid, module = api
    task = module.TASKS[tid]
    root = editor.parse(Path(task['result']['xml_path']).read_bytes())
    graphic = root.xpath('//*[@*[local-name()="href" and namespace-uri()="http://www.w3.org/1999/xlink"]][self::graphic or self::inline-graphic]')[0]
    graphic.set('{http://www.w3.org/1999/xlink}href', 'missing-image.png')
    Path(task['result']['xml_path']).write_bytes(etree.tostring(root))
    for endpoint in ('delivery', 'download'):
        response = client.get(f'/api/{endpoint}/{tid}')
        assert response.status_code == 409
        assert '单独下载 XML' in response.json()['detail']
    assert client.get('/api/xml/' + tid).status_code == 200


def test_new_download_routes_reject_unknown_and_pending(api):
    client, tid, module = api
    for endpoint in ('xml', 'delivery'):
        assert client.get('/api/' + endpoint + '/ffffffffffffffff').status_code == 404
        module.TASKS[tid]['status'] = 'running'
        assert client.get('/api/' + endpoint + '/' + tid).status_code == 409
        module.TASKS[tid]['status'] = 'done'
    Path(module.TASKS[tid]['result']['xml_path']).unlink()
    assert client.get('/api/xml/' + tid).status_code == 409


def test_download_write_failure_is_recoverable(api, monkeypatch):
    client, tid, module = api
    original = zipfile.ZipFile.writestr
    def broken(archive, *args, **kwargs):
        if isinstance(archive.filename, str) and 'download-' in archive.filename:
            raise OSError('test storage failure')
        return original(archive, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'writestr', broken)
    response = client.get('/api/download/' + tid)
    assert response.status_code == 503
    assert not list(Path(module.TASKS[tid]['workdir']).glob('download-*.zip'))
    assert client.get('/api/xml/' + tid).status_code == 200


def test_image_read_failure_never_removes_original(api, monkeypatch):
    client, tid, module = api
    task=module.TASKS[tid]
    xml, _=delivery.snapshot(task)
    media=delivery.resources(task,xml)
    assert media
    before={path:path.read_bytes() for path,_ in media}
    original=zipfile.ZipFile.write
    def broken(archive, path, *args, **kwargs):
        if Path(path) in before:
            raise OSError('simulated image read failure')
        return original(archive,path,*args,**kwargs)
    monkeypatch.setattr(zipfile.ZipFile,'write',broken)
    assert client.get('/api/download/'+tid).status_code==503
    assert all(path.is_file() and path.read_bytes()==data for path,data in before.items())
    assert not list(Path(task['workdir']).glob('download-*.zip'))
    assert client.get('/api/xml/'+tid).content==xml
    monkeypatch.setattr(zipfile.ZipFile,'write',original)
    assert client.get('/api/download/'+tid).status_code==200


def test_manifest_keeps_legacy_review_entry(api):
    client, tid, module = api
    (Path(module.TASKS[tid]['workdir']) / 'review.json').write_text('{}')
    manifest = client.get('/api/delivery/' + tid).json()
    assert '人工复核记录.json' in {file['name'] for file in manifest['files']}


def test_current_title_filename_not_automatic_original(tmp_path):
    from webapp.export import names
    old, new = tmp_path/'old.xml', tmp_path/'new.xml'
    old.write_text('<article><front><article-meta><title-group><article-title>Old title</article-title></title-group></article-meta></front></article>')
    new.write_bytes(old.read_bytes().replace(b'Old', b'New'))
    assert names({'filename':'初始文件.docx','task_id':'a'*16,'result':{
        'candidate_xml':str(old),'xml_path':str(new),'article_id':'article'}})['xml_filename'] == 'New title.xml'


def test_outline_hierarchy_and_source_not_changed(xml):
    root = editor.parse(xml)
    before = etree.tostring(root)
    prepared, blocks = presentation.prepare(xml)
    assert any(row['kind'] == 'abstract' and row['navigation'] for row in blocks)
    assert max(row['depth'] for row in blocks if row['kind'] == 'sec') >= 2
    assert len({row['id'] for row in blocks}) == len(blocks)
    assert etree.tostring(root) == before
    after = editor.parse(prepared)
    assert ''.join(after.itertext()) == ''.join(root.itertext())
    assert after.xpath('//xref/@rid') == root.xpath('//xref/@rid')
    from webapp.render import render_html
    from lxml import html
    preview = html.fromstring(render_html(prepared, 'fixture'))
    abstract = next(row for row in blocks if row['kind'] == 'abstract')
    assert preview.xpath('//*[@id=$id]', id=abstract['id'])


def test_issue_excerpt_and_action_not_an_approval(xml):
    item = {'code':'TEXT_UNCOVERED','title':'原稿内容尚未完整输出','action':'对照这段内容',
            'source_id':'doc/p1','source_text':'原稿摘录','blocking':True}
    result = {'validation':{'dtd_valid':True},'review_items':[item],'delivered':False}
    issues = presentation.issues(result, xml, {}, [])
    issue = next(row for row in issues if row['category'] == 'content')
    assert issue['excerpt'] == '原稿摘录' and issue['action'] == 'source'
    assert issue['blocking']


def test_source_tables_keyboard_accessible():
    from lxml import html
    from webapp.source_view import document
    root = Path(__file__).resolve().parents[1]
    preview = html.fromstring(document(root/'样例数据/01/初始文件.docx', 'fixture'))
    tables = preview.xpath('//div[@class="table-scroll"]')
    assert tables
    assert all(node.get('tabindex') == '0' and node.get('aria-label') for node in tables)


@pytest.mark.parametrize('provider', ['qwen','deepseek'])
@pytest.mark.parametrize('key', ['01','02','03','04','05','S01','S02','S03','S04','S05','X01','X02','X03','X04'])
def test_all_frozen_previews_navigation_and_text(provider, key):
    from scripts.output_manifest import resolve_output
    from webapp.render import render_html
    from lxml import html
    root = Path(__file__).resolve().parents[1]
    file = resolve_output(root/'reports/outputs'/f'finals-{provider}-20260914-r4', key).candidate_xml
    original = file.read_bytes()
    prepared, blocks = presentation.prepare(original)
    preview = html.fromstring(render_html(prepared, 'fixture'))
    for row in blocks:
        if row['navigation'] and row['kind'] != 'article-title':
            assert preview.xpath('//*[@id=$id]', id=row['id']), row
    assert editor.text(editor.parse(prepared)) == editor.text(editor.parse(original))
    assert file.read_bytes() == original


def test_browser_session_unit_contract():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(['node','--test',str(root/'tests/web_session.test.cjs')],capture_output=True,text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
