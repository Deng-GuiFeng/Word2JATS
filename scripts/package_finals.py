"""打包技术方案与可运行原型；不携带实验数据或开发过程文件。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ('src', 'webapp', 'requirements.txt', 'pyproject.toml',
           '.env.example', 'Dockerfile', '.dockerignore', 'LICENSE')
EXCLUDE = {'__pycache__', '.pytest_cache', '.git', '.env', '.venv', '_runs',
           '_uploads', 'node_modules', '引用示例候选.md'}


def ignore(directory, names):
    return [name for name in names if name in EXCLUDE or
            name.startswith(('_cache', '_llm_cache', '.llm_cache', '.crossref_cache')) or
            name.endswith(('.pyc', '.log', '.tmp'))]


def safe_texts(package):
    forbidden = []
    for file in package.rglob('*'):
        if not file.is_file():
            continue
        if file.name == '.env':
            forbidden.append(str(file.relative_to(package)))
        if file.suffix in {'.py', '.json', '.md', '.yaml', '.yml', '.txt', '.js', '.html'} or file.name == '.env.example':
            if re.search(rb'\bsk-[A-Za-z0-9_-]{20,}', file.read_bytes()):
                forbidden.append(str(file.relative_to(package)))
    if forbidden:
        raise ValueError('发现疑似凭据文件：' + ', '.join(forbidden))


def build():
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix='finals-build-', dir=dist) as tmp:
        package = Path(tmp) / 'JiangLab'
        prototype = package / '可运行原型'
        prototype.mkdir(parents=True)
        for name in RUNTIME:
            source, target = ROOT / name, prototype / name
            if source.is_dir():
                shutil.copytree(source, target, ignore=ignore)
            elif source.is_file():
                shutil.copy2(source, target)
            else:
                raise FileNotFoundError(source)
        shutil.copy2(ROOT / 'docs/原型运行说明.md', prototype / 'README.md')
        for ext in ('docx', 'pdf'):
            shutil.copy2(ROOT / '决赛提交' / ('技术方案说明书.' + ext), package / ('技术方案说明书.' + ext))
        (package / 'README.md').write_text(
            '# JiangLab · Word2JATS\n\n'
            '学术期刊结构化技术创新大赛 · 选题一\n\n'
            '- [技术方案说明书（PDF）](技术方案说明书.pdf)\n'
            '- [技术方案说明书（Word）](技术方案说明书.docx)\n'
            '- [可运行原型与运行说明](可运行原型/README.md)\n\n'
            '在线原型：[word2jats.jianglab.work](https://word2jats.jianglab.work)。\n', encoding='utf-8')
        safe_texts(package)
        files = {file.relative_to(package).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                 for file in sorted(package.rglob('*')) if file.is_file()}
        assert not any(name.startswith(('样例数据/', '输出样例/', 'reports/', 'docs/', 'scripts/', 'tests/'))
                       for name in files)
        manifest = {'team':'JiangLab', 'project':'Word2JATS', 'source_revision':revision,
                    'runtime_directory':'可运行原型',
                    'verified_dependency_versions':{name:version(name) for name in
                        ('lxml','python-docx','openai','fastapi','uvicorn','Pillow',
                         'python-multipart','python-dotenv','PyYAML','pydantic','starlette')},
                    'sha256':files}
        (package / '文件清单.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        archive_path = Path(tmp) / 'JiangLab.zip'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(package.rglob('*')):
                if file.is_file():
                    archive.write(file, Path('JiangLab') / file.relative_to(package))
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            assert len(archive.namelist()) == len(set(archive.namelist()))
        old = [path for path in (dist/'JiangLab',dist/'JiangLab.zip',dist/'JiangLab.zip.sha256') if path.exists()]
        if old:
            backup = dist/'archive'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            backup.mkdir(parents=True)
            for path in old:
                shutil.move(str(path), backup/path.name)
            print('旧包保留在', backup.relative_to(ROOT))
        shutil.move(str(package), dist/'JiangLab')
        shutil.move(str(archive_path), dist/'JiangLab.zip')
        digest = hashlib.sha256((dist/'JiangLab.zip').read_bytes()).hexdigest()
        (dist/'JiangLab.zip.sha256').write_text(digest + '  JiangLab.zip\n', encoding='utf-8')
    print('完成：dist/JiangLab.zip', round((dist/'JiangLab.zip').stat().st_size / 1024**2,2), 'MiB')


if __name__ == '__main__':
    build()
