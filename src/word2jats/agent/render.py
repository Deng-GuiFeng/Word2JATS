"""把输入 Word 渲染成页面图片——Agent 视觉闭环的"人眼真值"来源。

设计理由(对齐"以视觉核对做质量托底"的核心架构):
- 视觉闭环要拿"人看到的页面"当真值,与我们产出的 XML 对照。最忠实的"人看到的页面"
  就是把 docx 按原排版渲染出来的图。
- 用系统自带的 LibreOffice(soffice)把 docx→PDF(保留排版),再用 poppler 的 pdftoppm
  把 PDF→PNG。两者都是成熟的命令行工具,纯 CPU、无需 GPU、无需额外 Python 重依赖
  (实测环境已具备 /usr/bin/soffice 与 /usr/bin/pdftoppm)。
- 渲染结果按 docx 内容哈希缓存:同一文档不重复渲染,闭环多轮复用同一批页面图。

失败时抛异常由上层决定降级(无渲染→无视觉闭环,退回热启动草稿),不静默吞掉。
"""

from __future__ import annotations

import glob
import hashlib
import os
import shutil
import subprocess
import tempfile


def _sha8(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    return None


def docx_to_pdf(docx_path: str, out_dir: str, timeout: int = 180) -> str:
    """用 LibreOffice 无头模式把 docx 转 PDF,返回 PDF 路径。

    每次用独立的 UserInstallation profile,避免并发/多次调用时的 soffice 单实例锁冲突。
    """
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("未找到 soffice/libreoffice,无法渲染页面")
    os.makedirs(out_dir, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="w2j_soffice_")
    try:
        cmd = [
            soffice, "--headless", "--norestore", "--nolockcheck",
            "-env:UserInstallation=file://%s" % profile,
            "--convert-to", "pdf", "--outdir", out_dir, docx_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        base = os.path.splitext(os.path.basename(docx_path))[0]
        pdf = os.path.join(out_dir, base + ".pdf")
        if not os.path.exists(pdf):
            raise RuntimeError(
                "soffice 转 PDF 失败: %s\n%s"
                % (pdf, (proc.stderr or b"").decode("utf-8", "ignore")[:500]))
        return pdf
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def pdf_to_pngs(pdf_path: str, out_dir: str, dpi: int = 150,
                timeout: int = 180) -> list[str]:
    """用 pdftoppm 把 PDF 每页渲染成 PNG,按页码升序返回路径列表。"""
    if not shutil.which("pdftoppm"):
        raise RuntimeError("未找到 pdftoppm(poppler-utils),无法渲染页面")
    os.makedirs(out_dir, exist_ok=True)
    prefix = os.path.join(out_dir, "page")
    cmd = ["pdftoppm", "-png", "-r", str(dpi), pdf_path, prefix]
    subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    pages = sorted(
        glob.glob(prefix + "-*.png"),
        key=lambda p: int(p.rsplit("-", 1)[1].split(".")[0]))
    if not pages:
        raise RuntimeError("pdftoppm 未产出任何页面: %s" % pdf_path)
    return pages


def render_pages(docx_path: str, work_dir: str, dpi: int = 150) -> list[str]:
    """docx → 页面 PNG 列表(带内容哈希缓存)。

    :param work_dir: 渲染产物根目录(通常取转换 out_dir 下的 .agent/render)。
    :returns: 按页序排列的 PNG 绝对路径列表。
    """
    tag = "%s_%d" % (_sha8(docx_path), dpi)
    cache = os.path.join(work_dir, "pages_%s" % tag)
    done = os.path.join(cache, ".done")
    if os.path.exists(done):
        pages = sorted(
            glob.glob(os.path.join(cache, "page-*.png")),
            key=lambda p: int(p.rsplit("-", 1)[1].split(".")[0]))
        if pages:
            return pages
    os.makedirs(cache, exist_ok=True)
    pdf = docx_to_pdf(docx_path, cache)
    pages = pdf_to_pngs(pdf, cache, dpi=dpi)
    with open(done, "w") as f:
        f.write("%d\n" % len(pages))
    return pages
