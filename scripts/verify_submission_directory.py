"""最终只读目录核查；ZIP 可选校验。仅在提交目录之外写脱敏证据。"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re
import subprocess
import zipfile

from dotenv import dotenv_values
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'dist/JiangLab'
PROTO = PACKAGE / '可运行原型'
EVIDENCE = ROOT / 'reports/finals-directory-20260914'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(root):
    return {p.relative_to(root).as_posix(): digest(p.read_bytes())
            for p in sorted(root.rglob('*')) if p.is_file()}


def check():
    assert {p.name for p in PACKAGE.iterdir()} == {'技术方案说明书.pdf', '可运行原型'}
    assert not any(p.is_symlink() for p in PACKAGE.rglob('*'))
    files = fingerprint(PROTO)
    assert len(files) == 166
    for name in ('最终目录指纹.json', '本机原型审计.json', 'A800原型审计.json'):
        prior = json.loads((EVIDENCE / name).read_text())['files']
        assert files.keys() == prior.keys()
        assert {n for n in files if files[n] != prior[n]} == {'README.md', 'webapp/README.md'}
    for name in files:
        if name.startswith(('src/', 'webapp/')) or name in {'requirements.txt', 'Dockerfile', '.dockerignore', 'pyproject.toml', 'LICENSE', '.env.example'}:
            assert files[name] == digest((ROOT / name).read_bytes()), name
    assert files['README.md'] == digest((ROOT / 'docs/原型运行说明.md').read_bytes())
    forbidden = {'__pycache__', '.venv', '.git', 'tests', 'reports', 'docs', 'scripts', '样例数据', 'references', '_runs', '_cache', '_uploads', 'node_modules'}
    assert not any(set(Path(n).parts) & forbidden for n in files)
    for name in files:
        assert not name.endswith(('.pyc', '.log', '.bak', '.docx', '.zip', '.pptx'))
        assert not any(ord(c) < 32 for c in name)
        if name.endswith('.py'):
            ast.parse((PROTO / name).read_text(), filename=name)
    with zipfile.ZipFile(ROOT / '初赛提交材料.zip') as z:
        old_env = z.read('初赛提交材料/可运行原型/.env')
    env_raw = (PROTO / '.env').read_bytes()
    strip_deepseek = lambda b: re.sub(rb'(?m)^(DEEPSEEK_API_KEY=)[^\r\n]*', rb'\1', b)
    assert strip_deepseek(old_env) == strip_deepseek(env_raw)
    values = dotenv_values(PROTO / '.env')
    secrets = [values[k] for k in ('DASHSCOPE_API_KEY', 'DEEPSEEK_API_KEY')]
    assert all(v and v.startswith('sk-') for v in secrets)
    assert all(values[k] and values[k].startswith('https://') for k in ('DASHSCOPE_BASE_URL', 'DEEPSEEK_BASE_URL'))
    for p in PACKAGE.rglob('*'):
        if not p.is_file() or p == PROTO / '.env':
            continue
        raw = p.read_bytes()
        assert not any(key.encode() in raw for key in secrets), str(p.relative_to(PACKAGE))
    for p in (PROTO / 'README.md', PROTO / 'webapp/README.md'):
        for link in re.findall(r'\[[^]]+\]\(([^)]+)\)', p.read_text()):
            if '://' not in link:
                assert (p.parent / link).is_file(), link
    pdf_path = PACKAGE / '技术方案说明书.pdf'
    assert pdf_path.read_bytes() == (ROOT / '决赛提交/技术方案说明书.pdf').read_bytes()
    assert digest(pdf_path.read_bytes()) == '7474f3ad0b56899f22338549430f60eeee50634daf5eafffeb69a63dabdd684f'
    pdf = PdfReader(pdf_path)
    assert len(pdf.pages) == 15 and not pdf.is_encrypted
    page_ids = {p.indirect_reference.idnum for p in pdf.pages}
    links = 0
    for page in pdf.pages:
        assert 570 < float(page.mediabox.width) < 610
        assert 820 < float(page.mediabox.height) < 850
        for ref in page.get('/Annots', []):
            a = ref.get_object()
            if a.get('/Subtype') == '/Link':
                links += 1
                if '/Dest' in a:
                    assert a['/Dest'][0].idnum in page_ids
    assert links == 82 and len(pdf.named_destinations) == 43
    text = subprocess.check_output(['pdftotext', '-layout', str(pdf_path), '-'], text=True)
    assert not re.search(r'X0[1-4]|工作台|冷跑|初赛评委|测试套件|539 项|待补充|TODO', text)
    assert all(t in text for t in ('Word2JATS', '结构参考', '大语言模型', 'Qwen', 'DeepSeek', '11.8391', '8.4126'))
    # 数值必须来自同一冻结实验，逐表逐单元与 PDF 核对。
    from scripts.proposal_tables import sections, usage_cost
    from scripts.proposal_metrics import aggregate
    rows = []
    for provider in ('qwen', 'deepseek'):
        data = json.loads((ROOT / f'reports/proposal-experiments/finals-{provider}-20260914-r4.json').read_text())
        sample_rows = [r for r in data['samples'] if not r['sample'].startswith('X')]
        assert len(sample_rows) == 10
        assert aggregate(sample_rows) == data['submission']
        for row in sample_rows:
            assert row['usage']['complete'] and row['dtd_valid']
            assert row['usage']['input_tokens'] == row['usage']['cache_hit_tokens'] + row['usage']['cache_miss_tokens']
            assert row['usage']['total_tokens'] == row['usage']['input_tokens'] + row['usage']['output_tokens']
        rows.append(sample_rows)
    compact = re.sub(r'\s+', '', text)
    cells_checked = 0
    for line in sections(*rows).splitlines():
        if line.startswith('|'):
            cells = [s.strip() for s in line.strip('|').split('|')]
            if all(re.fullmatch(r'-+', s) for s in cells):
                continue
            assert re.sub(r'\s+', '', ''.join(cells)) in compact, cells[0]
            for cell in cells:
                assert re.sub(r'\s+', '', cell) in compact, cell
                cells_checked += 1
    prior_visual = json.loads((EVIDENCE / '视觉逐项复查.json').read_text())
    assert prior_visual['reviewed'] is True
    for group in prior_visual['groups']:
        assert all(Path(p).is_file() for p in group)
        assert len({digest(Path(p).read_bytes()) for p in group}) == 1
    for machine in ('本机', 'A800'):
        matrix = json.loads((EVIDENCE / f'{machine}全状态-最终/验收清单.json').read_text())
        assert len(matrix['functional_passed']) == 61 and len(matrix['screenshots']) == 58
        assert not matrix['browser_errors']
        for provider in ('Qwen', 'DeepSeek'):
            for mode in ('首次', '复用'):
                r = json.loads((EVIDENCE / f'{machine}-{provider}{mode}/验收.json').read_text())
                assert not r['errors'] and len(r['checks']) == 9
        assert 'passed' in (EVIDENCE / f'{machine}全量回归.txt').read_text()
    return {'prototype_files': len(files), 'submission_files': len(fingerprint(PACKAGE)),
            'latest_source_matches': True, 'changed_since_two_machine_acceptance': ['README.md', 'webapp/README.md'],
            'environment_origin_and_secret_boundary': True, 'pdf_pages': len(pdf.pages),
            'pdf_links': links, 'pdf_destinations': len(pdf.named_destinations),
            'experiment_table_cells_checked': cells_checked, 'prior_runtime_and_visual_evidence_matches': True,
            'costs': {name: str(usage_cost(aggregate(rs)['usage'], name)) for name, rs in zip(('Qwen', 'DeepSeek'), rows)},
            'sha256': fingerprint(PACKAGE)}


def check_archive(path, expected):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(set(z.namelist()))
        actual = {}
        for info in z.infolist():
            name = info.filename
            assert name.startswith('JiangLab/') and not Path(name).is_absolute() and '..' not in Path(name).parts
            assert '\\' not in name
            assert not info.flag_bits & 1
            if any(ord(c) > 127 for c in name):
                assert info.flag_bits & 0x800, name
            if not info.is_dir():
                actual[name.removeprefix('JiangLab/')] = digest(z.read(name))
        assert actual == expected
        assert '可运行原型/.env' in actual
    return {'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path.read_bytes()),
            'files': len(actual), 'crc_and_file_hashes_valid': True, 'utf8_names_valid': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = check()
    if args.archive:
        result['archive'] = check_archive(args.archive, result['sha256'])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != 'sha256'}, ensure_ascii=False, indent=2))
