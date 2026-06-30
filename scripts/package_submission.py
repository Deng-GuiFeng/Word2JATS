#!/usr/bin/env python3
"""打包初赛提交物到 dist/。

产出一个干净、可独立运行的提交目录与 zip：包含源码、文档、运行说明、依赖清单，
以及一份"输入 docx → 生成 XML + 外部化图片"的演示。排除虚拟环境/密钥/缓存等。

用法： python scripts/package_submission.py
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

DIST = os.path.join(ROOT, "dist")
PKG = os.path.join(DIST, "word2jats-submission")

# 纳入提交的顶层条目
INCLUDE = ["src", "docs", "tests", "scripts", "README.md", "requirements.txt",
           "pyproject.toml", ".env.example"]
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".llm_cache", "output", "dist",
                 ".venv", ".git", ".env"}


def _ignore(_dir, names):
    return [n for n in names if n in EXCLUDE_NAMES or n.endswith(".pyc")]


def _demo():
    """生成一份演示输出（样例3：含图/公式/表/参考文献）。"""
    from word2jats.pipeline import ConvertOptions, convert
    demo_in = os.path.join(ROOT, "样例/样例3/第一组/初始文件.docx")
    demo_fig = os.path.join(ROOT, "样例/样例3/第一组/figures.zip")
    if not os.path.exists(demo_in):
        return
    demo_dir = os.path.join(PKG, "演示-样例3")
    os.makedirs(demo_dir, exist_ok=True)
    shutil.copy(demo_in, os.path.join(demo_dir, "输入.docx"))
    r = convert(ConvertOptions(docx_path=demo_in, out_dir=demo_dir,
                               journal_id="JIN", doi="10.31083/JIN49347",
                               figures_path=demo_fig))
    print("  演示输出:", os.path.relpath(r.xml_path, ROOT),
          "| DTD 校验:", r.validation.ok if r.validation else "?")


def main():
    if os.path.exists(PKG):
        shutil.rmtree(PKG)
    os.makedirs(PKG, exist_ok=True)
    for item in INCLUDE:
        src = os.path.join(ROOT, item)
        if not os.path.exists(src):
            continue
        dst = os.path.join(PKG, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=_ignore)
        else:
            shutil.copy(src, dst)
    print("已复制源码/文档/说明 →", os.path.relpath(PKG, ROOT))
    _demo()

    # 打 zip
    zip_path = os.path.join(DIST, "word2jats-submission.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(PKG):
            for f in files:
                fp = os.path.join(base, f)
                z.write(fp, os.path.relpath(fp, DIST))
    size = os.path.getsize(zip_path) / 1024 / 1024
    print("已打包: %s (%.1f MB)" % (os.path.relpath(zip_path, ROOT), size))


if __name__ == "__main__":
    main()
