"""检查已重放样例的实际网页与下载；截图留供逐图审阅，不自动声称视觉通过。"""
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


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', default='selected-preview-v1')
    parser.add_argument('--samples', default='05')
    parser.add_argument('--url', default='http://127.0.0.1:18643')
    args = parser.parse_args()
    directory = BASE / args.preview
    tasks = json.loads((directory / 'tasks.json').read_text())
    if args.samples != 'all':
        tasks = [t for t in tasks if t['sample'] in args.samples.split(',')]
    evidence = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=['--disable-dev-shm-usage'])
        try:
            page = await browser.new_page(viewport={'width':1600,'height':1100}, device_scale_factor=1)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(args.url)
            await expect(page.locator('#upload-title')).to_be_visible()
            await expect(page.locator('input[type=file]')).to_be_attached()
            await page.wait_for_timeout(700)
            await page.screenshot(path=directory/'home.png')
            print('Home: visible upload entry; errors', errors, flush=True)
            for task in tasks:
                pair = task['sample']+'-'+task['provider']
                folder = directory / pair
                folder.mkdir(exist_ok=True)
                errors.clear()
                await page.goto(args.url+'/#task='+task['task'])
                await expect(page.locator('#result')).to_be_visible(timeout=90000)
                await expect(page.locator('#preview-loading')).to_be_hidden(timeout=60000)
                frame = page.frame_locator('#render-frame')
                body = frame.locator('body[data-w2j-preview]')
                await expect(body).to_be_visible()
                await frame.locator('body').evaluate('async e => {await document.fonts.ready; await Promise.all([...document.images].map(i => i.complete ? Promise.resolve() : new Promise(r => {i.onload=r;i.onerror=r})));}')
                await page.wait_for_timeout(700)
                await page.screenshot(path=folder/'front.png')
                dom = await body.evaluate('''e => ({text:e.innerText, headings:[...e.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(n=>({text:n.innerText,id:n.id})), images:[...e.querySelectorAll('img')].map(n=>({src:n.getAttribute('src'),width:n.naturalWidth,height:n.naturalHeight,complete:n.complete})), tables:e.querySelectorAll('table').length, math:e.querySelectorAll('math').length, classes:[...new Set([...e.querySelectorAll('[class]')].map(n=>n.className))].filter(n=>typeof n==='string')})''')
                assert all(i['complete'] and i['width'] > 0 for i in dom['images']), (pair, 'broken image')
                assert len(dom['text']) > 500, (pair, 'empty preview')
                (folder/'preview.txt').write_text(dom.pop('text'))
                async with page.expect_download() as download_info:
                    await page.locator('#download-btn').click()
                download = await download_info.value
                await download.save_as(folder / download.suggested_filename)
                raw = json.loads(Path(task['source_result']).read_text())
                expected = Path(raw['candidate_xml']).read_bytes()
                with zipfile.ZipFile(folder/download.suggested_filename) as z:
                    names = z.namelist()
                    xml_files = [n for n in names if n.endswith('.xml')]
                    assert len(xml_files) == 1, (pair, xml_files)
                    delivered = z.read(xml_files[0])
                    assert delivered == expected, (pair, 'download XML differs')
                    xml = etree.fromstring(delivered)
                    media = xml.xpath('//@xlink:href', namespaces={'xlink':'http://www.w3.org/1999/xlink'})
                    with zipfile.ZipFile(io.BytesIO(z.read('figures.zip'))) as figures:
                        for href in media:
                            if '://' not in href:
                                assert href in figures.namelist(), (pair, 'missing media', href)
                                original = Path(raw['candidate_xml']).parent / href
                                assert figures.read(href) == original.read_bytes(), (pair, 'media differs', href)
                shots = []
                targets = frame.locator('.body > .section, .fig.panel, .table-wrap.panel, .disp-formula, .back > .section, .back > .back-section')
                for index in range(await targets.count()):
                    target = targets.nth(index)
                    await target.evaluate('e=>e.scrollIntoView({block:"start",behavior:"instant"})')
                    await page.wait_for_timeout(140)
                    filename = f'content-{index:03}.png'
                    await page.screenshot(path=folder/filename)
                    shots.append({'file':filename,'id':await target.get_attribute('id'),
                                  'class':await target.get_attribute('class'),
                                  'start':(await target.inner_text())[:180]})
                record = {'pair':pair,'task':task['task'],**dom,'errors':list(errors),
                          'download':download.suggested_filename,'files':names,
                          'xml_sha256':hashlib.sha256(expected).hexdigest(), 'visual_reviewed':False,'screenshots':shots}
                assert not errors, record
                evidence.append(record)
                (folder/'evidence.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
                print(pair, 'preview + download matched;', len(dom['images']), 'images;', dom['tables'], 'tables;', dom['math'], 'formulae', flush=True)
            await page.close()
        finally:
            await browser.close()
    (directory/('review-'+args.samples.replace(',','_')+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
