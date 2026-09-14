"""Web 应用对转换器公开接口的集成测试。

这里用固定候选包验证上传、异步状态、预览、图片和下载，
不把某版模型提示词的历史缓存当成 Web 接口契约。真实转换走检查点评测。
"""

import io
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DOCX = ROOT / "样例数据" / "01" / "初始文件.docx"


def _fake_convert(opts):
    """根据 ConvertResult 公开字段生成固定候选包。"""
    # 坏 docx 仍须走 Web 应用的友好错误通道。
    with zipfile.ZipFile(opts.docx_path):
        pass
    for key, label in (
        ("parse", "解析 Word 文档"), ("understand", "大模型判断结构"),
        ("render", "渲染 JATS 并回填图片"), ("validate", "DTD 校验与内容守恒"),
    ):
        if opts.progress:
            opts.progress(key, label)
    article_id = "JIN49347"
    package = Path(opts.out_dir) / "candidates" / "web-fixture" / article_id
    figure = package / article_id / "fig.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    # 1×1 像素 PNG。
    figure.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99"
        b"=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<article article-type="research-article" dtd-version="1.3" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<front><article-meta><title-group><article-title>Fixture article'
        '</article-title></title-group></article-meta></front>'
        '<body><sec><title>Body</title><p>Source-backed fixture.</p>'
        '<fig id="F1"><label>Figure 1</label><caption><p>Fixture figure.</p></caption>'
        f'<graphic xlink:href="{article_id}/fig.png"/></fig></sec></body></article>'
    ).encode()
    xml_path = package / f"{article_id}.xml"
    xml_path.write_bytes(xml)
    validation = SimpleNamespace(
        well_formed=True, dtd_valid=True, ok=True, errors=[]
    )
    return SimpleNamespace(
        xml_path=str(xml_path), candidate_xml=str(xml_path),
        candidate_dir=str(package), delivered=True, article_id=article_id,
        validation=validation,
        stats={"authors": 0, "llm": {"provider": "fixture"}},
    )


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    import webapp.app as module
    patch = pytest.MonkeyPatch()
    patch.setattr(module, "convert", _fake_convert)
    patch.setattr(module, "RUNS_DIR", tmp_path_factory.mktemp("web-runs"))
    patch.setattr(module, "UPLOADS_DIR", tmp_path_factory.mktemp("web-uploads"))
    initial_tasks = set(module.TASKS)
    try:
        with TestClient(module.app) as c:
            yield c
    finally:
        patch.undo()
        with module._LOCK:
            for task_id in set(module.TASKS) - initial_tasks:
                module.TASKS.pop(task_id, None)


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


def test_reject_non_docx(client):
    files = {"docx": ("bad.txt", b"not a docx", "text/plain")}
    r = client.post("/api/convert", files=files)
    assert r.status_code == 400


def test_model_choice_reaches_converter_and_usage_export_is_uniform(client, monkeypatch):
    import json
    import webapp.app as module
    seen = []

    def measured(opts):
        seen.append(opts.llm)
        result = _fake_convert(opts)
        result.stats['llm'] = {
            'provider': opts.llm, 'model': 'model', 'usage_records': [{'private': 'trace'}],
            'usage': {'input_tokens': 120, 'output_tokens': 7, 'cache_hit_tokens': 100,
                      'cache_miss_tokens': 20, 'total_tokens': 127, 'complete': True},
            'by_model': [{'model':'model','usage_records':[{'private':'trace'}]}],
        }
        return result

    monkeypatch.setattr(module, 'convert', measured)
    r = client.post('/api/convert', files={'docx': ('test.docx', SAMPLE_DOCX.read_bytes())},
                    data={'provider':'deepseek'})
    assert r.status_code == 200
    tid = r.json()['task_id']
    _wait_done(client, tid)
    result = client.get('/api/result/' + tid).json()
    assert seen == ['deepseek']
    assert 'usage_records' not in result['stats']['llm']
    assert 'usage_records' not in result['stats']['llm']['by_model'][0]
    package = client.get('/api/download/' + tid)
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        usage = json.loads(archive.read('转换用量.json'))['model_usage']['usage']
        assert usage['input_tokens'] == usage['cache_hit_tokens'] + usage['cache_miss_tokens']


def test_unrecognized_model_provider_is_rejected_before_conversion(client):
    response = client.post('/api/convert', files={'docx': ('test.docx', b'not-used')},
                           data={'provider':'unconfigured-provider'})
    assert response.status_code == 422


def test_public_legacy_counters_use_complete_usage_without_mutating_report():
    from webapp.app import public_stats
    original = {'llm': {'calls': 1, 'tokens': 10, 'prompt_tokens': 8, 'completion_tokens': 2,
                       'usage': {'requests': 2, 'input_tokens': 16, 'output_tokens': 6,
                                 'total_tokens': 22}, 'usage_records': [{'private': True}]}}
    public = public_stats(original)['llm']
    assert (public['calls'],public['completed_calls'],public['tokens']) == (2,1,22)
    assert (public['prompt_tokens'],public['completion_tokens']) == (16,6)
    assert original['llm']['tokens'] == 10
    assert 'usage_records' in original['llm'] and 'usage_records' not in public


def test_review_record_preserves_automatic_result_and_download(client, monkeypatch):
    import json
    import webapp.app as module

    def candidate(opts):
        result = _fake_convert(opts)
        result.delivered = False
        return result

    item = {"id": "item-1", "title": "原稿内容需要核对", "blocking": True,
            "source_text": "Source paragraph", "source_id": "n1"}
    monkeypatch.setattr(module, "convert", candidate)
    monkeypatch.setattr(module, "review_items", lambda *_: [item])
    reply = client.post("/api/convert", files={"docx": ("test.docx", SAMPLE_DOCX.read_bytes())})
    task_id = reply.json()["task_id"]
    _wait_done(client, task_id)
    before = client.get(f"/api/result/{task_id}").json()
    payload = {"reviewer": "编辑", "decisions": {"item-1": {"status": "confirmed", "note": "已核对原稿"}}}
    assert client.post(f"/api/review/{task_id}", json=payload).status_code == 200
    after = client.get(f"/api/result/{task_id}").json()
    assert after["delivered"] is False
    assert before["xml"] == after["xml"]
    assert after["review_decisions"] == payload["decisions"]
    bundle = zipfile.ZipFile(io.BytesIO(client.get(f"/api/download/{task_id}").content))
    record = json.loads(bundle.read("人工复核记录.json"))
    assert record["automatic_delivered"] is False
    assert record["decisions"] == payload["decisions"]
    xml_name = next(name for name in bundle.namelist() if name.endswith(".xml"))
    assert bundle.read(xml_name).decode() == before["xml"]
    payload["decisions"]["unknown"] = {"status": "confirmed"}
    assert client.post(f"/api/review/{task_id}", json=payload).status_code == 400
    payload["decisions"] = {"item-1": {"status": "approved"}}
    assert client.post(f"/api/review/{task_id}", json=payload).status_code == 400


def test_fresh_conversion_has_task_local_cache(client, monkeypatch):
    import webapp.app as module
    captured = []

    def capture(opts):
        captured.append(opts.llm_cache_dir)
        return _fake_convert(opts)

    monkeypatch.setattr(module, "convert", capture)
    monkeypatch.setenv("W2J_WEBAPP_CACHE", "/not-used-shared-cache")
    reply = client.post("/api/convert", data={"fresh": "true"},
                        files={"docx": ("test.docx", SAMPLE_DOCX.read_bytes())})
    task_id = reply.json()["task_id"]
    _wait_done(client, task_id)
    assert Path(captured[0]) == Path(module.TASKS[task_id]["workdir"]) / "llm-cache"


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


def test_wmf_optional_decoder_preserves_original(tmp_path):
    import ctypes.util
    from webapp.images import wmf_preview_png
    if not ctypes.util.find_library("gdk_pixbuf-2.0"):
        pytest.skip("未安装可选 WMF 预览解码器")
    with zipfile.ZipFile(SAMPLE_DOCX) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".wmf"))
        original = archive.read(name)
    file = tmp_path / "formula.wmf"
    file.write_bytes(original)
    png = wmf_preview_png(file)
    if png is None:
        pytest.skip("系统未配置 WMF loader")
    assert png.startswith(b"\x89PNG")
    assert file.read_bytes() == original


def test_wmf_decoder_unavailable_is_optional(monkeypatch):
    import ctypes.util
    from webapp.images import wmf_preview_png
    monkeypatch.setattr(ctypes.util, "find_library", lambda _: None)
    assert wmf_preview_png("not-needed.wmf") is None


def test_strip_stylesheet_warning():
    """渲染层剥掉 NCBI 样式表诊断 span，但保留脚注正文。"""
    from webapp.render import _strip_stylesheet_warnings
    html = ('<p><span class="warning">{ label (or @symbol) needed for '
            "fn[@id='fn1'] }</span> <sup>&#8224;</sup>contributed equally</p>")
    out = _strip_stylesheet_warnings(html)
    assert "needed for" not in out
    assert 'class="warning"' not in out
    assert "contributed equally" in out


def test_figure_tiff_served_as_png(tmp_path):
    """TIFF 出版图浏览器不认：/api/figure 按需转 PNG（下载 zip 原始字节不变）。"""
    import io
    from PIL import Image
    from fastapi.testclient import TestClient
    import webapp.app as A

    with TestClient(A.app) as c:
        run = tmp_path / "tifftask"
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


def test_figure_transparent_tiff_composited_on_white(tmp_path):
    """透明背景 TIFF 须合成到白底（否则 convert RGB 填黑 → 黑轴黑字在黑底隐没）。"""
    import io
    from PIL import Image
    from fastapi.testclient import TestClient
    import webapp.app as A

    with TestClient(A.app) as c:
        run = tmp_path / "rgbatask"
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
    import webapp.app as module
    assembled = (module.RUNS_DIR / tid / "input.docx").read_bytes()
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


def test_review_nested_table_shows_source_excerpt(tmp_path, monkeypatch):
    """排版表节点自身无文本时，复核卡片展示其内部的真实表头。"""
    import json
    from types import SimpleNamespace
    from webapp import review
    source = SimpleNamespace(nodes=[
        SimpleNamespace(node_id="doc/t1", text=""),
        SimpleNamespace(node_id="doc/t1/r1/c1/p1", text="Question Type"),
    ], _occurrences={})
    monkeypatch.setattr(review, "read_source_docx", lambda _: source)
    report = {"understanding": {"issues": [
        {"code": "SINGLE_CELL_WRAPPER_UNWRAPPED", "source_id": "doc/t1", "severity": "warning"},
        {"code": "DECLARATION_TITLE_SPAN_STRIPPED", "source_id": "doc/p2", "severity": "warning"},
    ]}}
    (tmp_path / "report.json").write_text(json.dumps(report))
    result = SimpleNamespace(candidate_dir=tmp_path / "candidate")
    items = review.review_items("unused.docx", result)
    assert len(items) == 1
    assert items[0]["source_text"] == "Question Type"
    assert items[0]["blocking"] is False


def test_review_groups_character_issues_by_source_paragraph(tmp_path, monkeypatch):
    import json
    from webapp import review
    source = SimpleNamespace(nodes=[SimpleNamespace(node_id="doc/p1", text="Original caption.")], _occurrences={})
    monkeypatch.setattr(review, "read_source_docx", lambda _: source)
    issues = [{"code": "REUSE_WITHOUT_BASIS", "source_id": "doc/p1", "start": i,
               "end": i + 1, "severity": "warning" if i == 0 else "high"} for i in range(5)]
    (tmp_path / "report.json").write_text(json.dumps({"source_coverage": {"issues": issues}}))
    items = review.review_items("unused.docx", SimpleNamespace(candidate_dir=tmp_path / "candidate"))
    assert len(items) == 1
    assert items[0]["issue_count"] == 5
    assert items[0]["blocking"] is True
    assert items[0]["source_text"] == "Original caption."


def test_emf_preview_links_original_instead_of_broken_image():
    from lxml import html
    from webapp.render import render_html
    original = b'<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><p><inline-graphic xlink:href="fig.EMF"/></p></body></article>'
    preview = html.fromstring(render_html(original, "example"))
    assert not preview.xpath("//img")
    link = preview.xpath("//a[@class='w2j-media-fallback']")[0]
    assert link.get("href") == "/api/figure/example/fig.EMF"
    assert "下载原文件" in link.text_content()
    assert b"inline-graphic" in original
