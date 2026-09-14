"""单机任务快照：完成结果可在服务重启后找回，进行中任务明确报告中断。"""
import json
from pathlib import Path
import re
import time
import uuid


def persist(task):
    folder = Path(task["workdir"])
    temp = folder / ("task-" + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        temp.replace(folder / "task.json")
    finally:
        temp.unlink(missing_ok=True)


def restore(task_id, runs_dir):
    if not re.fullmatch(r"[0-9a-f]{16}", task_id):
        return None
    folder = Path(runs_dir) / task_id
    file = folder / "task.json"
    if not file.is_file():
        return None
    try:
        task = json.loads(file.read_text(encoding="utf-8"))
        if Path(task["workdir"]).resolve() != folder.resolve():
            return None
        if task["status"] not in {"done", "error"}:
            task.update(status="error", stage="处理中断", stage_key="error", finished_at=time.time(),
                        error="服务重启中断了这次转换，请重新提交。原文件仍然保留。")
        return task
    except (OSError, ValueError, KeyError, TypeError):
        return None
