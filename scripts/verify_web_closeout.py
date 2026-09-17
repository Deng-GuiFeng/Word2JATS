"""已批准产品收尾的浏览器验收；只创建独立测试稿件，不修改已有任务。"""
import argparse
import asyncio
import json
from pathlib import Path
import zipfile

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def verify(url, output):
    output.mkdir(parents=True, exist_ok=True)
    checks, shots, errors = [], [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={'width':1440,'height':1000}, accept_downloads=True)
        page = await context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))

        async def get(path):
            response = await page.request.get(url + path)
            assert response.ok, (path,response.status)
            return await response.json()

        async def shot(name):
            await page.screenshot(path=output/(name+'.png'), animations='disabled')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), name
            shots.append({'file':name+'.png','viewport':page.viewport_size})
            (output/'进行中.json').write_text(json.dumps({'checks':checks,'screenshots':shots},ensure_ascii=False,indent=2))

        response = await page.request.post(url + '/api/convert', multipart={
            'docx':{'name':'产品收尾验收.docx','mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':(ROOT/'样例数据/01/初始文件.docx').read_bytes()},
            'provider':'dashscope','journal':'RCM','doi':'10.31083/RCM46777'})
        assert response.ok
        tid = (await response.json())['task_id']
        await page.goto(url + '/#task=' + tid)
        await expect(page.locator('#result')).to_be_visible(timeout=180000)
        await expect(page.locator('#preview-loading')).to_be_hidden(timeout=30000)
        original = await get('/api/result/' + tid)
        work = await get('/api/workbench/' + tid)
        title = work['fields']['title']
        await page.locator('[data-panel="article"]').click()
        await page.locator('[data-field="title"]').fill(title + ' · 修订')
        await page.locator('[data-field="authors.0.surname"]').fill('TestSurname')
        await page.locator('[data-aff-author="0"]').last.uncheck()
        await page.locator('[data-field="contacts.0.emails.0.value"]').fill('test@example.org')
        editor_scroll = await page.locator('#panel-content').evaluate('(e)=>e.scrollTop')
        await page.locator('#editor-source').click()
        await expect(page.locator('#action-dialog')).to_be_hidden()
        await page.frame_locator('#source-frame').locator('.source-document').wait_for()
        await page.locator('#source-frame').evaluate('(e)=>e.contentWindow.scrollTo({top:900,behavior:"instant"})')
        source_scroll = await page.locator('#source-frame').evaluate('(e)=>e.contentWindow.scrollY')
        await shot('C01-草稿保留时查看原稿')
        await page.locator('#return-edit').click()
        await expect(page.locator('[data-field="contacts.0.emails.0.value"]')).to_have_value('test@example.org')
        assert abs(await page.locator('#panel-content').evaluate('(e)=>e.scrollTop')-editor_scroll)<5
        await page.locator('#editor-source').click()
        await page.frame_locator('#source-frame').locator('.source-document').wait_for()
        await asyncio.sleep(.15)
        assert abs(await page.locator('#source-frame').evaluate('(e)=>e.contentWindow.scrollY')-source_scroll)<5
        await page.locator('#panel-close').click()
        await expect(page.locator('#side-panel')).to_be_hidden()
        await expect(page.locator('#resume-edit')).to_be_visible()
        await shot('C03-关闭原稿后继续编辑入口')
        await page.locator('#resume-edit').click()
        await page.locator('#editor-other').click()
        await page.locator('[data-field="publication.doi"]').fill('10.1234/closeout')
        await page.locator('[data-field="publication.title"]').fill('Closeout Journal')
        await page.locator('#editor-other').click()
        await expect(page.locator('[data-field="title"]')).to_have_value(title+' · 修订')
        await expect(page.locator('[data-field="authors.0.surname"]')).to_have_value('TestSurname')
        await expect(page.locator('[data-aff-author="0"]').last).not_to_be_checked()
        for name in ['checks','usage','delivery']:
            if name == 'delivery': await page.locator('#more-menu summary').click()
            await page.locator('[data-panel="'+name+'"]').click()
            await expect(page.locator('#action-dialog')).to_be_hidden()
            await expect(page.locator('#return-edit')).to_be_visible()
            await page.locator('#return-edit').click()
            await expect(page.locator('[data-field="title"]')).to_have_value(title+' · 修订')
        checks += ['C01','C02','C03']
        assert (await get('/api/result/'+tid))['xml'] == original['xml']

        async def failed(route): await route.fulfill(status=503,json={'detail':'测试保存失败'})
        await page.route('**/api/edit/'+tid, failed)
        await page.locator('#save-edit').click()
        await expect(page.locator('#edit-error')).to_contain_text('测试保存失败')
        await page.locator('#editor-source').click(); await page.locator('#return-edit').click()
        await expect(page.locator('[data-field="title"]')).to_have_value(title+' · 修订')
        await page.unroute('**/api/edit/'+tid, failed)
        await page.locator('#save-edit').click()
        await expect(page.locator('#save-state')).to_have_text('未作修改')
        saved = await get('/api/workbench/'+tid)
        assert saved['fields']['publication']['doi'] == '10.1234/closeout'
        assert saved['fields']['publication']['title'] == 'Closeout Journal'
        assert saved['fields']['authors'][0]['surname'] == 'TestSurname'
        assert saved['fields']['contacts'][0]['emails'][0]['value'] == 'test@example.org'
        await page.locator('#panel-close').click()
        async with page.expect_download() as download_info:
            await page.locator('#download-btn').click()
        download = await download_info.value
        package = output/download.suggested_filename
        await download.save_as(package)
        result = await get('/api/result/'+tid)
        assert zipfile.ZipFile(package).read(result['xml_filename']).decode() == result['xml']
        checks.append('C04')  # 版本冲突/保存时离开另由既有两套浏览器回归覆盖。

        for kind in ['metadata','abstract','objects','math','references','all']:
            await page.locator('#outline-kind').select_option(kind)
            if await page.locator('#outline-list button').count():
                target = page.locator('#outline-list button').last
                ident = await target.get_attribute('data-location')
                await target.click()
                await page.wait_for_function('''(id)=>{
                    const doc=document.getElementById('render-frame').contentDocument, node=doc.getElementById(id);
                    const target=node?.tagName==='A' && !node.textContent.trim() ? node.parentElement : node;
                    return target?.classList.contains('w2j-selected');
                }''', arg=ident)
                await page.locator('#render-frame').evaluate('''async (frame) => {
                    const win=frame.contentWindow;
                    let previous=win.scrollY, stable=0;
                    for(let i=0;i<180;i++) {
                        await new Promise(resolve=>requestAnimationFrame(resolve));
                        stable=Math.abs(win.scrollY-previous)<.5 ? stable+1 : 0;
                        previous=win.scrollY;
                        if(stable>=10) return;
                    }
                    throw new Error('目录定位后预览未停止滚动');
                }''')
                assert await page.locator('#render-frame').evaluate('(frame)=>{const r=frame.contentDocument.querySelector(".w2j-selected").getBoundingClientRect(); return r.bottom>0 && r.top<frame.contentWindow.innerHeight;}'), kind
            await shot('C06-'+kind)
        await page.locator('#outline-kind').select_option('references')
        assert await page.locator('#outline-list button').count() == sum(b['kind']=='ref' for b in saved['blocks'])
        await page.locator('#outline-query').fill('不存在的条目')
        await expect(page.locator('#outline-empty')).to_be_visible(); await shot('C06-搜索空结果')
        await page.locator('#outline-kind').select_option('all')
        checks.append('C06')

        for name, width, height in [('桌面',1440,1000),('笔记本',1366,768),('平板横屏',1024,768),('平板竖屏',768,1024),('手机',390,844),('手机横屏',844,390)]:
            await page.set_viewport_size({'width':width,'height':height})
            await page.locator('[data-panel="article"]').click()
            await page.locator('[data-field="title"]').click()
            await page.locator('[data-field="title"]').fill(title+' · '+name)
            await shot('C05-'+name+'-编辑')
            await page.locator('#editor-source').click()
            await page.frame_locator('#source-frame').locator('.source-document').wait_for()
            await shot('C05-'+name+'-原稿与草稿')
            await page.locator('#panel-close').focus()
            if width<=850:
                await page.keyboard.press('Shift+Tab')
                assert await page.locator('#return-edit').evaluate('(e)=>e===document.activeElement')
            await page.locator('#return-edit').click()
            await expect(page.locator('[data-field="title"]')).to_have_value(title+' · '+name)
            await page.locator('#editor-other').click(); await shot('C05-'+name+'-出版信息草稿')
            await page.locator('#editor-source').click(); await page.locator('#panel-close').click()
            await expect(page.locator('#resume-edit')).to_be_visible(); await shot('C05-'+name+'-继续编辑')
            await page.locator('#next-btn').click(); await expect(page.locator('#action-dialog')).to_be_visible()
            await page.locator('#dialog-cancel').click()
            await page.locator('#resume-edit').click(); await page.locator('#cancel-edit').click()
            await page.locator('#dialog-confirm').click()
        checks.append('C05')
        await page.set_viewport_size({'width':1440,'height':1000})
        await page.locator('#more-menu summary').click(); await page.locator('#reconvert-btn').click()
        await expect(page.locator('#reconvert-publication')).to_have_value('current')
        await expect(page.locator('#reconvert-provider')).to_have_value('dashscope')
        await shot('C07-重新转换设置')
        await page.locator('#dialog-confirm').click()
        await page.wait_for_function('(old)=>location.hash!=="#task="+old',arg=tid)
        await expect(page.locator('#result')).to_be_visible(timeout=180000)
        new = page.url.split('#task=')[1]
        actual = await get('/api/workbench/'+new)
        assert actual['fields']['publication']['doi'] == '10.1234/closeout'
        assert actual['fields']['publication']['title'] == 'Closeout Journal'
        assert actual['fields']['title'] == title
        assert actual['fields']['authors'][0]['surname'] != 'TestSurname'
        assert (await get('/api/result/'+tid))['xml'] == result['xml']
        checks.append('C07')
        assert not errors, errors
        (output/'验收.json').write_text(json.dumps({'checks':checks,'screenshots':shots,'errors':errors,'tasks':[tid,new]},ensure_ascii=False,indent=2))
        print(json.dumps({'checks':checks,'screenshots':len(shots),'tasks':[tid,new]},ensure_ascii=False))
        await browser.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:18641')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web-closeout/product')
    args=parser.parse_args()
    if not args.url.startswith(('http://127.0.0.1:18641','http://localhost:18641')): raise SystemExit('只能指向独立夹具端口')
    asyncio.run(verify(args.url.rstrip('/'),args.output))
