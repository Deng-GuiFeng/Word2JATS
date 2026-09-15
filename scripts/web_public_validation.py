"""通过公网表单验证错误输入不覆盖已保存结果，保留逐步截图与响应。"""
from scripts.web_public_evidence import get, write_json
from playwright.async_api import expect


def invalid_inputs(fields):
    """覆盖界面能提交的校验分支；不构造只能篡改接口才会出现的字段。"""
    cases = []

    def add(name, mode, values, message):
        cases.append({'name': name, 'mode': mode, 'values': values, 'message': message})

    add('空题名', 'article', {'title': ''}, '题名不能为空')
    add('全空格题名', 'article', {'title': '   '}, '题名不能为空')
    add('题名超过长度限制', 'article', {'title': '题' * 10001}, '题名的内容无效或过长')
    add('DOI 格式错误', 'publication', {'publication.doi': 'not-a-doi'}, 'DOI 格式不正确')
    for key in ('issn_print', 'issn_electronic'):
        for kind, value in (('格式', 'not-an-issn'), ('校验位', '2049-3631')):
            add(key + ' ' + kind, 'publication', {'publication.' + key: value}, 'ISSN 格式或校验位不正确')
    add('缺少刊名', 'publication', {'publication.title': '', 'publication.issn_electronic': '2049-3630',
        'publication.publisher': '错误输入验收'}, '请填写期刊名称和至少一个有效的 ISSN')
    add('缺少刊号', 'publication', {'publication.title': '错误输入验收期刊',
        'publication.issn_print': '', 'publication.issn_electronic': ''}, '请填写期刊名称和至少一个有效的 ISSN')
    add('出版字段超过长度限制', 'publication', {'publication.publisher': '出' * 1001}, '出版信息的内容无效或过长')
    name_kinds = set()
    for i, author in enumerate(fields['authors']):
        for kind, value in (('格式', 'not-an-orcid'), ('校验位', '0000-0002-1825-0098')):
            add(f'作者 {i+1} ORCID {kind}', 'article', {f'authors.{i}.orcid': value}, 'ORCID 格式或校验位不正确')
        if author['kind'] not in name_kinds:
            name_kinds.add(author['kind'])
            field = 'surname' if author['kind'] == 'name' else 'name'
            add(f'作者 {i+1} 姓名超过长度限制', 'article', {f'authors.{i}.{field}': '名' * 1001}, '姓名的内容无效或过长')
    if fields['affiliations']:
        add('单位超过长度限制', 'article', {'affiliations.0.text': '位' * 10001}, '单位的内容无效或过长')
    if fields['contacts']:
        add('通讯说明超过长度限制', 'article', {'contacts.0.text': '通' * 10001}, '通讯信息的内容无效或过长')
    for i, contact in enumerate(fields['contacts']):
        for k, _ in enumerate(contact['emails']):
            add(f'通讯 {i+1} 邮箱 {k+1} 格式', 'article',
                {f'contacts.{i}.emails.{k}.value': 'not-an-email'}, '通讯邮箱格式不正确')
            add(f'通讯 {i+1} 邮箱 {k+1} 长度', 'article',
                {f'contacts.{i}.emails.{k}.value': 'a' * 310 + '@example.org'}, '邮箱的内容无效或过长')
    return cases


async def run(j):
    page = j.page
    await j.reset_view()
    baseline = await get(page, '/api/result/' + j.tid)
    fields = (await get(page, '/api/workbench/' + j.tid))['fields']
    cases = invalid_inputs(fields)
    write_json(j.e.folder / 'validation-input-plan.json', {'task': j.tid, 'cases': cases})
    for kind in ('authors', 'affiliations', 'contacts'):
        if not fields[kind]:
            j.e.event('not-applicable', feature='错误输入 ' + kind, reason='稿件无此类可编辑内容')

    async def unchanged():
        current = await get(page, '/api/result/' + j.tid)
        for key in ('version', 'xml', 'stats'):
            assert current[key] == baseline[key], '拒绝错误输入后已保存的 ' + key + ' 发生变化'

    for index, case in enumerate(cases, 1):
        async def attempt(case=case, index=index):
            await j.reset_view()
            await j.panel(case['mode'])
            for field, value in case['values'].items():
                async def fill(field=field, value=value):
                    node = page.locator('[data-field="' + field + '"]')
                    await node.click()
                    await node.fill(value)
                ok, _ = await j.step(f'N{index:02d} 填写 ' + field, fill)
                assert ok, '错误输入尚未填入，不能假定已经触发校验'
            async with page.expect_response(lambda r: r.request.method == 'POST' and
                    r.url.split('?')[0].endswith('/api/edit/' + j.tid)) as response:
                await j.click('#save-edit')
            result = await response.value
            assert result.status == 400, f'错误输入未被拒绝：HTTP {result.status}'
            await expect(page.locator('#edit-error')).to_contain_text(case['message'])
            await expect(page.locator('#save-state')).to_have_text('尚未保存')
            for field, value in case['values'].items():
                await expect(page.locator('[data-field="' + field + '"]')).to_have_value(value)
            await j.e.shot(f'N{index:02d} 拒绝保存并保留输入')
            await unchanged()
            await j.close()  # 实际确认放弃错误输入，不能用写接口或内存赋值清除。
            await unchanged()
        await j.step(f'N{index:02d} ' + case['name'], attempt)

    async def final_result():
        await j.reset_view()
        await unchanged()
        await j.more()
        await j.download('#download-xml', 'xml')
        await unchanged()
        write_json(j.e.folder / 'validation-inputs-restored.json',
                   {'task': j.tid, 'version': baseline['version']})
    await j.step('N99 错误输入不影响当前 XML 下载', final_result)
