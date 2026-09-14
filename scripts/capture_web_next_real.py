"""重启后只读打开四份真实结果，记录当前代码的界面及浏览器错误。"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/web-next/real-current'
URL='http://127.0.0.1:18640'


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    shots=[]; errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        for folder in ('real-deepseek','real-qwen','reuse-deepseek','reuse-qwen'):
            tid=json.loads((OUT.parent/folder/'验收.json').read_text())['task_id']
            await page.goto(URL+'/#task='+tid)
            await expect(page.locator('#result')).to_be_visible()
            await expect(page.locator('#preview-loading')).to_be_hidden()
            frame=page.frame_locator('#render-frame')
            await frame.locator('.document-title').wait_for()
            for mode in ('result','source','article','usage','delivery'):
                if mode!='result':
                    if mode=='delivery': await page.locator('#more-menu summary').click()
                    await page.locator(f'[data-panel="{mode}"]').click()
                    if mode=='source': await page.frame_locator('#source-frame').locator('.source-document').wait_for()
                    if mode=='delivery': await expect(page.locator('.delivery-name')).to_be_visible()
                    if mode=='usage' and folder.startswith('reuse'):
                        await expect(page.locator('#panel-content')).to_contain_text('没有新增消耗')
                        assert await page.locator('.usage-stat strong').first.inner_text()!='0'
                name=f'{folder}-{mode}.png'
                await page.screenshot(path=OUT/name,animations='disabled')
                shots.append({'file':name,'viewport':page.viewport_size})
                if mode!='result': await page.locator('#panel-close').click()
        assert not errors,errors
        (OUT/'验收.json').write_text(json.dumps({'checks':['重启后四任务浏览器打开','预览与原稿','编辑表单','复用用量','实际下载清单'],'screenshots':shots,'errors':errors},ensure_ascii=False,indent=2))
        await browser.close()


if __name__=='__main__': asyncio.run(main())
