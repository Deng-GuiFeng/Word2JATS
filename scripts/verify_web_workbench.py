"""独立 Web 全流程/状态与截图验收。仅对 web_workbench_fixture 隔离服务运行。"""
import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import zipfile

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def verify(url, output):
    output.mkdir(parents=True, exist_ok=True)
    shots, passed, errors = [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width':1440,'height':1000}, accept_downloads=True)
        page = await context.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        cdp = await context.new_cdp_session(page)
        await cdp.send('Profiler.enable')
        await cdp.send('Profiler.startPreciseCoverage', {'callCount':True,'detailed':True})
        async def check(*ids):
            for ident in ids:
                if ident not in passed:
                    passed.append(ident)
        async def shot(ident, name):
            await page.screenshot(path=output/f'{ident}-{name}.png',animations='disabled')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'页面横向溢出：{name}'
            shots.append({'id':ident,'file':f'{ident}-{name}.png','viewport':page.viewport_size})
            (output/'验收进行中.json').write_text(json.dumps({'functional_passed':passed,'screenshots':shots},ensure_ascii=False,indent=2),encoding='utf-8')
        async def result(number=1):
            await page.goto(f'{url}/#task={number:016x}')
            await expect(page.locator('#result')).to_be_visible(timeout=30000)
            await expect(page.locator('#preview-loading')).to_be_hidden(timeout=30000)
            await page.frame_locator('#render-frame').locator('.document-title').wait_for(timeout=30000)
        async def open_panel(name):
            await page.locator(f'[data-panel="{name}"]').click()
            await expect(page.locator('#side-panel')).to_be_visible()
        async def close_panel():
            await page.locator('#panel-close').click()
            await expect(page.locator('#side-panel')).to_be_hidden()
        async def get(path):
            response = await page.request.get(url+path)
            assert response.ok, (path,response.status)
            return await response.json()
        async def save_and_wait():
            await page.locator('#save-edit').click()
            await expect(page.locator('#save-edit')).to_be_disabled(timeout=20000)
            await expect(page.locator('#toast')).to_contain_text('修改已保存',timeout=30000)
        async def reset(number):
            tid=f'{number:016x}'; data=await get('/api/workbench/'+tid)
            response=await page.request.post(url+'/api/restore/'+tid,data={'version':data['version']})
            assert response.ok

        await page.goto(url)
        await expect(page.locator('#submit-btn')).to_be_disabled()
        await check('U01'); await shot('V01','桌面首页')
        await page.locator('#publication-options summary').click()
        await page.locator('#doi-input').fill('10.31083/RCM46777')
        await page.locator('#journal-input').select_option('RCM')
        await check('U08'); await shot('V03','出版信息展开')
        for provider in ('dashscope','deepseek'):
            await page.locator('#provider-input').select_option(provider)
            assert await page.locator('#provider-input').input_value()==provider
        await check('U07')
        for kind,payload,expected in (
            ('格式错误',{'name':'bad.pdf','mimeType':'application/pdf','buffer':b'abc'},'docx'),
            ('空文件',{'name':'empty.docx','mimeType':'application/octet-stream','buffer':b''},'为空')):
            await page.locator('#docx-input').set_input_files(payload)
            await expect(page.locator('#upload-error')).to_contain_text(expected)
            await expect(page.locator('#submit-btn')).to_be_disabled()
            await shot('V04',kind)
        await page.evaluate('''() => {const file=new File(['x'],'large.docx');Object.defineProperty(file,'size',{value:301*1024*1024});const data=new DataTransfer();data.items.add(file);document.querySelector('#drop').dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true}));}''')
        await expect(page.locator('#upload-error')).to_contain_text('300 MB')
        await shot('V04','文件超限'); await check('U04','U05','U06')
        await page.evaluate('''() => {const data=new DataTransfer();data.items.add(new File(['test'],'drop.docx'));document.querySelector('#drop').dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true}));}''')
        await expect(page.locator('#drop-title')).to_have_text('drop.docx'); await check('U03')
        await page.locator('#docx-input').set_input_files(ROOT/'样例数据/01/初始文件.docx')
        await expect(page.locator('#drop-title')).to_have_text('初始文件.docx')
        await page.locator('#remove-file').click(); await expect(page.locator('#submit-btn')).to_be_disabled()
        await page.locator('#docx-input').set_input_files(ROOT/'样例数据/01/初始文件.docx')
        await shot('V02','文件就绪'); await check('U02')

        # 分片失败重传：仅第一次请求断开，其后通过真实上传接口。
        attempts=0
        async def flaky_chunk(route):
            nonlocal attempts
            attempts+=1
            if attempts==1:
                await route.abort('connectionfailed')
            else:
                await asyncio.sleep(.4); await route.continue_()
        await page.route('**/api/upload/*/0',flaky_chunk)
        await page.locator('#submit-btn').click()
        await expect(page.locator('#progress')).to_be_visible()
        await shot('V05','上传'); await check('P01')
        await expect(page.locator('#result')).to_be_visible(timeout=60000)
        assert attempts>=2
        await page.unroute('**/api/upload/*/0',flaky_chunk)
        uploaded=page.url.split('task=')[-1]
        uploaded_data=await get('/api/result/'+uploaded)
        assert uploaded_data['stats']['llm']['provider']=='deepseek'
        await check('P02','R01','R08')
        await page.reload(); await expect(page.locator('#result')).to_be_visible(); await check('P10')
        original=await page.request.get(url+'/api/original/'+uploaded)
        assert await original.body()==(ROOT/'样例数据/01/初始文件.docx').read_bytes(); await check('S09')

        await reset(1); await result(1)
        await shot('V13','内容问题结果'); await check('R03')
        await open_panel('checks'); await page.locator('.technical summary').click()
        await expect(page.locator('.technical pre')).to_contain_text('dtd_valid')
        await shot('V16','检查技术详情'); await check('R04'); await close_panel()
        await result(3); await shot('V11','正常结果')
        await result(1)
        frame=page.frame_locator('#render-frame')
        blocks=(await get('/api/workbench/0000000000000001'))['blocks']
        # 每个目录目标必须存在，避免有目录、点不动。
        for block in [b for b in blocks if b['navigation']]:
            assert await frame.locator('[id="'+block['id']+'"]').count()>0, block
        await page.locator('[data-location]').nth(5).click()
        await expect(frame.locator('.w2j-selected')).to_have_count(1)
        await shot('V14','目录定位'); await check('R05')
        for kind,name in [('table-wrap','表格'),('fig','图片'),('ref-list','参考文献')]:
            block=next(b for b in blocks if b['kind']==kind)
            await page.locator('[data-location="'+block['id']+'"]').click()
            await asyncio.sleep(.3)
            await shot('V15',name)
        images=await frame.locator('img').evaluate_all('els=>els.map(e=>({src:e.src,ok:e.complete&&e.naturalWidth>0}))')
        assert images and all(row['ok'] for row in images),images
        assert await frame.locator('math').count()>0
        await frame.locator('math').first.scroll_into_view_if_needed()
        await shot('V15','行内公式')
        await check('R06')
        await page.locator('#xml-tab').click(); await expect(page.locator('#xml-view')).to_contain_text('<article')
        await shot('V17','XML'); await check('R07'); await page.locator('#preview-tab').click()
        for number,name in [(1,'Qwen用量'),(2,'DeepSeek用量'),(6,'零新增用量'),(7,'用量不完整')]:
            await result(number); await open_panel('usage')
            actual=await get(f'/api/result/{number:016x}'); usage=actual['stats']['llm']['usage']
            for token in ('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens','total_tokens'):
                assert f'{usage[token]:,}' in await page.locator('#panel-content').inner_text()
            await shot('V18',name)
        await check('R08','R09','R10')

        await result(1); await open_panel('source')
        source=page.frame_locator('#panel-content iframe')
        await source.locator('.source-document').wait_for()
        assert await source.locator('p').count()>100
        assert await source.locator('strong').count()>0 and await source.locator('em').count()>0
        assert await source.locator('sup').count()>0 and await source.locator('table').count()>0
        assert await source.locator('math').count()>0 and await source.locator('img').count()>0
        await shot('V19','全稿对照'); await check('S01','S02','S03','S04','S05','S08')
        fallback=source.locator('a[download]')
        assert await fallback.count()>0
        link=await fallback.first.get_attribute('href'); response=await page.request.get(url+link); assert response.ok
        await fallback.first.scroll_into_view_if_needed(); await shot('V19','不支持媒体入口'); await check('S06')
        await close_panel()
        known=next(b for b in blocks if b['navigation'] and b['source_anchor'])
        await page.locator('[data-location="'+known['id']+'"]').click(); await open_panel('source')
        await source.locator('#'+known['source_anchor']).wait_for(); await shot('V19','原稿位置跳转'); await check('S07')
        await close_panel()

        # 编辑经真实接口保存；用下载 XML 验证最终结果，不只验证提示文字。
        baseline=await get('/api/result/0000000000000001')
        await open_panel('article'); await shot('V20','文章编辑')
        title='修订后的文章题名 · '+(await page.locator('[data-field="title"]').input_value())
        await page.locator('[data-field="title"]').fill(title)
        await page.locator('#download-btn').click(); await expect(page.locator('#action-dialog')).to_be_visible()
        await shot('V23','未保存下载提醒'); await page.locator('#dialog-cancel').click()
        await page.locator('#panel-close').click(); await expect(page.locator('#action-dialog')).to_be_visible()
        await shot('V23','未保存离开提醒'); await page.locator('#dialog-cancel').click(); await check('E14')
        await page.locator('[data-field="authors.0.surname"]').fill('测试姓')
        await page.locator('[data-field="authors.0.given_names"]').fill('测试名')
        await page.locator('[data-field="authors.0.orcid"]').fill('0000-0002-1825-0097')
        await page.locator('[data-field="authors.0.corresponding"]').check()
        await page.locator('[data-aff-author="0"]').first.uncheck()
        await page.locator('[data-field="affiliations.0.text"]').fill('修订单位名称')
        await page.locator('[data-field="contacts.0.emails.0.value"]').fill('editor@example.org')
        await page.locator('[data-move="0"][data-direction="1"]').click()
        await page.locator('#panel-content').evaluate('el=>el.scrollTop=0')
        async def slow_save(route):
            await asyncio.sleep(1.2); await route.continue_()
        await page.route('**/api/edit/*',slow_save)
        await page.locator('#save-edit').click(); await expect(page.locator('#save-state')).to_have_text('正在保存…')
        await shot('V24','保存中'); await expect(page.locator('#toast')).to_contain_text('修改已保存',timeout=30000)
        await page.unroute('**/api/edit/*',slow_save)
        await shot('V24','保存成功')
        saved=await get('/api/workbench/0000000000000001')
        assert saved['fields']['title']==title
        author=saved['fields']['authors'][1]
        assert author['surname']=='测试姓' and author['given_names']=='测试名' and author['corresponding']
        assert author['orcid']=='0000-0002-1825-0097'
        assert saved['fields']['affiliations'][0]['text']=='修订单位名称'
        assert saved['fields']['contacts'][0]['emails'][0]['value']=='editor@example.org'
        await check('E04','E05','E06','E07','E08','E09','E10')
        await close_panel()
        await expect(frame.locator('.document-title')).to_have_text(title)
        after=await get('/api/result/0000000000000001')
        assert after['stats']['llm']==baseline['stats']['llm']; assert after['validation']['dtd_valid']
        with_download=await page.request.get(url+'/api/download/0000000000000001')
        archive=zipfile.ZipFile(io.BytesIO(await with_download.body()))
        assert archive.read(after['article_id']+'.xml').decode()==after['xml']
        assert '修改记录.json' in archive.namelist()
        assert set(n for n in archive.namelist() if '/' in n and not n.endswith('/'))
        assert 'filename*=' in with_download.headers['content-disposition']
        async with page.expect_download() as download_info:
            await page.locator('#download-btn').click()
        download=await download_info.value
        assert download.suggested_filename=='学术稿件-01.zip'
        await download.save_as(output/'已修改结果.zip')
        assert zipfile.ZipFile(output/'已修改结果.zip').read(after['article_id']+'.xml').decode()==after['xml']
        await check('R12','E19','E20','E21')
        await page.reload(); await expect(page.locator('#result')).to_be_visible(); await open_panel('article')
        assert await page.locator('[data-field="title"]').input_value()==title; await check('E16')

        await page.locator('[data-field="authors.0.orcid"]').fill('invalid')
        await page.locator('#save-edit').click(); await expect(page.locator('#edit-error')).to_contain_text('ORCID')
        await shot('V22','字段错误')
        await page.locator('#cancel-edit').click(); await page.locator('#dialog-confirm').click()
        await expect(page.locator('#side-panel')).to_be_hidden(); await check('E12')
        await open_panel('article'); await page.locator('[data-field="title"]').fill(title+' 保存失败测试')
        async def failed_save(route):
            await route.fulfill(status=503,json={'detail':'暂时无法保存，请保留当前输入并重试。'})
        await page.route('**/api/edit/*',failed_save)
        await page.locator('#save-edit').click(); await expect(page.locator('#edit-error')).to_contain_text('暂时无法保存')
        assert (await page.locator('[data-field="title"]').input_value()).endswith('保存失败测试')
        await shot('V24','保存失败'); await check('E13')
        await page.unroute('**/api/edit/*',failed_save)
        # 另一个客户端真实写入，当前编辑页必须检测版本冲突。
        current=await get('/api/workbench/0000000000000001'); current['fields']['title']+=' 另一页面'
        response=await page.request.post(url+'/api/edit/0000000000000001',data={'version':current['version'],'fields':current['fields']}); assert response.ok
        await page.locator('#save-edit').click(); await expect(page.locator('#edit-error')).to_contain_text('其他页面')
        await shot('V24','版本冲突'); await check('E15')
        await page.get_by_role('button',name='重新载入已保存结果').click(); await page.locator('#dialog-confirm').click()
        await expect(page.locator('[data-field="title"]')).to_have_value(current['fields']['title'])
        await close_panel()
        await page.locator('#more-menu summary').click(); await shot('V26','更多操作')
        await page.locator('#restore-btn').click(); await shot('V25','恢复确认')
        await page.locator('#dialog-confirm').click(); await expect(page.locator('#toast')).to_contain_text('已恢复')
        assert (await get('/api/result/0000000000000001'))['xml']==baseline['xml']; await check('E17')

        await reset(5); await result(5); await shot('V12','缺少期刊信息')
        await page.locator('#alert-action').click(); await shot('V21','出版信息编辑')
        await page.locator('[data-field="publication.title"]').fill('自定义学术期刊')
        await page.locator('[data-field="publication.issn_print"]').fill('1530-6550')
        await page.locator('[data-field="publication.publisher"]').fill('测试出版方')
        await page.locator('[data-field="publication.doi"]').fill('10.1234/revised')
        await save_and_wait()
        pub=await get('/api/result/0000000000000005'); assert pub['validation']['dtd_valid']
        assert '自定义学术期刊' in pub['xml'] and '10.1234/revised' in pub['xml']
        await check('U09','R02','E01','E02','E03')
        await close_panel()
        await page.locator('#more-menu summary').click(); await page.locator('#reconvert-btn').click()
        await shot('V26','重新转换'); await page.locator('#reconvert-provider').select_option('dashscope')
        await page.locator('#dialog-confirm').click()
        await page.wait_for_url(lambda value: 'task=' in value and '0000000000000005' not in value,timeout=30000)
        await expect(page.locator('#result')).to_be_visible(timeout=60000)
        assert page.url.split('task=')[-1]!='0000000000000005'
        assert (await get('/api/result/0000000000000005'))['xml']==pub['xml']
        await check('P13')
        await page.locator('#next-btn').click(); await expect(page.locator('#upload')).to_be_visible(); await check('P12')

        # 状态异常由浏览器网络层控制；不改动服务和冻结产物。
        status={'task_id':'aaaaaaaaaaaaaaaa','status':'pending','stage_key':'queued','filename':'稿件.docx','elapsed':0,'error':None}
        async def staged(route):
            await route.fulfill(json=status)
        await page.route('**/api/status/aaaaaaaaaaaaaaaa',staged)
        await page.goto(url+'/#task=aaaaaaaaaaaaaaaa'); await expect(page.locator('#progress-title')).to_have_text('等待开始')
        await shot('V06','排队'); await check('P04')
        for key in ('parse','understand','render','validate'):
            status.update(status='running',stage_key=key,elapsed=12)
            await expect(page.locator(f'[data-step="{key}"]')).to_have_class('active',timeout=5000)
            await shot('V07',key)
        await check('P05')
        await page.reload(); await expect(page.locator('[data-step="validate"]')).to_have_class('active'); await check('P09')
        await context.set_offline(True); await page.unroute('**/api/status/aaaaaaaaaaaaaaaa',staged)
        await expect(page.locator('#connection-notice')).to_be_visible(timeout=25000)
        await shot('V08','临时断网'); await page.route('**/api/status/aaaaaaaaaaaaaaaa',staged); await context.set_offline(False)
        await expect(page.locator('#connection-notice')).to_be_hidden(timeout=10000); await check('P06')
        status.update(status='error',error='这个文件打不开，请另存为 .docx 后重试。')
        await expect(page.locator('#error')).to_be_visible(timeout=5000); await shot('V09','转换失败'); await check('P08')
        await page.unroute('**/api/status/aaaaaaaaaaaaaaaa',staged)
        await page.goto(url+'/#task=ffffffffffffffff'); await expect(page.locator('#error')).to_be_visible()
        await shot('V10','任务失效'); await check('P07')
        await page.locator('#retry-btn').click(); await page.locator('#docx-input').set_input_files(ROOT/'样例数据/01/初始文件.docx')
        async def fail_init(route):
            await route.fulfill(status=503,json={'detail':'上传暂时不可用，请重新上传。'})
        await page.route('**/api/upload/init',fail_init); await page.locator('#submit-btn').click()
        await expect(page.locator('#error')).to_be_visible(); await shot('V09','上传失败'); await check('P03')
        await page.unroute('**/api/upload/init',fail_init)
        async def preview_failure(route):
            await route.fulfill(content_type='text/html',body='<p>暂时无法显示预览。您仍可查看 XML 或下载当前结果。</p>')
        await page.route('**/api/render/*',preview_failure)
        await page.goto(url+'/#task=0000000000000001'); await expect(page.locator('#result')).to_be_visible()
        await page.frame_locator('#render-frame').get_by_text('暂时无法显示预览',exact=False).wait_for()
        await shot('V11','预览失败退路'); assert (await page.request.get(url+'/api/download/0000000000000001')).ok; await check('R11')
        await page.unroute('**/api/render/*',preview_failure)

        # 独立窄屏验收，覆盖抽屉、表单滚动与键盘焦点。
        await page.set_viewport_size({'width':390,'height':844}); await page.goto(url)
        await shot('V27','窄屏首页')
        await page.locator('#publication-options summary').click(); await shot('V27','窄屏出版选项')
        await result(1); await shot('V27','窄屏结果')
        await open_panel('source'); await page.frame_locator('#panel-content iframe').locator('.source-document').wait_for(); await shot('V27','窄屏原稿'); await close_panel()
        await open_panel('article'); await shot('V27','窄屏编辑')
        await page.locator('[data-field="title"]').fill('中文题名与 English terminology '*12)
        await shot('V29','中英文长内容')
        await page.locator('#panel-content').evaluate('el=>el.scrollTop=el.scrollHeight'); await shot('V28','表单底部滚动')
        await page.locator('#panel-close').focus(); await page.keyboard.press('Shift+Tab')
        assert await page.evaluate('document.activeElement.id')=='save-edit'
        await shot('V28','键盘焦点')
        await page.keyboard.press('Escape'); await expect(page.locator('#action-dialog')).to_be_visible(); await shot('V27','窄屏未保存对话框')
        await page.locator('#dialog-confirm').click(); await expect(page.locator('#side-panel')).to_be_hidden()
        await page.set_viewport_size({'width':1440,'height':1000})
        assert not errors, errors
        coverage=await cdp.send('Profiler.takePreciseCoverage')
        coverage['result']=[row for row in coverage['result'] if '/static/' in row['url']]
        (output/'浏览器代码覆盖原始记录.json').write_text(json.dumps(coverage,ensure_ascii=False),encoding='utf-8')
        summary={'functional_passed':sorted(passed),'screenshots':shots,'browser_errors':errors,'uploaded_task':uploaded,
                 'scope':'正常流程连接真实 Web 接口；模型转换使用冻结产物。网络和异常状态用显式故障注入。',
                 'visual_review':'待逐张人工查看；文件生成不等于视觉通过。'}
        (output/'验收清单.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':len(passed),'screenshots':len(shots),'output':str(output)},ensure_ascii=False))
        await browser.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8514')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web-product-20260914')
    args=parser.parse_args()
    if not args.url.startswith(('http://127.0.0.1:','http://localhost:')):
        parser.error('这个测试包含数据修改，仅允许隔离本机服务。')
    asyncio.run(verify(args.url,args.output))


if __name__=='__main__':
    main()
