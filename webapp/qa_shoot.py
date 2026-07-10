"""参数化全流程密集截图（10 样例视觉验收用）。

用法: python webapp/qa_shoot.py <key> <docx> <figures|-> <doi> <outdir> [baseurl]
把一个样例喂给 Web 应用，走完整前端流程，逐环节密集截图到 <outdir>：
  初始页 → 选文件 → 提交后连拍(抓进度步骤条) → 结果各标签页 → 整篇渲染全长。
"""
import sys
import time
import pathlib

from playwright.sync_api import sync_playwright

key = sys.argv[1]
docx = sys.argv[2]
figures = sys.argv[3]
doi = sys.argv[4]
OUT = pathlib.Path(sys.argv[5])
BASE = sys.argv[6] if len(sys.argv) > 6 else "http://127.0.0.1:8033"
OUT.mkdir(parents=True, exist_ok=True)


def shot(pg, name):
    pg.screenshot(path=str(OUT / ("%s_%s.png" % (key, name))), full_page=True)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    pg.set_default_timeout(60000)

    pg.goto(BASE, wait_until="networkidle")
    time.sleep(0.3)
    shot(pg, "01_initial")

    pg.set_input_files("#docx-input", docx)
    if figures and figures != "-":
        pg.set_input_files("#figures-input", figures)
    if doi and doi != "-":
        pg.fill("#doi-input", doi)
    # 期刊留空 → 测 DOI 自动推断
    time.sleep(0.2)
    shot(pg, "02_chosen")

    pg.click("#submit-btn")
    # 提交后连拍，尽量抓到进度步骤条各态
    for i in range(4):
        time.sleep(0.15)
        try:
            shot(pg, "03_prog%d" % (i + 1))
        except Exception:
            pass

    pg.wait_for_selector("#result-card:not([hidden])", timeout=120000)
    time.sleep(1.8)  # 等 iframe 渲染 + 图片加载
    shot(pg, "04_render")

    for tab, name in [("xml", "05_xml"), ("validate", "06_validate"),
                      ("structure", "07_structure"), ("fidelity", "08_fidelity")]:
        pg.click(".tab[data-tab='%s']" % tab)
        time.sleep(0.4)
        shot(pg, name)

    # 整篇渲染全长（直接打开渲染接口，full_page 抓完整文章，查通篇图/公式/表）
    href = pg.get_attribute("#download-btn", "href") or ""
    tid = href.rstrip("/").split("/")[-1]
    if tid:
        rp = b.new_page(viewport={"width": 1000, "height": 900}, device_scale_factor=1)
        try:
            rp.goto(BASE + "/api/render/" + tid, wait_until="networkidle", timeout=60000)
            time.sleep(1.5)
            rp.screenshot(path=str(OUT / ("%s_09_render_full.png" % key)), full_page=True)
        except Exception as e:
            print("render_full skip:", e)

    b.close()
print("DONE", key)
