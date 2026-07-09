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
SAMPLE_FIGS = ROOT / "样例数据" / "01" / "figures.zip"
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
    if SAMPLE_FIGS.exists():
        files["figures"] = ("figures.zip", SAMPLE_FIGS.read_bytes(), "application/zip")
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

    # 配套 CSS + 结果图片可取
    assert client.get("/assets/jats-preview.css").status_code == 200
    fig = client.get("/api/figure/%s/JIN49347/fig-01.jpg" % task_id)
    assert fig.status_code == 200
    assert fig.headers["content-type"].startswith("image/")


def test_reject_non_docx(client):
    files = {"docx": ("bad.txt", b"not a docx", "text/plain")}
    r = client.post("/api/convert", files=files)
    assert r.status_code == 400


def test_status_404_for_unknown(client):
    r = client.get("/api/status/deadbeef")
    assert r.status_code == 404


def test_journals_list(client):
    r = client.get("/api/journals")
    assert r.status_code == 200
    js = r.json()["journals"]
    assert any(j["id"] == "JIN" for j in js)


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
