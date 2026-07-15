"""word2jats Web 应用（FastAPI 单服务）。

上传 docx（+可选 DOI / 期刊）→ 立即拿 task_id → 轮询状态 → 下载 zip。图片一律从 docx 内嵌媒体提取。
转换是阻塞 10–60s 的云端大模型调用，甩到线程池，不卡住服务；任务状态先用进程内字典
（单机够用，升级路径见设计文档）。服务端持 API key，磁盘缓存让同文件重传秒回免费。
"""

from __future__ import annotations

import os
import sys
import time
import uuid
import shutil
import hashlib
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

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, FileResponse, Response  # noqa: E402
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
UPLOADS_DIR = WEBAPP_DIR / "_uploads"    # 分片上传暂存：每次上传一个子目录（<index>.part）
_DEFAULT_CACHE = WEBAPP_DIR / "_cache"   # LLM 磁盘缓存：同文件重传命中、秒回免费
RUNS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ---- 分片上传约束 ----
# 依据：本服务经 cloudflared 隧道对外，隧道主机名的 CNAME 必须橙云代理、无法灰云(DNS-only)
# 绕过 Cloudflare（否则隧道失效），而 Cloudflare 免费版代理读超时约 100s、单请求体上限约 100MB。
# 结论：整文件单请求上传在弱/高时延链路上必超时(524)或中途断连；唯一稳妥做法是有界分片——
# 每片远小于 100s 传完、断了只重传一片。片大小取 256KB 整数倍(GCS 惯例)，4MiB 兼顾高 RTT
# 请求开销与丢连接重传成本。分片亦顺带绕开 100MB 单请求上限。
CHUNK_SIZE = 4 * 1024 * 1024             # 建议分片大小（前端据此切片；服务端按声明的分片数收）
MAX_CHUNK_BYTES = 16 * 1024 * 1024       # 单片硬上限（防滥用）
MAX_UPLOAD_BYTES = 300 * 1024 * 1024     # 单文件硬上限
MAX_TOTAL_CHUNKS = 4096                  # 分片数上限
UPLOAD_TTL = 6 * 3600                    # 分片会话过期秒数（超时未完成则清理）


def _cache_dir() -> str:
    """LLM 缓存目录。可用 W2J_WEBAPP_CACHE 覆盖（测试指向预热缓存，免费秒回）。"""
    return os.environ.get("W2J_WEBAPP_CACHE") or str(_DEFAULT_CACHE)


# ---- 进程内任务表 ----
EXECUTOR = ThreadPoolExecutor(max_workers=4)
_LOCK = threading.Lock()
TASKS: dict = {}

# ---- 分片上传会话表 ----
_UPLOADS_LOCK = threading.Lock()
UPLOADS: dict = {}       # upload_id -> {filename, size, total_chunks, received:set, dir, created_at}


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
    _set(task_id, status="running", stage="转换中", stage_key="parse",
         started_at=time.time())
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

        # 没配 Key / 模型不可达时，convert() 仍出 DTD 合法骨架，但正文为空——明确告知
        notice = None
        if (res.stats.get("llm", {}) or {}).get("provider") == "off":
            notice = "未配置模型 API Key 或模型不可达，本次只产出了空的 JATS 骨架；配好 .env 里的 DASHSCOPE_API_KEY 再试。"

        _set(task_id, status="done", stage="完成", stage_key="done",
             finished_at=time.time(),
             result={
                 "xml_path": res.xml_path,
                 "article_id": res.article_id,
                 "out_dir": opts.out_dir,
                 "stats": res.stats,
                 "validation": validation,
                 "checks": checks,
                 "fidelity": fidelity,
                 "notice": notice,
             })
    except Exception as e:  # noqa: BLE001 —— 转换失败必须给人话、服务不崩
        _set(task_id, status="error", stage="失败", stage_key="error",
             finished_at=time.time(), error=_friendly_error(e),
             result={"traceback": traceback.format_exc()})


def _friendly_error(e: Exception) -> str:
    """把内部异常翻成人话，且不泄露服务器路径。"""
    name = type(e).__name__
    text = str(e)
    if name in ("PackageNotFoundError", "BadZipFile") or "not a zip" in text.lower():
        return "这个文件打不开，可能不是有效的 Word 文档（.docx）或已损坏。请另存为 .docx 后重试。"
    return "转换失败（%s）。请确认上传的是有效的 .docx；若问题持续，请查看服务端日志。" % name


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


def _submit_conversion(task_id: str, workdir: Path, docx_path: Path,
                       name: str, doi: str, journal: str) -> None:
    """登记任务并把转换甩进线程池。/api/convert 与分片 complete 两条上传路径共用。"""
    with _LOCK:
        TASKS[task_id] = {
            "task_id": task_id, "status": "pending", "stage": "排队中",
            "stage_key": "queued", "filename": name, "workdir": str(workdir),
            "created_at": time.time(), "started_at": None,
            "finished_at": None, "error": None, "result": None,
        }

    def _progress(key, label, tid=task_id):
        _set(tid, stage_key=key, stage=label)

    opts = ConvertOptions(
        docx_path=str(docx_path), out_dir=str(workdir / "output"),
        journal_id=(journal.strip() or None), doi=(doi.strip() or None),
        llm_cache_dir=_cache_dir(), progress=_progress,
    )
    EXECUTOR.submit(_run_conversion, task_id, opts)


@app.post("/api/convert")
async def api_convert(
    docx: UploadFile = File(...),
    doi: str = Form(""),
    journal: str = Form(""),
) -> dict:
    """单请求上传（小文件 / 命令行 / 内网直连够用）。大文件走 /api/upload/* 分片。"""
    name = docx.filename or "upload.docx"
    if not name.lower().endswith(".docx"):
        raise HTTPException(400, "请上传 .docx 文件")

    task_id = uuid.uuid4().hex[:16]
    workdir = RUNS_DIR / task_id
    workdir.mkdir(parents=True, exist_ok=True)
    docx_path = workdir / "input.docx"
    data = await docx.read()
    if not data:
        raise HTTPException(400, "上传的文件是空的")
    docx_path.write_bytes(data)

    _submit_conversion(task_id, workdir, docx_path, name, doi, journal)
    return {"task_id": task_id}


# ---- 分片 / 断点续传上传 ----
# 经 cloudflared 隧道时，整文件单请求在弱网/高时延下会撞 Cloudflare ~100s 超时(524)或中途
# 断连(502 / “Failed to fetch”)。这里把上传拆成有界分片：init 开会话 → 逐片 PUT(可乱序/并发/
# 重传) → status 查缺失(续传) → complete 校验并组装后交给同一套转换。语义借鉴 tus/GCS。

def _sweep_uploads() -> None:
    """清理过期未完成的分片会话，避免磁盘泄漏（best-effort）。"""
    now = time.time()
    with _UPLOADS_LOCK:
        stale = [uid for uid, u in UPLOADS.items()
                 if now - u["created_at"] > UPLOAD_TTL]
        dirs = [UPLOADS.pop(uid)["dir"] for uid in stale]
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


@app.post("/api/upload/init")
async def upload_init(filename: str = Form(...), size: int = Form(...),
                      total_chunks: int = Form(...)) -> dict:
    if not filename.lower().endswith(".docx"):
        raise HTTPException(400, "请上传 .docx 文件")
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件为空或过大（上限 %d MB）" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
    if total_chunks <= 0 or total_chunks > MAX_TOTAL_CHUNKS:
        raise HTTPException(400, "分片数非法")
    _sweep_uploads()
    upload_id = uuid.uuid4().hex[:16]
    d = UPLOADS_DIR / upload_id
    d.mkdir(parents=True, exist_ok=True)
    with _UPLOADS_LOCK:
        UPLOADS[upload_id] = {
            "filename": filename, "size": int(size),
            "total_chunks": int(total_chunks), "received": set(),
            "dir": d, "created_at": time.time(),
        }
    return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE,
            "total_chunks": int(total_chunks)}


@app.put("/api/upload/{upload_id}/{index}")
async def upload_chunk(upload_id: str, index: int, request: Request) -> dict:
    with _UPLOADS_LOCK:
        u = UPLOADS.get(upload_id)
        d = u["dir"] if u else None
        total = u["total_chunks"] if u else 0
    if d is None:
        raise HTTPException(404, "上传会话不存在或已过期，请重新开始上传")
    if index < 0 or index >= total:
        raise HTTPException(400, "分片序号越界")
    body = await request.body()
    if not body:
        raise HTTPException(400, "分片内容为空")
    if len(body) > MAX_CHUNK_BYTES:
        raise HTTPException(413, "单片过大")
    # 原子落盘：先写临时文件再改名，避免半截分片被 complete 当成完整片
    tmp = d / ("%d.part.tmp" % index)
    tmp.write_bytes(body)
    tmp.replace(d / ("%d.part" % index))
    with _UPLOADS_LOCK:
        u = UPLOADS.get(upload_id)
        if u is None:
            raise HTTPException(404, "上传会话不存在或已过期，请重新开始上传")
        u["received"].add(index)
        received = len(u["received"])
    return {"index": index, "received": received, "total": total}


@app.get("/api/upload/{upload_id}")
def upload_status(upload_id: str) -> dict:
    """查询已收 / 缺失分片，供续传（对应 tus 的 HEAD 查偏移）。"""
    with _UPLOADS_LOCK:
        u = UPLOADS.get(upload_id)
        if u is None:
            raise HTTPException(404, "上传会话不存在或已过期")
        total = u["total_chunks"]
        received = sorted(u["received"])
    missing = [i for i in range(total) if i not in received]
    return {"upload_id": upload_id, "total_chunks": total,
            "received": received, "missing": missing}


@app.post("/api/upload/{upload_id}/complete")
async def upload_complete(upload_id: str, doi: str = Form(""),
                          journal: str = Form(""), sha256: str = Form("")) -> dict:
    with _UPLOADS_LOCK:
        u = UPLOADS.get(upload_id)
        if u is None:
            raise HTTPException(404, "上传会话不存在或已过期，请重新开始上传")
        total = u["total_chunks"]
        size = u["size"]
        filename = u["filename"]
        src_dir = u["dir"]
        received = set(u["received"])
    # 依据 GCS「绝不假设收全」：缺片就明确回报，前端补传后再 complete
    missing = [i for i in range(total) if i not in received]
    if missing:
        raise HTTPException(409, {"error": "分片不完整", "missing": missing})

    task_id = uuid.uuid4().hex[:16]
    workdir = RUNS_DIR / task_id
    workdir.mkdir(parents=True, exist_ok=True)
    docx_path = workdir / "input.docx"
    h = hashlib.sha256()
    try:
        with open(docx_path, "wb") as out:
            for i in range(total):
                b = (src_dir / ("%d.part" % i)).read_bytes()
                h.update(b)
                out.write(b)
    except OSError:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(409, {"error": "分片丢失，请续传", "missing": [
            i for i in range(total) if not (src_dir / ("%d.part" % i)).is_file()]})

    actual = docx_path.stat().st_size
    if actual != size:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, "上传大小不一致（收到 %d 字节，声明 %d），请重试。" % (actual, size))
    if sha256 and sha256.lower() != h.hexdigest():
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, "文件校验和不匹配，传输可能损坏，请重试。")

    # 组装成功：清掉分片会话与暂存
    with _UPLOADS_LOCK:
        UPLOADS.pop(upload_id, None)
    shutil.rmtree(src_dir, ignore_errors=True)

    _submit_conversion(task_id, workdir, docx_path, filename, doi, journal)
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
        "stage_key": t.get("stage_key", ""),
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
        "notice": r.get("notice"),
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
    # 浏览器不认 TIFF（出版图常为 TIFF）：仅为预览按需转 PNG，不改动下载 zip 里的原始字节。
    with open(target, "rb") as f:
        magic = f.read(4)
    if magic in (b"II*\x00", b"MM\x00*"):
        try:
            import io
            from PIL import Image
            with Image.open(str(target)) as im:
                # 透明背景(RGBA/LA/带 transparency 的 P)须合成到**白底**，
                # 否则 convert("RGB") 把透明区填黑 → 黑轴黑字在黑底上全隐没。
                if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    bg.alpha_composite(rgba)
                    out_im = bg.convert("RGB")
                else:
                    out_im = im.convert("RGB")
                buf = io.BytesIO()
                out_im.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
        except Exception:
            pass  # 转换失败则原样返回，至少可下载
    return FileResponse(str(target))


# 静态资源（放最后，避免遮蔽上面的 API 路由）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
