"""已知共同原因的浏览器回归：隔离夹具或本轮公网测试任务，不创建模型任务。"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import zipfile

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def verify(args):
    args.output.mkdir(parents=True, exist_ok=True)
    tasks = args.tasks.split(',')
    if args.url.startswith('https://'):
        allowed = {json.loads(p.read_text())['task'] for p in (ROOT/'reports/web-public-20260915/round-01').glob('*/convert/result.json')}
        assert args.url == 'https://word2jats.jianglab.work' and set(tasks) <= allowed
    else:
        assert args.url.startswith('http://127.0.0.1:')
    report = {'url':args.url, 'checks':[], 'screenshots':[], 'errors':[]}
    def record():
        (args.output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for tid in tasks:
            context = await browser.new_context(viewport={'width':1440,'height':1000}, accept_downloads=True)
            page = await context.new_page()
            page.set_default_timeout(25000)
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            async def api(path):
                response = await page.evaluate('''async path => { const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(60000)}); return {status:r.status,data:await r.json()}; }''', path)
                assert response['status'] == 200, response
                return response['data']
            async def shot(name):
                file = f'{tid}-{name}.png'
                await page.screenshot(path=args.output/file)
                report['screenshots'].append(file); record()
            async def done(name):
                report['checks'].append({'task':tid,'check':name}); record(); print(tid, name, flush=True)
            async def more():
                if not await page.locator('#more-menu').evaluate('e=>e.open'):
                    await page.locator('#more-menu summary').click()
            async def close():
                if await page.locator('#side-panel').is_visible():
                    await page.locator('#panel-close').click()
                    if await page.locator('#action-dialog').is_visible():
                        await page.locator('#dialog-confirm').click()
            async def ready():
                await expect(page.locator('#result')).to_be_visible(timeout=90000)
                await expect(page.frame_locator('#render-frame').locator('body[data-w2j-preview]')).to_be_visible(timeout=60000)
                await expect(page.locator('#preview-loading')).to_be_hidden(timeout=30000)
            async def download(button, kind, result):
                async with page.expect_download(timeout=120000) as event:
                    await button.click()
                item = await event.value
                name = result['xml_filename'] if kind == 'xml' else result['download_filename']
                assert item.suggested_filename == name
                file = args.output/(tid+'-'+kind+'-'+name)
                await item.save_as(file)
                data = file.read_bytes() if kind == 'xml' else zipfile.ZipFile(file).read(result['xml_filename'])
                assert hashlib.sha256(data).hexdigest() == result['version']
                assert data.decode() == result['xml']
            original = None
            try:
                await page.goto(args.url+'/#task='+tid)
                await ready()
                original = await api('/api/result/'+tid)
                assert not original.get('edited'), '只允许从已恢复的本轮测试任务开始'
                original_work = await api('/api/workbench/'+tid)
                await shot('01-结果页')

                # 多目标引用实际点击；同一篇中覆盖多个文献入口。
                frame = page.frame_locator('#render-frame')
                links = frame.locator('a[href^="#"]')
                candidates = await links.evaluate_all('es=>es.map(e=>e.getAttribute("href")).filter(s=>/^#b[0-9]+$/.test(s))')
                for href in list(dict.fromkeys(candidates))[:4]:
                    await frame.locator(f'a[href="{href}"]').first.click()
                    assert await frame.locator('[id="'+href[1:]+'"]').count()
                    # 浏览器的平滑滚动尚未结束时，锚点已更新但目标仍在屏外。
                    await page.wait_for_function('id=>{const f=document.getElementById("render-frame"),r=f.contentDocument.getElementById(id).getBoundingClientRect();return r.top<f.contentWindow.innerHeight && r.bottom>=0}', arg=href[1:])
                assert not await links.evaluate_all(r'es=>es.some(e=>/\s|%20/.test(e.getAttribute("href")))')
                await done('多文献引用可分别定位')

                # 来源失败、关闭重开、明确重试；晚到响应不可覆写文章信息面板。
                async def source_fail(route):
                    await route.fulfill(status=503, content_type='text/html', body='<p>unavailable</p>')
                source_pattern = '**/api/source/'+tid+'*'
                await page.route(source_pattern, source_fail)
                await page.locator('[data-panel="source"]').click()
                await expect(page.locator('#retry-source')).to_be_visible()
                await shot('02-原稿失败可重试')
                await page.unroute(source_pattern, source_fail)
                await page.locator('#retry-source').click()
                await expect(page.frame_locator('#source-frame').locator('.source-document')).to_be_visible(timeout=60000)
                await close()
                # 成功载入的原稿会保留以供往返阅读；先换面板，再构造一次新加载失败。
                await page.locator('[data-panel="article"]').click(); await close()
                await page.route(source_pattern, source_fail)
                await page.locator('[data-panel="source"]').click()
                await expect(page.locator('#retry-source')).to_be_visible()
                await close(); await page.unroute(source_pattern, source_fail)
                await page.locator('[data-panel="source"]').click()
                await expect(page.frame_locator('#source-frame').locator('.source-document')).to_be_visible(timeout=60000)
                await close()
                async def source_slow(route):
                    await asyncio.sleep(1.5); await route.continue_()
                await page.route(source_pattern, source_slow)
                await page.locator('[data-panel="article"]').click()
                await page.locator('#editor-source').click()
                await page.locator('[data-panel="article"]').click()
                await page.wait_for_timeout(1800)
                await expect(page.locator('#edit-form')).to_be_visible()
                assert await page.locator('#source-frame').count() == 0
                await page.unroute(source_pattern, source_slow)
                await shot('03-晚到原稿不覆盖编辑'); await done('原稿失败重试及异步面板隔离')

                # 长表单错误精准定位，错误拒绝后结果不变；跨面板保留草稿。
                fields = original_work['fields']
                index = len(fields['authors'])-1
                error_field = f'authors.{index}.orcid' if index >= 0 else 'title'
                invalid = 'invalid' if index >= 0 else ''
                await page.locator(f'[data-field="{error_field}"]').fill(invalid)
                await page.locator('#editor-other').click()
                await page.locator('#save-edit').click()
                await expect(page.locator('#edit-error')).to_be_visible()
                await expect(page.locator(f'[data-field="{error_field}"]')).to_be_focused()
                await expect(page.locator(f'[data-field="{error_field}"]')).to_have_attribute('aria-invalid','true')
                assert (await api('/api/result/'+tid))['version'] == original['version']
                await shot('04-跨面板定位具体错误')
                await close()
                await done('错误字段定位、输入保留、拒绝保存不改结果')

                await page.locator('[data-panel="publication"]').click()
                custom = {'publication.title':'验收自定义期刊','publication.issn_print':'1530-6550','publication.issn_electronic':'','publication.publisher':'验收出版方'}
                await page.locator('#editor-journal').select_option('')
                for path, value in custom.items():
                    await page.locator(f'[data-field="{path}"]').fill(value)
                await page.locator('#save-edit').click()
                await expect(page.locator('#toast')).to_contain_text('修改已保存',timeout=90000)
                await ready()
                await expect(page.locator('#editor-journal')).to_have_value('')
                await expect(page.locator('#editor-journal option:checked')).to_have_text('自定义期刊信息')
                edited = await api('/api/result/'+tid)
                assert edited['stats']['llm'] == original['stats']['llm']
                await page.reload(); await ready()
                await page.locator('[data-panel="publication"]').click()
                await expect(page.locator('#editor-journal')).to_have_value('')
                await expect(page.locator('[data-field="publication.title"]')).to_have_value(custom['publication.title'])
                await shot('05-自定义期刊保存与刷新')
                await close(); await more()
                await download(page.locator('#download-xml'), 'xml', edited)
                await download(page.locator('#download-btn'), 'all', edited)
                await done('自定义期刊保存、刷新、XML与成果包一致且用量不变')

                # 前一次成功提示不得与本次下载失败混杂。
                async def download_fail(route):
                    await route.fulfill(status=503, json={'detail':'下载暂时未能完成，请重试。'})
                await page.route('**/api/download/'+tid+'*', download_fail)
                await page.locator('#download-btn').click()
                await expect(page.locator('#download-error')).to_be_visible()
                await expect(page.locator('#toast')).to_be_hidden()
                await shot('06-下载失败无旧成功提示')
                await page.unroute('**/api/download/'+tid+'*', download_fail)
                await download(page.locator('#download-btn'), 'all', edited)
                await done('下载失败提示与重试')

                # 恢复与慢预览分离：XML 已恢复时，不露出上一版本。
                async def preview_slow(route):
                    await asyncio.sleep(2); await route.continue_()
                await page.route('**/api/render/'+tid+'*', preview_slow)
                await more(); await page.locator('#restore-btn').click(); await page.locator('#dialog-confirm').click()
                await expect(page.locator('#toast')).to_contain_text('已恢复',timeout=90000)
                await expect(page.locator('#preview-loading')).to_be_visible()
                await shot('07-恢复后预览载入状态')
                await ready(); await page.unroute('**/api/render/'+tid+'*', preview_slow)
                assert (await api('/api/result/'+tid))['version'] == original['version']
                await done('恢复结果及预览版本同步')

                async def preview_fail(route):
                    await route.fulfill(status=200, content_type='text/html', body='<p>暂时无法显示预览</p>')
                await page.route('**/api/render/'+tid+'*', preview_fail)
                await page.reload(); await expect(page.locator('#retry-preview')).to_be_visible(timeout=90000)
                await shot('08-预览失败可重试')
                await page.locator('#xml-tab').click()
                await expect(page.locator('#xml-view')).to_contain_text('<article')
                await page.locator('#preview-tab').click()
                await page.unroute('**/api/render/'+tid+'*', preview_fail)
                await page.locator('#retry-preview').click(); await ready()
                await done('预览失败重试、XML退路')

                async def result_fail(route):
                    await route.fulfill(status=503, json={'detail':'unavailable'})
                await page.route('**/api/result/'+tid, result_fail)
                await page.reload()
                await expect(page.locator('#connection-notice')).to_be_visible(timeout=30000)
                await expect(page.locator('#progress-title')).to_have_text('正在读取任务')
                await expect(page.locator('#progress-detail')).to_contain_text('不会重新发起转换')
                await shot('09-读取失败不假报排队')
                await page.unroute('**/api/result/'+tid, result_fail); await ready()
                await done('已有结果读取中断与自动恢复')

                async def failed_task(route):
                    await route.fulfill(json={'status':'error','filename':original['filename'],'error':'Word 文件损坏，无法读取。','provider':original['provider']})
                await page.route('**/api/status/'+tid, failed_task)
                await page.reload(); await expect(page.locator('#error')).to_be_visible(timeout=30000)
                await page.locator('#retry-task-btn').click()
                await expect(page.locator('#dialog-body')).to_contain_text('若文件损坏')
                assert '保留当前结果' not in await page.locator('#dialog-body').inner_text()
                await shot('11-失败任务重试说明')
                await page.locator('#dialog-cancel').click()
                await page.unroute('**/api/status/'+tid, failed_task)
                await page.reload(); await ready()
                await done('失败任务重试说明不混入成功结果口径')

                await page.set_viewport_size({'width':390,'height':844})
                await page.locator('[data-panel="article"]').click()
                await page.locator('[data-field="title"]').fill('')
                await page.locator('#save-edit').click()
                await expect(page.locator('[data-field="title"]')).to_be_focused()
                await expect(page.locator('#edit-error')).to_be_in_viewport()
                await shot('10-手机错误定位'); await close()
                await done('手机编辑错误可见且可操作')
                after = await api('/api/result/'+tid)
                assert after['version'] == original['version'] and after['stats']['llm'] == original['stats']['llm']
                await done('测试结束原版本与用量恢复')
            except Exception as error:
                report['errors'].append({'task':tid,'error':str(error)})
                try:
                    await shot('failure')
                except Exception:
                    pass
                record()
                raise
            finally:
                await page.unroute_all(behavior='wait')
                if original:
                    # 只恢复本脚本的测试任务；现场清理独立记证据，不能吞掉失败。
                    try:
                        await page.set_viewport_size({'width':1440,'height':1000})
                        await page.reload(); await ready(); await close(); await more()
                        if await page.locator('#restore-btn').is_enabled():
                            await page.locator('#restore-btn').click(); await page.locator('#dialog-confirm').click()
                            await expect(page.locator('#toast')).to_contain_text('已恢复',timeout=90000)
                        final = await api('/api/result/'+tid)
                        assert final['version'] == original['version']
                        report.setdefault('restored',[]).append(tid)
                    except Exception as error:
                        report['errors'].append({'task':tid,'cleanup':str(error)})
                record(); await context.close()
        await browser.close()
    assert not report['errors'], report['errors']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18642')
    parser.add_argument('--tasks', default='0000000000000001,0000000000000002,0000000000000003,0000000000000004')
    parser.add_argument('--output', type=Path, default=ROOT/'reports/web-targeted-20260915/local')
    asyncio.run(verify(parser.parse_args()))
