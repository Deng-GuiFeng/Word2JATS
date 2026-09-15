"""公网编辑验收：从实际输入到保存、刷新、XML 与下载，不写应用数据接口。"""
from copy import deepcopy
import re

from lxml import etree
from playwright.async_api import expect

from scripts.web_public_evidence import get, write_json


def semantic_fields(value):
    """XPath 行标识随作者顺序变化；比较内容与顺序，不比较旧位置标识。"""
    if isinstance(value, dict):
        return {k: semantic_fields(v) for k, v in value.items() if k not in {'key', 'group'}}
    if isinstance(value, list):
        return [semantic_fields(v) for v in value]
    return value


def xml_fields(xml):
    """独立读取交付 XML，不使用被测 editor.extract / apply 作为判定依据。"""
    root = etree.fromstring(xml.encode() if isinstance(xml, str) else xml,
                            etree.XMLParser(resolve_entities=False, no_network=True))
    def text(node, excluded=()):
        if node is None:
            return ''
        return (node.text or '') + ''.join(
            (text(child, excluded) if child.tag not in excluded else '') + (child.tail or '')
            for child in node)
    meta, journal = root.find('front/article-meta'), root.find('front/journal-meta')
    def find(node, path):
        return text(node.find(path)) if node is not None else ''
    publication = {
        'doi': find(meta, 'article-id[@pub-id-type="doi"]'),
        'journal_id': find(journal, 'journal-id'),
        'title': find(journal, 'journal-title-group/journal-title'),
        'issn_print': find(journal, 'issn[@pub-type="ppub"]'),
        'issn_electronic': find(journal, 'issn[@pub-type="epub"]'),
        'publisher': find(journal, 'publisher/publisher-name'),
    }
    if not publication['issn_print'] and not publication['issn_electronic']:
        publication['issn_electronic'] = find(journal, 'issn')
    authors = []
    for row in meta.xpath('./contrib-group/contrib[not(@contrib-type) or @contrib-type="author"]'):
        kind = next((k for k in ('name', 'string-name', 'collab') if row.find(k) is not None), 'collab')
        authors.append({
            'kind': kind, 'surname': find(row, 'name/surname') if kind == 'name' else '',
            'given_names': find(row, 'name/given-names') if kind == 'name' else '',
            'name': find(row, kind) if kind != 'name' else '',
            'orcid': find(row, 'contrib-id[@contrib-id-type="orcid"]'),
            'corresponding': row.get('corresp') == 'yes' or bool(row.findall('xref[@ref-type="corresp"]')),
            'affiliations': [rid for x in row.findall('xref[@ref-type="aff"]') for rid in x.get('rid', '').split()],
        })
    return {'title': find(meta, 'title-group/article-title'), 'publication': publication,
            'authors': authors,
            'affiliations': [{'id': row.get('id', ''), 'label': find(row, 'label'),
                              'text': text(row, ('label',))} for row in meta.xpath('.//aff')],
            'contacts': [{'text': text(row, ('label', 'email')),
                          'emails': [{'value': text(e)} for e in row.findall('.//email')]}
                         for row in meta.findall('author-notes/corresp')]}


def assert_fields(actual, expected):
    actual, expected = semantic_fields(actual), semantic_fields(expected)
    def compare(a, b, path='fields'):
        assert type(a) is type(b), f'{path}: 类型不一致'
        if isinstance(b, dict):
            assert a.keys() == b.keys(), f'{path}: 字段不一致'
            for key in b:
                compare(a[key], b[key], path+'.'+key)
        elif isinstance(b, list):
            assert len(a) == len(b), f'{path}: 条数不一致'
            for i, item in enumerate(b):
                compare(a[i], item, path+'.'+str(i))
        else:
            assert a == b, f'{path}: 实际 {a!r}，预期 {b!r}'
    compare(actual, expected)


def article_plan(fields):
    target = deepcopy(fields)
    target['title'] += '（字段保存验收）'
    for i, row in enumerate(target['authors']):
        if row['kind'] == 'name':
            row['surname'] += f' 核验姓{i+1}'
            row['given_names'] += f' 核验名{i+1}'
        else:
            row['name'] += f' 核验名称{i+1}'
        row['orcid'] = ('0000-0002-1825-0097' if row['orcid'] != '0000-0002-1825-0097'
                        else '0000-0001-5109-3700')
        row['corresponding'] = not row['corresponding']
        row['affiliations'] = [a['id'] for a in fields['affiliations']
                               if a['id'] and a['id'] not in row['affiliations']]
    for i, row in enumerate(target['affiliations']):
        row['text'] += f' 核验单位{i+1}'
    for i, row in enumerate(target['contacts']):
        row['text'] += f' 核验通讯{i+1}'
        for j, email in enumerate(row['emails']):
            email['value'] = f'web-metadata-{i+1}-{j+1}@example.org'
    return target


def field_value(fields, path):
    for key in path.split('.'):
        fields = fields[int(key)] if isinstance(fields, list) else fields[key]
    return fields


async def run(journey):
    j, page = journey, journey.page
    await j.reset_view()
    baseline = await get(page, '/api/result/'+j.tid)
    write_json(j.e.folder/'metadata-before.json', baseline)
    original_root = etree.fromstring(baseline['xml'].encode())

    async def required(name, operation):
        ok, result = await j.step(name, operation)
        assert ok, name+' 未完成；本阶段不继续假定成功'
        return result

    async def fill_fields(mode, target, prefix):
        await j.panel(mode)
        controls = await page.locator('[data-field]').evaluate_all(
            'els=>els.map(e=>({field:e.dataset.field,type:e.type}))')
        for control in controls:
            path = control['field']
            async def fill(path=path, control=control):
                node = page.locator('[data-field="'+path+'"]')
                await node.click()
                value = field_value(target, path)
                if control['type'] == 'checkbox':
                    await node.set_checked(value)
                else:
                    await node.fill(value)
            await required(prefix+' 填写 '+path, fill)
        if mode == 'article':
            for i, row in enumerate(target['authors']):
                for aff in target['affiliations']:
                    if not aff['id']:
                        j.e.event('not-applicable', feature='单位关联', reason='该单位无可引用标识')
                        continue
                    async def choose(i=i, row=row, aff=aff):
                        node = page.locator('[data-aff-author="'+str(i)+'"][value="'+aff['id']+'"]')
                        await node.set_checked(aff['id'] in row['affiliations'])
                    await required(prefix+' 单位关联 '+str(i+1)+'/'+aff['id'], choose)

    async def save():
        await j.click('#save-edit')
        await page.wait_for_function('''()=>!document.getElementById('edit-error').hidden ||
          document.getElementById('save-state').textContent==='未作修改' ''', timeout=90000)
        assert not await page.locator('#edit-error').is_visible(), await page.locator('#edit-error').inner_text()
        await expect(page.locator('#save-edit')).to_be_disabled()

    async def verify(target, prefix):
        data = await get(page, '/api/result/'+j.tid)
        assert_fields(xml_fields(data['xml']), target)
        assert_fields((await get(page, '/api/workbench/'+j.tid))['fields'], target)
        root = etree.fromstring(data['xml'].encode())
        for part in ('body', 'back'):
            a, b = original_root.find(part), root.find(part)
            assert (etree.tostring(a) if a is not None else None) == (
                etree.tostring(b) if b is not None else None), part+' 被字段编辑连带改写'
        assert data['stats']['llm'] == baseline['stats']['llm'], '字段编辑改变了模型用量'
        write_json(j.e.folder/('metadata-'+prefix+'.json'), {'expected':target,'result':data})
        await required(prefix+' 刷新后保持保存结果', j.reset_view)
        for mode in ('article', 'publication'):
            await j.panel(mode)
            for control in await page.locator('[data-field]').evaluate_all(
                    'els=>els.map(e=>({field:e.dataset.field,type:e.type}))'):
                node = page.locator('[data-field="'+control['field']+'"]')
                value = field_value(target, control['field'])
                if control['type'] == 'checkbox':
                    assert await node.is_checked() == value, control['field']
                else:
                    await expect(node).to_have_value(value)
            await j.scroll_all('#panel-content', prefix+' 刷新后 '+mode)
            await j.close()
        await j.wait_preview_document()
        rendered = await page.frame_locator('#render-frame').locator('.front').inner_text()
        compact = lambda s: re.sub(r'\s+', '', s)
        texts = [target['title'], target['publication']['title']]
        texts += [r['text'] for r in target['affiliations']]
        texts += [r[k] for r in target['authors'] for k in ('surname','given_names','name')]
        for value in texts:
            assert compact(value) in compact(rendered), '保存内容未进入预览：'+value
        await required(prefix+' XML 视图', lambda:j.click('#xml-tab'))
        assert await page.locator('#xml-view').inner_text() == data['xml']
        await required(prefix+' 返回预览', lambda:j.click('#preview-tab'))
        await j.more()
        await required(prefix+' 下载 XML', lambda:j.download('#download-xml','xml'))
        await required(prefix+' 下载成果包', lambda:j.download('#download-btn','all'))

    try:
        async def article():
            fields = (await get(page, '/api/workbench/'+j.tid))['fields']
            for kind in ('authors','affiliations','contacts'):
                if not fields[kind]:
                    j.e.event('not-applicable', feature=kind, reason='稿件无此类可编辑内容')
            target = article_plan(fields)
            await fill_fields('article', target, 'M01')
            await required('M02 保存全部文章字段', save)
            await verify(target, 'M03')
        await j.step('M10 文章字段完整保存链路', article)

        async def order():
            await j.reset_view()
            target = deepcopy((await get(page, '/api/workbench/'+j.tid))['fields'])
            await j.panel('article')
            moved = 0
            for i in range(len(target['authors'])-1):
                if target['authors'][i]['group'] == target['authors'][i+1]['group']:
                    await required('M11 保存前作者下移 '+str(i+1),
                                   lambda i=i:j.click('[data-move="'+str(i)+'"][data-direction="1"]'))
                    target['authors'][i], target['authors'][i+1] = target['authors'][i+1], target['authors'][i]
                    moved += 1
            if not moved:
                j.e.event('not-applicable', feature='作者顺序保存', reason='没有同组相邻作者')
                return
            await required('M12 保存作者新顺序', save)
            await verify(target, 'M13')
        await j.step('M20 作者顺序完整保存链路', order)

        async def custom():
            await j.reset_view()
            target = deepcopy((await get(page, '/api/workbench/'+j.tid))['fields'])
            await j.panel('publication')
            await required('M21 选择自定义期刊', lambda:page.locator('#editor-journal').select_option(''))
            target['publication'].update(doi='10.9999/metadata-'+j.record['sample'], journal_id='',
                title='字段保存验收期刊', issn_print='1530-6550', issn_electronic='2049-3630', publisher='字段保存验收出版方')
            await fill_fields('publication', target, 'M22')
            await required('M23 保存自定义出版字段', save)
            # 自定义期刊未提供内部标识，交付 XML 以有效 ISSN 作为 journal-id。
            target['publication']['journal_id'] = target['publication']['issn_electronic']
            await verify(target, 'M24')
        await j.step('M30 自定义出版信息完整保存链路', custom)

        await j.reset_view()
        await j.panel('publication')
        options = await page.locator('#editor-journal option').evaluate_all('els=>els.map(e=>e.value).filter(Boolean)')
        for value in options:
            async def preset(value=value):
                await j.reset_view()
                target = deepcopy((await get(page, '/api/workbench/'+j.tid))['fields'])
                await j.panel('publication')
                await required('M31 选择配置期刊 '+value, lambda:page.locator('#editor-journal').select_option(value))
                target['publication']['journal_id'] = value
                for control in await page.locator('[data-field]').evaluate_all('els=>els.map(e=>e.dataset.field)'):
                    target['publication'][control.split('.')[1]] = await page.locator('[data-field="'+control+'"]').input_value()
                if await page.locator('#save-edit').is_enabled():
                    await required('M32 保存配置期刊 '+value, save)
                await verify(target, 'M33-'+value)
            await j.step('M40 配置期刊保存链路 '+value, preset)
    finally:
        async def restore():
            await j.reset_view()
            await j.more()
            if await page.locator('#restore-btn').is_enabled():
                await j.click('#restore-btn')
                await j.click('#dialog-confirm')
                await expect(page.locator('#toast')).to_contain_text('已恢复', timeout=90000)
            result = await get(page, '/api/result/'+j.tid)
            assert result['version'] == j.record['version'], '测试任务未恢复为最初自动转换结果'
            assert result['stats']['llm'] == baseline['stats']['llm']
            write_json(j.e.folder/'metadata-restored.json', {'task':j.tid,'version':result['version']})
        await j.step('M99 还原本次测试任务的自动结果', restore)
