"""公网浏览器验收证据采集：连续渲染帧、动作前后截图及可恢复日志。"""
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

URL = 'https://word2jats.jianglab.work'
ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Evidence:
    def __init__(self, page, folder):
        if (folder / 'events.jsonl').exists():
            folder = folder / ('continuation-' + str(time.time_ns()))
        self.page, self.folder = page, folder
        self.actions, self.issues, self.shots = [], [], []
        self.label, self.count, self.frame_count = 'initial', 0, 0
        self.pending = set()
        self.seen = {}
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'frames').mkdir(exist_ok=True)
        (folder / 'screenshots').mkdir(exist_ok=True)
        self.log = (folder / 'events.jsonl').open('a', encoding='utf-8')

    def event(self, kind, **values):
        self.log.write(json.dumps({'time': time.time(), 'kind': kind, 'action': self.label, **values}, ensure_ascii=False) + '\n')
        self.log.flush()

    async def start(self):
        await self.page.expose_binding('_w2jEvidenceEvent', lambda source, value: self.event('user-event', **value))
        capture = '''(()=>{
          if(window.__w2jEvidenceInstalled)return;window.__w2jEvidenceInstalled=true;
          for(const type of ['click','input','change','keydown','wheel']) document.addEventListener(type,event=>{
            const e=event.target?.closest?.('button,a,input,select,textarea,summary')||event.target;
            window._w2jEvidenceEvent({event:type,tag:e?.tagName,id:e?.id||'',field:e?.dataset?.field||'',
              panel:e?.dataset?.panel||'',location:e?.dataset?.location||'',issue:e?.dataset?.issue||'',
              key:event.key||'',deltaY:event.deltaY||0,frame_url:location.href});
          },true);
        })();'''
        await self.page.add_init_script(capture)
        await self.page.evaluate(capture)
        self.page.on('pageerror', lambda error: self.event('pageerror', message=str(error)))
        self.page.on('requestfailed', lambda req: self.event('requestfailed', url=req.url, failure=req.failure))
        self.page.on('response', lambda r: self.event('http-error', url=r.url, status=r.status) if r.status >= 400 else None)
        self.cdp = await self.page.context.new_cdp_session(self.page)
        await self.cdp.send('Page.enable')
        await self.cdp.send('Profiler.enable')
        await self.cdp.send('Profiler.startPreciseCoverage', {'callCount': True, 'detailed': True})
        self.cdp.on('Page.screencastFrame', self.frame)
        await self.cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': 75, 'everyNthFrame': 1})

    def frame(self, frame):
        self.frame_count += 1
        content = base64.b64decode(frame['data'])
        digest = hashlib.sha256(content).hexdigest()
        filename = self.seen.get(digest)
        if filename is None:
            filename = f'frames/{self.frame_count:07d}.jpg'
            (self.folder / filename).write_bytes(content)
            self.seen[digest] = filename
        self.event('frame', frame=self.frame_count, file=filename, sha256=digest, metadata=frame['metadata'])
        task = asyncio.create_task(self.cdp.send('Page.screencastFrameAck', {'sessionId': frame['sessionId']}))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def shot(self, phase):
        self.count += 1
        filename = f'screenshots/{self.count:05d}-{phase}.png'
        await self.page.screenshot(path=self.folder / filename, timeout=30000)
        state = await self.page.evaluate('''() => ({url:location.href, state:document.body.dataset.state,
          viewport:{width:innerWidth,height:innerHeight}, width:document.documentElement.scrollWidth,
          controls:[...document.querySelectorAll('button,a,input,select,textarea,summary')]
            .filter(e=>e.getClientRects().length && !e.closest('[hidden]'))
            .map(e=>({tag:e.tagName,id:e.id,label:e.getAttribute('aria-label')||e.textContent.trim().slice(0,100),
              field:e.dataset.field,disabled:e.disabled,inert:!!e.closest('[inert]')}))})''')
        self.shots.append({'file': filename, 'action': self.label, **state})
        self.event('screenshot', file=filename, **state)
        if state['width'] > state['viewport']['width'] + 1:
            self.issue('horizontal-overflow', f"页面宽度 {state['width']} > 视口 {state['viewport']['width']}")

    def issue(self, kind, message):
        issue = {'action': self.label, 'kind': kind, 'message': str(message), 'time': time.time()}
        self.issues.append(issue)
        self.event('issue', issue_kind=kind, message=str(message))
        self.flush()

    def flush(self):
        write_json(self.folder / 'progress.json', {'actions': self.actions, 'issues': self.issues,
                    'screenshots': self.shots, 'continuous_frames': self.frame_count,
                    'unique_continuous_frames': len(self.seen), 'visual_review': 'pending'})

    async def step(self, label, operation):
        self.label = label
        self.event('action-start')
        await self.shot('before')
        start = time.time()
        try:
            result = await operation()
        except Exception as error:
            self.issue('assertion-or-operation', str(error))
            await self.shot('failure')
            self.actions.append({'name': label, 'status': 'failed', 'duration': time.time()-start})
            self.flush()
            return False, None
        await self.shot('after')
        self.actions.append({'name': label, 'status': 'passed', 'duration': time.time()-start})
        self.flush()
        return True, result

    async def stop(self):
        await self.cdp.send('Page.stopScreencast')
        if self.pending:
            await asyncio.gather(*self.pending, return_exceptions=True)
        write_json(self.folder / 'javascript-coverage.json', await self.cdp.send('Profiler.takePreciseCoverage'))
        await self.cdp.send('Profiler.stopPreciseCoverage')
        self.flush()
        self.log.close()


async def get(page, route):
    response = await page.request.get(URL + route, timeout=60000)
    assert response.ok, (route, response.status)
    return await response.json()
