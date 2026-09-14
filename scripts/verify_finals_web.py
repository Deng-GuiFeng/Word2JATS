"""本机浏览器验收，实际操作上传、预览、复核与下载；不替代 pytest。"""
from pathlib import Path
import argparse
import json
import zipfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8505")
    parser.add_argument("--sample", default="S03")
    parser.add_argument("--fresh", action="store_true", help="实际勾选重新识别全文")
    parser.add_argument("--provider", choices=("dashscope", "deepseek"), default="dashscope")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    registry = json.loads((ROOT / "样例数据/样例登记.json").read_text())["样例"]
    sample = next(s for s in registry if s["key"] == args.sample)
    output = args.output or ROOT / "reports/web-final" / args.sample
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.locator("#doi-input").fill(sample["doi"])
        page.locator("#journal-input").select_option(sample["journal"])
        page.locator("#provider-input").select_option(args.provider)
        if args.fresh:
            page.locator("#fresh-input").check()
        page.locator("#docx-input").set_input_files(ROOT / "样例数据" / args.sample / "初始文件.docx")
        page.locator("#submit-btn").click()
        try:
            page.wait_for_function("['result','error'].includes(document.body.dataset.state)",
                                   timeout=600000 if args.fresh else 120000)
        except Exception:
            page.screenshot(path=output / '结果等待异常.png')
            (output / '浏览器异常.json').write_text(json.dumps({
                'state': page.locator('body').get_attribute('data-state'),
                'errors': errors, 'text': page.locator('body').inner_text(),
            }, ensure_ascii=False, indent=2))
            raise
        assert page.locator("body").get_attribute("data-state") == "result", page.locator("#error-msg").inner_text()
        frame = page.locator("#render-frame").element_handle().content_frame()
        # 结果外壳先显示，iframe 的 about:blank 也可能已处于 networkidle。
        # 必须等待真正的校样文档，不能把空白帧和空图片列表判为验收通过。
        frame.wait_for_url("**/api/render/*", timeout=60000)
        frame.locator(".document-title").wait_for(timeout=60000)
        frame.wait_for_load_state("networkidle")
        image_state = frame.locator("img").evaluate_all("els => els.map(e => ({src:e.getAttribute('src'),ok:e.complete && e.naturalWidth>0}))")
        assert all(i["ok"] for i in image_state), image_state
        fallback_links = frame.locator("a.w2j-media-fallback").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        for href in fallback_links:
            assert page.request.get(args.url + href).ok, href
        page.screenshot(path=output / "成品预览.png")
        sheet = page.locator('.sheet').bounding_box()
        page.screenshot(path=output / '校样结果区.png', clip={
            'x': sheet['x'], 'y': sheet['y'], 'width': sheet['width'],
            'height': min(850, sheet['height']),
        })
        download_url = page.locator("#download-btn").get_attribute("href")
        task_id = download_url.rsplit("/", 1)[-1]
        result = page.request.get(args.url + "/api/result/" + task_id).json()
        assert result['stats']['llm']['provider'] == args.provider
        assert 'usage_records' not in result['stats']['llm']
        page.locator('#usage-panel').evaluate('el => el.open = true')
        page.locator('#usage-panel').screenshot(path=output / '转换用量.png')
        if args.fresh:
            assert result['stats']['llm']['usage']['complete']
            assert result['stats']['llm']['usage']['input_tokens'] > 0
        page.locator("#review-open").click()
        if result["review_items"]:
            page.locator(".review-card").first.evaluate("el => el.open = true")
            page.set_viewport_size({"width": 1000, "height": 1100})
            page.locator("#review-view").screenshot(path=output / "工作台演示.png", animations="disabled")
            reference_card = page.locator('.review-card').filter(has_text='参考文献保留了完整原文').first
            if reference_card.count():
                page.locator('.review-card').evaluate_all('els => els.forEach(el => el.open = false)')
                reference_card.evaluate('el => el.open = true')
                page.locator('#review-view').screenshot(path=output / '文献复核演示.png', animations='disabled')
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.locator('.review-card').first.evaluate('el => el.open = true')
            page.locator("[data-review-id]").first.select_option("revise")
            page.locator("[data-review-note]").first.fill("浏览器验收：对照原稿核对该项。")
            page.locator("#reviewer-name").fill("功能验收")
            page.locator("#review-save").click()
            page.get_by_text("已保存，下载文件包会包含本次复核记录。").wait_for()
        page.screenshot(path=output / "原文核对.png")
        with page.expect_download() as info:
            page.locator("#download-btn").click()
        info.value.save_as(output / "下载包.zip")
        with zipfile.ZipFile(output / "下载包.zip") as bundle:
            assert any(n.endswith(".xml") for n in bundle.namelist())
            usage = json.loads(bundle.read('转换用量.json'))
            assert usage['model_usage']['usage'] == result['stats']['llm']['usage']
            if result["review_items"]:
                record = json.loads(bundle.read("人工复核记录.json"))
                assert record["automatic_delivered"] == result["delivered"]
        after = page.request.get(args.url + "/api/result/" + task_id).json()
        assert after["delivered"] == result["delivered"]
        assert after["xml"] == result["xml"]
        assert not errors, errors
        summary = {"sample": args.sample, "task_id": task_id, "delivered": result["delivered"],
                   "review_items": len(result["review_items"]), "llm": result["stats"].get("llm"),
                   "errors": errors, "preview_images": image_state,
                   "original_media_links": fallback_links,
                   "verdict": page.locator("#verdict-line").inner_text()}
        (output / "验收.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
