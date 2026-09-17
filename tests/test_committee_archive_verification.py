"""新样例包验证器必须使用包内字节，并拒绝缺样、重复或已变化的 Word。"""
import importlib.util
import io
from pathlib import Path
import sys
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('committee_verifier', SCRIPTS/'verify_committee_archive.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def make_zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return output.getvalue()


@pytest.fixture
def archive_case(tmp_path):
    samples = [{'key':'01','group':'main'}, {'key':'S01','group':'supp'}]
    entries = [('新包/主样例/选题一/样例1/初始word.docx', b'word-main'),
               ('新包/补充/选题一/样例1.docx', b'word-supp')]
    data_root = tmp_path/'samples'
    for sample, (_, data) in zip(samples, entries):
        (data_root/sample['key']).mkdir(parents=True)
        (data_root/sample['key']/'初始文件.docx').write_bytes(data)
    return tmp_path/'new.zip', data_root, samples, entries


def test_archive_uses_original_filename_and_bytes(archive_case):
    path, root, samples, entries = archive_case
    path.write_bytes(make_zip(entries+[('新包/选题二/补充材料.docx', b'not-an-input')]))
    report, uploads = verifier.inspect_archive(path, root, samples)
    assert report['word_samples'] == ['01','S01']
    assert len(report['files']) == 3
    assert uploads['01'] == {'name':'初始word.docx','member':entries[0][0],'buffer':b'word-main'}
    assert uploads['S01']['name'] == '样例1.docx'


@pytest.mark.parametrize('kind', ['changed','missing','duplicate'])
def test_archive_rejects_changed_missing_duplicate_inputs(archive_case, kind):
    path, root, samples, entries = archive_case
    if kind == 'changed':
        entries[0] = (entries[0][0], b'changed-word')
    elif kind == 'missing':
        entries.pop()
    else:
        entries.append(('新包/选题一/重复.docx', b'word-main'))
    path.write_bytes(make_zip(entries))
    with pytest.raises(AssertionError):
        verifier.inspect_archive(path, root, samples)


def test_zip_members_compare_bytes_and_multiplicity():
    a = make_zip([('a.png', b'pixels')])
    b = make_zip([('renamed/b.png', b'pixels')])
    c = make_zip([('a.png', b'pixels'), ('b.png', b'pixels')])
    assert verifier.zip_contents(a) == verifier.zip_contents(b)
    assert verifier.zip_contents(a) != verifier.zip_contents(c)


def test_chinese_legacy_zip_filename():
    info = zipfile.ZipInfo('样例1.docx'.encode('gb18030').decode('cp437'))
    assert verifier.member_name(info) == '样例1.docx'
