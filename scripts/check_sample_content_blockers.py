"""以实际重放产物核实明显内容缺陷；诊断记录，不将已知缺陷判通过。"""
import asyncio
import hashlib
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/sample-cache-20260915'


async def main():
    tasks=json.loads((BASE/'preview-v1/tasks.json').read_text())
    out=BASE/'blockers'
    out.mkdir(exist_ok=True)
    cases=[('X01','dashscope','2.5 Data Synthesis and Statistical Analysis','doc/p56',
            'When change-from-baseline SDs were not directly reported'),
           ('X01','deepseek','2.5 Data Synthesis and Statistical Analysis','doc/p56',
            'When change-from-baseline SDs were not directly reported'),
           ('X02','deepseek','Effects of pH and Concentration','doc/p53',
            'In polar solvents such as Decanol and 1-octanol'),
           ('X04','deepseek','Swelling','doc/p62','Swelling (%)')]
    results=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(args=['--disable-dev-shm-usage'])
        for sample,provider,heading,source_node,phrase in cases:
            task=next(t for t in tasks if t['sample']==sample and t['provider']==provider)
            page=await browser.new_page(viewport={'width':1600,'height':1100})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto('http://127.0.0.1:18643/#task='+task['task'])
            await expect(page.locator('#result')).to_be_visible(timeout=90000)
            await expect(page.locator('#preview-loading')).to_be_hidden(timeout=60000)
            frame=page.frame_locator('#render-frame')
            await expect(frame.locator('body[data-w2j-preview]')).to_be_visible()
            texts=await frame.locator('body').inner_text()
            target=frame.locator('h1,h2,h3,h4,h5,h6').filter(has_text=heading).first
            if await target.count():
                await target.evaluate('e=>e.scrollIntoView({block:"start",behavior:"instant"})')
            await page.locator('[data-panel="source"]').click()
            source=page.frame_locator('#source-frame')
            await expect(source.locator('.source-document')).to_be_visible(timeout=60000)
            anchor='s-'+hashlib.sha256(source_node.encode()).hexdigest()[:16]
            source_el=source.locator('[id="'+anchor+'"]')
            await source_el.evaluate('e=>e.scrollIntoView({block:"start",behavior:"instant"})')
            await page.wait_for_timeout(400)
            await page.screenshot(path=out/f'{sample}-{provider}.png')
            source_text=await source_el.inner_text()
            result={'sample':sample,'provider':provider,'phrase':phrase,
                'source_contains':phrase in source_text,'preview_contains':phrase in texts,
                'math_count':await frame.locator('math').count(),
                'source_text':source_text,'errors':errors}
            results.append(result)
            print(sample,provider,'source',result['source_contains'],'output',result['preview_contains'],flush=True)
            await page.close()
        await browser.close()
    (out/'evidence.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':
    asyncio.run(main())
