"""字段修改的独立测试：真实冻结结果＋异常输入，不调用任何模型。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import io
import json
import shutil
import time
import zipfile

from lxml import etree
import pytest

from webapp import editor, source_view, task_store
from scripts.output_manifest import resolve_output
from word2jats.validate.validator import Validator

ROOT = Path(__file__).resolve().parents[1]
KEYS = ['01', '02', '03', '04', '05', 'S01', 'S02', 'S03', 'S04', 'S05', 'X01', 'X02', 'X03', 'X04']


def frozen(key='01', provider='qwen'):
    return resolve_output(ROOT / 'reports/outputs' / f'finals-{provider}-20260914-r4', key)


@pytest.fixture
def xml():
    location = frozen()
    if location.candidate_xml is None:
        location = resolve_output(ROOT / '输出样例', '01')
    return location.candidate_xml.read_bytes()


@pytest.mark.parametrize('provider', ['qwen', 'deepseek'])
@pytest.mark.parametrize('key', KEYS)
def test_real_documents_noop_and_title_preserve_other_content(key, provider):
    path = frozen(key, provider).candidate_xml
    if not path or not path.exists():
        pytest.skip('独立分发包不携带实验目录；另有固定夹具测试')
    original = path.read_bytes()
    data = editor.extract(original)
    assert editor.apply(original, data) == original
    data['title'] += ' — revised'
    updated = editor.apply(original, data)
    assert Validator().validate_bytes(updated).ok
    a, b = editor.parse(original), editor.parse(updated)
    assert etree.tostring(a.find('body')) == etree.tostring(b.find('body'))
    assert etree.tostring(a.find('back')) == etree.tostring(b.find('back'))
    assert a.xpath('//contrib-id/text()') == b.xpath('//contrib-id/text()')
    assert a.xpath('//xref/@rid') == b.xpath('//xref/@rid')
    assert editor.extract(updated)['title'].endswith(' — revised')


def test_rich_text_only_changes_selected_characters():
    root = etree.fromstring(b'<p>A <italic>rich</italic> title<sup>2</sup>.</p>')
    editor.replace_text(root, 'A new rich title2.')
    assert editor.text(root) == 'A new rich title2.'
    assert root.find('italic').text == 'rich'
    assert root.find('sup').text == '2'
    editor.replace_text(root, 'A new title2.')
    assert root.find('sup').text == '2'
    empty = etree.Element('p'); editor.replace_text(empty, '中文')
    assert empty.text == '中文'


def test_publication_doi_custom_journal(xml):
    data = editor.extract(xml)
    data['publication'].update(title='测试期刊', journal_id='TEST', issn_print='1530-6550',
                               issn_electronic='', publisher='测试出版方', doi='https://doi.org/10.1234/example')
    updated = editor.apply(xml, data)
    assert Validator().validate_bytes(updated).ok
    actual = editor.extract(updated)['publication']
    assert actual['title'] == '测试期刊' and actual['doi'] == '10.1234/example'
    assert etree.tostring(editor.parse(xml).find('body')) == etree.tostring(editor.parse(updated).find('body'))


def test_missing_journal_can_be_completed(xml):
    root = editor.parse(xml)
    journal = root.find('front/journal-meta')
    journal.clear()
    xml = etree.tostring(root)
    data = editor.extract(xml)
    data['publication'].update(title='测试期刊', journal_id='TEST', issn_print='1530-6550', publisher='出版方')
    assert Validator().validate_bytes(editor.apply(xml, data)).ok


def test_author_order_affiliations_names_orcid_contacts(xml):
    data = editor.extract(xml)
    original = editor.parse(xml)
    data['authors'][0]['surname'] += ' 修订'
    data['authors'][0]['given_names'] += ' New'
    data['authors'][0]['orcid'] = '0000-0002-1825-0097'
    data['authors'][0]['affiliations'] = [data['affiliations'][-1]['id']]
    data['authors'][0]['corresponding'] = True
    data['authors'][0], data['authors'][1] = data['authors'][1], data['authors'][0]
    data['affiliations'][0]['text'] += ' 修订单位'
    for contact in data['contacts']:
        contact['text'] += ' 联系地址修订'
        for email in contact['emails']:
            email['value'] = 'editor@example.org'
    updated = editor.apply(xml, data)
    actual = editor.extract(updated)
    assert actual['authors'][1]['surname'].endswith(' 修订')
    assert actual['authors'][1]['orcid'] == '0000-0002-1825-0097'
    assert actual['authors'][1]['affiliations'] == [data['affiliations'][-1]['id']]
    assert actual['affiliations'][0]['text'].endswith(' 修订单位')
    assert Validator().validate_bytes(updated).ok
    assert original.xpath('//fn//text()') == editor.parse(updated).xpath('//fn//text()')


@pytest.mark.parametrize('kind', ['title', 'doi', 'issn', 'orcid', 'email', 'extra', 'unknown-author', 'duplicate-author', 'unknown-aff', 'duplicate-aff', 'corresp', 'long', 'group', 'unit-id', 'entity-text'])
def test_invalid_edits_rejected_or_escaped(xml, kind):
    data = editor.extract(xml)
    if kind == 'title': data['title'] = ''
    elif kind == 'doi': data['publication']['doi'] = 'wrong'
    elif kind == 'issn': data['publication']['issn_print'] = '1234-5678'
    elif kind == 'orcid': data['authors'][0]['orcid'] = '0000-0000-0000-0000'
    elif kind == 'email': data['contacts'][0]['emails'][0]['value'] = 'not-email'
    elif kind == 'extra': data['injected'] = 'x'
    elif kind == 'unknown-author': data['authors'][0]['key'] = '../../bad'
    elif kind == 'duplicate-author': data['authors'][1] = deepcopy(data['authors'][0])
    elif kind == 'unknown-aff': data['authors'][0]['affiliations'] = ['missing']
    elif kind == 'duplicate-aff': data['authors'][0]['affiliations'] *= 2
    elif kind == 'corresp': data['authors'][0]['corresponding'] = 'yes'
    elif kind == 'long': data['title'] = 'x'*10001
    elif kind == 'group': data['authors'][0]['group'] = '/wrong'
    elif kind == 'unit-id': data['affiliations'][0]['id'] = 'changed'
    else:
        data['title'] = '<script>alert(1)</script> & literal text'
        result = editor.apply(xml, data)
        assert not editor.parse(result).xpath('//script')
        assert editor.extract(result)['title'] == data['title']
        return
    with pytest.raises(editor.EditError):
        editor.apply(xml, data)


def test_entities_and_bad_root():
    with pytest.raises(editor.EditError):
        editor.parse(b'<!DOCTYPE article [<!ENTITY x SYSTEM "file:///etc/passwd">]><article>&x;</article>')
    with pytest.raises(editor.EditError): editor.parse(b'<front/>')
    with pytest.raises(editor.EditError): editor.extract(b'<article/>')


def test_source_real_docx_structures():
    from lxml import html
    raw = source_view.document(ROOT / '样例数据/01/初始文件.docx', 'example')
    page = html.fromstring(raw)
    assert page.xpath('//table') and page.xpath('//img') and page.xpath('//math')
    assert page.xpath('//strong') and page.xpath('//em') and page.xpath('//sup')
    ids = page.xpath('//@id')
    assert len(ids) == len(set(ids))
    assert '/api/source-media/example/' in raw


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import webapp.app as module
    monkeypatch.setattr(module, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(module, 'UPLOADS_DIR', tmp_path / 'uploads')
    module.RUNS_DIR.mkdir(); module.UPLOADS_DIR.mkdir()
    monkeypatch.setattr(module, 'TASKS', {})
    def convert(opts):
        location = frozen()
        if location.candidate_xml is None:
            location = resolve_output(ROOT / '输出样例', '01')
        target = Path(opts.out_dir) / 'original' / 'candidate'
        shutil.copytree(location.candidate_dir, target)
        report = location.candidate_dir.parent / 'report.json'
        if report.is_file():
            shutil.copy2(report, target.parent / 'report.json')
        xmlpath = target / location.candidate_xml.name
        return SimpleNamespace(candidate_xml=str(xmlpath), candidate_dir=str(target),
                               delivered=True, article_id=xmlpath.stem,
                               validation=Validator().validate_bytes(xmlpath.read_bytes()),
                               stats={'llm': {'provider': opts.llm, 'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12, 'cache_hit_tokens': 8, 'cache_miss_tokens': 2, 'requests': 1, 'complete': True}}})
    monkeypatch.setattr(module, 'convert', convert)
    with TestClient(module.app) as client:
        response = client.post('/api/convert', files={'docx': ('稿件.docx', (ROOT / '样例数据/01/初始文件.docx').read_bytes())}, data={'journal':'JIN','doi':'10.31083/JIN49347'})
        task_id = response.json()['task_id']
        for _ in range(200):
            state = client.get('/api/status/' + task_id).json()
            if state['status'] in {'done','error'}: break
            time.sleep(.01)
        assert state['status'] == 'done', state
        yield client, task_id, module


def test_edit_save_download_restore_and_restart(api):
    client, tid, module = api
    before = client.get('/api/result/' + tid).json()
    view = client.get('/api/workbench/' + tid).json()
    fields = deepcopy(view['fields']); fields['title'] += ' 修订版'
    response = client.post('/api/edit/' + tid, json={'version':view['version'], 'fields':fields})
    assert response.status_code == 200, response.text
    after = client.get('/api/result/' + tid).json()
    assert before['xml'] != after['xml'] and before['stats']['llm'] == after['stats']['llm']
    assert '修订版' in client.get('/api/render/' + tid).text
    archive = zipfile.ZipFile(io.BytesIO(client.get('/api/download/' + tid).content))
    assert len([n for n in archive.namelist() if n.endswith('.xml')]) == 1
    assert archive.read(after['article_id'] + '.xml').decode() == after['xml']
    assert '修改记录.json' in archive.namelist()
    assert client.post('/api/edit/' + tid, json={'version':view['version'], 'fields':fields}).status_code == 409
    module.TASKS.clear()
    assert client.get('/api/result/' + tid).json()['xml'] == after['xml']
    assert client.post('/api/restore/' + tid, json={'version':response.json()['version']}).status_code == 200
    assert client.get('/api/result/' + tid).json()['xml'] == before['xml']
    assert client.get('/api/original/' + tid).content == (ROOT / '样例数据/01/初始文件.docx').read_bytes()
    assert '<math' in client.get('/api/source/' + tid).text


def test_api_rejects_bad_payload_and_source_ids(api):
    client, tid, _ = api
    view = client.get('/api/workbench/' + tid).json()
    assert client.post('/api/edit/' + tid, json={'version':view['version'], 'fields':{}, 'extra':True}).status_code == 422
    assert client.post('/api/edit/' + tid, json={'version':view['version'], 'fields':{}}).status_code == 400
    assert client.get('/api/source-media/' + tid + '/missing').status_code == 404
    assert client.get('/api/workbench/ffffffffffffffff').status_code == 404
    assert client.post('/api/reconvert/' + tid, json={'provider':'unknown'}).status_code == 400


def test_save_failure_keeps_previous_result(api, monkeypatch):
    client, tid, module = api
    view = client.get('/api/workbench/' + tid).json()
    data = view['fields']; data['title'] += ' 修订'
    before = client.get('/api/result/' + tid).json()['xml']
    monkeypatch.setattr(task_store, 'persist', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    response = client.post('/api/edit/' + tid, json={'version':view['version'], 'fields':data})
    assert response.status_code == 503
    assert client.get('/api/result/' + tid).json()['xml'] == before


def test_task_restore_interrupt_and_validation(tmp_path):
    tid = 'a'*16; folder = tmp_path / tid; folder.mkdir()
    task_store.persist({'workdir':str(folder), 'status':'running'})
    assert task_store.restore(tid, tmp_path)['status'] == 'error'
    assert task_store.restore('../etc', tmp_path) is None
    (folder / 'task.json').write_text('{bad')
    assert task_store.restore(tid, tmp_path) is None


def test_doi_only_preserves_journal_details(xml):
    root = editor.parse(xml)
    journal = root.find('front/journal-meta')
    etree.SubElement(journal.find('journal-title-group'), 'abbrev-journal-title').text = 'Original abbreviation'
    etree.SubElement(journal.find('publisher'), 'publisher-loc').text = 'Original location'
    xml = etree.tostring(root)
    before = etree.tostring(journal)
    data = editor.extract(xml); data['publication']['doi'] = '10.1234/changed'
    updated = editor.apply(xml,data)
    assert etree.tostring(editor.parse(updated).find('front/journal-meta')) == before
    data = editor.extract(updated); data['publication']['title'] = '新的刊名'
    changed = editor.parse(editor.apply(updated,data))
    assert changed.xpath('front/journal-meta/journal-title-group/abbrev-journal-title/text()') == root.xpath('front/journal-meta/journal-title-group/abbrev-journal-title/text()')
    assert changed.findtext('front/journal-meta/publisher/publisher-loc') == 'Original location'


def test_unmodified_contact_not_rejected_by_unrelated_title_edit(xml):
    root=editor.parse(xml)
    root.find('front/article-meta/author-notes/corresp/email').text='两个邮箱待整理'
    raw=etree.tostring(root); data=editor.extract(raw); data['title']+=' 新标题'
    updated=editor.apply(raw,data)
    assert editor.extract(updated)['contacts'][0]['emails'][0]['value']=='两个邮箱待整理'


def test_publication_remove_optional_fields_and_no_doi(xml):
    data=editor.extract(xml)
    data['publication'].update(doi='',publisher='',issn_electronic='')
    updated=editor.apply(xml,data)
    actual=editor.extract(updated)['publication']
    assert actual['doi']=='' and actual['publisher']=='' and actual['issn_electronic']==''
    root=editor.parse(updated)
    root.find('front/article-meta').remove(root.find('front/article-meta/article-id')) if root.find('front/article-meta/article-id') is not None else None
    raw=etree.tostring(root); data=editor.extract(raw); data['publication']['doi']='10.1234/new'
    assert editor.extract(editor.apply(raw,data))['publication']['doi']=='10.1234/new'


def test_remove_orcid_correspondence_and_affiliation_links(xml):
    data=editor.extract(xml)
    for author in data['authors']:
        author['orcid']=''; author['corresponding']=False; author['affiliations']=[]
    updated=editor.apply(xml,data); after=editor.extract(updated)
    assert all(not row['corresponding'] and not row['orcid'] and not row['affiliations'] for row in after['authors'])
    assert editor.parse(xml).xpath('//fn//text()')==editor.parse(updated).xpath('//fn//text()')


def test_presentation_navigation_and_missing_publication(xml):
    from webapp import presentation
    _, blocks=presentation.prepare(xml)
    assert len([b for b in blocks if b['kind']=='article-title'])==1
    root=editor.parse(xml); root.find('front/journal-meta').clear(); raw=etree.tostring(root)
    issues=presentation.issues({'validation':{'dtd_valid':False,'errors':['journal-meta content invalid','Other format error']},'delivered':False},raw,{},blocks)
    assert any(row['action']=='publication' for row in issues)
    assert any(row['action']=='checks' for row in issues)
    issues=presentation.issues({'validation':{'dtd_valid':True},'edited':True,'delivered':False},xml,{},blocks)
    assert issues, '人工修订不能自动消除未解决的转换检查问题'


def test_pending_bad_xml_and_restore_noop_api(api):
    client,tid,module=api
    view=client.get('/api/workbench/'+tid).json()
    assert client.post('/api/edit/'+tid,json={'version':view['version'],'fields':view['fields']}).status_code==200
    assert client.post('/api/restore/'+tid,json={'version':view['version']}).status_code==200
    original=deepcopy(module.TASKS[tid])
    module.TASKS[tid]['status']='running'
    assert client.get('/api/workbench/'+tid).status_code==409
    module.TASKS[tid]=original
    xmlpath=Path(original['result']['xml_path']); before=xmlpath.read_bytes()
    xmlpath.write_bytes(b'<bad')
    assert client.get('/api/workbench/'+tid).status_code==422
    assert '暂时无法显示预览' in client.get('/api/render/'+tid).text
    xmlpath.write_bytes(before)


def test_reconvert_keeps_original_and_choices(api):
    client,tid,module=api
    original=client.get('/api/result/'+tid).json()
    response=client.post('/api/reconvert/'+tid,json={'provider':'deepseek'})
    assert response.status_code==200 and response.json()['previous_task_id']==tid
    new=response.json()['task_id']
    for _ in range(200):
        status=client.get('/api/status/'+new).json()
        if status['status'] in {'done','error'}:break
        time.sleep(.01)
    assert status['status']=='done',status
    assert module.TASKS[new]['options']['provider']=='deepseek'
    assert client.get('/api/result/'+tid).json()['xml']==original['xml']
    assert client.get('/api/original/'+new).content==client.get('/api/original/'+tid).content


def test_source_media_download_and_raster(api):
    client,tid,module=api
    source=source_view.source(Path(module.TASKS[tid]['workdir'])/'input.docx')
    seen=set()
    for occ in source.occurrences:
        resource=source.resource(occ.resource_id) if occ.resource_id else None
        if not hasattr(resource,'blob'):continue
        response=client.get(f'/api/source-media/{tid}/{occ.occ_id}')
        assert response.status_code==200
        seen.add(resource.fmt.lower())
        if resource.fmt.lower() in {'wmf','emf','svg'}:
            assert response.headers['content-disposition'].startswith('attachment')
            assert response.content==resource.blob
    assert seen


def test_preview_reference_author_separators_do_not_change_xml(xml):
    from webapp.render import render_html
    before=bytes(xml)
    html=render_html(xml,'test')
    assert 'Faggiano, ' in html and 'Dasseni, ' in html
    assert xml==before
    data=editor.extract(xml); data['authors'][0]['orcid']='0000-0002-1825-0097'
    html=render_html(editor.apply(xml,data),'test')
    assert 'class="w2j-orcid" href="https://orcid.org/0000-0002-1825-0097"' in html
