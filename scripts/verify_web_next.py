"""独立验证后续 Web 用户流程；只允许本机测试服务，测试稿件另行创建。"""
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
    checks, shots, errors = [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        async def shot(name):
            await page.screenshot(path=output/(name+'.png'),animations='disabled')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), name
            if await page.locator('body').get_attribute('data-state') == 'result':
                assert await page.evaluate('window.scrollY===0'), name
            shots.append({'file':name+'.png','viewport':page.viewport_size})
            (output/'进行中.json').write_text(json.dumps({'checks':checks,'screenshots':shots},ensure_ascii=False,indent=2))
        async def get(path):
            response=await page.request.get(url+path)
            assert response.ok,(path,response.status)
            return await response.json()
        async def result(tid):
            await page.goto(url+'/#task='+tid)
            await expect(page.locator('#result')).to_be_visible()
            await expect(page.locator('#preview-loading')).to_be_hidden(timeout=30000)
        async def panel(name):
            if name == 'delivery': await page.locator('#more-menu summary').click()
            await page.locator('[data-panel="'+name+'"]').click()
            await expect(page.locator('#side-panel')).to_be_visible()
        async def close():
            await page.locator('#panel-close').click()
            await expect(page.locator('#side-panel')).to_be_hidden()
        async def snapshot_download(button, kind, expected):
            async with page.expect_download() as info:
                await button.click()
            download=await info.value
            expected_name=expected['xml_filename'] if kind=='xml' else expected['download_filename']
            assert download.suggested_filename==expected_name
            path=output/(kind+'-'+expected_name)
            await download.save_as(path)
            data=path.read_bytes() if kind=='xml' else zipfile.ZipFile(path).read(expected['xml_filename'])
            assert data.decode()==expected['xml']
            assert hashlib.sha256(data).hexdigest()==expected['version']

        await page.goto(url)
        await expect(page.locator('#recent-section')).to_be_hidden()
        await page.set_viewport_size({'width':1366,'height':768})
        await expect(page.locator('#submit-btn')).to_be_in_viewport()
        await shot('N01-笔记本首页')
        # 新测试稿件：延迟上传，验证导航不会在后台继续提交另一次任务。
        await page.locator('#docx-input').set_input_files(ROOT/'样例数据/01/初始文件.docx')
        async def slow_init(route):
            await asyncio.sleep(1.5)
            await route.continue_()
        await page.route('**/api/upload/init',slow_init)
        await page.locator('#submit-btn').click()
        await expect(page.locator('#progress')).to_be_visible()
        await page.locator('#header-home').click()
        await expect(page.locator('#toast')).to_contain_text('仍在上传')
        await expect(page.locator('#progress')).to_be_visible()
        await shot('N03-上传期间返回保护')
        await expect(page.locator('#result')).to_be_visible(timeout=60000)
        await page.unroute('**/api/upload/init',slow_init)
        tid=page.url.split('task=')[-1]
        before=await get('/api/result/'+tid)
        original=await page.request.get(url+'/api/original/'+tid)
        assert await original.body()==(ROOT/'样例数据/01/初始文件.docx').read_bytes()
        await page.locator('#header-home').click()
        await expect(page.locator('#recent-list .recent-item')).to_have_count(1)
        await page.reload()
        await expect(page.locator('#recent-list .recent-item')).to_have_count(1)
        await shot('N01-近期转换')
        await page.locator('.recent-open').click()
        await expect(page.locator('#result')).to_be_visible()
        assert page.url.endswith(tid)
        checks.append('N01')

        # 跨标签同步与移除只影响本地入口。
        other=await context.new_page(); await other.goto(url)
        await page.locator('#header-home').click()
        await other.locator('.recent-remove').click()
        await expect(page.locator('#recent-section')).to_be_hidden()
        assert (await page.request.get(url+'/api/result/'+tid)).ok
        await other.close()
        await page.evaluate("localStorage.setItem('word2jats.recent.v1','{bad')")
        await page.reload(); await expect(page.locator('#submit-btn')).to_be_visible()
        await result(tid); await page.locator('#header-home').click()
        await expect(page.locator('.recent-item')).to_have_count(1)
        blocked=await browser.new_context()
        await blocked.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new Error('disabled')}})")
        blocked_page=await blocked.new_page(); blocked_page.on('pageerror',lambda e:errors.append(str(e)))
        await blocked_page.goto(url+'/#task='+tid)
        await expect(blocked_page.locator('#result')).to_be_visible()
        await blocked_page.locator('#header-home').click()
        await expect(blocked_page.locator('.recent-item')).to_have_count(1)
        await blocked.close(); checks.append('N02')

        # 已上传任务可离开；首页刷新既有任务状态，不发起新转换。
        status={'task_id':tid,'filename':before['filename'],'status':'running','stage_key':'understand','elapsed':30,'provider':'deepseek'}
        async def hold_status(route): await route.fulfill(json=status)
        await page.route('**/api/status/'+tid,hold_status)
        await result_progress(page,url,tid)
        await shot('N03-转换进度与离开入口')
        await page.locator('#progress-home').click()
        await expect(page.locator('.recent-state')).to_contain_text('查看进度')
        status['status']='done'
        await expect(page.locator('.recent-state')).to_contain_text('查看结果',timeout=12000)
        await page.unroute('**/api/status/'+tid,hold_status)
        await page.locator('.recent-open').click(); await expect(page.locator('#result')).to_be_visible()
        checks.append('N03')

        # 非 HTTPS 或拒绝剪贴板时仍有可复制链接；HTTPS 成功分支在受控 clipboard 中验证。
        await page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('denied')}}})")
        await page.locator('#more-menu summary').click(); await page.locator('#copy-link').click()
        await expect(page.locator('#task-link')).to_have_value(url+'/#task='+tid)
        assert await page.locator('#task-link').evaluate('e=>e.selectionStart===0 && e.selectionEnd===e.value.length')
        await shot('N04-复制链接退路'); await page.locator('#dialog-confirm').click()
        await page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copiedTaskLink=text}})")
        await page.locator('#copy-link').click()
        await expect(page.locator('#toast')).to_contain_text('已复制')
        assert await page.evaluate('window.copiedTaskLink')==url+'/#task='+tid
        checks.append('N04')

        await page.set_viewport_size({'width':1440,'height':1000})
        await result(tid)
        blocks=(await get('/api/workbench/'+tid))['blocks']
        assert max(row.get('depth',0) for row in blocks)>1
        for row in (row for row in blocks if row['navigation']):
            assert await page.frame_locator('#render-frame').locator('[id="'+row['id']+'"]').count()>0
        await shot('N05-分层目录')
        await page.locator('#outline-query').fill('Mathematical')
        assert await page.locator('[data-location]').count()>0
        assert all('mathematical' in t.lower() for t in await page.locator('[data-location]').all_text_contents())
        await shot('N05-目录查找')
        await page.locator('#outline-query').fill('没有这个章节-zzyy')
        await expect(page.locator('#outline-empty')).to_be_visible(); await shot('N05-目录无匹配')
        await page.locator('#outline-query').fill('')
        await page.locator('#outline-toggle').click(); await expect(page.locator('#article-outline')).to_be_hidden()
        await shot('N05-收起目录'); await page.locator('#outline-toggle').click()
        checks.append('N05')

        requests=[]
        page.on('request',lambda request: requests.append(request.url) if '/api/source/' in request.url else None)
        known=[row for row in blocks if row.get('source_anchor')]
        await panel('source'); await page.frame_locator('#source-frame').locator('.source-document').wait_for()
        source=page.frame_locator('#source-frame')
        for row in known[:4]:
            await page.locator('#render-frame').evaluate('(frame,id)=>frame.contentWindow.postMessage({type:"w2j-locate",id},location.origin)',row['id'])
            await page.locator('#render-frame').evaluate('(frame,id)=>frame.contentWindow.eval(`parent.postMessage(${JSON.stringify({type:"w2j-select",id})},location.origin)`) ',row['id'])
            await expect(source.locator('#'+row['source_anchor'])).to_have_class('source-selected')
        assert len(requests)==1,requests
        await shot('N06-原稿定位且无重载')
        old_scroll=await page.locator('#source-frame').evaluate('frame=>frame.contentWindow.scrollY')
        await page.locator('#render-frame').evaluate('frame=>frame.contentWindow.eval(\'parent.postMessage({type:"w2j-select",id:"no-mapping"},location.origin)\')')
        await expect(page.locator('.source-note')).to_contain_text('没有精确对应位置')
        assert await page.locator('#source-frame').evaluate('frame=>frame.contentWindow.scrollY')==old_scroll
        assert len(requests)==1
        await shot('N06-没有精确位置'); await close(); checks.append('N06')

        await panel('delivery'); await expect(page.locator('.delivery-name')).to_have_text(before['download_filename'])
        await shot('N08-成果文件说明')
        await snapshot_download(page.locator('#panel-download-xml'),'xml',before)
        await snapshot_download(page.locator('#panel-download-all'),'all',before)
        await close(); checks.append('N07')
        # 下载失败留在页面，并允许再次下载；重复点击只产生一个请求。
        attempts=0
        async def fail_download(route):
            nonlocal attempts
            attempts+=1; await asyncio.sleep(.6)
            await route.fulfill(status=503,json={'detail':'下载文件暂时无法生成，请稍后重试。'})
        await page.route('**/api/download/*',fail_download)
        await page.locator('#download-btn').click()
        await page.locator('#download-btn').dispatch_event('click')
        await expect(page.locator('#download-error')).to_contain_text('稍后重试')
        assert attempts==1
        assert page.url.endswith(tid)
        await shot('N08-下载失败留在结果')
        await page.unroute('**/api/download/*',fail_download)
        await snapshot_download(page.locator('#download-btn'),'all',before)
        await expect(page.locator('#download-error')).to_be_hidden()

        # 保存中不允许切任务；异步回包不能覆盖别的稿件。
        await panel('article'); await page.locator('[data-field="title"]').fill(before['filename']+' · 已修改')
        await expect(page.locator('#version-label')).to_have_text('有未保存的修改')
        await expect(page.locator('#reader-save-status')).to_contain_text('已保存版本')
        await shot('N10-编辑未保存状态')
        async def slow_save(route): await asyncio.sleep(1.5); await route.continue_()
        await page.route('**/api/edit/*',slow_save)
        await page.locator('#save-edit').click()
        await expect(page.locator('#version-label')).to_have_text('正在保存')
        await page.evaluate("location.hash='task=0000000000000002'")
        await expect(page).to_have_url(url+'/#task='+tid)
        await expect(page.locator('#toast')).to_contain_text('正在保存')
        await shot('N10-保存期间路由保护')
        await expect(page.locator('#toast')).to_contain_text('修改已保存',timeout=30000)
        await page.unroute('**/api/edit/*',slow_save)
        await expect(page.locator('#version-label')).to_have_text('已保存修改')
        after=await get('/api/result/'+tid)
        assert after['version']!=before['version'] and after['stats']['llm']==before['stats']['llm']
        await page.locator('[data-edit-jump="contacts"]').click()
        assert await page.locator('#edit-contacts').evaluate('e=>e.contains(document.activeElement)')
        await shot('N10-通讯信息直达'); await close()
        await snapshot_download(page.locator('#download-btn'),'all',after)
        await panel('delivery'); await expect(page.locator('.delivery-files')).to_contain_text('修改记录.json')
        await shot('N08-已修改成果说明'); await close()
        # 另一个页面保存后，旧页面的下载请求必须拒绝，不能悄悄下载新版本。
        work=await get('/api/workbench/'+tid); work['fields']['title']+=' 另一页面'
        assert (await page.request.post(url+'/api/edit/'+tid,data={'version':work['version'],'fields':work['fields']})).ok
        await page.locator('#download-btn').click()
        await expect(page.locator('#download-error')).to_contain_text('其他页面')
        await shot('N08-下载版本冲突')
        await page.locator('#download-error button').click()
        await expect(page.locator('#download-error')).to_be_hidden()
        latest=await get('/api/result/'+tid)
        await snapshot_download(page.locator('#download-btn'),'all',latest)
        checks.append('N08')

        # 已过期的旧请求不能污染新任务。
        async def delayed_work(route): await asyncio.sleep(1.2); await route.continue_()
        await page.route('**/api/workbench/'+tid,delayed_work)
        await page.goto(url)
        await page.goto(url+'/#task='+tid)
        await expect(page.locator('#progress')).to_be_visible()
        await page.evaluate("location.hash='task=0000000000000002'")
        await expect(page.locator('#result-filename')).to_have_text('学术稿件-01.docx',timeout=30000)
        await expect(page.locator('#result-meta')).to_contain_text('DeepSeek')
        await asyncio.sleep(1.4)
        assert page.url.endswith('0000000000000002')
        await expect(page.locator('#result-filename')).to_have_text('学术稿件-01.docx')
        await page.unroute('**/api/workbench/'+tid,delayed_work); checks.append('N10')

        await result(tid); await panel('checks')
        await expect(page.locator('#panel-content')).to_contain_text('XML')
        await expect(page.locator('#checks-xml')).to_be_visible()
        await shot('N09-问题与处理入口'); await close(); checks.append('N09')

        # 桌面、平板、手机、横屏逐状态截图，保证主要操作和滚动区域可用。
        for width,height,name in [(1366,768,'笔记本'),(1024,768,'平板横屏'),(768,1024,'平板竖屏'),(390,844,'手机'),(844,390,'手机横屏')]:
            await page.set_viewport_size({'width':width,'height':height})
            await page.goto(url); await expect(page.locator('#submit-btn')).to_be_visible(); await shot('N11-'+name+'-首页')
            await result(tid); await shot('N11-'+name+'-结果')
            for mode in ('source','article','publication','checks','usage','delivery'):
                await panel(mode)
                if mode=='source': await page.frame_locator('#source-frame').locator('.source-document').wait_for()
                if mode=='delivery': await expect(page.locator('.delivery-name')).to_be_visible()
                await shot('N11-'+name+'-'+mode)
                if width<=850:
                    await expect(page.locator('#side-panel')).to_have_attribute('role','dialog')
                    await expect(page.locator('.preview-region')).to_have_attribute('inert','')
                await close()
            if width==390:
                await panel('delivery'); await page.route('**/api/download/*',fail_download)
                await page.locator('#panel-download-all').click()
                await expect(page.locator('#panel-download-error')).to_be_visible()
                await shot('N11-手机-下载错误'); await page.unroute('**/api/download/*',fail_download); await close()
                await panel('article'); await page.locator('[data-field="title"]').fill('尚未保存的新题名')
                await page.locator('#panel-close').focus(); await page.keyboard.press('Shift+Tab')
                assert await page.evaluate('document.activeElement.id')=='save-edit'
                await page.keyboard.press('Escape'); await expect(page.locator('#action-dialog')).to_be_visible()
                await shot('N11-手机-未保存确认'); await page.locator('#dialog-cancel').click()
                assert await page.evaluate('document.activeElement.id')=='save-edit'
                await page.locator('#panel-close').click(); await page.locator('#dialog-confirm').click()
                await expect(page.locator('[data-panel="article"]')).to_be_focused()
        checks.append('N11')
        assert not errors, errors
        summary={'functional_passed':sorted(checks),'screenshots':shots,'browser_errors':errors,'task':tid,
                 'visual_review':'待逐张查看；未自动宣称视觉验收完成'}
        (output/'验收清单.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print(json.dumps({'checks':checks,'screenshots':len(shots),'task':tid},ensure_ascii=False))
        await browser.close()


async def result_progress(page,url,tid):
    await page.goto(url+'/#task='+tid)
    await expect(page.locator('#progress-actions')).to_be_visible()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:18641')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web-next/expanded')
    args=parser.parse_args()
    if not args.url.startswith(('http://127.0.0.1:','http://localhost:')):
        parser.error('仅允许本机隔离测试服务')
    asyncio.run(verify(args.url,args.output))


if __name__=='__main__': main()
