"""验收辅助脚本不得忽略像素差异或自动标记视觉通过。"""
from PIL import Image, ImageChops

from scripts.review_web_public_deltas import category, delta, spatial_delta
from scripts.audit_web_public_round import reviewed_delta_frames


def test_readonly_check_uses_browser_network_and_propagates_failures():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import pytest
    from scripts.web_public_evidence import get, URL

    page = SimpleNamespace(evaluate=AsyncMock(return_value={
        'ok': True, 'status': 200, 'data': {'version': 'current'}}))
    assert asyncio.run(get(page, '/api/result/test')) == {'version': 'current'}
    script, url = page.evaluate.call_args.args
    assert url == URL + '/api/result/test'
    assert 'AbortSignal.timeout(60000)' in script
    assert "cache: 'no-store'" in script
    page.evaluate.return_value = {'ok': False, 'status': 503, 'data': None}
    with pytest.raises(AssertionError, match='503'):
        asyncio.run(get(page, '/api/result/test'))
    page.evaluate.side_effect = TimeoutError('browser timeout')
    with pytest.raises(TimeoutError, match='browser timeout'):
        asyncio.run(get(page, '/api/result/test'))


def test_nested_step_restores_parent_label_for_failure_and_followup(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    from scripts.web_public_evidence import Evidence

    evidence = Evidence(None, tmp_path / 'evidence')
    evidence.shot = AsyncMock()

    async def inner():
        assert evidence.label == '填写字段'

    async def outer():
        assert (await evidence.step('填写字段', inner))[0]
        assert evidence.label == '核对保存结果'
        raise TimeoutError('只读核对超时')

    async def check():
        ok, _ = await evidence.step('核对保存结果', outer)
        assert not ok
        assert evidence.label == 'initial'

    asyncio.run(check())
    assert evidence.issues[0]['action'] == '核对保存结果'
    assert evidence.issues[0]['message'] == '只读核对超时'
    assert [item['name'] for item in evidence.actions] == ['填写字段', '核对保存结果']
    evidence.log.close()


def test_delta_preserves_all_changed_pixels():
    before=Image.new('RGB',(300,200),'white')
    after=before.copy()
    after.putpixel((2,3),(254,255,255))
    after.putpixel((270,198),(255,255,254))
    box,patch=delta(before,after)
    assert box==(2,3,271,199)
    restored=before.copy();restored.paste(patch,box)
    assert ImageChops.difference(after,restored).getbbox() is None


def test_same_frame_and_context_change():
    im=Image.new('RGB',(300,200),'white')
    assert delta(im,im)==(None,None)
    box,patch=delta(im,im,full=True)
    assert box==(0,0,300,200)
    assert patch.tobytes()==im.tobytes()


def test_spatial_delta_preserves_separated_one_channel_changes():
    before = Image.new('RGB', (1440, 1000), 'white')
    after = before.copy()
    for point, color in [((0,0),(254,255,255)), ((1000,500),(255,254,255)),
                         ((1400,900),(255,255,254)), ((1439,999),(0,0,0))]:
        after.putpixel(point, color)
    pieces = spatial_delta(before, after)
    assert len(pieces) == 4
    assert sum(patch.width*patch.height for _,patch in pieces) < 10000
    restored = before.copy()
    for box, patch in pieces:
        restored.paste(patch, box)
    assert ImageChops.difference(restored, after).getbbox() is None
    assert spatial_delta(after, after) == []
    for previous in [None, Image.new('RGB', (10,10))]:
        assert spatial_delta(previous, after)[0][0] == (0,0,1440,1000)


def test_spatial_review_requires_every_region_and_base():
    data = {'patches':[{'id':0,'reviewed_at':1},{'id':1,'reviewed_at':1},
                       {'id':2,'reviewed_at':None}], 'frames':[
        {'file':'a','previous_file':None,'source_sha256':'A','size':[100,100],
         'regions':[{'patch':0,'box':[0,0,100,100]}]},
        {'file':'b','previous_file':'a','source_sha256':'B','size':[100,100],
         'regions':[{'patch':1,'box':[0,0,10,10]},{'patch':2,'box':[80,80,90,90]}]}]}
    assert reviewed_delta_frames(data) == {'A'}
    data['patches'][2]['reviewed_at'] = 1
    assert reviewed_delta_frames(data) == {'A','B'}
    data['patches'][0]['reviewed_at'] = None
    assert reviewed_delta_frames(data) == set()


def test_spatial_screenshot_manifest_preserves_all_events_without_auto_review(tmp_path):
    import hashlib
    import json
    from types import SimpleNamespace
    from scripts.review_web_public_deltas import build
    stream = tmp_path/'case'
    stream.mkdir()
    im = Image.new('RGB',(400,300),'white')
    im.save(stream/'before.png')
    im.putpixel((50,100),(254,255,255))
    im.save(stream/'after.png')
    events = [
        {'kind':'screenshot','file':'before.png','action':'before'},
        {'kind':'frame','file':'not-selected.jpg','action':'not-selected'},
        {'kind':'screenshot','file':'after.png','action':'different action'},
        {'kind':'screenshot','file':'after.png','action':'same pixels'}]
    (stream/'events.jsonl').write_text('\n'.join(json.dumps(row) for row in events))
    out = tmp_path/'review'
    (out/'patches').mkdir(parents=True)
    data = build(SimpleNamespace(root=tmp_path,prefix=['case'],spatial=True,event_kind='screenshot'),out)
    assert len(data['frames']) == 3
    assert len(data['patches']) == 2
    assert data['frames'][-1]['same_as'] == 1
    assert data['frames'][1]['source_sha256'] == hashlib.sha256((stream/'after.png').read_bytes()).hexdigest()
    assert data['frames'][1]['regions'][0]['box'] == (38,88,63,113)
    assert reviewed_delta_frames(data) == set()


def test_first_frame_and_resized_frame_are_full():
    im=Image.new('RGB',(300,200),'white')
    for previous in [None,Image.new('RGB',(390,844),'white')]:
        box,patch=delta(previous,im)
        assert box==(0,0,300,200)
        assert patch.size==im.size


def test_review_grouping_does_not_drop_large_changes():
    assert category((128,128))=='micro'
    assert category((850,180))=='strip'
    assert category((851,180))=='full'
    assert category((500,181))=='full'


def test_delta_review_requires_base_and_every_patch():
    data={'patches':[{'id':0,'reviewed_at':1},{'id':1,'reviewed_at':None}],
          'frames':[
              {'file':'a','previous_file':None,'source_sha256':'A','patch':0,
               'box':[0,0,10,10],'size':[10,10]},
              {'file':'b','previous_file':'a','source_sha256':'B','patch':1,
               'box':[1,1,2,2],'size':[10,10]},
              {'file':'c','previous_file':'b','source_sha256':'C','patch':0,
               'box':[1,1,2,2],'size':[10,10]},
              {'file':'a','source_sha256':'A','same_as':0}]}
    assert reviewed_delta_frames(data)=={'A'}
    data['patches'][1]['reviewed_at']=2
    assert reviewed_delta_frames(data)=={'A','B','C'}


def test_full_review_does_not_require_unreviewed_preceding_frame():
    data={'patches':[{'id':0,'reviewed_at':None},{'id':1,'reviewed_at':1}],
          'frames':[
              {'file':'a','previous_file':None,'source_sha256':'A','patch':0,
               'box':[0,0,10,10],'size':[10,10]},
              {'file':'b','previous_file':'a','source_sha256':'B','patch':1,
               'box':[0,0,10,10],'size':[10,10]}]}
    assert reviewed_delta_frames(data)=={'B'}


def test_responsive_failure_does_not_skip_other_viewports():
    import asyncio
    from types import SimpleNamespace
    import pytest
    pytest.importorskip('playwright.async_api')
    from scripts.verify_web_public_actions import Journey, VIEWPORTS

    class Probe:
        def __init__(self):
            self.visited, self.failures, self.resets = [], [], 0
            self.page = SimpleNamespace(set_viewport_size=self.resize)

        async def resize(self, size):
            self.final_size = size

        async def step(self, name, operation):
            try:
                await operation()
                return True, None
            except AssertionError:
                self.failures.append(name)
                return False, None

        async def responsive_viewport(self, name, width, height):
            self.visited.append(name)
            if name == '平板竖屏':
                raise AssertionError('编辑面板被原稿覆盖')

        async def reset_view(self):
            self.resets += 1

    probe = Probe()
    asyncio.run(Journey.responsive(probe))
    assert probe.visited == [row[0] for row in VIEWPORTS]
    assert probe.failures == ['V00 平板竖屏 全部入口']
    assert probe.resets == 1
    assert probe.final_size == {'width': 1440, 'height': 1000}


def test_reference_audit_clicks_even_a_missing_target():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from scripts.verify_web_public_actions import Journey
    from scripts.web_public_evidence import URL

    calls=[]
    async def click():
        calls.append('click')
    async def target_exists(_script, target):
        calls.append(('target',target))
        return False
    links=Mock()
    links.evaluate_all=AsyncMock(return_value=[{'i':0,'href':'#b1%20b2'}])
    links.nth.return_value.click=click
    body=SimpleNamespace(evaluate=target_exists)
    frame=SimpleNamespace(locator=lambda selector:body if selector=='body' else links)
    failures=[]
    async def step(name, operation):
        try:
            await operation()
        except AssertionError:
            failures.append(name)
    fake=SimpleNamespace(page=SimpleNamespace(frame_locator=lambda _selector:frame,url=URL+'/#task=test'),
                         tid='test',reset_view=AsyncMock(),wait_preview_document=AsyncMock(),step=step,e=Mock())
    asyncio.run(Journey.reference_links(fake))
    assert calls==['click',('target','b1 b2')]
    assert failures==['B11 预览内部链接 0 #b1%20b2']
    fake.wait_preview_document.assert_awaited_once()


def test_blank_preview_is_not_reported_as_no_internal_links():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    import pytest
    from scripts.verify_web_public_actions import Journey

    fake=SimpleNamespace(reset_view=AsyncMock(),
                         wait_preview_document=AsyncMock(side_effect=TimeoutError('preview blank')),
                         page=Mock(),e=Mock())
    with pytest.raises(TimeoutError,match='preview blank'):
        asyncio.run(Journey.reference_links(fake))
    fake.e.event.assert_not_called()
    fake.page.frame_locator.assert_not_called()


def test_trace_failure_is_recorded_and_context_is_closed(tmp_path):
    import asyncio
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from scripts.verify_web_public_actions import finish_artifacts

    evidence=SimpleNamespace(stop=AsyncMock(),folder=tmp_path)
    context=SimpleNamespace(tracing=SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError('trace failed'))),
                            close=AsyncMock())
    asyncio.run(finish_artifacts(evidence,context))
    evidence.stop.assert_awaited_once()
    context.close.assert_awaited_once()
    assert json.loads((tmp_path/'artifact-errors.json').read_text())==[
        {'artifact':'trace','message':'trace failed'}]


def test_chapter_recovery_does_not_change_network_state():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from scripts.verify_web_public_actions import Journey

    context=SimpleNamespace(set_offline=AsyncMock())
    fake=SimpleNamespace(e=SimpleNamespace(issues=[],event=Mock()),
                         step=AsyncMock(return_value=(False,None)),
                         page=SimpleNamespace(context=context,unroute_all=AsyncMock(),
                                              set_viewport_size=AsyncMock()),
                         reset_view=AsyncMock())
    asyncio.run(Journey.chapter(fake,'checks',AsyncMock()))
    fake.e.event.assert_called_once_with('chapter-end',chapter='checks',status='issues-found')
    fake.page.unroute_all.assert_awaited_once_with(behavior='wait')
    fake.reset_view.assert_awaited_once()
    context.set_offline.assert_not_called()
