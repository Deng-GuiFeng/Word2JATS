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
