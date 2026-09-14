"""真实服务浏览器验收：上传、预览、原稿、修改、下载和恢复。

会创建独立转换任务；仅修改本脚本上传所得的结果，不修改已有任务。
默认允许复用服务端分析缓存；--fresh 在上传完成后通过重新转换入口重新识别。
"""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8505')
    parser.add_argument('--sample', default='S03')
    parser.add_argument('--fresh', action='store_true', help='上传后通过“重新转换”再做一次完整识别，会产生模型调用')
    parser.add_argument('--provider', choices=('dashscope', 'deepseek'), default='dashscope')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    args.url = args.url.rstrip('/')
    registry = json.loads((ROOT / '样例数据/样例登记.json').read_text())['样例']
    sample = next(s for s in registry if s['key'] == args.sample)
    docx = ROOT / '样例数据' / args.sample / '初始文件.docx'
    output = args.output or ROOT / 'reports/web-final' / args.provider / args.sample
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def read(path):
            response = page.request.get(args.url + path)
            assert response.ok, (path, response.status)
            return response.json()

        def wait_result():
            try:
                page.wait_for_function("['result','error'].includes(document.body.dataset.state)", timeout=600000)
                assert page.locator('body').get_attribute('data-state') == 'result', page.locator('#error-msg').inner_text()
                expect(page.locator('#preview-loading')).to_be_hidden(timeout=60000)
                frame = page.frame_locator('#render-frame')
                frame.locator('.document-title').wait_for(timeout=60000)
                return frame
            except Exception:
                page.screenshot(path=output / '结果等待异常.png')
                (output / '浏览器异常.json').write_text(json.dumps({'state': page.locator('body').get_attribute('data-state'),
                    'errors': errors, 'text': page.locator('body').inner_text()}, ensure_ascii=False, indent=2))
                raise

        page.goto(args.url)
        page.locator('#publication-options summary').click()
        page.locator('#doi-input').fill(sample['doi'])
        page.locator('#journal-input').select_option(sample['journal'])
        page.locator('#provider-input').select_option(args.provider)
        page.locator('#docx-input').set_input_files(docx)
        page.locator('#submit-btn').click()
        frame = wait_result()
        if args.fresh:
            previous = page.url
            page.locator('#more-menu summary').click()
            page.locator('#reconvert-btn').click()
            page.locator('#reconvert-provider').select_option(args.provider)
            page.locator('#dialog-confirm').click()
            page.wait_for_url(lambda url: str(url) != previous, timeout=30000)
            frame = wait_result()
        task_id = page.url.split('task=')[-1]
        result = read('/api/result/' + task_id)
        assert result['stats']['llm']['provider'] == args.provider
        assert 'usage_records' not in result['stats']['llm']
        if args.fresh:
            assert result['stats']['llm']['usage']['complete']
            assert result['stats']['llm']['usage']['input_tokens'] > 0
        image_state = frame.locator('img').evaluate_all('els => els.map(e => ({src:e.getAttribute("src"),ok:e.complete && e.naturalWidth>0}))')
        assert all(i['ok'] for i in image_state), image_state
        fallback_links = frame.locator('a.w2j-media-fallback').evaluate_all('els => els.map(e => e.getAttribute("href"))')
        for href in fallback_links:
            assert page.request.get(args.url + href).ok, href
        page.screenshot(path=output / '成品预览.png')
        page.locator('[data-panel="source"]').click()
        source = page.frame_locator('#panel-content iframe')
        source.locator('body').wait_for()
        expect(source.locator('body')).not_to_be_empty()
        page.screenshot(path=output / '原稿对照.png')
        original = page.request.get(args.url + '/api/original/' + task_id)
        assert original.ok and original.body() == docx.read_bytes()
        page.locator('#panel-close').click()
        page.locator('[data-panel="usage"]').click()
        page.screenshot(path=output / '转换用量.png')
        page.locator('#panel-close').click()
        page.locator('[data-panel="article"]').click()
        page.screenshot(path=output / '文章信息修订.png')
        before = read('/api/workbench/' + task_id)
        title = before['fields']['title'] + '（修订验证）'
        page.locator('[data-field="title"]').fill(title)
        page.locator('#save-edit').click()
        expect(page.locator('#toast')).to_contain_text('修改已保存', timeout=30000)
        expect(frame.locator('.document-title')).to_have_text(title, timeout=30000)
        after = read('/api/result/' + task_id)
        assert after['xml'] != result['xml']
        assert after['stats']['llm'] == result['stats']['llm']
        with page.expect_download() as info:
            page.locator('#download-btn').click()
        download = info.value
        assert download.suggested_filename == result['download_filename'], download.suggested_filename
        assert download.suggested_filename.startswith('Word2JATS-')
        download.save_as(output / '下载包.zip')
        with zipfile.ZipFile(output / '下载包.zip') as bundle:
            names = bundle.namelist()
            xml_names = [name for name in names if name.endswith('.xml')]
            assert len(xml_names) == 1, xml_names
            assert xml_names[0] == result['xml_filename']
            assert 'figures.zip' in names
            assert bundle.read(xml_names[0]).decode('utf-8') == after['xml']
            assert '修改记录.json' in names
            assert json.loads(bundle.read('转换用量.json'))['model_usage']['usage'] == result['stats']['llm']['usage']
        page.locator('#panel-close').click()
        page.locator('#more-menu summary').click()
        page.locator('#restore-btn').click()
        page.locator('#dialog-confirm').click()
        expect(page.locator('#toast')).to_contain_text('已恢复', timeout=30000)
        restored = read('/api/result/' + task_id)
        assert restored['xml'] == result['xml']
        page.reload()
        wait_result()
        assert read('/api/result/' + task_id)['xml'] == result['xml']
        assert not errors, errors
        summary = {'sample': args.sample, 'task_id': task_id, 'provider': args.provider, 'fresh': args.fresh,
                   'delivered': result['delivered'], 'llm': result['stats'].get('llm'), 'errors': errors,
                   'preview_images': image_state, 'original_media_links': fallback_links,
                   'original_sha256': hashlib.sha256(original.body()).hexdigest(),
                   'xml_sha256': hashlib.sha256(result['xml'].encode()).hexdigest(),
                   'checks': ['真实上传', '预览', '原稿一致', '用量', '实际修改', '下载修订XML', '修改不新增用量', '恢复原始XML', '刷新恢复']}
        (output / '验收.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
