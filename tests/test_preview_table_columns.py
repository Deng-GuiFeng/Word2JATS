"""HTML 表格列容器不得因注入定位锚点而产生幽灵列。"""
from lxml import etree
from playwright.sync_api import sync_playwright

from webapp.render import render_html


XML=b'''<article><front><article-meta><title-group><article-title>Test</article-title></title-group></article-meta></front>
<body><table-wrap id="T1"><label>Table 1</label><table id="grid">
<colgroup><col width="10%"/><col width="10%"/><col width="8%"/><col width="72%"/></colgroup>
<thead><tr><th>Content</th><th colspan="2">Question Type</th><th>Question</th></tr></thead>
<tbody><tr><td rowspan="2">Prevention</td><td rowspan="2">Multiple choice</td><td>A</td><td>First question with an intentionally long text</td></tr>
<tr><td>B</td><td>Second question</td></tr></tbody></table></table-wrap></body></article>'''


def test_table_containers_do_not_contain_illegal_anchors():
    html=render_html(XML,'sample')
    tree=etree.HTML(html)
    assert not tree.xpath('//table/a|//colgroup/a|//col/a|//thead/a|//tbody/a|//tr/a')
    assert tree.xpath('//table[@id="grid"]')
    assert tree.xpath('//td[@rowspan="2"]')
    assert len(tree.xpath('//col'))==4


def test_browser_keeps_four_columns_and_original_width_relationship():
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':1200,'height':900})
            page.route('**/*',lambda route:route.abort())
            page.set_content(render_html(XML,'sample'))
            page.add_style_tag(content='table{width:900px;table-layout:fixed;border-collapse:collapse}td,th{padding:4px}')
            columns=page.locator('#grid col')
            assert columns.count()==4
            assert page.locator('#grid colgroup').count()==1
            widths=columns.evaluate_all('cols=>cols.map(c=>c.getBoundingClientRect().width)')
            assert sum(widths)==900
            assert 0.70<widths[-1]/sum(widths)<0.74
            assert page.locator('#grid tr').nth(1).locator('td').count()==4
        finally:
            browser.close()
