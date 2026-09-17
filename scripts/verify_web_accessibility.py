"""对可见页面逐态运行 axe-core；不调用模型、不修改稿件。"""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def verify(url, output, strict):
    script = ROOT/'reports/web-next/check-tools/node_modules/axe-core/axe.min.js'
    rows = []
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page()
        async def audit(name):
            for frame in page.frames:
                await frame.add_script_tag(path=script)
            data=await page.evaluate("axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}})")
            row={'name':name,'violations':data['violations'],'incomplete':data['incomplete'],'passed_rules':len(data['passes'])}
            rows.append(row)
            output.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        for width,height in [(1440,1000),(390,844)]:
            await page.set_viewport_size({'width':width,'height':height})
            await page.goto(url)
            await audit(f'{width}-首页')
            await page.locator('#publication-options summary').click()
            await audit(f'{width}-出版选项')
            await page.goto(url+'/#task=0000000000000001')
            await expect(page.locator('#result')).to_be_visible()
            await expect(page.locator('#preview-loading')).to_be_hidden()
            await audit(f'{width}-结果')
            for mode in ('source','article','publication','checks','usage','delivery'):
                if mode=='delivery': await page.locator('#more-menu summary').click()
                await page.locator(f'[data-panel="{mode}"]').click()
                if mode=='source': await page.frame_locator('#source-frame').locator('.source-document').wait_for()
                if mode=='delivery': await expect(page.locator('.delivery-name')).to_be_visible()
                await audit(f'{width}-{mode}')
                await page.locator('#panel-close').click()
        await browser.close()
    summary=[{'state':row['name'],'violations':[{ 'id':v['id'],'nodes':len(v['nodes'])} for v in row['violations']]} for row in rows]
    print(json.dumps(summary,ensure_ascii=False))
    if strict: assert all(not row['violations'] for row in rows), '仍有无障碍规则违规'


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:18641')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web-next/accessibility.json')
    parser.add_argument('--require-clean',action='store_true')
    args=parser.parse_args()
    asyncio.run(verify(args.url,args.output,args.require_clean))
