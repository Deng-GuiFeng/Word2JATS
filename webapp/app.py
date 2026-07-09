"""word2jats Web 应用（FastAPI 单服务）。

上传 docx（+可选 图片 zip / DOI / 期刊）→ 立即拿 task_id → 轮询状态 → 下载 zip。
转换是阻塞 10–60s 的云端大模型调用，甩到线程池，不卡住服务；任务状态先用进程内字典
（单机够用，升级路径见设计文档）。服务端持 API key，磁盘缓存让同文件重传秒回免费。
"""

from __future__ import annotations

import os
import sys
import time
import uuid
import zipfile
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

# 免安装即可跑：把 src/ 挂到 sys.path
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from word2jats.pipeline import ConvertOptions, convert  # noqa: E402
from word2jats.enrich.journals import JournalRegistry  # noqa: E402
from word2jats.validate.checks import run_checks  # noqa: E402
from webapp.render import render_html  # noqa: E402
from webapp.fidelity import summary as fidelity_summary  # noqa: E402

# ---- 运行期目录 ----
WEBAPP_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEBAPP_DIR / "static"
RUNS_DIR = WEBAPP_DIR / "_runs"          # 每任务一个子目录（上传件 + 输出）
_DEFAULT_CACHE = WEBAPP_DIR / "_cache"   # LLM 磁盘缓存：同文件重传命中、秒回免费
RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _cache_dir() -> str:
    """LLM 缓存目录。可用 W2J_WEBAPP_CACHE 覆盖（测试指向预热缓存，免费秒回）。"""
    return os.environ.get("W2J_WEBAPP_CACHE") or str(_DEFAULT_CACHE)


# ---- 进程内任务表 ----
EXECUTOR = ThreadPoolExecutor(max_workers=4)
_LOCK = threading.Lock()
TASKS: dict = {}


def _set(task_id: str, **kw) -> None:
    with _LOCK:
        t = TASKS.get(task_id)
        if t is not None:
            t.update(kw)


def _get(task_id: str) -> Optional[dict]:
    with _LOCK:
        t = TASKS.get(task_id)
        return dict(t) if t is not None else None


def _run_conversion(task_id: str, opts: ConvertOptions) -> None:
    """后台线程：跑 convert()，把结果/错误写回任务表。绝不让异常逃逸搞崩线程池。"""
    _set(task_id, status="running", stage="转换中", started_at=time.time())
    try:
        res = convert(opts)
        v = res.validation
        validation = None
        if v is not None:
            validation = {
                "well_formed": bool(getattr(v, "well_formed", False)),
                "dtd_valid": bool(getattr(v, "dtd_valid", False)),
                "ok": bool(getattr(v, "ok", False)),
                "errors": list(getattr(v, "errors", []) or []),
            }

        xml_bytes = b""
        try:
            with open(res.xml_path, "rb") as f:
                xml_bytes = f.read()
        except OSError:
            pass

        # 结构检查明细（分级）：DTD 之外的质量问题
        checks = []
        try:
            for it in run_checks(xml_bytes):
                checks.append({"code": it.code, "severity": it.severity,
                               "detail": it.detail})
        except Exception:
            pass

        # 内容忠实自检（诚实口径，见 fidelity.py）
        fidelity = None
        try:
            fidelity = fidelity_summary(opts.docx_path, xml_bytes)
        except Exception:
            pass

        _set(task_id, status="done", stage="完成", finished_at=time.time(),
             result={
                 "xml_path": res.xml_path,
                 "article_id": res.article_id,
                 "out_dir": opts.out_dir,
                 "stats": res.stats,
                 "validation": validation,
                 "checks": checks,
                 "fidelity": fidelity,
             })
    except Exception as e:  # noqa: BLE001 —— 转换失败必须给人话、服务不崩
        _set(task_id, status="error", stage="失败", finished_at=time.time(),
             error="%s: %s" % (type(e).__name__, e),
             result={"traceback": traceback.format_exc()})


# ---- FastAPI ----
app = FastAPI(title="word2jats", description="Word → JATS 结构化转换")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/journals")
def journals() -> dict:
    """期刊下拉列表（id + 刊名），供前端选择；留空则由 DOI 推断。"""
    reg = JournalRegistry()
    data = reg.data.get("journals", {})
    items = [{"id": k, "title": v.get("journal-title", k)}
             for k, v in data.items()]
    items.sort(key=lambda x: x["id"])
    return {"journals": items}


@app.post("/api/convert")
async def api_convert(
    docx: UploadFile = File(...),
    figures: Optional[UploadFile] = File(None),
    doi: str = Form(""),
    journal: str = Form(""),
) -> dict:
    name = docx.filename or "upload.docx"
    if not name.lower().endswith(".docx"):
        raise HTTPException(400, "请上传 .docx 文件")

    task_id = uuid.uuid4().hex[:16]
    workdir = RUNS_DIR / task_id
    workdir.mkdir(parents=True, exist_ok=True)
    out_dir = workdir / "output"

    docx_path = workdir / "input.docx"
    data = await docx.read()
    if not data:
        raise HTTPException(400, "上传的文件是空的")
    docx_path.write_bytes(data)

    figures_path = None
    if figures is not None and figures.filename:
        fdata = await figures.read()
        if fdata:
            figures_path = workdir / "figures.zip"
            figures_path.write_bytes(fdata)

    # 注册任务（复用上面生成的 task_id，保持 workdir 与 id 一致）
    with _LOCK:
        TASKS[task_id] = {
            "task_id": task_id, "status": "pending", "stage": "排队中",
            "filename": name, "workdir": str(workdir),
            "created_at": time.time(), "started_at": None,
            "finished_at": None, "error": None, "result": None,
        }

    opts = ConvertOptions(
        docx_path=str(docx_path), out_dir=str(out_dir),
        journal_id=(journal.strip() or None), doi=(doi.strip() or None),
        figures_path=(str(figures_path) if figures_path else None),
        llm_cache_dir=_cache_dir(),
    )
    EXECUTOR.submit(_run_conversion, task_id, opts)
    return {"task_id": task_id}


@app.get("/api/status/{task_id}")
def api_status(task_id: str) -> dict:
    t = _get(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    start = t["started_at"] or t["created_at"]
    end = t["finished_at"] or time.time()
    return {
        "task_id": task_id,
        "status": t["status"],
        "stage": t["stage"],
        "filename": t["filename"],
        "elapsed": round(end - start, 1),
        "error": t["error"],
    }


@app.get("/api/result/{task_id}")
def api_result(task_id: str) -> dict:
    t = _get(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    if t["status"] == "error":
        raise HTTPException(500, t["error"] or "转换失败")
    if t["status"] != "done":
        raise HTTPException(409, "转换尚未完成")
    r = t["result"]
    xml_text = ""
    try:
        xml_text = Path(r["xml_path"]).read_text(encoding="utf-8")
    except Exception:
        pass
    return {
        "task_id": task_id,
        "filename": t["filename"],
        "article_id": r["article_id"],
        "stats": r["stats"],
        "validation": r["validation"],
        "checks": r.get("checks", []),
        "fidelity": r.get("fidelity"),
        "xml": xml_text,
    }


@app.get("/api/render/{task_id}", response_class=HTMLResponse)
def api_render(task_id: str) -> HTMLResponse:
    """服务端把 JATS 渲染成期刊样式 HTML（供结果页 iframe 加载）。渲染失败给人话，不崩。"""
    t = _get(task_id)
    if t is None or t["status"] != "done":
        raise HTTPException(404, "任务不存在或未完成")
    try:
        with open(t["result"]["xml_path"], "rb") as f:
            xml_bytes = f.read()
        html = render_html(xml_bytes, task_id)
        return HTMLResponse(html)
    except Exception as e:  # noqa: BLE001
        msg = ("<!DOCTYPE html><meta charset='utf-8'>"
               "<div style='font-family:sans-serif;padding:24px;color:#B42318'>"
               "渲染视图生成失败：%s<br>可切到“原始 XML”查看完整结果。</div>"
               % (type(e).__name__))
        return HTMLResponse(msg, status_code=200)


@app.get("/assets/jats-preview.css")
def jats_css():
    """NCBI 预览样式表配套 CSS（公有领域），供渲染视图引用。"""
    css = WEBAPP_DIR / "vendor" / "jats" / "jats-preview.css"
    return FileResponse(str(css), media_type="text/css")


@app.get("/api/download/{task_id}")
def api_download(task_id: str):
    t = _get(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    if t["status"] != "done":
        raise HTTPException(409, "转换尚未完成")
    r = t["result"]
    out_dir = Path(r["out_dir"])
    article_id = r["article_id"] or "article"
    zip_path = Path(t["workdir"]) / ("%s.zip" % article_id)
    # 把输出目录（XML + 外部化图片）打成 zip
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out_dir))
    return FileResponse(str(zip_path), media_type="application/zip",
                        filename="%s.zip" % article_id)


@app.get("/api/figure/{task_id}/{name:path}")
def api_figure(task_id: str, name: str):
    """结果图片（供渲染视图引用）。name 可含子目录，故用 path 匹配；防目录穿越。"""
    t = _get(task_id)
    if t is None or t["status"] != "done":
        raise HTTPException(404, "任务不存在或未完成")
    out_dir = Path(t["result"]["out_dir"]).resolve()
    target = (out_dir / name).resolve()
    if out_dir not in target.parents and target != out_dir:
        raise HTTPException(400, "非法路径")
    if not target.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(str(target))


# 静态资源（放最后，避免遮蔽上面的 API 路由）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
