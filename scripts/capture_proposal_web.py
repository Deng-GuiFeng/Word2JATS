"""截取已存在的实际转换结果，供技术说明书排版；不更改任务或页面内容。"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--output', type=Path, default=Path('决赛提交/assets'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1440, 'height':900}, device_scale_factor=2)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url.rstrip('/') + '/#task=' + args.task)
        expect(page.locator('#result')).to_be_visible(timeout=60000)
        expect(page.locator('#preview-loading')).to_be_hidden(timeout=60000)
        page.frame_locator('#render-frame').locator('.document-title').wait_for()
        page.screenshot(path=args.output / '转换结果预览.png', animations='disabled')
        page.locator('[data-panel="article"]').click()
        expect(page.locator('[data-field="title"]')).to_be_visible()
        page.screenshot(path=args.output / '文章信息修订.png', animations='disabled')
        assert not errors, errors
        browser.close()
    print('已保存两张 2880 × 1800 实际界面截图，无页面修改。')


if __name__ == '__main__':
    main()
