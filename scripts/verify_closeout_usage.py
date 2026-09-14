"""内部验收：实际缓存响应、两次转换、成果包与界面计量逐层核对。"""
from pathlib import Path
import io
import json
import sys
import zipfile

from lxml import etree
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from word2jats.llm.usage import summarize_usage

OUT = ROOT/'reports/finals-closeout-20260914'


def main():
    prototype = Path(json.loads((OUT/'独立包位置.json').read_text())['prototype'])
    evidence = {'actual_conversions': [], 'ui_states': []}
    for provider in ('DeepSeek', 'Qwen'):
        first = json.loads((OUT/f'独立原型-{provider}首次/验收.json').read_text())
        second = json.loads((OUT/f'独立原型-{provider}复用/验收.json').read_text())
        assert first['xml_sha256'] == second['xml_sha256']
        assert first['llm']['calls'] > 0 and first['llm']['cache_hits'] == 0
        assert second['llm']['calls'] == 0
        assert first['llm']['result_usage'] == second['llm']['result_usage']
        assert second['llm']['incremental_usage']['total_tokens'] == 0
        task = json.loads((prototype/'webapp/_runs'/second['task_id']/'task.json').read_text())
        records = task['result']['stats']['llm']['reused_usage_records']
        for record in records:
            cached = json.loads((prototype/'webapp/_cache'/(record['cache_key']+'.json')).read_text())
            assert record['usage'] == cached['usage']
            assert record['request_id'] == cached['response_meta']['request_id']
        expected = summarize_usage(records)
        assert {k:v for k,v in second['llm']['result_usage'].items() if k != 'available'} == expected
        for stage in ('首次','复用'):
            with zipfile.ZipFile(OUT/f'独立原型-{provider}{stage}/下载包.zip') as package:
                assert package.namelist().count('CEOG50327.xml') == 1
                xml = etree.fromstring(package.read('CEOG50327.xml'))
                assert '修订验证' in ''.join(xml.find('front/article-meta/title-group/article-title').itertext())
                hrefs = xml.xpath('//@xlink:href', namespaces={'xlink':'http://www.w3.org/1999/xlink'})
                hrefs = {href for href in hrefs if ':' not in href and not href.startswith('#')}
                with zipfile.ZipFile(io.BytesIO(package.read('figures.zip'))) as figures:
                    assert set(figures.namelist()) == hrefs
                    with zipfile.ZipFile(ROOT/'样例数据/S03/初始文件.docx') as source:
                        original = {source.read(n) for n in source.namelist() if n.startswith('word/media/')}
                    assert all(figures.read(n) in original for n in figures.namelist())
                usage = json.loads(package.read('转换用量.json'))['model_usage']
                assert usage['result_usage'] == second['llm']['result_usage']
        evidence['actual_conversions'].append({'provider':provider, 'initial_task':first['task_id'],
            'reused_task':second['task_id'], 'usage':expected, 'checks':['缓存原始响应逐项一致',
            '首次与复用 XML 一致', '原始计量一致', '新增计量为零', '下载当前修订 XML', '媒体原字节与引用一致']})
    folder = OUT/'用量状态最终截图'; folder.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        for number, label in ((1,'Qwen'),(4,'DeepSeek'),(6,'全部复用'),(7,'部分计量缺失')):
            page.goto(f'http://127.0.0.1:8514/#task={number:016x}')
            expect(page.locator('#preview-loading')).to_be_hidden(timeout=30000)
            page.locator('[data-panel="usage"]').click()
            expect(page.locator('.usage-grid')).to_be_visible()
            if number == 6:
                expect(page.locator('#panel-content')).to_contain_text('没有新增消耗')
                assert page.locator('.usage-stat strong').first.inner_text() != '0'
            if number == 7:
                expect(page.locator('#panel-content')).to_contain_text('部分原始用量记录缺失')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=folder/f'{label}.png')
            evidence['ui_states'].append(label)
        # 缺失旧记录的兼容状态仅通过测试浏览器拦截构造，不修改真实任务。
        response = page.request.get('http://127.0.0.1:8514/api/result/0000000000000006').json()
        llm = response['stats']['llm']; llm['result_usage'].update(available=False, complete=False)
        page.route('**/api/result/0000000000000006', lambda route:route.fulfill(json=response))
        page.goto('http://127.0.0.1:8514/#task=0000000000000006')
        page.locator('[data-panel="usage"]').click()
        expect(page.locator('#panel-content')).to_contain_text('暂无法提供完整数值')
        assert set(page.locator('.usage-stat strong').all_text_contents()) == {'—'}
        page.screenshot(path=folder/'缺失历史计量.png')
        evidence['ui_states'].append('缺失历史计量')
        page.unroute('**/api/result/0000000000000006')
        page.locator('#panel-close').click()
        page.set_viewport_size({'width':390,'height':844})
        page.goto('http://127.0.0.1:8514/#task=0000000000000006')
        page.reload()
        page.locator('[data-panel="usage"]').click()
        expect(page.locator('#panel-content')).to_contain_text('没有新增消耗')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.screenshot(path=folder/'窄屏复用计量.png')
        evidence['ui_states'].append('窄屏复用计量')
        assert not errors, errors
        evidence['browser_errors'] = errors
        browser.close()
    (OUT/'用量与成果最终核验.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print('实际计量、两次转换、当前成果包和 6 种用量界面状态全部通过。')


if __name__ == '__main__':
    main()
