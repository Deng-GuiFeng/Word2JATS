#!/usr/bin/env python3
"""打包决赛提交物到 dist/JiangLab（决赛通知要求文件按队伍名称命名）。

内容：技术方案说明书（md/docx/pdf）+ 可运行原型（源码/网页/测试/评测器/文档）
+ 输入样例（14 份 docx）+ 输出样例（终验轮 fin-submit 的 XML、外部化图片与
自检报告）+ 运行说明。排除虚拟环境、密钥、缓存等。

用法： python scripts/package_finals.py
"""

from __future__ import annotations

import glob
import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
PKG = os.path.join(DIST, "JiangLab")
OUTPUT_TAG = "fin-submit"

INCLUDE = ["src", "docs", "tests", "scripts", "webapp", "README.md",
           "requirements.txt", "pyproject.toml", ".env.example",
           "Dockerfile", "LICENSE"]
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".llm_cache", ".crossref_cache",
                 "output", "dist", ".venv", ".git", ".env",
                 "_弃用_旧伪标签工具", "_llm_cache",
                 "_runs", "_cache", "_uploads", "_cache_preheat",
                 "node_modules",
                 # 内部工程档案与工作底稿不随提交物外发
                 "11-重构工程", "引用示例候选.md"}


def _ignore(_dir, names):
    return [n for n in names if n in EXCLUDE_NAMES or n.endswith(".pyc")]


def _copy_tree_or_file(name: str) -> None:
    source = os.path.join(ROOT, name)
    target = os.path.join(PKG, name)
    if os.path.isdir(source):
        shutil.copytree(source, target, ignore=_ignore)
    elif os.path.exists(source):
        shutil.copy2(source, target)
    else:
        print("  缺失，跳过:", name)


def _samples() -> None:
    src_root = os.path.join(ROOT, "样例数据")
    in_dir = os.path.join(PKG, "输入样例")
    os.makedirs(in_dir, exist_ok=True)
    keys = sorted(
        d for d in os.listdir(src_root)
        if os.path.isdir(os.path.join(src_root, d))
    )
    for key in keys:
        docx = os.path.join(src_root, key, "初始文件.docx")
        if os.path.exists(docx):
            shutil.copy2(docx, os.path.join(in_dir, f"{key}.docx"))
    print("  输入样例:", len(os.listdir(in_dir)), "份")


def _outputs() -> None:
    src_root = os.path.join(ROOT, "reports", "outputs", OUTPUT_TAG)
    out_dir = os.path.join(PKG, "输出样例")
    count = 0
    for key in sorted(os.listdir(src_root)):
        sample_dir = os.path.join(src_root, key)
        if not os.path.isdir(sample_dir):
            continue
        target = os.path.join(out_dir, key)
        delivered = glob.glob(os.path.join(sample_dir, "*.xml"))
        if delivered:
            os.makedirs(target, exist_ok=True)
            xml = delivered[0]
            article = os.path.splitext(os.path.basename(xml))[0]
            shutil.copy2(xml, os.path.join(target, os.path.basename(xml)))
            media = os.path.join(sample_dir, article)
            if os.path.isdir(media):
                shutil.copytree(media, os.path.join(target, article))
            reports = glob.glob(os.path.join(
                sample_dir, "candidates", "*", "report.json"))
        else:
            xmls = glob.glob(os.path.join(
                sample_dir, "failed", "*", "candidate", "*.xml"))
            if not xmls:
                print("  无输出，跳过:", key)
                continue
            candidate = os.path.dirname(xmls[0])
            shutil.copytree(candidate, target)
            reports = [os.path.join(os.path.dirname(candidate), "report.json")]
        if reports and os.path.exists(reports[0]):
            shutil.copy2(reports[0], os.path.join(target, "自检报告.json"))
        count += 1
    print("  输出样例:", count, "份（终验轮", OUTPUT_TAG + "）")


def _statement() -> None:
    for ext in ("md", "docx", "pdf"):
        source = os.path.join(ROOT, "决赛提交", f"技术方案说明书.{ext}")
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(PKG, f"技术方案说明书.{ext}"))
        else:
            print("  说明书缺失:", ext)


def main() -> None:
    if os.path.exists(PKG):
        shutil.rmtree(PKG)
    os.makedirs(PKG, exist_ok=True)
    print("打包决赛提交物 →", os.path.relpath(PKG, ROOT))
    for name in INCLUDE:
        _copy_tree_or_file(name)
    _statement()
    _samples()
    _outputs()
    zip_path = os.path.join(DIST, "JiangLab.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for base, _dirs, files in os.walk(PKG):
            for name in files:
                path = os.path.join(base, name)
                bundle.write(path, os.path.relpath(path, DIST))
    size = os.path.getsize(zip_path) / 1024 / 1024
    print(f"完成: {os.path.relpath(zip_path, ROOT)} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
