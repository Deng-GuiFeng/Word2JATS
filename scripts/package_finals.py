#!/usr/bin/env python3
"""决赛提交包：严格按候选清单取文件，验证后发布；旧包保留到 archive。"""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from scripts.eval_v1.samples import SAMPLES
from scripts.output_manifest import resolve_output
from webapp.render import render_html

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["src", "docs", "tests", "scripts", "webapp", "README.md",
           "requirements.txt", "requirements-dev.txt", "pyproject.toml",
           ".env.example", "Dockerfile", ".dockerignore", "LICENSE"]
EXCLUDE = {"__pycache__", ".pytest_cache", ".git", ".env", ".venv",
           "_runs", "_uploads", "node_modules", "11-重构工程", "引用示例候选.md",
           "_弃用_旧伪标签工具", "output", "dist"}

def ignore(directory, names):
    return [name for name in names if name in EXCLUDE or
            name.startswith(("_cache", "_llm_cache", ".llm_cache", ".crossref_cache")) or
            name.endswith((".pyc", ".log"))]

def safe_texts(package):
    """仅报告文件名，不打印可能敏感的值。"""
    forbidden = []
    for file in package.rglob("*"):
        if not file.is_file():
            continue
        if file.name == ".env":
            forbidden.append(str(file.relative_to(package)))
        if file.suffix in {".py", ".json", ".md", ".yaml", ".yml", ".txt", ".js", ".html"} or file.name == ".env.example":
            import re
            if re.search(rb"\bsk-[A-Za-z0-9_-]{20,}", file.read_bytes()):
                forbidden.append(str(file.relative_to(package)))
    if forbidden:
        raise ValueError("发现疑似凭据文件：" + ", ".join(forbidden))

def build(tag):
    out_root = ROOT / "reports/outputs" / tag
    timing = json.loads((out_root / "timing.json").read_text())
    if timing["failures"] or len(timing["samples"]) != 14:
        raise ValueError("验证批次未完整结束")
    evidence = json.loads((ROOT / "reports/finals-closeout" / (tag + ".json")).read_text())
    if len(evidence["samples"]) != 14:
        raise ValueError("独立内容核对不完整")
    for row in evidence["samples"]:
        if (not row["dtd_valid"] or row["references"] != row["expected_references"]
                or row["missing_formula_texts"] or row["missing_orcids"]
                or not row["media_source_bytes"] or row["broken_rids"]):
            raise ValueError("独立内容核对尚有未解决项：" + row["sample"])
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="finals-build-", dir=dist) as tmp:
        package = Path(tmp) / "JiangLab"
        package.mkdir()
        for name in INCLUDE:
            source, target = ROOT / name, package / name
            if source.is_dir():
                shutil.copytree(source, target, ignore=ignore)
            elif source.is_file():
                shutil.copy2(source, target)
            else:
                raise FileNotFoundError(name)
        for ext in ("md", "docx", "pdf"):
            shutil.copy2(ROOT / "决赛提交" / ("技术方案说明书." + ext), package / ("技术方案说明书." + ext))
        data = package / "样例数据"
        data.mkdir()
        for name in ("样例登记.json", "说明.md"):
            shutil.copy2(ROOT / "样例数据" / name, data / name)
        for sample in SAMPLES:
            dest = data / sample.key
            dest.mkdir()
            for name in ("初始文件.docx", "结构参考.xml", "figures.zip"):
                shutil.copy2(Path(sample.dir) / name, dest / name)
            published = Path(sample.dir) / "上线版本.xml"
            if published.is_file():
                shutil.copy2(published, dest / published.name)
        summaries = {}
        for sample in SAMPLES:
            location = resolve_output(out_root, sample.key)
            if not location.candidate_xml or not location.candidate_xml.is_file():
                raise ValueError("缺候选：" + sample.key)
            dest = package / "输出样例" / sample.key
            shutil.copytree(location.candidate_dir, dest)
            row = next(r for r in evidence["samples"] if r["sample"] == sample.key)
            if hashlib.sha256(location.candidate_xml.read_bytes()).hexdigest() != row["xml_sha256"]:
                raise ValueError("核对后文件发生变化：" + sample.key)
            report = json.loads((location.candidate_dir.parent / "report.json").read_text())
            # 对外摘要只保留本次结论、可定位问题和统计，不附模型输入、机器绝对路径等调试记录。
            summary = {"sample": sample.key, "automatic_checks_passed": location.delivered,
                       "gates": report["gates"], "runtime": report.get("runtime", {}),
                       "issues": report["understanding"]["issues"],
                       "source_coverage_issues": report["source_coverage"]["issues"],
                       "output_provenance_issues": report["output_provenance"]["issues"],
                       "independent_check": row}
            (dest / "检查摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(dest / "figures.zip", "w", zipfile.ZIP_DEFLATED) as figures:
                for media in sorted(location.candidate_dir.rglob("*")):
                    if media.is_file() and media != location.candidate_xml:
                        figures.write(media, media.relative_to(location.candidate_dir))
            preview = render_html(location.candidate_xml.read_bytes(), "frozen")
            preview = preview.replace("/api/figure/frozen/", "").replace(
                "/assets/jats-preview.css", "../../webapp/vendor/jats/jats-preview.css")
            # TIFF 的离线预览使用独立派生 PNG；原始媒体及 figures.zip 保持不变。
            for media in sorted(location.candidate_dir.rglob("*")):
                if not media.is_file():
                    continue
                if media.suffix.lower() == ".wmf":
                    from webapp.images import wmf_preview_png
                    png_bytes = wmf_preview_png(media)
                    if png_bytes:
                        png = dest / "预览资源" / (media.name + ".png")
                        png.parent.mkdir(exist_ok=True)
                        png.write_bytes(png_bytes)
                        preview = preview.replace(media.relative_to(location.candidate_dir).as_posix(),
                                                  "预览资源/" + png.name)
                if media.read_bytes()[:4] in (b"II*\x00", b"MM\x00*"):
                    from PIL import Image
                    png = dest / "预览资源" / (media.name + ".png")
                    png.parent.mkdir(exist_ok=True)
                    with Image.open(media) as im:
                        rgba = im.convert("RGBA")
                        bg = Image.new("RGBA", rgba.size, "white")
                        bg.alpha_composite(rgba)
                        bg.convert("RGB").save(png)
                    preview = preview.replace(media.relative_to(location.candidate_dir).as_posix(),
                                              "预览资源/" + png.name)
            note = "<div style='padding:14px;background:#edf4f2;color:#164d50'>"
            note += "冻结转换结果 · " + html.escape(sample.key) + " · "
            note += "自动检查通过，仍建议复核校样" if location.delivered else "部分内容需要核对，详见检查摘要"
            note += "。本页面为已有结果预览，不是一次新的模型转换。</div>"
            preview = preview.replace("<body>", "<body>" + note, 1)
            (dest / "预览.html").write_text(preview, encoding="utf-8")
            summaries[sample.key] = {"xml": "输出样例/" + sample.key + "/" + location.candidate_xml.name,
                                     "xml_sha256": row["xml_sha256"], "automatic_checks_passed": location.delivered}
        shutil.copy2(ROOT / "docs/06-评测与成绩.md", package / "验证结果.md")
        safe_texts(package)
        # 固定样例数、源 XML 哈希和包内每个文件的哈希，便于验收时确认拿到的是同一批成果。
        files = {f.relative_to(package).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                 for f in sorted(package.rglob("*")) if f.is_file()}
        manifest = {"team": "JiangLab", "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "conversion_revision": timing["code_revision"], "output_tag": tag,
            "samples": summaries, "sha256": files}
        (package / "文件清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        archive_path = Path(tmp) / "JiangLab.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for f in sorted(package.rglob("*")):
                if f.is_file():
                    archive.write(f, Path("JiangLab") / f.relative_to(package))
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("压缩包完整性检查失败")
        old = [p for p in (dist / "JiangLab", dist / "JiangLab.zip", dist / "JiangLab.zip.sha256") if p.exists()]
        if old:
            backup = dist / "archive" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup.mkdir(parents=True)
            for path in old:
                shutil.move(str(path), backup / path.name)
            print("旧包保留在", backup.relative_to(ROOT))
        shutil.move(str(package), dist / "JiangLab")
        shutil.move(str(archive_path), dist / "JiangLab.zip")
        digest = hashlib.sha256((dist / "JiangLab.zip").read_bytes()).hexdigest()
        (dist / "JiangLab.zip.sha256").write_text(digest + "  JiangLab.zip\n")
    print("完成：dist/JiangLab.zip", round((dist / "JiangLab.zip").stat().st_size / 1024**2, 1), "MiB")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="codex-final-cold-r2")
    args = parser.parse_args()
    if Path(args.tag).name != args.tag or args.tag in {".", ".."}:
        parser.error("tag 必须是单个目录名")
    build(args.tag)

if __name__ == "__main__":
    main()
