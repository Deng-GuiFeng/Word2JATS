"""结果浏览、原稿和修订接口。转换核心与自动实验产物保持不变。"""
from copy import deepcopy
from pathlib import Path
import io
import shutil
import threading
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from pydantic import BaseModel, ConfigDict
from lxml import etree

from word2jats.validate.validator import Validator
from word2jats.validate.checks import run_checks
from . import editor, presentation, source_view


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    fields: dict


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    publication_source: Literal["current", "original"] = "current"
    version: str | None = None


_EDIT_LOCK = threading.Lock()


def install_routes(app, get_task, set_task, runs_dir, submit):
    def task_for(task_id, done=True):
        task = get_task(task_id)
        if task is None:
            raise HTTPException(404, "未找到这次转换，请重新上传文件。")
        if done and task["status"] != "done":
            raise HTTPException(409, "转换尚未完成。")
        return task

    @app.get("/api/workbench/{task_id}")
    def workbench(task_id: str):
        task = task_for(task_id)
        result = task["result"]
        xml = Path(result["xml_path"]).read_bytes()
        try:
            fields = editor.extract(xml)
            report = presentation.read_report(result)
            _, blocks = presentation.prepare(xml, report)
            issues = presentation.issues(result, xml, report, blocks)
            doc = source_view.source(Path(task["workdir"]) / "input.docx")
            # 只给确实能找到的原稿节点建立跳转；对象 ID 先解析到所在段落。
            for row in [*blocks, *issues]:
                source_id = row.get("source_id", "")
                if source_id in doc._occurrences:
                    source_id = doc.occurrence(source_id).node_id
                row["source_anchor"] = source_view.anchor(source_id) if source_id in doc._nodes else ""
        except (ValueError, SyntaxError, etree.LxmlError) as error:
            raise HTTPException(422, "结果暂时无法打开编辑，您仍可下载文件。") from error
        return {"version": editor.fingerprint(xml), "fields": fields, "blocks": blocks, "structure": presentation.structure(xml),
                "issues": issues, "edited": result.get("edited", False),
                "saved_at": result.get("saved_at"), "options": task.get("options", {})}

    def save(task_id, request, restore=False):
        with _EDIT_LOCK:
            task = task_for(task_id)
            result = task["result"]
            current = Path(result["xml_path"]).read_bytes()
            if request.version != editor.fingerprint(current):
                raise HTTPException(409, "结果已在其他页面更新，请先重新载入。当前输入尚未丢弃。")
            if restore:
                original = result.get("original_result")
                if original:
                    set_task(task_id, result=deepcopy(original))
                return workbench(task_id)
            try:
                updated = editor.apply(current, request.fields)
            except editor.EditError as error:
                return JSONResponse(status_code=400, content={"detail":str(error), "field":error.field})
            except (TypeError, KeyError, AttributeError) as error:
                raise HTTPException(400, "填写内容的格式不正确，请检查后再保存。") from error
            if updated == current:
                return workbench(task_id)
            directory = Path(task["workdir"]) / "revisions"
            directory.mkdir(exist_ok=True)
            file = directory / (uuid.uuid4().hex + ".xml")
            temporary = file.with_suffix(".tmp")
            try:
                temporary.write_bytes(updated)
                temporary.replace(file)
                validation = Validator().validate_bytes(updated)
                next_result = {**result, "xml_path": str(file), "edited": True,
                    "saved_at": time.time(), "original_result": result.get("original_result") or deepcopy(result),
                    "validation": {"well_formed": validation.well_formed, "dtd_valid": validation.dtd_valid,
                                   "ok": validation.ok, "errors": validation.errors},
                    "checks": [{"code": c.code, "severity": c.severity, "detail": c.detail} for c in run_checks(updated)],
                    "edit_history": [*result.get("edit_history", []), {
                        "saved_at": time.time(), "before_sha256": editor.fingerprint(current),
                        "after_sha256": editor.fingerprint(updated), "fields": request.fields}]}
                set_task(task_id, result=next_result)
            except OSError as error:
                temporary.unlink(missing_ok=True)
                raise HTTPException(503, "暂时无法保存，请保留当前输入并重试。") from error
            return workbench(task_id)

    @app.post("/api/edit/{task_id}")
    def edit(task_id: str, request: EditRequest):
        return save(task_id, request)

    @app.post("/api/restore/{task_id}")
    def restore(task_id: str, request: VersionRequest):
        return save(task_id, request, restore=True)

    @app.get("/api/source/{task_id}", response_class=HTMLResponse)
    def original_view(task_id: str):
        task = task_for(task_id)
        return HTMLResponse(source_view.document(Path(task["workdir"]) / "input.docx", task_id))

    @app.get("/api/original/{task_id}")
    def original_download(task_id: str):
        task = task_for(task_id, done=False)
        return FileResponse(Path(task["workdir"]) / "input.docx", filename=task["filename"])

    @app.get("/api/source-media/{task_id}/{occ_id}")
    def original_media(task_id: str, occ_id: str):
        task = task_for(task_id)
        source = source_view.source(Path(task["workdir"]) / "input.docx")
        try:
            occ = source.occurrence(occ_id)
            resource = source.resource(occ.resource_id)
        except (KeyError, ValueError):
            raise HTTPException(404, "未找到原稿对象。")
        if not hasattr(resource, "blob"):
            raise HTTPException(404, "请下载 Word 查看这个对象。")
        if resource.fmt.lower() in {"tif", "tiff"}:
            from PIL import Image
            with Image.open(io.BytesIO(resource.blob)) as im:
                output = io.BytesIO()
                rgba = im.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                background.convert("RGB").save(output, format="PNG")
            return Response(output.getvalue(), media_type="image/png")
        mime = resource.content_type if resource.content_type.startswith("image/") else "application/octet-stream"
        headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}
        if resource.fmt.lower() in {"emf", "wmf", "svg"}:
            headers["Content-Disposition"] = f'attachment; filename="{occ_id}.{resource.fmt}"'
        return Response(resource.blob, media_type=mime, headers=headers)

    @app.post("/api/reconvert/{task_id}")
    def retry(task_id: str, request: RetryRequest):
        task = task_for(task_id, done=False)
        if request.provider not in {"deepseek", "dashscope"}:
            raise HTTPException(400, "请选择 DeepSeek 或 Qwen。")
        options = task.get("options", {})
        initial = options.get("initial") or {"doi":options.get("doi", ""), "journal":options.get("journal", "")}
        publication = options.get("publication") if request.publication_source == "current" else None
        if request.publication_source == "current" and task["status"] == "done":
            with _EDIT_LOCK:
                task = task_for(task_id)
                current = Path(task["result"]["xml_path"]).read_bytes()
                if request.version is not None and request.version != editor.fingerprint(current):
                    raise HTTPException(409, "结果已在其他页面更新，请重新载入后再转换。")
                publication = editor.extract(current)["publication"]
                if not publication["title"] or not (publication["issn_print"] or publication["issn_electronic"]):
                    # 尚未补齐的期刊不是已确认设置，不用空字段覆盖新的识别结果。
                    publication = {"doi": publication["doi"]}
        doi = publication["doi"] if publication is not None else initial["doi"]
        # 出版设置属于明确输入；题名、作者等内容仍重新识别，不复制到新稿。
        new_id = uuid.uuid4().hex[:16]
        folder = runs_dir() / new_id
        folder.mkdir(parents=True)
        shutil.copy2(Path(task["workdir"]) / "input.docx", folder / "input.docx")
        submit(new_id, folder, folder / "input.docx", task["filename"], doi, initial["journal"], True, request.provider, publication=publication, initial_options=initial)
        return {"task_id": new_id, "previous_task_id": task_id}
