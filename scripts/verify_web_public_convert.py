"""14 例 × 双模型：公网前端真实新上传、处理及返回路径，禁止缓存结果代替调用。"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect

from scripts.web_public_evidence import Evidence, ROOT, URL, get, write_json


async def convert(browser, sample, provider, output):
    key = sample['key']
    folder = output / (key + '-' + provider) / 'convert'
    if (folder / 'result.json').exists():
        print(f'{key} {provider}: 已有本轮任务，继续后续验收，不重复发起', flush=True)
        return
    if (folder / 'task.json').exists():
        context = await browser.new_context(viewport={'width':1440,'height':1000})
        page = await context.new_page()
        evidence = Evidence(page, folder)
        await evidence.start()
        row = json.loads((folder / 'task.json').read_text())
        try:
            async def recover():
                await page.goto(URL+'/#task='+row['task'])
                await page.wait_for_function('document.body.dataset.state==="result" || document.body.dataset.state==="error"',timeout=1800000)
                await expect(page.locator('#result')).to_be_visible(timeout=90000)
                await expect(page.locator('#preview-loading')).to_be_hidden(timeout=90000)
                result = await get(page,'/api/result/'+row['task'])
                llm = result['stats']['llm']
                assert llm['calls']>0 and llm['usage']['total_tokens']>0
                assert (llm.get('reused_responses') or llm.get('cache_hits') or 0)==0
                r = await page.request.get(URL+'/api/original/'+row['task'],timeout=180000)
                assert hashlib.sha256(await r.body()).hexdigest()==row['source_sha256']
                write_json(folder/'result.json',{**row,'version':result['version'],'llm':llm,'validation':result['validation']})
                print(f'{key} {provider}: 接续已发起的本轮新任务验证完成',flush=True)
            await evidence.step('P06 接续新转换任务核验',recover)
        finally:
            await evidence.stop(); await context.close()
        return
    context = await browser.new_context(viewport={'width':1440, 'height':1000},
                                       accept_downloads=True, permissions=['clipboard-read', 'clipboard-write'])
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()
    page.set_default_timeout(20000)
    evidence = Evidence(page, folder)
    await evidence.start()
    path = ROOT / '样例数据' / key / '初始文件.docx'
    tid = None
    try:
        await evidence.step('U01 公网首页与空状态', lambda: page.goto(URL))
        await expect(page.locator('#submit-btn')).to_be_disabled()
        await evidence.step('U02 键盘跳到主要内容', lambda: page.locator('.skip-link').press('Enter'))
        for name, payload, expected in [
            ('非 Word', {'name':'错误.pdf','mimeType':'application/pdf','buffer':b'not-word'}, '.docx'),
            ('空文件', {'name':'空.docx','mimeType':'application/octet-stream','buffer':b''}, '为空')]:
            async def invalid(payload=payload, expected=expected):
                await page.locator('#docx-input').set_input_files(payload)
                await expect(page.locator('#upload-error')).to_contain_text(expected)
                await expect(page.locator('#submit-btn')).to_be_disabled()
            await evidence.step('U03 '+name, invalid)
        async def multiple():
            await page.evaluate('''()=>{const d=new DataTransfer();d.items.add(new File(['x'],'a.docx'));d.items.add(new File(['x'],'b.docx'));document.querySelector('#drop').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:d}));}''')
            await expect(page.locator('#upload-error')).to_contain_text('每次转换一篇')
        await evidence.step('U04 多文件拖入', multiple)
        async def too_large():
            await page.evaluate('''()=>{const f=new File(['x'],'超限.docx');Object.defineProperty(f,'size',{value:301*1024*1024});const d=new DataTransfer();d.items.add(f);document.querySelector('#drop').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:d}));}''')
            await expect(page.locator('#upload-error')).to_contain_text('300 MB')
        await evidence.step('U05 超限文件提示', too_large)
        async def choose():
            async with page.expect_file_chooser() as chooser:
                await page.locator('#drop').click()
            await (await chooser.value).set_files(path)
            await expect(page.locator('#submit-btn')).to_be_enabled()
        await evidence.step('U06 文件选择器', choose)
        await evidence.step('U07 移除文件', lambda: page.locator('#remove-file').click())
        await expect(page.locator('#submit-btn')).to_be_disabled()
        await evidence.step('U08 重新选择文件', choose)
        for value in ('deepseek', 'dashscope', provider):
            await evidence.step('U09 选择模型 '+value, lambda value=value: page.locator('#provider-input').select_option(value))
        await evidence.step('U10 展开出版信息', lambda: page.locator('#publication-options summary').click())
        await page.locator('#journal-input option[value="'+sample['journal']+'"]').wait_for(state='attached')
        for value in ('', sample['journal']):
            await evidence.step('U11 期刊 '+(value or '暂不设置'), lambda value=value: page.locator('#journal-input').select_option(value))
        await evidence.step('U12 输入 DOI', lambda: page.locator('#doi-input').fill(sample['doi']))
        await evidence.step('U13 收起出版信息', lambda: page.locator('#publication-options summary').click())

        # 仅当前浏览器中第一次分片模拟断线；重试后均穿过公网真实接口。
        attempts = 0
        async def first_chunk(route):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await route.abort('connectionfailed')
            else:
                await asyncio.sleep(.6)
                await route.continue_()
        await page.route('**/api/upload/*/0', first_chunk)
        async def submit():
            await page.locator('#submit-btn').click()
            await expect(page.locator('#progress')).to_be_visible()
            await page.locator('#header-home').click()
            await expect(page.locator('#toast')).to_contain_text('仍在上传')
            await page.wait_for_function('location.hash.startsWith("#task=")', timeout=180000)
        ok, _ = await evidence.step('P01 上传断线重传与上传中导航保护', submit)
        if not ok:
            return
        tid = page.url.split('#task=')[1]
        write_json(folder / 'task.json', {'sample':key,'provider':provider,'task':tid,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        print(f'{key} {provider}: 真实转换 {tid}', flush=True)
        assert attempts >= 2
        await page.unroute('**/api/upload/*/0', first_chunk)
        await page.locator('#progress-actions').wait_for(state='visible', timeout=30000)
        await evidence.step('P02 复制处理中链接', lambda: page.locator('#progress-link').click())
        async def leave_return():
            await page.locator('#progress-home').click()
            await expect(page.locator('#recent-list')).to_contain_text('初始文件.docx')
            await page.locator('.recent-open[href="#task='+tid+'"]').click()
            await expect(page.locator('#progress')).to_be_visible()
        await evidence.step('P03 返回首页与最近记录继续', leave_return)
        await evidence.step('P04 刷新继续处理', lambda: page.reload())
        async def disconnect():
            await context.set_offline(True)
            await expect(page.locator('#connection-notice')).to_be_visible(timeout=30000)
            await evidence.shot('offline')
            await context.set_offline(False)
            await expect(page.locator('#connection-notice')).to_be_hidden(timeout=30000)
        await evidence.step('P05 处理中断线重连', disconnect)
        await context.set_offline(False)
        async def wait_result():
            await page.wait_for_function('document.body.dataset.state==="result" || document.body.dataset.state==="error"', timeout=1800000)
            state = await page.locator('body').get_attribute('data-state')
            assert state == 'result', await page.locator('#error-msg').inner_text()
            await expect(page.locator('#preview-loading')).to_be_hidden(timeout=90000)
            result = await get(page, '/api/result/'+tid)
            llm = result['stats']['llm']
            assert llm['calls'] > 0, '未发生真实模型调用'
            usage = llm.get('incremental_usage') or llm['usage']
            assert usage['total_tokens'] > 0, '无新增接口用量'
            assert (llm.get('reused_responses') or llm.get('cache_hits') or 0) == 0, '本次首次转换复用了本地模型结果'
            response = await page.request.get(URL+'/api/original/'+tid,timeout=180000)
            assert hashlib.sha256(await response.body()).hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest()
            write_json(folder / 'result.json', {'sample':key,'provider':provider,'task':tid,
                       'version':result['version'],'llm':llm,'validation':result['validation'],
                       'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            print(f'{key} {provider}: 新调用完成，{llm["calls"]} 次，新增 {usage["total_tokens"]} Token', flush=True)
        await evidence.step('P06 全新转换完成与原稿字节校验', wait_result)
        await evidence.step('R01 完成后刷新并保持结果', lambda: page.reload())
        await expect(page.locator('#result')).to_be_visible(timeout=60000)
        await expect(page.locator('#preview-loading')).to_be_hidden(timeout=60000)
    except Exception as error:
        evidence.issue('scenario-interruption', str(error))
        await evidence.shot('interrupted')
        print(f'{key} {provider}: 路径中断，已记录 {error}', flush=True)
    finally:
        await context.set_offline(False)
        await evidence.stop()
        await context.tracing.stop(path=folder/'trace.zip')
        await context.close()


async def main(args):
    samples = json.loads((ROOT/'样例数据/样例登记.json').read_text())['样例']
    if args.samples:
        samples = [s for s in samples if s['key'] in args.samples.split(',')]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        gate = asyncio.Semaphore(args.concurrency)
        async def case(sample, provider):
            async with gate:
                await convert(browser, sample, provider, args.output)
        await asyncio.gather(*(case(sample, provider) for sample in samples for provider in ('deepseek','dashscope')))
        await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'reports/web-public-20260915/round-01')
    parser.add_argument('--samples')
    parser.add_argument('--concurrency', type=int, default=2)
    asyncio.run(main(parser.parse_args()))
