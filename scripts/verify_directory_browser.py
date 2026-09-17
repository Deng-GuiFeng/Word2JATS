"""补充提交版浏览器验收：密钥隔离、图片降级和缺失/复用计量。"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from dotenv import dotenv_values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1]/'dist/JiangLab/可运行原型/.env')
    args = ap.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    assert args.env_file.is_file(), '须提供实际配置文件，不能用空密钥列表代替泄露检查'
    keys = [v for k,v in dotenv_values(args.env_file).items()
            if k.endswith('_API_KEY') and v]
    assert len(keys) >= 2
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        for route in ('/.env', '/static/.env', '/static/%2e%2e/.env'):
            response = page.request.get(args.url+route)
            assert response.status == 404
            assert all(key.encode() not in response.body() for key in keys)
        # 精确模拟无解码能力；原图下载仍走未修改的真实 API。
        page.route('**/api/figure/**/*.wmf', lambda route:route.fulfill(
            status=200,content_type='image/wmf',body=b'not-browser-decodable'))
        page.goto(args.url+'/#task=0000000000000001')
        frame = page.frame_locator('#render-frame')
        fallback = frame.locator('a.w2j-media-fallback')
        expect(fallback.first).to_contain_text('下载原文件',timeout=30000)
        assert fallback.count() >= 2
        for link in fallback.all():
            response = page.request.get(link.get_attribute('href'))
            assert response.ok and response.body()
        assert frame.locator('img').evaluate_all('els=>els.every(e=>e.complete&&e.naturalWidth>0)')
        fallback.first.scroll_into_view_if_needed()
        page.screenshot(path=args.output/'图片无法解码.png')
        page.unroute('**/api/figure/**/*.wmf')
        data = page.request.get(args.url+'/api/result/0000000000000006').json()
        data['stats']['llm']['result_usage'].update(available=False, complete=False)
        page.route('**/api/result/0000000000000006', lambda route:route.fulfill(json=data))
        page.goto(args.url+'/#task=0000000000000006')
        page.locator('[data-panel="usage"]').click()
        expect(page.locator('#panel-content')).to_contain_text('暂无法提供完整数值')
        assert set(page.locator('.usage-stat strong').all_text_contents()) == {'—'}
        page.screenshot(path=args.output/'缺失历史计量.png')
        page.unroute('**/api/result/0000000000000006')
        page.set_viewport_size({'width':390,'height':844})
        page.goto(args.url+'/#task=0000000000000006'); page.reload()
        page.locator('[data-panel="usage"]').click()
        expect(page.locator('#panel-content')).to_contain_text('没有新增消耗')
        assert page.locator('.usage-stat strong').first.inner_text() != '0'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.screenshot(path=args.output/'窄屏复用计量.png')
        assert not errors, errors
        (args.output/'验收.json').write_text(json.dumps({'checks':[
            '密钥路径不可访问','WMF 不可解码时原图下载可用','无破图',
            '缺失历史计量不显示假零','窄屏复用保留原计量'], 'errors':errors},ensure_ascii=False,indent=2))
        browser.close()


if __name__ == '__main__':
    main()
