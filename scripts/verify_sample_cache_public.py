"""从网页上传真实稿件，核验缓存、预览、原稿对照及实际下载；不触发重新转换。"""
import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import zipfile

from lxml import etree
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/sample-cache-20260915'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


async def get(page, path):
    response = await page.evaluate('''async path => {
        const r = await fetch(path, {cache:'no-store', signal:AbortSignal.timeout(60000)});
        return {status:r.status, body:await r.json()};
    }''', path)
    assert response['status'] == 200, (path, response['status'])
    return response['body']


async def preview(page):
    await expect(page.locator('#result')).to_be_visible(timeout=300000)
    await expect(page.locator('#preview-loading')).to_be_hidden(timeout=90000)
    body = page.frame_locator('#render-frame').locator('body[data-w2j-preview]')
    await expect(body).to_be_visible()
    await body.evaluate('''async e => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(i => i.complete ? Promise.resolve() :
            new Promise(r => {i.onload=r; i.onerror=r;})));
    }''')
    return body


async def verify(browser, args, sample, provider, *, upload=None):
    pair = sample['key'] + '-' + provider
    out = args.output / pair
    out.mkdir(parents=True, exist_ok=False)
    source = ROOT / '样例数据' / sample['key'] / '初始文件.docx'
    raw = source.read_bytes() if upload is None else upload['buffer']
    upload_name = sample['key']+'-稿件.docx' if upload is None else upload['name']
    context = await browser.new_context(viewport={'width':1600,'height':1100}, accept_downloads=True)
    page = await context.new_page()
    page.set_default_timeout(60000)
    errors, shots = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    record = {'sample':sample['key'], 'provider':provider, 'url':args.url,
              'source_sha256':hashlib.sha256(raw).hexdigest(), 'visual_reviewed':False,
              'upload_name':upload_name,
              'archive_member':None if upload is None else upload['member']}
    async def shot(name):
        await page.wait_for_timeout(220)
        await page.screenshot(path=out/(name+'.png'))
        shots.append(name+'.png')
    try:
        await page.goto(args.url)
        await expect(page.locator('#upload-title')).to_be_visible()
        # 默认改名测试；指定压缩包输入时保留包内实际文件名和字节。
        await page.locator('#docx-input').set_input_files({
            'name':upload_name,
            'mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'buffer':raw})
        await page.locator('#provider-input').select_option(provider)
        if not args.default_publication:
            await page.locator('#publication-options summary').click()
            await page.locator('#journal-input').select_option(sample['journal'])
            await page.locator('#doi-input').fill(sample['doi'])
        await shot('01-upload')
        await page.locator('#submit-btn').click()
        await page.wait_for_function('location.hash.startsWith("#task=")', timeout=180000)
        tid = page.url.split('#task=')[1]
        record['task'] = tid
        dump(out/'task.json', record)
        await shot('02-progress')
        body = await preview(page)
        await shot('03-result')
        result = await get(page, '/api/result/'+tid)
        llm = result['stats']['llm']
        assert llm['calls'] == 0 and llm['cache_misses'] == 0, llm
        assert llm['result_usage']['complete'] and llm['result_usage']['available']
        assert llm['result_usage']['input_tokens'] > 0 and llm['result_usage']['output_tokens'] > 0
        assert llm['incremental_usage']['total_tokens'] == 0
        if args.default_publication:
            assert not result['validation']['dtd_valid']
            await expect(page.locator('#alert-action')).to_have_text('补充出版信息')
            await page.locator('#alert-action').click()
            await page.locator('#editor-journal').select_option(sample['journal'])
            await page.locator('[data-field="publication.doi"]').fill(sample['doi'])
            await shot('04-publication-edit')
            await page.locator('#save-edit').click()
            await expect(page.locator('#save-state')).to_have_text('未作修改')
            await expect(page.locator('#save-edit')).to_be_disabled()
            await page.locator('#panel-close').click()
            body = await preview(page)
            result = await get(page, '/api/result/'+tid)
            assert result['stats']['llm']['result_usage'] == llm['result_usage']
            current_work = await get(page, '/api/workbench/'+tid)
            assert not any(i['title'] in {'缺少期刊信息','部分内容需要进一步检查'} for i in current_work['issues']), current_work['issues']
            await shot('05-publication-saved')
        assert result['validation']['dtd_valid'], result['validation']
        xml = result['xml'].encode()
        tree = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
        expected = json.loads((args.expected/pair/'result.json').read_text())
        if not args.default_publication:
            assert xml == Path(expected['xml']).read_bytes(), '网页转换与已验收重放的 XML 不一致'
            assert llm['result_usage'] == expected['usage']['result_usage']
        dom = await body.evaluate('''e => ({
            textLength:e.innerText.length,
            images:[...e.querySelectorAll('img')].map(i=>({src:i.getAttribute('src'),width:i.naturalWidth,height:i.naturalHeight})),
            unavailableImages:e.querySelectorAll('.w2j-media-fallback').length,
            brokenInternalLinks:[...e.querySelectorAll('a[href^="#"]')].map(a=>a.getAttribute('href').slice(1)).filter(id=>id && !document.getElementById(id) && !document.getElementsByName(id).length),
            tables:e.querySelectorAll('table').length,
            phantomColumns:[...e.querySelectorAll('colgroup')].filter(c=>!c.children.length).length
        })''')
        assert dom['textLength'] > 500
        assert all(i['width'] > 0 and i['height'] > 0 for i in dom['images'])
        assert dom['unavailableImages'] == 0
        assert not dom['brokenInternalLinks'], dom['brokenInternalLinks']
        assert dom['phantomColumns'] == 0
        frame = page.frame_locator('#render-frame')
        citations = frame.locator('.body a[href^="#b"]')
        if pair == '02-deepseek':
            citations = frame.locator('.body a[href="#b19"]')
            assert await citations.count(), '混合著录的第 19 条文献没有可点击入口'
        if await citations.count():
            target = (await citations.first.get_attribute('href'))[1:]
            await citations.first.click()
            document_frame = await (await page.locator('#render-frame').element_handle()).content_frame()
            # 浏览器平滑滚动长文需要时间，等实际定位完成，不用固定 250 ms 猜测。
            await document_frame.wait_for_function('''id=>{
                const n=document.getElementById(id)||document.getElementsByName(id)[0];
                const top=n.getBoundingClientRect().top;
                return top>=-2 && top<window.innerHeight;
            }''', arg=target, timeout=10000)
            await shot('06-citation-jump')
        for kind, selector in [('figure','.fig.panel'), ('table','.table-wrap.panel'),
                               ('formula','.disp-formula'), ('references','.back .ref-list')]:
            nodes = frame.locator(selector)
            if await nodes.count():
                await nodes.first.evaluate('e=>e.scrollIntoView({block:"start",behavior:"instant"})')
                await shot('06-'+kind)
        await page.locator('[data-panel="usage"]').click()
        await expect(page.locator('#panel-title')).to_have_text('转换用量')
        await shot('07-usage')
        await page.locator('#panel-close').click()
        await page.locator('[data-panel="source"]').click()
        await expect(page.frame_locator('#source-frame').locator('.source-document')).to_be_visible(timeout=90000)
        await shot('08-source')
        await page.locator('#panel-close').click()
        async with page.expect_download(timeout=120000) as event:
            await page.locator('#download-btn').click()
        download = await event.value
        assert download.suggested_filename == result['download_filename']
        await download.save_as(out/download.suggested_filename)
        media_count = 0
        with zipfile.ZipFile(out/download.suggested_filename) as bundle:
            assert bundle.read(result['xml_filename']) == xml
            with zipfile.ZipFile(io.BytesIO(bundle.read('figures.zip'))) as media:
                for href in tree.xpath('//@xlink:href', namespaces={'xlink':'http://www.w3.org/1999/xlink'}):
                    if not href.startswith(('http:','https:','mailto:','#')):
                        assert href in media.namelist(), href
                        if not args.default_publication:
                            assert media.read(href) == (Path(expected['xml']).parent/href).read_bytes(), href
                        media_count += 1
        await page.locator('#more-menu summary').click()
        async with page.expect_download(timeout=120000) as event:
            await page.locator('#original-download').click()
        original = await event.value
        await original.save_as(out/'downloaded-original.docx')
        assert (out/'downloaded-original.docx').read_bytes() == raw
        await page.reload()
        await preview(page)
        assert (await get(page, '/api/result/'+tid))['version'] == result['version']
        assert not errors, errors
        status = await get(page, '/api/status/'+tid)
        record.update(status='passed', elapsed=status['elapsed'], version=result['version'],
                      llm=llm, validation=result['validation'], dom=dom,
                      media_references=media_count, download=download.suggested_filename,
                      screenshots=shots, errors=errors, default_publication=args.default_publication)
        dump(out/'evidence.json', record)
        print(pair, 'PASS',tid,'seconds',status['elapsed'],'media',media_count,'calls',llm['calls'], flush=True)
        return record
    except Exception as error:
        record.update(status='failed', error=str(error), errors=errors, screenshots=shots)
        dump(out/'failure.json', record)
        await page.screenshot(path=out/'failure.png')
        raise
    finally:
        await context.close()


async def main(args):
    args.output.mkdir(parents=True, exist_ok=False)
    samples = json.loads((ROOT/'样例数据/样例登记.json').read_text())['样例']
    samples = [s for s in samples if args.samples == 'all' or s['key'] in args.samples.split(',')]
    assert samples
    evidence = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=['--disable-dev-shm-usage'])
        try:
            for sample in samples:
                for provider in args.providers.split(','):
                    assert provider in {'deepseek','dashscope'}
                    evidence.append(await verify(browser,args,sample,provider))
                    dump(args.output/'summary.json',evidence)
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://word2jats.jianglab.work')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected', type=Path, default=BASE/'selected-replay-v5')
    parser.add_argument('--samples', default='all')
    parser.add_argument('--providers', default='deepseek,dashscope')
    parser.add_argument('--default-publication', action='store_true')
    asyncio.run(main(parser.parse_args()))
