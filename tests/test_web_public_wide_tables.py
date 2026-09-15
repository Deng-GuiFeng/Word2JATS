"""验收脚本的宽表遍历用真实浏览器自测，不修改被测网页实现。"""
import asyncio
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from scripts.verify_web_public_actions import Journey


async def exercise(overflow,document_scroll=False):
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        try:
            page=await browser.new_page(viewport={'width':700,'height':500})
            await page.set_content('<iframe id="source-frame" style="width:320px;height:250px"></iframe>')
            await page.locator('iframe').evaluate('''(e,overflow)=>{
              e.srcdoc=`<style>body{margin:8px}.table-scroll{width:290px;overflow:${overflow}}
                table{width:900px;height:950px;border-collapse:collapse}td{min-width:290px;border:1px solid}</style>
                <p>table below</p><div class="table-scroll" tabindex="0"><table>
                ${Array.from({length:10},(_,i)=>'<tr><td>'+i+' left</td><td>middle</td><td>right</td></tr>').join('')}
                </table></div><p>end</p>`;
            }''',overflow)
            node=page.frame_locator('iframe').locator('.table-scroll')
            await node.wait_for()
            if document_scroll:
                await node.evaluate('e=>{e.style.width="900px";e.style.overflow="visible";}')
            positions=[]
            async def shot(label):
                positions.append((label,await node.evaluate('''(e,useDocument)=>({
                  left:(useDocument?document.scrollingElement:e).scrollLeft,
                  top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,view:innerHeight})''',document_scroll)))
            probe=SimpleNamespace(page=page,e=SimpleNamespace(label='',shot=shot))
            await Journey.wide_table(probe,'#source-frame',node,'wide',document_scroll)
            final=await node.evaluate('(e,useDocument)=>(useDocument?document.scrollingElement:e).scrollLeft',document_scroll)
            return positions,final
        finally:
            await browser.close()


def test_wide_table_reads_every_horizontal_slice_top_to_bottom_and_returns():
    positions,final=asyncio.run(exercise('auto'))
    grid=[row for label,row in positions if '行屏' in label]
    columns={round(row['left']) for row in grid}
    assert min(columns)==0 and max(columns)>600
    for column in columns:
        rows=[row for row in grid if round(row['left'])==column]
        assert rows[0]['top']<=10
        assert rows[-1]['bottom']<=rows[-1]['view']+2
        assert len(rows)>=4
    assert any('键盘左移' in label for label,_ in positions)
    assert final<=1


def test_wide_table_does_not_pass_when_hidden_columns_cannot_be_scrolled():
    with pytest.raises(AssertionError,match='横向滚动没有前进'):
        asyncio.run(exercise('hidden'))


def test_preview_document_scroll_is_not_confused_with_table_container_scroll():
    positions,final=asyncio.run(exercise('visible',True))
    grid=[row for label,row in positions if '行屏' in label]
    assert max(row['left'] for row in grid)>500
    for column in {round(row['left']) for row in grid}:
        rows=[row for row in grid if round(row['left'])==column]
        assert rows[0]['top']<=10
        assert rows[-1]['bottom']<=rows[-1]['view']+2
    assert final<=1
