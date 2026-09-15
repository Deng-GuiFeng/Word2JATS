"""公网收敛检查：既有任务不变，以及 01 的双模型新转换与实际文件交付。"""
import argparse
import asyncio
import hashlib
import io
import json
import re
from pathlib import Path
import zipfile

from lxml import etree
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://word2jats.jianglab.work'


async def get(page, path):
    data = await page.evaluate('''async path=>{const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(60000)});return {status:r.status,body:await r.json()}}''', path)
    assert data['status'] == 200, (path, data['status'])
    return data['body']


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL)
        await expect(page.locator('#submit-btn')).to_be_visible()
        await page.screenshot(path=args.output/'public-home.png')
        baseline = json.loads((ROOT/'reports/web-public-20260915/release-baseline.json').read_text())
        for row in baseline:
            status = await get(page, '/api/status/'+row['task'])
            assert status['status'] == row['status']
            if row['status'] == 'done':
                result = await get(page, '/api/result/'+row['task'])
                assert hashlib.sha256(result['xml'].encode()).hexdigest() == row['xml_sha256']
                assert result['stats']['llm'] == row['llm']
        (args.output/'existing-tasks.json').write_text(json.dumps({'unchanged':len(baseline),'tasks':[r['task'] for r in baseline]}))
        print('既有任务结果与用量不变：',len(baseline),flush=True)
        await page.close()
        if args.existing_only:
            await browser.close()
            return

        sample = next(s for s in json.loads((ROOT/'样例数据/样例登记.json').read_text())['样例'] if s['key'] == '01')
        source = (ROOT/'样例数据/01/初始文件.docx').read_bytes()
        async def fresh(provider):
            out = args.output/provider
            out.mkdir(exist_ok=True)
            context = await browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
            page = await context.new_page()
            page.set_default_timeout(60000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            task_record = out/'task.json'
            try:
                if task_record.exists():
                    tid = json.loads(task_record.read_text())['task']
                    await page.goto(URL+'/#task='+tid)
                else:
                    await page.goto(URL)
                    await page.locator('#docx-input').set_input_files({'name':'学术稿件-01.docx','mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':source})
                    await page.locator('#provider-input').select_option(provider)
                    await page.locator('#publication-options summary').click()
                    await page.locator('#journal-input').select_option(sample['journal'])
                    await page.locator('#doi-input').fill(sample['doi'])
                    await page.screenshot(path=out/'01-before-upload.png')
                    await page.locator('#submit-btn').click()
                    await page.wait_for_function('location.hash.startsWith("#task=")',timeout=180000)
                    tid = page.url.split('#task=')[1]
                    task_record.write_text(json.dumps({'task':tid,'provider':provider,'source_sha256':hashlib.sha256(source).hexdigest()}))
                    await page.screenshot(path=out/'02-converting.png')
                    print(provider,'新转换已发起',tid,flush=True)
                await expect(page.locator('#result')).to_be_visible(timeout=600000)
                await expect(page.locator('#preview-loading')).to_be_hidden(timeout=90000)
                await expect(page.frame_locator('#render-frame').locator('body[data-w2j-preview]')).to_be_visible()
                result = await get(page, '/api/result/'+tid)
                llm = result['stats']['llm']
                assert llm['calls'] > 0 and llm['usage']['total_tokens'] > 0
                assert not (llm.get('reused_responses') or llm.get('cache_hits'))
                assert result['validation']['ok'], result['validation']
                root = etree.fromstring(result['xml'].encode(), etree.XMLParser(resolve_entities=False,no_network=True))
                # 续表提示会与同一表注合并为一个文本节点，按出现次数核验，不能要求独立节点。
                continued = re.findall(r'\(?Continued\)?', ''.join(root.itertext()))
                assert len(continued) == 2, continued
                await page.screenshot(path=out/'03-result.png')
                await page.locator('[data-panel="usage"]').click()
                await page.screenshot(path=out/'04-usage.png')
                await page.locator('#panel-close').click()
                await page.locator('[data-panel="source"]').click()
                await expect(page.frame_locator('#source-frame').locator('.source-document')).to_be_visible()
                await page.screenshot(path=out/'05-source.png')
                await page.locator('#panel-close').click()
                async with page.expect_download(timeout=120000) as event:
                    await page.locator('#download-btn').click()
                item = await event.value
                assert item.suggested_filename == result['download_filename']
                target = out/item.suggested_filename
                await item.save_as(target)
                with zipfile.ZipFile(target) as bundle:
                    xml = bundle.read(result['xml_filename'])
                    assert xml.decode() == result['xml']
                    assert hashlib.sha256(xml).hexdigest() == result['version']
                    with zipfile.ZipFile(io.BytesIO(bundle.read('figures.zip'))) as figures:
                        names = figures.namelist()
                        assert len([n for n in names if not n.endswith('/')]) == 7
                        for href in root.xpath('//@xlink:href',namespaces={'xlink':'http://www.w3.org/1999/xlink'}):
                            if not href.startswith(('http:','https:','mailto:','#')):
                                assert href in names, href
                await page.locator('#more-menu summary').click()
                async with page.expect_download(timeout=120000) as event:
                    await page.locator('#original-download').click()
                original = await event.value
                await original.save_as(out/'original.docx')
                assert (out/'original.docx').read_bytes() == source
                await page.reload()
                await expect(page.locator('#result')).to_be_visible()
                assert (await get(page,'/api/result/'+tid))['version'] == result['version']
                assert not errors, errors
                (out/'result.json').write_text(json.dumps({'task':tid,'provider':provider,'version':result['version'],'llm':llm,'validation':result['validation'],'continued_textboxes':len(continued),'images':7,'errors':errors},ensure_ascii=False,indent=2))
                print(provider,'完成：新调用、两个实际文本框、7 个图像、下载与原稿字节一致',flush=True)
            except Exception as error:
                (out/'failure.json').write_text(json.dumps({'error':str(error),'page_errors':errors},ensure_ascii=False,indent=2))
                await page.screenshot(path=out/'failure.png')
                raise
            finally:
                await context.close()
        outcomes = await asyncio.gather(fresh('deepseek'),fresh('dashscope'),return_exceptions=True)
        await browser.close()
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                raise outcome


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web-targeted-20260915/fresh-public')
    parser.add_argument('--existing-only',action='store_true')
    asyncio.run(main(parser.parse_args()))
