"""验收辅助脚本不得忽略像素差异或自动标记视觉通过。"""
from PIL import Image, ImageChops

from scripts.review_web_public_deltas import category, delta
from scripts.audit_web_public_round import reviewed_delta_frames


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
