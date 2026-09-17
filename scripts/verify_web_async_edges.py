"""延迟与失败条件下的恢复/重转/原稿/键盘验证，只操作夹具中新建任务。"""
import asyncio
import argparse
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:18641'
OUT = ROOT/'reports/web-next/async-edges'


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1366,'height':768})
        errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(URL)
        await page.locator('#docx-input').set_input_files(ROOT/'样例数据/01/初始文件.docx')
        await page.locator('#submit-btn').click()
        await expect(page.locator('#result')).to_be_visible(timeout=60000)
        tid=page.url.split('task=')[-1]
        # 跳转正文只移动焦点，不改变任务地址。
        await page.locator('.skip-link').focus(); await page.keyboard.press('Enter')
        await expect(page.locator('#main')).to_be_focused()
        assert page.url.endswith(tid)
        async def fail_source(route):
            await route.fulfill(status=503,content_type='text/html',body='<p>暂不可用</p>')
        await page.route('**/api/source/'+tid,fail_source)
        await page.locator('[data-panel="source"]').click()
        await expect(page.locator('.source-note')).to_contain_text('可下载 Word 查看')
        assert (await page.request.get(URL+'/api/original/'+tid)).ok
        await page.screenshot(path=OUT/'原稿预览失败.png')
        await page.locator('#panel-close').click(); await page.unroute('**/api/source/'+tid,fail_source)
        await page.locator('[data-panel="article"]').click()
        title=page.locator('[data-field="title"]')
        await title.fill((await title.input_value())+'（交互验证）')
        await page.locator('#save-edit').click()
        await expect(page.locator('#toast')).to_contain_text('修改已保存')
        await page.locator('#panel-close').click()
        async def slow(route):
            await asyncio.sleep(1.8); await route.continue_()
        await page.route('**/api/restore/'+tid,slow)
        await page.locator('#more-menu summary').click(); await page.locator('#restore-btn').click()
        await page.locator('#dialog-confirm').click()
        await expect(page.locator('#version-label')).to_contain_text('正在恢复')
        await page.locator('#header-home').click()
        await expect(page.locator('#toast')).to_contain_text('正在恢复')
        await page.evaluate("location.hash='task=0000000000000002'")
        await expect(page).to_have_url(URL+'/#task='+tid)
        await page.locator('#download-btn').click()
        await expect(page.locator('#toast')).to_contain_text('正在恢复')
        await page.screenshot(path=OUT/'恢复期间离开保护.png')
        await expect(page.locator('#toast')).to_contain_text('已恢复',timeout=30000)
        await page.unroute('**/api/restore/'+tid,slow)
        await page.route('**/api/reconvert/'+tid,slow)
        await page.locator('#more-menu summary').click(); await page.locator('#reconvert-btn').click()
        await page.locator('#dialog-confirm').click()
        await expect(page.locator('#version-label')).to_contain_text('正在创建')
        await page.locator('#header-home').click()
        await expect(page.locator('#toast')).to_contain_text('正在创建')
        await page.screenshot(path=OUT/'重转期间离开保护.png')
        await page.wait_for_url(lambda url:str(url).endswith('task='+tid) is False)
        await expect(page.locator('#result')).to_be_visible(timeout=60000)
        assert (await page.request.get(URL+'/api/result/'+tid)).ok
        assert not errors,errors
        (OUT/'验收.json').write_text(json.dumps({'task':tid,'checks':['跳至正文不改任务地址','原稿失败仍可下载','恢复中离开及下载保护','重转中离开保护','原结果保留'],'errors':errors},ensure_ascii=False,indent=2))
        await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    args=parser.parse_args(); OUT=args.output
    asyncio.run(main())
