"""Web 应用集成测试（Phase 1）。

用 FastAPI TestClient 走完整流程：上传样例 docx → 异步转换 → 轮询到完成 →
结果 XML 通过 DTD 校验 → 下载的 zip 里含该 XML。

为免费 + 秒回 + 可复现，把 LLM 缓存指向样例 01 的预热缓存
（reports/eval/_llm_cache/dashscope/01），命中缓存则零调用、零费用。
若该缓存不在，则退化为一次真实转换（需 .env 里的 key），仍应产出合法 XML。
"""

import io
import os
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DOCX = ROOT / "样例数据" / "01" / "初始文件.docx"
SAMPLE_CACHE = ROOT / "reports" / "eval" / "_llm_cache" / "dashscope" / "01"


@pytest.fixture(scope="module")
def client():
    # 转换前把缓存指向样例 01 预热缓存（命中则免费秒回）
    if SAMPLE_CACHE.is_dir():
        os.environ["W2J_WEBAPP_CACHE"] = str(SAMPLE_CACHE)
    from fastapi.testclient import TestClient
    from webapp.app import app
    with TestClient(app) as c:
        yield c


def _wait_done(client, task_id, timeout=180):
    """轮询状态直到完成/失败；缓存命中时通常 1–2 秒内结束。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get("/api/status/%s" % task_id)
        assert r.status_code == 200
        s = r.json()
        if s["status"] == "done":
            return s
        if s["status"] == "error":
            pytest.fail("转换失败：%s" % s.get("error"))
        time.sleep(0.5)
    pytest.fail("转换超时未完成")


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="缺样例 docx")
def test_convert_flow_produces_valid_jats(client):
    files = {"docx": ("初始文件.docx", SAMPLE_DOCX.read_bytes(),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    data = {"doi": "10.31083/JIN49347", "journal": "JIN"}

    r = client.post("/api/convert", files=files, data=data)
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    assert task_id

    _wait_done(client, task_id)

    # 结果：DTD 合法 + stats 齐全
    rr = client.get("/api/result/%s" % task_id)
    assert rr.status_code == 200, rr.text
    res = rr.json()
    assert res["validation"]["dtd_valid"] is True
    assert res["validation"]["well_formed"] is True
    assert "authors" in res["stats"]
    assert res["xml"].lstrip().startswith("<?xml")
    assert "<article" in res["xml"]

    # 下载：zip 里含该文章的 XML
    dl = client.get("/api/download/%s" % task_id)
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(dl.content))
    names = zf.namelist()
    assert any(n.endswith(".xml") for n in names), names

    # 校验明细 + 内容忠实自检
    assert isinstance(res["checks"], list)
    fid = res["fidelity"]
    assert fid is not None
    assert 0 <= fid["from_source_pct"] <= 100
    assert 0 <= fid["kept_pct"] <= 100
    assert isinstance(fid["extra_words"], list)

    # 渲染视图：服务端 XSLT → HTML，图片改写到本服务接口、MathML 去前缀
    rn = client.get("/api/render/%s" % task_id)
    assert rn.status_code == 200
    assert "/api/figure/" in rn.text
    assert "<mml:" not in rn.text

    # 配套 CSS + 结果图片可取（图片名随原格式，从渲染视图里取真实引用，不写死扩展名）
    assert client.get("/assets/jats-preview.css").status_code == 200
    import re as _re
    m = _re.search(r"/api/figure/%s/[^\"'\s)]+" % task_id, rn.text)
    assert m, "渲染视图未见图片引用"
    fig = client.get(m.group(0))
    assert fig.status_code == 200
    assert fig.headers["content-type"].startswith("image/")


@pytest.mark.skipif(not SAMPLE_DOCX.exists() or not SAMPLE_CACHE.is_dir(),
                    reason="缺样例或预热缓存")
def test_convert_emits_stage_progress():
    """convert() 的进度回调按 解析→理解→渲染→校验 依次上报（阶段钩子）。"""
    import tempfile
    from word2jats.pipeline import ConvertOptions, convert
    stages = []
    opts = ConvertOptions(
        docx_path=str(SAMPLE_DOCX), out_dir=tempfile.mkdtemp(),
        journal_id="JIN", doi="10.31083/JIN49347",
        llm_cache_dir=str(SAMPLE_CACHE),
        progress=lambda key, label: stages.append(key),
    )
    convert(opts)
    assert stages == ["parse", "understand", "render", "validate"]


def test_reject_non_docx(client):
    files = {"docx": ("bad.txt", b"not a docx", "text/plain")}
    r = client.post("/api/convert", files=files)
    assert r.status_code == 400


def test_status_404_for_unknown(client):
    r = client.get("/api/status/deadbeef")
    assert r.status_code == 404


def test_corrupt_docx_fails_gracefully(client):
    """坏文件：任务转 error、给人话（不泄露路径）、服务不崩。"""
    import time as _t
    files = {"docx": ("bad.docx", b"not a real docx, just bytes",
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    r = client.post("/api/convert", files=files)
    assert r.status_code == 200
    tid = r.json()["task_id"]
    for _ in range(20):
        s = client.get("/api/status/%s" % tid).json()
        if s["status"] in ("done", "error"):
            break
        _t.sleep(0.3)
    assert s["status"] == "error"
    assert "Word" in s["error"] or "docx" in s["error"]
    assert "/" not in s["error"]                 # 不泄露服务器路径
    # 服务仍可用
    assert client.get("/api/journals").status_code == 200


def test_journals_list(client):
    r = client.get("/api/journals")
    assert r.status_code == 200
    js = r.json()["journals"]
    assert any(j["id"] == "JIN" for j in js)


def test_strip_stylesheet_warning():
    """渲染层剥掉 NCBI 样式表诊断 span，但保留脚注正文。"""
    from webapp.render import _strip_stylesheet_warnings
    html = ('<p><span class="warning">{ label (or @symbol) needed for '
            "fn[@id='fn1'] }</span> <sup>&#8224;</sup>contributed equally</p>")
    out = _strip_stylesheet_warnings(html)
    assert "needed for" not in out
    assert 'class="warning"' not in out
    assert "contributed equally" in out


def test_figure_tiff_served_as_png():
    """TIFF 出版图浏览器不认：/api/figure 按需转 PNG（下载 zip 原始字节不变）。"""
    import io
    from PIL import Image
    from fastapi.testclient import TestClient
    import webapp.app as A

    with TestClient(A.app) as c:
        run = ROOT / "webapp" / "_runs" / "tifftask"
        (run / "output" / "ART").mkdir(parents=True, exist_ok=True)
        tif = run / "output" / "ART" / "fig-01.tif"
        Image.new("RGB", (8, 8), (200, 30, 30)).save(str(tif), format="TIFF")
        with A._LOCK:
            A.TASKS["tifftask"] = {
                "task_id": "tifftask", "status": "done", "stage": "完成",
                "result": {"out_dir": str(run / "output")},
            }
        try:
            r = c.get("/api/figure/tifftask/ART/fig-01.tif")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            assert r.content[:4] == b"\x89PNG"
            Image.open(io.BytesIO(r.content))  # 可被解码
        finally:
            import shutil
            shutil.rmtree(run, ignore_errors=True)
            with A._LOCK:
                A.TASKS.pop("tifftask", None)


def test_preview_polish_orcid_and_img_width():
    """预览补丁：ORCID 裸 URL → 绿色 iD 徽章；注入 img 限宽防大图撑爆版式。"""
    from webapp.render import _polish_preview
    html = ('<head><title>t</title></head><body>'
            '<span class="generated">[</span>https://orcid.org/0009-0004-8148-7152'
            '<span class="generated">] </span>Shuang Wang</body>')
    out = _polish_preview(html)
    assert 'class="w2j-orcid"' in out
    assert 'href="https://orcid.org/0009-0004-8148-7152"' in out
    assert '<span class="generated">[</span>https://orcid' not in out
    assert "max-width:100%" in out and "</head>" in out


def test_figure_transparent_tiff_composited_on_white():
    """透明背景 TIFF 须合成到白底（否则 convert RGB 填黑 → 黑轴黑字在黑底隐没）。"""
    import io
    from PIL import Image
    from fastapi.testclient import TestClient
    import webapp.app as A

    with TestClient(A.app) as c:
        run = ROOT / "webapp" / "_runs" / "rgbatask"
        (run / "output" / "ART").mkdir(parents=True, exist_ok=True)
        tif = run / "output" / "ART" / "fig-01.tif"
        # 透明背景 + 黑色绘制内容
        im = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        im.putpixel((5, 5), (0, 0, 0, 255))
        im.save(str(tif), format="TIFF")
        with A._LOCK:
            A.TASKS["rgbatask"] = {
                "task_id": "rgbatask", "status": "done", "stage": "完成",
                "result": {"out_dir": str(run / "output")},
            }
        try:
            r = c.get("/api/figure/rgbatask/ART/fig-01.tif")
            assert r.status_code == 200 and r.headers["content-type"] == "image/png"
            png = Image.open(io.BytesIO(r.content)).convert("RGB")
            assert png.getpixel((0, 0)) == (255, 255, 255)   # 透明区 → 白，不是黑
        finally:
            import shutil
            shutil.rmtree(run, ignore_errors=True)
            with A._LOCK:
                A.TASKS.pop("rgbatask", None)


def test_render_rewrites_figures_and_mathml():
    """render_html：本地图片改写到接口、外链保留、MathML 去前缀。"""
    from webapp.render import render_html
    xml = (
        b'<?xml version="1.0"?>\n<!DOCTYPE article>\n'
        b'<article xmlns:mml="http://www.w3.org/1998/Math/MathML"'
        b' xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<body><sec><title>T</title>'
        b'<p>x <inline-formula><mml:math><mml:mi>y</mml:mi></mml:math></inline-formula></p>'
        b'<fig><graphic xlink:href="pics/f1.jpg"/></fig>'
        b'<p><graphic xlink:href="https://ex.org/cc.png"/></p>'
        b'</sec></body></article>'
    )
    html = render_html(xml, "TID")
    assert "/api/figure/TID/pics/f1.jpg" in html   # 本地图改写
    assert "https://ex.org/cc.png" in html          # 外链保留
    assert "<mml:" not in html                       # MathML 去前缀
    assert "<math" in html


def test_fidelity_join_reduces_false_fabrication():
    """fidelity：docx 按段拼合切词，能识别多出/缺失，覆盖率为百分数。"""
    import io as _io
    import zipfile as _zip
    from webapp.fidelity import summary

    def make_docx(paragraphs):
        buf = _io.BytesIO()
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        ps = "".join(
            "<w:p>%s</w:p>" % "".join("<w:r><w:t>%s</w:t></w:r>" % t for t in runs)
            for runs in paragraphs)
        doc = ('<?xml version="1.0"?><w:document xmlns:w="%s"><w:body>%s</w:body></w:document>'
               % (W, ps))
        with _zip.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", doc)
        return buf.getvalue()

    # docx 把 "paradigm" 拆成 "p"+"aradigm"（Word 常见排版拆词）
    docx_bytes = make_docx([["p", "aradigm shift methodology"]])
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.write(fd, docx_bytes)
    os.close(fd)
    try:
        xml = (b'<article><body><sec><title>t</title>'
               b'<p>paradigm shift methodology</p></sec></body></article>')
        s = summary(path, xml)
        # 输出的 paradigm/shift/methodology 都能在按段拼合的 docx 里找到 → 不算多出
        assert "paradigm" not in s["extra_words"]
        assert s["from_source_pct"] >= 99.0
    finally:
        os.remove(path)


# ============ 分片 / 断点续传上传 ============

def _chunks(data: bytes, size: int):
    return [data[i:i + size] for i in range(0, len(data), size)] or [b""]


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="缺样例 docx")
def test_chunked_flow_produces_valid_jats(client):
    """分片路径端到端：init→逐片PUT→complete→轮询完成，产出与单请求同样合法的 JATS。"""
    import hashlib
    data = SAMPLE_DOCX.read_bytes()
    parts = _chunks(data, 256 * 1024)
    r = client.post("/api/upload/init",
                    data={"filename": "初始文件.docx", "size": len(data),
                          "total_chunks": len(parts)})
    assert r.status_code == 200, r.text
    uid = r.json()["upload_id"]
    for i, p in enumerate(parts):
        rr = client.put("/api/upload/%s/%d" % (uid, i), content=p)
        assert rr.status_code == 200, rr.text
    r2 = client.post("/api/upload/%s/complete" % uid,
                     data={"doi": "10.31083/JIN49347", "journal": "JIN",
                           "sha256": hashlib.sha256(data).hexdigest()})
    assert r2.status_code == 200, r2.text
    tid = r2.json()["task_id"]
    _wait_done(client, tid)
    res = client.get("/api/result/%s" % tid).json()
    assert res["validation"]["dtd_valid"] is True
    assert res["xml"].lstrip().startswith("<?xml")


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="缺样例 docx")
def test_chunked_reassembly_byte_identical(client):
    """乱序 + 重复上传各片，服务端组装出的 docx 与原文件逐字节一致。"""
    data = SAMPLE_DOCX.read_bytes()
    parts = _chunks(data, 300 * 1024)                          # 非 2 的幂，测边界
    r = client.post("/api/upload/init",
                    data={"filename": "x.docx", "size": len(data),
                          "total_chunks": len(parts)})
    uid = r.json()["upload_id"]
    for i in reversed(range(len(parts))):                      # 乱序
        assert client.put("/api/upload/%s/%d" % (uid, i), content=parts[i]).status_code == 200
    assert client.put("/api/upload/%s/0" % uid, content=parts[0]).status_code == 200  # 重复幂等
    r2 = client.post("/api/upload/%s/complete" % uid,
                     data={"journal": "JIN", "doi": "10.31083/JIN49347"})
    assert r2.status_code == 200, r2.text
    tid = r2.json()["task_id"]
    assembled = (ROOT / "webapp" / "_runs" / tid / "input.docx").read_bytes()
    assert assembled == data


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="缺样例 docx")
def test_chunked_resume_missing_chunk(client):
    """缺片时 complete 返回 409 + 缺失清单；补传后再 complete 成功（断点续传）。"""
    data = SAMPLE_DOCX.read_bytes()
    parts = _chunks(data, 256 * 1024)
    assert len(parts) >= 2
    r = client.post("/api/upload/init",
                    data={"filename": "x.docx", "size": len(data),
                          "total_chunks": len(parts)})
    uid = r.json()["upload_id"]
    for i, p in enumerate(parts[:-1]):                         # 故意漏最后一片
        assert client.put("/api/upload/%s/%d" % (uid, i), content=p).status_code == 200
    r409 = client.post("/api/upload/%s/complete" % uid, data={})
    assert r409.status_code == 409
    assert r409.json()["detail"]["missing"] == [len(parts) - 1]
    st = client.get("/api/upload/%s" % uid).json()             # status 报同一缺片
    assert st["missing"] == [len(parts) - 1]
    last = len(parts) - 1
    assert client.put("/api/upload/%s/%d" % (uid, last), content=parts[last]).status_code == 200
    r2 = client.post("/api/upload/%s/complete" % uid,
                     data={"journal": "JIN", "doi": "10.31083/JIN49347"})
    assert r2.status_code == 200, r2.text


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="缺样例 docx")
def test_chunked_size_mismatch_rejected(client):
    """声明大小与实收不符 → complete 400。"""
    data = SAMPLE_DOCX.read_bytes()
    r = client.post("/api/upload/init",
                    data={"filename": "x.docx", "size": len(data) + 99, "total_chunks": 1})
    uid = r.json()["upload_id"]
    client.put("/api/upload/%s/0" % uid, content=data)
    assert client.post("/api/upload/%s/complete" % uid, data={}).status_code == 400


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="缺样例 docx")
def test_chunked_sha_mismatch_rejected(client):
    """SHA-256 不匹配 → complete 400。"""
    data = SAMPLE_DOCX.read_bytes()
    r = client.post("/api/upload/init",
                    data={"filename": "x.docx", "size": len(data), "total_chunks": 1})
    uid = r.json()["upload_id"]
    client.put("/api/upload/%s/0" % uid, content=data)
    assert client.post("/api/upload/%s/complete" % uid,
                       data={"sha256": "00" * 32}).status_code == 400


def test_chunked_init_rejects_non_docx(client):
    assert client.post("/api/upload/init",
                       data={"filename": "x.txt", "size": 10, "total_chunks": 1}).status_code == 400


def test_chunked_init_rejects_oversize(client):
    assert client.post("/api/upload/init",
                       data={"filename": "x.docx", "size": 10 ** 12,
                             "total_chunks": 1}).status_code == 413


def test_chunked_index_out_of_range(client):
    r = client.post("/api/upload/init",
                    data={"filename": "x.docx", "size": 5, "total_chunks": 1})
    uid = r.json()["upload_id"]
    assert client.put("/api/upload/%s/5" % uid, content=b"hello").status_code == 400


def test_chunked_unknown_session_404(client):
    assert client.get("/api/upload/nope").status_code == 404
    assert client.put("/api/upload/nope/0", content=b"x").status_code == 404
    assert client.post("/api/upload/nope/complete", data={}).status_code == 404
