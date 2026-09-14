"""内部验收：独立解包、清单核对、全新环境安装及运行入口检查。"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'reports/finals-closeout-20260914'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='word2jats-final-package-'))
    with zipfile.ZipFile(ROOT/'dist/JiangLab.zip') as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist())
        archive.extractall(directory)
    package = directory/'JiangLab'; prototype = package/'可运行原型'
    manifest = json.loads((package/'文件清单.json').read_text())
    actual = {p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
              for p in package.rglob('*') if p.is_file() and p.name != '文件清单.json'}
    assert manifest['sha256'] == actual
    assert set(p.name for p in package.iterdir()) == {'README.md','技术方案说明书.docx','技术方案说明书.pdf','可运行原型','文件清单.json'}
    assert not any(set(Path(n).parts) & {'样例数据','输出样例','reports','tests','scripts','docs','.env','.venv','_runs','_uploads','__pycache__'} for n in actual)
    for name in ('src/word2jats/understand/prompts','src/word2jats/resources/dtd','webapp/vendor'):
        assert (prototype/name).is_dir()
    for name in ('README.md','requirements.txt','pyproject.toml','.env.example','LICENSE'):
        assert (prototype/name).is_file()
    # 包内每个相对 Markdown 链接均可解析。
    import re
    for source in (package/'README.md',prototype/'README.md'):
        for href in re.findall(r'\[[^]]+\]\(([^)]+)\)',source.read_text()):
            if '://' not in href:
                assert (source.parent/href).exists(), (source,href)
    pdf = PdfReader(package/'技术方案说明书.pdf')
    assert len(pdf.pages) == 15
    page_ids = {page.indirect_reference.idnum for page in pdf.pages}
    links = []
    for page in pdf.pages:
        for item in page.get('/Annots',[]):
            annotation = item.get_object()
            if annotation.get('/Subtype') == '/Link':
                if '/Dest' in annotation:
                    assert annotation['/Dest'][0].idnum in page_ids
                links.append(annotation)
    assert len(pdf.named_destinations) == 43 and len(links) >= 70
    summary = {'package':str(package),'prototype':str(prototype),
               'source_revision':manifest['source_revision'],'files':len(actual),
               'pdf':{'pages':len(pdf.pages),'links':len(links),'destinations':len(pdf.named_destinations)},
               'archive_sha256':hashlib.sha256((ROOT/'dist/JiangLab.zip').read_bytes()).hexdigest()}
    (OUT/'独立包位置.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    env = os.environ.copy()
    for key in list(env):
        if key.endswith('_API_KEY') or key.startswith('W2J_') or key in {'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'}:
            env.pop(key)
    python = prototype/'.venv/bin/python'
    with (OUT/'独立环境安装.txt').open('w') as log:
        commands = [
            [sys.executable,'-m','venv',str(prototype/'.venv')],
            [str(python),'-m','pip','install','-r','requirements.txt'],
            [str(python),'-m','pip','install','.','--no-deps'],
            [str(python),'-m','pip','check'],
            [str(python),'-m','word2jats','convert','--help'],
            [str(python),'-m','webapp','--help'],
            [str(python),'-c', 'from word2jats.validate.validator import Validator; '
             'from webapp.render import render_html; Validator(); print("DTD 与预览资源加载成功")'],
        ]
        for command in commands:
            print('验证：' + ' '.join(command[:4]),flush=True)
            run = subprocess.run(command,cwd=prototype,env=env,stdout=log,stderr=subprocess.STDOUT)
            log.flush()
            assert run.returncode == 0, '独立运行失败，见独立环境安装.txt'
    summary['clean_install'] = True
    summary['python'] = str(python)
    summary['runtime_checks'] = ['全新虚拟环境','依赖安装','项目安装','pip check','CLI 参数','Web 启动参数','DTD 与预览资源']
    (OUT/'独立包安装验收.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print('独立环境安装与入口验证通过。',flush=True)


if __name__ == '__main__':
    main()
