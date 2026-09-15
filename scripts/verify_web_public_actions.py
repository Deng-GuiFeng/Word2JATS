"""对本轮公网新任务逐例执行完整结果页功能，不改既有用户任务。"""
import argparse
import asyncio
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree
from playwright.async_api import async_playwright, expect

from scripts.web_public_evidence import Evidence, ROOT, URL, get, write_json

VIEWPORTS = [('桌面',1440,1000),('笔记本',1366,768),('平板横屏',1024,768),
             ('平板竖屏',768,1024),('手机',390,844),('手机横屏',844,390)]


class Journey:
    def __init__(self, page, record, folder):
        self.page, self.tid, self.record = page, record['task'], record
        self.e = Evidence(page, folder)
        self.downloads, self.reconversions = [], []

    async def click(self, selector):
        await self.page.locator(selector).click()

    async def ready(self):
        await expect(self.page.locator('#result')).to_be_visible(timeout=90000)
        await expect(self.page.locator('#preview-loading')).to_be_hidden(timeout=90000)

    async def reset_view(self):
        target=URL+'/#task='+self.tid
        if self.page.url==target:
            await self.page.reload()
        else:
            await self.page.goto(target)
        await self.ready()

    async def close(self):
        if await self.page.locator('#side-panel').is_visible():
            await self.click('#panel-close')
            if await self.page.locator('#action-dialog').is_visible():
                await self.click('#dialog-confirm')
            await expect(self.page.locator('#side-panel')).to_be_hidden()

    async def more(self):
        if not await self.page.locator('#more-menu').evaluate('e=>e.open'):
            await self.click('#more-menu summary')

    async def panel(self, mode):
        if await self.page.locator('#side-panel').is_visible():
            expected = {'article':'文章信息','publication':'出版信息','source':'Word 原稿',
                        'checks':'检查详情','usage':'转换用量','delivery':'下载文件说明'}[mode]
            if await self.page.locator('#panel-title').inner_text() == expected:
                return
            if self.page.viewport_size['width'] <= 850:
                await self.close()
        if mode == 'delivery':
            await self.more()
        await self.click('[data-panel="'+mode+'"]')
        await expect(self.page.locator('#side-panel')).to_be_visible()
        if mode == 'source':
            await self.page.frame_locator('#source-frame').locator('.source-document').wait_for(timeout=60000)
        elif mode == 'delivery':
            await expect(self.page.locator('.delivery-name')).to_be_visible(timeout=60000)

    async def step(self, name, operation):
        return await self.e.step(name, operation)

    async def chapter(self, name, operation):
        before=len(self.e.issues)
        ok, _ = await self.step(name, operation)
        self.e.event('chapter-end',chapter=name,status='passed' if ok and len(self.e.issues)==before else 'issues-found')
        if not ok:
            await self.page.unroute_all(behavior='wait')
            await self.page.context.set_offline(False)
            await self.page.set_viewport_size({'width':1440,'height':1000})
            await self.reset_view()

    async def scroll_all(self, selector, name, frame=False):
        node = self.page.locator(selector)
        self.e.label = name
        if frame:
            await node.evaluate('''async e=>{
              while(e.contentDocument.readyState!=="complete") await new Promise(r=>setTimeout(r,50));
              await e.contentDocument.fonts.ready;
              await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
            }''')
        await node.evaluate('(e,isFrame)=>{const w=isFrame?e.contentWindow:e;w.scrollTo({top:0,behavior:"instant"});}', frame)
        await node.evaluate('''async (e,isFrame)=>{const w=isFrame?e.contentWindow:e;
          for(let i=0;i<20;i++){if((isFrame?w.scrollY:e.scrollTop)<1)return;
            w.scrollTo({top:0,behavior:'instant'});await new Promise(r=>setTimeout(r,50));}
          throw Error('全文滚动起点未回到顶部');}''',frame)
        index, previous = 0, -1
        while True:
            data = await node.evaluate('''(e,isFrame)=>{const w=isFrame?e.contentWindow:e;
              return isFrame?{top:w.scrollY,height:w.innerHeight,total:w.document.documentElement.scrollHeight}
                :{top:e.scrollTop,height:e.clientHeight,total:e.scrollHeight};}''', frame)
            await self.e.shot(name+'-'+str(index))
            if data['top']+data['height'] >= data['total']-2:
                break
            if data['top']==previous:
                raise AssertionError('未到全文末尾却停止滚动，不能当作已覆盖')
            previous = data['top']
            self.e.label = name+' 连续滚动 '+str(index)
            # 侧栏内有可独立滚动的单位列表和文本框。鼠标放在侧栏留白处，
            # 避免滚轮被内层控件接走，误把外层尚未读完判成无法滚动。
            if frame:
                await node.hover()
            else:
                size = await node.bounding_box()
                assert size, '滚动容器不可见'
                await node.hover(position={'x':max(1,size['width']-12),'y':size['height']/2})
            await self.page.mouse.wheel(0, max(100, int(data['height']*.78)))
            await asyncio.sleep(.2)
            index += 1
            if index > 1500:
                raise AssertionError('滚动未到末尾，不能截断为通过')

    async def download(self, selector, kind):
        expected = await get(self.page, '/api/result/'+self.tid)
        async with self.page.expect_download(timeout=120000) as event:
            await self.click(selector)
        item = await event.value
        name = item.suggested_filename
        path = self.e.folder / 'downloads' / (str(len(self.downloads)+1)+'-'+name)
        path.parent.mkdir(exist_ok=True)
        await asyncio.wait_for(item.save_as(path),timeout=300)
        content = path.read_bytes()
        if kind == 'original':
            assert hashlib.sha256(content).hexdigest() == self.record['source_sha256']
        elif kind == 'xml':
            assert name == expected['xml_filename']
            assert content.decode() == expected['xml']
        else:
            assert name == expected['download_filename'] and name.startswith('Word2JATS-')
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                assert archive.testzip() is None
                assert archive.read(expected['xml_filename']).decode() == expected['xml']
                assert json.loads(archive.read('检查摘要.json'))['xml_sha256'] == expected['version']
                assert json.loads(archive.read('转换用量.json'))['model_usage'] == expected['stats']['llm']
                root = etree.fromstring(expected['xml'].encode())
                with zipfile.ZipFile(io.BytesIO(archive.read('figures.zip'))) as figures:
                    assert figures.testzip() is None
                    for href in root.xpath('//@*[local-name()="href"]'):
                        if not re.match(r'^(https?:|mailto:|#)', href):
                            assert href in figures.namelist(), 'ZIP 缺少 XML 引用图片 '+href
                            assert figures.read(href)
                assert 'XML' in archive.read('文件说明.txt').decode()
        self.downloads.append({'entry':selector,'kind':kind,'name':name,'sha256':hashlib.sha256(content).hexdigest(), 'version':expected['version']})
        write_json(self.e.folder/'downloads.json', self.downloads)

    async def read_content(self):
        await self.wait_preview_document()
        await self.step('R02 收起目录', lambda:self.click('#outline-toggle'))
        await self.step('R03 展开目录', lambda:self.click('#outline-toggle'))
        for kind in ['all','metadata','abstract','objects','math','references']:
            await self.step('R04 类型筛选 '+kind, lambda kind=kind:self.page.locator('#outline-kind').select_option(kind))
            ids = await self.page.locator('#outline-list button').evaluate_all('els=>els.map(e=>e.dataset.location)')
            if not ids:
                await expect(self.page.locator('#outline-empty')).to_be_visible()
                self.e.event('not-applicable', content_type=kind, reason='当前稿件没有此类目录项，已检查空态')
            for ident in ids:
                async def locate(ident=ident):
                    await self.click('#outline-list [data-location="'+ident+'"]')
                    await self.page.wait_for_function('''id=>{const doc=document.getElementById('render-frame').contentDocument,n=doc.getElementById(id);
                      const t=n?.tagName==='A'&&!n.textContent.trim()?n.parentElement:n;
                      return t?.classList.contains('w2j-selected');}''', arg=ident)
                    await self.page.wait_for_function('''()=>{const f=document.getElementById('render-frame'),n=f.contentDocument.querySelector('.w2j-selected');
                      if(!n)return false;const r=n.getBoundingClientRect();return r.bottom>0&&r.top<f.contentWindow.innerHeight;}''')
                await self.step('R05 '+kind+' 定位 '+ident, locate)
            await self.step('R06 搜索无匹配 '+kind, lambda:self.page.locator('#outline-query').fill('不存在的目录项-ZZ验收'))
            await expect(self.page.locator('#outline-empty')).to_be_visible()
            await self.step('R07 清空搜索 '+kind, lambda:self.page.locator('#outline-query').fill(''))
        await self.page.locator('#outline-kind').select_option('all')
        first = self.page.locator('#outline-list button').first
        if await first.count():
            title = (await first.inner_text()).strip()[:8]
            await self.step('R08 搜索已有条目', lambda:self.page.locator('#outline-query').fill(title))
            assert await self.page.locator('#outline-list button').count() > 0
            await self.page.locator('#outline-query').fill('')
        await self.step('R09 XML 入口', lambda:self.click('#xml-tab'))
        data = await get(self.page,'/api/result/'+self.tid)
        assert (await self.page.locator('#xml-view').inner_text()) == data['xml']
        await self.step('R10 返回内容预览', lambda:self.click('#preview-tab'))
        await self.scroll_all('#render-frame','R11 转换结果全文',True)
        async def preview_images():
            images = await self.page.frame_locator('#render-frame').locator('img').evaluate_all('els=>els.map(e=>({src:e.src,loaded:e.complete&&e.naturalWidth>0}))')
            assert all(i['loaded'] for i in images), images
        await self.step('R12 预览全部图片加载',preview_images)
        for link in await self.page.frame_locator('#render-frame').locator('a.w2j-media-fallback').all():
            async with self.page.expect_download() as event:
                await link.click()
            assert not await (await event.value).failure()
        await self.source_content()

    async def wait_preview_document(self):
        # iframe 的初始空白页不能被当成“没有引用/没有图片”的有效预览。
        await self.page.wait_for_function('''tid=>{
          const doc=document.getElementById('render-frame')?.contentDocument;
          return doc&&new URL(doc.URL).pathname==='/api/render/'+tid
            &&doc.readyState==='complete'&&doc.querySelector('.front')
            &&doc.body.textContent.trim().length>0;
        }''',arg=self.tid,timeout=90000)

    async def source_content(self):
        """图片加载失败须记录，但不得使可用的正文定位、原稿下载漏验。"""
        await self.wait_preview_document()
        await self.panel('source')
        await self.step('S01 Word 原稿全文检查',lambda:self.scroll_all('#source-frame','S01 Word 原稿全文',True))
        async def source_images():
            images = await self.page.frame_locator('#source-frame').locator('img').evaluate_all('els=>els.map(e=>({src:e.src,loaded:e.complete&&e.naturalWidth>0}))')
            assert all(i['loaded'] for i in images), images
        await self.step('S01b 原稿全部图片加载',source_images)
        work = await get(self.page,'/api/workbench/'+self.tid)
        for block in work['blocks']:
            if block['kind'] != 'p' or not block.get('source_anchor'):
                continue
            node = self.page.frame_locator('#render-frame').locator('[id="'+block['id']+'"]')
            if not await node.count():
                self.e.issue('missing-preview-target',block['id'])
                continue
            async def select_source(node=node,block=block):
                await node.click(position={'x':2,'y':2})
                await self.page.wait_for_function('''anchor=>document.getElementById('source-frame').contentDocument.getElementById(anchor)?.classList.contains('source-selected')''', arg=block['source_anchor'])
            await self.step('S02 正文点击对照 '+block['id'],select_source)
        await self.step('S03 原稿侧栏下载 Word',lambda:self.download('.source-note a','original'))
        await self.close()

    async def checks_and_downloads(self):
        await self.panel('checks')
        await self.scroll_all('#panel-content','K01 检查全文')
        await self.step('K02 展开技术详情',lambda:self.click('.technical summary'))
        await expect(self.page.locator('.technical pre')).to_contain_text('dtd_valid')
        await self.step('K03 收起技术详情',lambda:self.click('.technical summary'))
        actions = await self.page.locator('[data-issue]').evaluate_all('els=>els.map(e=>e.dataset.issue)')
        for ident in actions:
            await self.step('K04 检查处理入口 '+ident,lambda ident=ident:self.click('[data-issue="'+ident+'"]'))
            await self.close()
            await self.panel('checks')
        await self.step('K05 检查面板下载 XML',lambda:self.download('#checks-xml','xml'))
        await self.close()
        await self.panel('usage')
        data = await get(self.page,'/api/result/'+self.tid)
        usage = data['stats']['llm']['result_usage']
        for field in ['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens','total_tokens']:
            assert f'{usage[field]:,}' in await self.page.locator('#panel-content').inner_text(), field
        assert usage['input_tokens'] == usage['cache_hit_tokens']+usage['cache_miss_tokens']
        assert usage['total_tokens'] == usage['input_tokens']+usage['output_tokens']
        await self.step('K06 展开模型型号',lambda:self.click('.technical summary'))
        await self.step('K07 收起模型型号',lambda:self.click('.technical summary'))
        await self.close()
        await self.step('D01 顶栏成果下载',lambda:self.download('#download-btn','all'))
        await self.more()
        await self.step('D02 更多菜单单独 XML',lambda:self.download('#download-xml','xml'))
        await self.more()
        await self.step('D03 更多菜单 Word 原稿',lambda:self.download('#original-download','original'))
        await self.click('#copy-link')
        assert await self.page.evaluate('navigator.clipboard.readText()') == URL+'/#task='+self.tid
        await self.e.shot('D04-link-copied')
        await self.page.evaluate("window.originalClipboard=navigator.clipboard;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('denied')}}})")
        await self.step('D05 剪贴板权限拒绝退路',lambda:self.click('#copy-link'))
        await expect(self.page.locator('#task-link')).to_have_value(URL+'/#task='+self.tid)
        async def select_link():
            field=self.page.locator('#task-link')
            await field.click()
            await field.press('ControlOrMeta+A')
            assert await field.evaluate('e=>e.selectionStart===0&&e.selectionEnd===e.value.length')
        await self.step('D05b 手动选中完整任务链接',select_link)
        await self.click('#dialog-confirm')
        await self.page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:window.originalClipboard})")
        await self.panel('delivery')
        await self.scroll_all('#panel-content','D06 文件说明全文')
        await self.step('D07 文件说明下载 XML',lambda:self.download('#panel-download-xml','xml'))
        await self.step('D08 文件说明下载成果',lambda:self.download('#panel-download-all','all'))
        await self.close()

    async def save(self):
        await self.click('#save-edit')
        await expect(self.page.locator('#save-state')).to_have_text('未作修改',timeout=90000)
        await expect(self.page.locator('#save-edit')).to_be_disabled()

    async def editing(self):
        work = await get(self.page,'/api/workbench/'+self.tid)
        self.initial_fields = work['fields']
        await self.panel('article')
        jumps = await self.page.locator('[data-edit-jump]').evaluate_all('els=>els.map(e=>e.dataset.editJump)')
        for jump in jumps:
            await self.step('E01 字段定位 '+jump,lambda jump=jump:self.click('[data-edit-jump="'+jump+'"]'))
        fields = await self.page.locator('[data-field]').evaluate_all('els=>els.map(e=>({field:e.dataset.field,type:e.type,value:e.value,checked:e.checked}))')
        for field in fields:
            selector='[data-field="'+field['field']+'"]'
            async def operate(field=field,selector=selector):
                node=self.page.locator(selector)
                await node.click()
                if field['type']=='checkbox':
                    await node.set_checked(not field['checked'])
                    await self.e.shot('field-changed')
                    await node.set_checked(field['checked'])
                else:
                    value = field['value']+' 测试输入'
                    if field['field'].endswith('.orcid'): value='0000-0002-1825-0097'
                    if '.emails.' in field['field']: value='web-test@example.org'
                    await node.fill(value)
                    await self.e.shot('field-changed')
                    await node.fill(field['value'])
            await self.step('E02 输入控件 '+field['field'],operate)
        affs = await self.page.locator('[data-aff-author]').evaluate_all('els=>els.map(e=>({author:e.dataset.affAuthor,value:e.value,checked:e.checked}))')
        for aff in affs:
            async def toggle(aff=aff):
                node=self.page.locator('[data-aff-author="'+aff['author']+'"][value="'+aff['value']+'"]')
                await node.set_checked(not aff['checked'])
                await self.e.shot('affiliation-changed')
                await node.set_checked(aff['checked'])
            await self.step('E03 作者单位关联 '+aff['author']+'/'+aff['value'],toggle)
        authors=work['fields']['authors']
        for index in range(len(authors)-1):
            if authors[index]['group'] != authors[index+1]['group']:
                continue
            await self.step('E04 作者下移 '+str(index+1),lambda index=index:self.click('[data-move="'+str(index)+'"][data-direction="1"]'))
            await self.step('E05 作者上移 '+str(index+2),lambda index=index:self.click('[data-move="'+str(index+1)+'"][data-direction="-1"]'))
        await expect(self.page.locator('#save-edit')).to_be_disabled()
        title=work['fields']['title']+'（网页验收修改）'
        await self.step('E06 编辑题名',lambda:self.page.locator('[data-field="title"]').fill(title))
        await self.step('E07 编辑中对照原稿',lambda:self.click('#editor-source'))
        await self.page.frame_locator('#source-frame').locator('.source-document').wait_for()
        await self.step('E08 继续编辑',lambda:self.click('#return-edit'))
        await expect(self.page.locator('[data-field="title"]')).to_have_value(title)
        await self.step('E09 切换出版信息',lambda:self.click('#editor-other'))
        for field in await self.page.locator('[data-field]').evaluate_all('els=>els.map(e=>({field:e.dataset.field,value:e.value}))'):
            async def publication_input(field=field):
                node=self.page.locator('[data-field="'+field['field']+'"]')
                await node.click(); await node.fill(field['value']+'1')
                await self.e.shot('publication-input-changed')
                await node.fill(field['value'])
            await self.step('E10 出版字段 '+field['field'],publication_input)
        for value in await self.page.locator('#editor-journal option').evaluate_all('els=>els.map(e=>e.value)'):
            await self.step('E11 已配置期刊 '+(value or '自定义'),lambda value=value:self.page.locator('#editor-journal').select_option(value))
        await self.page.locator('#editor-journal').select_option(work['fields']['publication']['journal_id'] or '')
        for key,value in work['fields']['publication'].items():
            node=self.page.locator('[data-field="publication.'+key+'"]')
            if await node.count(): await node.fill(value)
        await self.page.locator('[data-field="publication.doi"]').fill('10.9999/web-public-'+self.record['sample'])
        await self.step('E12 返回文章保留共同草稿',lambda:self.click('#editor-other'))
        await expect(self.page.locator('[data-field="title"]')).to_have_value(title)
        for mode in ['source','checks','usage','delivery']:
            await self.step('E13 草稿切换 '+mode,lambda mode=mode:self.panel(mode))
            await self.step('E14 从 '+mode+' 继续编辑',lambda:self.click('#return-edit'))
            await expect(self.page.locator('[data-field="title"]')).to_have_value(title)
        for selector in ['#next-btn','#header-home','#home-btn','#panel-close','#cancel-edit']:
            await self.step('E15 放弃提示入口 '+selector,lambda selector=selector:self.click(selector))
            await expect(self.page.locator('#action-dialog')).to_be_visible()
            await self.step('E16 取消放弃 '+selector,lambda:self.click('#dialog-cancel'))
            await expect(self.page.locator('[data-field="title"]')).to_have_value(title)
        await self.click('#download-btn')
        await expect(self.page.locator('#dialog-title')).to_have_text('修改尚未保存')
        await self.step('E17 取消下载未保存版本',lambda:self.click('#dialog-cancel'))
        async def download_saved():
            async with self.page.expect_download() as event:
                await self.click('#download-btn'); await self.click('#dialog-confirm')
            item=await event.value
            await item.save_as(self.e.folder/'download-before-save.zip')
            with zipfile.ZipFile(self.e.folder/'download-before-save.zip') as archive:
                old=await get(self.page,'/api/result/'+self.tid)
                assert archive.read(old['xml_filename']).decode()==old['xml']
                assert title not in old['xml']
        await self.step('E18 确认下载已保存结果',download_saved)
        async def failed_save(route):
            await route.fulfill(status=503,json={'detail':'保存服务暂时不可用，请重试。'})
        await self.page.route('**/api/edit/'+self.tid,failed_save)
        async def failure():
            await self.click('#save-edit')
            await expect(self.page.locator('#edit-error')).to_contain_text('暂时不可用')
            await expect(self.page.locator('[data-field="title"]')).to_have_value(title)
        await self.step('E19 保存失败输入保留',failure)
        await self.page.unroute('**/api/edit/'+self.tid,failed_save)
        async def slow(route):
            await asyncio.sleep(1.5); await route.continue_()
        await self.page.route('**/api/edit/'+self.tid,slow)
        async def save_protected():
            await self.click('#save-edit')
            await expect(self.page.locator('#version-label')).to_have_text('正在保存')
            await self.click('#header-home')
            await expect(self.page.locator('#toast')).to_contain_text('正在保存')
            await self.click('#download-btn')
            await expect(self.page.locator('#toast')).to_contain_text('正在保存')
            await expect(self.page.locator('#save-state')).to_have_text('未作修改',timeout=90000)
        await self.step('E20 真实保存及保存中导航下载保护',save_protected)
        await self.page.unroute('**/api/edit/'+self.tid,slow)
        saved=await get(self.page,'/api/workbench/'+self.tid)
        assert saved['fields']['title']==title
        assert saved['fields']['publication']['doi']=='10.9999/web-public-'+self.record['sample']
        data=await get(self.page,'/api/result/'+self.tid)
        assert data['stats']['llm']==self.record['llm']
        await self.close()
        await self.step('E21 下载真实修改结果',lambda:self.download('#download-btn','all'))

    async def exceptional_paths(self):
        await self.reset_view()
        download_attempts=0
        download_failure_sent=asyncio.Event()
        async def failed_download(route):
            nonlocal download_attempts
            download_attempts+=1
            await asyncio.sleep(.7)
            await route.fulfill(status=503,json={'detail':'下载文件暂时无法生成，请稍后重试。'})
            download_failure_sent.set()
        await self.page.route('**/api/download/'+self.tid+'*',failed_download)
        async def download_error():
            await self.page.locator('#download-btn').dblclick()
            await expect(self.page.locator('#download-error')).to_contain_text('稍后重试')
            await download_failure_sent.wait()
            assert download_attempts==1,'重复点击触发了多个下载请求'
        await self.step('F01 下载失败与防重复点击',download_error)
        await self.page.unroute('**/api/download/'+self.tid+'*',failed_download)
        await self.step('F02 下载重试成功',lambda:self.download('#download-btn','all'))
        async def failed_info(route):
            await route.fulfill(status=503,json={'detail':'文件信息暂时无法读取。'})
        await self.page.route('**/api/delivery/'+self.tid+'*',failed_info)
        await self.more(); await self.click('[data-panel="delivery"]')
        await expect(self.page.locator('#retry-delivery')).to_be_visible()
        await self.e.shot('F03-file-information-failed')
        await self.page.unroute('**/api/delivery/'+self.tid+'*',failed_info)
        await self.step('F04 文件说明重试',lambda:self.click('#retry-delivery'))
        await expect(self.page.locator('.delivery-name')).to_be_visible()
        await self.close()
        async def failed_source(route):
            await route.fulfill(status=503,content_type='text/html',body='<p>暂时无法读取</p>')
        await self.page.route('**/api/source/'+self.tid,failed_source)
        await self.click('[data-panel="source"]')
        await expect(self.page.locator('.source-note')).to_contain_text('可下载 Word 查看')
        await self.step('F05 原稿预览失败仍可下载',lambda:self.download('.source-note a','original'))
        await self.close(); await self.page.unroute('**/api/source/'+self.tid,failed_source)
        recovered,_=await self.step('F05b 关闭后重开失败原稿',lambda:self.panel('source'))
        if recovered:
            await self.close()
        else:
            # 记录产品断点后，通过真实刷新继续后续独立场景；不是将断点判为通过。
            await self.reset_view()

        # 真正的两个浏览器页面修改同一份本轮测试稿件，不由后台伪造版本。
        other=await self.page.context.new_page()
        second=Journey(other,self.record,self.e.folder/'second-page')
        await second.e.start()
        try:
            await second.reset_view(); await second.panel('article')
            node=other.locator('[data-field="title"]')
            await second.step('F06 另一页面修改',lambda:node.fill((self.initial_fields['title'] if hasattr(self,'initial_fields') else '稿件')+'（另一页面保存）'))
            await second.step('F07 另一页面保存',second.save)
            async def stale_download():
                await self.click('#download-btn')
                await expect(self.page.locator('#download-error')).to_contain_text('其他页面更新')
            await self.step('F08 旧页面下载版本提示',stale_download)
            await self.step('F09 下载提示重新载入',lambda:self.click('#download-error button'))
            await expect(self.page.locator('#download-error')).to_be_hidden()
            await self.panel('article')
            await self.page.locator('[data-field="title"]').fill('本页尚未保存的修改')
            await node.fill((await node.input_value())+' 更新')
            await second.step('F10 另一页面再次保存',second.save)
            await self.click('#save-edit')
            await expect(self.page.locator('#edit-error')).to_contain_text('重新载入')
            await self.e.shot('F11-edit-conflict')
            await self.click('#edit-error button'); await self.click('#dialog-cancel')
            await expect(self.page.locator('[data-field="title"]')).to_have_value('本页尚未保存的修改')
            await self.step('F12 冲突重新载入确认',lambda:self.click('#edit-error button'))
            await self.click('#dialog-confirm')
            await expect(self.page.locator('#save-state')).to_have_text('未作修改')
            await self.close()
        finally:
            await second.e.stop(); await other.close()

    async def responsive(self):
        for name,width,height in VIEWPORTS:
            ok,_=await self.step('V00 '+name+' 全部入口',lambda name=name,width=width,height=height:
                                self.responsive_viewport(name,width,height))
            if not ok:
                # 一种尺寸遇到产品断点后保留证据，真实刷新再继续其他尺寸，
                # 不能让其余独立尺寸整段没有执行，也不把失败变为通过。
                await self.reset_view()
        await self.page.set_viewport_size({'width':1440,'height':1000})

    async def responsive_viewport(self,name,width,height):
            await self.page.set_viewport_size({'width':width,'height':height})
            await self.step('V01 '+name+' 首页',lambda:self.click('#header-home'))
            await self.page.locator('.recent-open[href="#task='+self.tid+'"]').click()
            await self.ready(); await self.e.shot('V02-'+name+'-result')
            await self.step('V03 '+name+' XML',lambda:self.click('#xml-tab'))
            await self.click('#preview-tab')
            for mode in ['source','article','publication','checks','usage','delivery']:
                await self.step('V04 '+name+' '+mode,lambda mode=mode:self.panel(mode))
                if width<=850:
                    await expect(self.page.locator('#side-panel')).to_have_attribute('role','dialog')
                    await expect(self.page.locator('.preview-region')).to_have_attribute('inert','')
                    await self.page.locator('#panel-close').focus()
                    await self.page.keyboard.press('Shift+Tab')
                    assert await self.page.evaluate('document.getElementById("side-panel").contains(document.activeElement)')
                if mode != 'source':
                    await self.scroll_all('#panel-content','V05 '+name+' '+mode)
                if mode == 'article':
                    field=self.page.locator('[data-field="title"]')
                    old=await field.input_value()
                    await field.click(); await field.fill(old+' 未保存')
                    await self.click('#editor-source')
                    await self.page.frame_locator('#source-frame').locator('.source-document').wait_for()
                    await self.e.shot('V06-'+name+'-draft-source')
                    await self.click('#return-edit')
                    await expect(field).to_have_value(old+' 未保存')
                    await self.page.keyboard.press('Escape')
                    await expect(self.page.locator('#action-dialog')).to_be_visible()
                    await self.e.shot('V07-'+name+'-discard-dialog')
                    await self.click('#dialog-cancel')
                    await field.fill(old)
                await self.step('V08 '+name+' 关闭 '+mode,self.close)
            await self.click('#outline-toggle')
            await self.e.shot('V09-'+name+'-outline-toggle')
            await self.click('#outline-toggle')

    async def restore_and_recent(self):
        await self.reset_view(); await self.more()
        if await self.page.locator('#restore-btn').is_disabled():
            # 单独续跑时可能已恢复过；先核对禁用状态，再通过前端保存一次修改。
            # 不能跳过恢复流程，也不能把正常禁用的按钮误报为产品故障。
            original=await get(self.page,'/api/result/'+self.tid)
            assert original['version']==self.record['version']
            await self.e.shot('A00-original-restore-disabled')
            await self.click('#more-menu summary')
            await self.panel('article')
            field=self.page.locator('[data-field="title"]')
            title=await field.input_value()
            await self.step('A00 为恢复操作保存修改',lambda:field.fill(title+'（恢复测试）'))
            await self.step('A00 保存恢复测试修改',self.save)
            await self.close(); await self.more()
        await self.click('#restore-btn')
        await self.step('A01 恢复取消',lambda:self.click('#dialog-cancel'))
        await self.more(); await self.click('#restore-btn')
        async def slow(route):
            await asyncio.sleep(1.5); await route.continue_()
        await self.page.route('**/api/restore/'+self.tid,slow)
        async def restore():
            await self.click('#dialog-confirm')
            await expect(self.page.locator('#version-label')).to_contain_text('正在恢复')
            await self.click('#header-home')
            await expect(self.page.locator('#toast')).to_contain_text('正在恢复')
            await expect(self.page.locator('#toast')).to_contain_text('已恢复',timeout=90000)
            data=await get(self.page,'/api/result/'+self.tid)
            assert data['version']==self.record['version']
        await self.step('A02 恢复确认与期间导航保护',restore)
        await self.page.unroute('**/api/restore/'+self.tid,slow)
        await self.step('A03 恢复后下载',lambda:self.download('#download-btn','all'))
        for entry in ['#next-btn','#home-btn','#header-home']:
            await self.step('H01 返回首页 '+entry,lambda entry=entry:self.click(entry))
            await self.step('H02 最近记录重新进入',lambda:self.click('.recent-open[href="#task='+self.tid+'"]'))
            await self.ready()
        await self.click('#header-home')
        other=await self.page.context.new_page()
        await other.goto(URL)
        await self.step('H03 移除本地记录',lambda:self.click('[data-remove="'+self.tid+'"]'))
        await expect(other.locator('.recent-open[href="#task='+self.tid+'"]')).to_have_count(0)
        await other.close()
        await self.reset_view()
        await self.step('H04 刷新已完成任务',lambda:self.page.reload())
        await self.ready()
        await self.page.goto(URL+'/#task=0000000000000000')
        await expect(self.page.locator('#error-title')).to_have_text('未找到这次转换')
        await self.step('H05 失效链接重新上传入口',lambda:self.click('#retry-btn'))
        await expect(self.page.locator('#upload')).to_be_visible()
        await self.reset_view()

    async def reconvert(self):
        for source in ['current','original']:
            await self.reset_view()
            await self.panel('publication')
            await self.page.locator('[data-field="publication.doi"]').fill('10.9999/inherit-'+self.record['sample'])
            if await self.page.locator('#save-edit').is_enabled(): await self.save()
            pub=(await get(self.page,'/api/workbench/'+self.tid))['fields']['publication']
            old=(await get(self.page,'/api/result/'+self.tid))['version']
            await self.close(); await self.more(); await self.click('#reconvert-btn')
            await self.step('A04 重新转换取消 '+source,lambda:self.click('#dialog-cancel'))
            await self.more(); await self.click('#reconvert-btn')
            for provider in ['deepseek','dashscope',self.record['provider']]:
                await self.step('A05 重转模型 '+provider,lambda provider=provider:self.page.locator('#reconvert-provider').select_option(provider))
            await self.step('A06 出版设置 '+source,lambda:self.page.locator('#reconvert-publication').select_option(source))
            async def run():
                await self.click('#dialog-confirm')
                await self.page.wait_for_function('old=>location.hash!=="#task="+old',arg=self.tid)
                new=self.page.url.split('#task=')[1]
                self.e.event('reconvert-task',task=new,publication_source=source)
                write_json(self.e.folder/('reconvert-'+source+'-task.json'),{'task':new,'source':source,'old':self.tid})
                print(self.record['sample']+' '+self.record['provider']+' '+source+' 重转 '+new,flush=True)
                await self.page.wait_for_function('document.body.dataset.state==="result"||document.body.dataset.state==="error"',timeout=1800000)
                await self.ready()
                data=await get(self.page,'/api/result/'+new)
                assert data['stats']['llm']['calls']>0
                assert data['stats']['llm']['usage']['total_tokens']>0
                actual=await get(self.page,'/api/workbench/'+new)
                if source=='current': assert actual['fields']['publication']==pub
                else: assert actual['fields']['publication']['doi']!=pub['doi']
                assert (await get(self.page,'/api/result/'+self.tid))['version']==old
                self.reconversions.append({'task':new,'source':source,'llm':data['stats']['llm'],'version':data['version']})
                write_json(self.e.folder/'reconversions.json',self.reconversions)
                if await self.page.locator('#more-menu .previous-result').count():
                    await self.more()
                    await self.click('#more-menu .previous-result'); await self.ready()
            await self.step('A07 真正重新转换 '+source,run)
        await self.reset_view()

    async def remaining_entries(self):
        await self.reset_view()
        if await self.page.locator('#result-alert').is_visible():
            await self.step('B01 结果提示处理入口',lambda:self.click('#alert-action'))
            await self.close()
        await self.panel('article')
        old=await self.page.locator('[data-field="title"]').input_value()
        await self.page.locator('[data-field="title"]').fill(old+' 草稿')
        await self.click('#editor-source'); await self.page.frame_locator('#source-frame').locator('.source-document').wait_for()
        await self.step('B02 关闭对照保留草稿',lambda:self.click('#panel-close'))
        await expect(self.page.locator('#resume-edit')).to_be_visible()
        await self.step('B03 顶栏继续编辑',lambda:self.click('#resume-edit'))
        await expect(self.page.locator('[data-field="title"]')).to_have_value(old+' 草稿')
        await self.click('#cancel-edit'); await self.click('#dialog-confirm')
        await self.panel('article')
        await expect(self.page.locator('[data-field="title"]')).to_have_value(old)
        async def invalid_title():
            await self.page.locator('[data-field="title"]').fill('')
            await self.click('#save-edit')
            await expect(self.page.locator('#edit-error')).to_be_visible()
        await self.step('B04 空题名保存拒绝',invalid_title)
        await self.close()
        async def unavailable(route):
            await route.fulfill(status=503,json={'detail':'编辑信息暂时不可用'})
        await self.page.route('**/api/workbench/'+self.tid,unavailable)
        await self.reset_view()
        await expect(self.page.locator('#alert-action')).to_have_text('重新载入')
        await expect(self.page.locator('[data-panel="article"]')).to_be_disabled()
        await self.e.shot('B05-partial-result')
        await self.page.unroute('**/api/workbench/'+self.tid,unavailable)
        await self.step('B06 补充载入编辑与目录',lambda:self.click('#alert-action'))
        await expect(self.page.locator('[data-panel="article"]')).to_be_enabled()
        async def broken_render(route):
            await route.fulfill(status=503,content_type='text/html',body='<p>暂时无法预览</p>')
        await self.page.route('**/api/render/'+self.tid+'*',broken_render)
        await self.reset_view()
        await self.step('B07 预览不可用仍可查看 XML',lambda:self.click('#xml-tab'))
        await expect(self.page.locator('#xml-view')).to_contain_text('<article')
        await self.step('B08 预览不可用仍可下载',lambda:self.download('#download-btn','all'))
        await self.page.unroute('**/api/render/'+self.tid+'*',broken_render)
        await self.reset_view()
        # 转换失败状态仅在此浏览器返回；重试仍调用真实公网转换接口。
        status=await get(self.page,'/api/status/'+self.tid)
        async def failed_status(route):
            await route.fulfill(json={**status,'status':'error','stage_key':'error','error':'转换暂时未能完成，请重试。'})
        await self.page.route('**/api/status/'+self.tid,failed_status)
        await self.page.reload()
        await expect(self.page.locator('#error')).to_be_visible()
        await self.step('B09 转换失败重新上传入口',lambda:self.click('#retry-btn'))
        await self.page.goto(URL+'/#task='+self.tid)
        await expect(self.page.locator('#error')).to_be_visible()
        await self.step('B10 转换失败重新转换入口',lambda:self.click('#retry-task-btn'))
        await self.click('#dialog-cancel')
        await self.page.unroute('**/api/status/'+self.tid,failed_status)
        await self.reset_view()
        await self.reference_links()
        await self.reset_view()

    async def reference_links(self):
        """实际点击每条内部引用后再核对目标；失效引用也不能跳过点击。"""
        await self.reset_view()
        await self.wait_preview_document()
        frame=self.page.frame_locator('#render-frame')
        links=await frame.locator('a[href^="#"]').evaluate_all('els=>els.map((e,i)=>({i,href:e.getAttribute("href")}))')
        if not links:
            self.e.event('not-applicable',content_type='internal-links',reason='当前预览没有内部引用链接')
        for row in links:
            async def reference_link(row=row):
                await frame.locator('a[href^="#"]').nth(row['i']).click()
                assert self.page.url==URL+'/#task='+self.tid
                target=unquote(row['href'][1:])
                assert await frame.locator('body').evaluate('(e,id)=>Boolean(document.getElementById(id)||document.getElementsByName(id).length)',target),row
            await self.step('B11 预览内部链接 '+str(row['i'])+' '+row['href'],reference_link)

    async def upload_paths(self):
        """补充真实 Word 拖入、替换及损坏文件的服务器失败退路。"""
        await self.reset_view()
        await self.click('#header-home')
        path=ROOT/'样例数据'/self.record['sample']/'初始文件.docx'
        async with self.page.expect_file_chooser() as chooser:
            await self.page.locator('#drop').press('Enter')
        await (await chooser.value).set_files(path)
        original=await self.page.locator('#docx-input').evaluate_handle('e=>e.files[0]')
        await self.step('U14 键盘选择 Word 后移除',lambda:self.click('#remove-file'))
        async def drag(event):
            await self.page.evaluate('''({file,event})=>{const d=new DataTransfer();d.items.add(file);
              document.getElementById('drop').dispatchEvent(new DragEvent(event,{bubbles:true,cancelable:true,dataTransfer:d}));}''',{'file':original,'event':event})
        for event in ['dragenter','dragleave','dragenter','drop']:
            await self.step('U15 真实 Word 拖放 '+event,lambda event=event:drag(event))
        await expect(self.page.locator('#submit-btn')).to_be_enabled()
        await expect(self.page.locator('#drop-title')).to_have_text('初始文件.docx')
        async def replace():
            await self.page.evaluate('''file=>{const d=new DataTransfer();d.items.add(new File([file],'替换后的稿件.docx',{type:file.type}));
              document.getElementById('drop').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:d}));}''',original)
            await expect(self.page.locator('#drop-title')).to_have_text('替换后的稿件.docx')
        await self.step('U16 已选文件直接替换',replace)
        await original.dispose()
        await self.click('#remove-file')
        async def corrupt():
            await self.page.locator('#docx-input').set_input_files({'name':'损坏文件-'+self.record['sample']+'.docx',
                'mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':b'not a valid Word archive'})
            await self.page.locator('#provider-input').select_option(self.record['provider'])
            await self.click('#submit-btn')
            await expect(self.page.locator('#error')).to_be_visible(timeout=90000)
            await expect(self.page.locator('#error-msg')).to_contain_text('文件打不开')
            bad=self.page.url.split('#task=')[1]
            self.e.event('invalid-word-task',task=bad)
            await self.click('#retry-task-btn')
            await self.click('#dialog-cancel')
            await self.click('#retry-btn')
            await expect(self.page.locator('#upload')).to_be_visible()
        await self.step('U17 损坏 Word 真实失败及两个重试入口',corrupt)
        await self.reset_view()


async def case(browser, record, output, chapters, repeat=False):
    folder=output/(record['sample']+'-'+record['provider'])/'actions'
    previous={}
    if (folder/'complete.json').exists():
        previous=json.loads((folder/'complete.json').read_text())
        if not repeat:
            chapters=[name for name in chapters if name not in previous['chapters']]
        if not chapters:return
    context=await browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True,
                                      permissions=['clipboard-read','clipboard-write'])
    # 动态续跑期间测试源码可能更新；浏览器 trace 保留 DOM 和图像，
    # 不把正在改写的本地源码塞进 ZIP，源码版本由 Git 单独追踪。
    await context.tracing.start(screenshots=True,snapshots=True,sources=False)
    page=await context.new_page(); page.set_default_timeout(20000)
    page.on('dialog',lambda dialog:dialog.accept())
    journey=Journey(page,record,folder)
    await journey.e.start()
    try:
        await journey.reset_view()
        for name in chapters:
            print(record['sample']+' '+record['provider']+' 操作章节 '+name,flush=True)
            await journey.chapter(name,getattr(journey,name))
        write_json(folder/'complete.json',{'sample':record['sample'],'provider':record['provider'],
                   'task':record['task'],'chapters':previous.get('chapters',[])+chapters,
                   'issues':previous.get('issues',[])+journey.e.issues,
                   'visual_review':'pending'})
    except Exception as error:
        journey.e.issue('case-interruption',str(error))
        await journey.e.shot('case-interruption')
        print(record['sample']+' '+record['provider']+' 中断 '+str(error),flush=True)
    finally:
        await finish_artifacts(journey.e,context)


async def finish_artifacts(evidence,context):
    """附加记录写出失败单列保存，不能取消同队列的其他稿件。"""
    errors=[]
    for name,operation in [
        ('evidence',evidence.stop),
        ('trace',lambda:context.tracing.stop(path=evidence.folder/'trace.zip')),
        ('browser-context',context.close),
    ]:
        try:
            await operation()
        except Exception as error:
            errors.append({'artifact':name,'message':str(error)})
            print('验收附加记录失败 '+name+': '+str(error),flush=True)
    if errors:
        write_json(evidence.folder/'artifact-errors.json',errors)


async def main(args):
    paths=sorted(args.output.glob('*/convert/result.json'))
    if args.samples: paths=[p for p in paths if p.parent.parent.name.split('-')[0] in args.samples.split(',')]
    if args.providers: paths=[p for p in paths if p.parent.parent.name.rsplit('-',1)[1] in args.providers.split(',')]
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        gate=asyncio.Semaphore(args.concurrency)
        async def run(path):
            async with gate: await case(browser,json.loads(path.read_text()),args.output,args.chapters.split(','),args.repeat)
        await asyncio.gather(*(run(path) for path in paths))
        await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web-public-20260915/round-01')
    parser.add_argument('--samples')
    parser.add_argument('--providers',help='只续跑指定模型，避免与进行中的同例任务交叉修改')
    parser.add_argument('--concurrency',type=int,default=2)
    parser.add_argument('--repeat',action='store_true',help='保留旧证据，补跑指定的验收脚本阻断章节')
    parser.add_argument('--chapters',default='read_content,checks_and_downloads,editing,exceptional_paths,responsive,restore_and_recent,reconvert,remaining_entries,upload_paths')
    asyncio.run(main(parser.parse_args()))
