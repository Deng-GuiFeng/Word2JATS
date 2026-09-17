"""临时服务中的真实出版设置继承与重启验收；只编辑本脚本创建的任务。"""
import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import zipfile

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'reports/web-closeout/real'
URL = 'http://127.0.0.1:18640'


async def main(action):
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
        async def get(route):
            r=await page.request.get(URL+route,timeout=60000); assert r.ok,(route,r.status)
            return await r.json()
        async def open_task(tid):
            await page.goto(URL+'/#task='+tid)
            await expect(page.locator('#result')).to_be_visible(timeout=600000)
            await expect(page.locator('#preview-loading')).to_be_hidden(timeout=60000)
        async def package(tid):
            data=await get('/api/result/'+tid)
            response=await page.request.get(URL+'/api/download/'+tid+'?version='+data['version'])
            assert response.ok
            with zipfile.ZipFile(io.BytesIO(await response.body())) as z:
                assert hashlib.sha256(z.read(data['xml_filename'])).hexdigest()==data['version']
                assert json.loads(z.read('检查摘要.json'))['xml_sha256']==data['version']
            return data
        if action=='run':
            existing={}
            for path in (ROOT/'webapp/_runs').glob('*/task.json'):
                task=json.loads(path.read_text())
                if task['status']=='done':
                    existing[task['task_id']]=(await get('/api/result/'+task['task_id']))['version']
            (OUT/'existing.json').write_text(json.dumps(existing,indent=2))
            r=await page.request.post(URL+'/api/convert',multipart={
                'docx':{'name':'产品收尾真实验收-S03.docx','mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':(ROOT/'样例数据/S03/初始文件.docx').read_bytes()},
                'provider':'deepseek','journal':'CEOG','doi':'10.31083/CEOG50327'})
            assert r.ok
            tid=(await r.json())['task_id']; await open_task(tid)
            rows=[]
            print('seed ready: '+tid,flush=True)
            await page.locator('[data-panel="article"]').click()
            await page.locator('[data-field="title"]').fill('仅用于验收，不带入重新识别')
            await page.locator('#editor-source').click()
            await page.frame_locator('#source-frame').locator('.source-document').wait_for()
            await page.locator('#return-edit').click()
            await expect(page.locator('[data-field="title"]')).to_have_value('仅用于验收，不带入重新识别')
            await page.locator('#editor-other').click()
            await page.locator('[data-field="publication.doi"]').fill('10.31083/CEOG50327-closeout')
            await page.locator('#save-edit').click()
            await expect(page.locator('#save-state')).to_have_text('未作修改',timeout=30000)
            await page.locator('#panel-close').click()
            expected_pub=(await get('/api/workbench/'+tid))['fields']['publication']
            data=await package(tid)
            rows.append({'task':tid,'version':data['version'],'provider':'seed'})
            for provider in ('deepseek','dashscope'):
                old=tid
                await page.locator('#more-menu summary').click(); await page.locator('#reconvert-btn').click()
                await page.locator('#reconvert-provider').select_option(provider)
                await expect(page.locator('#reconvert-publication')).to_have_value('current')
                await page.locator('#dialog-confirm').click()
                await page.wait_for_function('(old)=>location.hash!=="#task="+old',arg=old)
                tid=page.url.split('#task=')[1]
                print(provider+' running: '+tid,flush=True)
                await expect(page.locator('#result')).to_be_visible(timeout=600000)
                await expect(page.locator('#preview-loading')).to_be_hidden(timeout=60000)
                work=await get('/api/workbench/'+tid)
                assert work['fields']['publication']==expected_pub
                assert work['fields']['title']!='仅用于验收，不带入重新识别'
                data=await package(tid)
                assert data['stats']['llm']['calls']>0
                assert data['stats']['llm']['result_usage']['total_tokens']>0
                assert data['validation']['dtd_valid']
                rows.append({'task':tid,'version':data['version'],'provider':provider,'publication':expected_pub,'llm':data['stats']['llm']})
                await page.locator('[data-panel="publication"]').click()
                await page.screenshot(path=OUT/(provider+'-出版信息.png'))
                await page.locator('#panel-close').click()
                print(provider+' passed',flush=True)
            assert not errors,errors
            (OUT/'before-restart.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        else:
            rows=json.loads((OUT/'before-restart.json').read_text())
            for row in rows:
                tid=row['task']; await open_task(tid)
                data=await package(tid)
                assert data['version']==row['version']
                if 'publication' in row:
                    assert (await get('/api/workbench/'+tid))['fields']['publication']==row['publication']
                    assert data['stats']['llm']==row['llm']
            existing=json.loads((OUT/'existing.json').read_text())
            for tid,sha in existing.items():
                assert (await get('/api/result/'+tid))['version']==sha
            assert not errors,errors
            (OUT/'验收.json').write_text(json.dumps({'tasks':[r['task'] for r in rows], 'existing_unchanged':len(existing),'checks':['实际双模型调用','草稿往返与保存','出版设置继承','文章内容重新识别','原结果保留','XML及成果包一致','重启保留结果与用量'],'errors':errors},ensure_ascii=False,indent=2))
            print('restart and existing-task preservation passed',flush=True)
        await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run','after'])
    asyncio.run(main(parser.parse_args().action))
