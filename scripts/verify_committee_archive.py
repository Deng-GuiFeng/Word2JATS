"""比对组委会重新整理的样例包，并直接从包内读取 Word 完成公网缓存验证。"""
import argparse
import asyncio
from collections import Counter
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile

from playwright.async_api import async_playwright

from verify_sample_cache_public import BASE, ROOT, dump, verify


def sha(data):
    return hashlib.sha256(data).hexdigest()


def member_name(info):
    if not info.flag_bits & 0x800:
        try:
            return info.filename.encode('cp437').decode('gb18030')
        except UnicodeError:
            pass
    return info.filename


def zip_contents(data):
    """忽略 ZIP 容器时间戳与目录名，比对实际成员字节及重复数量。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return Counter(sha(archive.read(i)) for i in archive.infolist() if not i.is_dir())


def inspect_archive(archive_path, data_root, samples):
    known = {}
    for path in data_root.rglob('*'):
        if path.is_file():
            known.setdefault(sha(path.read_bytes()), []).append(str(path.relative_to(data_root)))
    by_sha = {sha((data_root/s['key']/'初始文件.docx').read_bytes()):s for s in samples}
    assert len(by_sha) == len(samples), '登记表含重复 Word'
    raw_archive = archive_path.read_bytes()
    files, uploads = [], {}
    with zipfile.ZipFile(io.BytesIO(raw_archive)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = member_name(info)
            parts = PurePosixPath(name).parts
            data = archive.read(info)  # 同时校验 ZIP 成员 CRC。
            digest = sha(data)
            row = {'member':name, 'size':len(data), 'sha256':digest,
                   'identical_local_files':known.get(digest, [])}
            files.append(row)
            if '选题一' in parts and name.lower().endswith('.docx'):
                assert digest in by_sha, '新包 Word 没有逐字节一致的已登记样例：'+name
                sample = by_sha[digest]
                assert sample['key'] not in uploads, '包内 Word 重复：'+name
                uploads[sample['key']] = {'name':parts[-1], 'member':name, 'buffer':data}
                row['sample'] = sample['key']
            if '选题一' in parts and parts[-1] in {'figure.zip','figures.zip'}:
                sample_key = f"{int(parts[-2].removeprefix('样例')):02}"
                local = data_root/sample_key/'figures.zip'
                row['media_member_bytes_equal'] = zip_contents(data) == zip_contents(local.read_bytes())
                row['compared_local_media'] = str(local.relative_to(data_root))
    expected = {s['key'] for s in samples if s['group'] in {'main','supp'}}
    assert set(uploads) == expected, (set(uploads), expected)
    report = {'archive':str(archive_path.resolve()), 'archive_sha256':sha(raw_archive),
              'word_samples':sorted(uploads), 'word_bytes_identical':True, 'files':files}
    return report, uploads


async def main(args):
    samples = json.loads((ROOT/'样例数据/样例登记.json').read_text())['样例']
    report, uploads = inspect_archive(args.archive, ROOT/'样例数据', samples)
    args.output.mkdir(parents=True, exist_ok=False)
    dump(args.output/'archive-comparison.json', report)
    print('Word bytes identical:', ','.join(report['word_samples']), flush=True)
    selected = [s for s in samples if s['key'] in uploads and
                (args.samples == 'all' or s['key'] in args.samples.split(','))]
    assert selected
    records = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=['--disable-dev-shm-usage'])
        try:
            for sample in selected:
                for provider in args.providers.split(','):
                    assert provider in {'deepseek','dashscope'}
                    records.append(await verify(browser, args, sample, provider,
                                                upload=uploads[sample['key']]))
                    dump(args.output/'summary.json', records)
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--url', default='https://word2jats.jianglab.work')
    parser.add_argument('--expected', type=Path, default=BASE/'selected-replay-v5')
    parser.add_argument('--samples', default='all')
    parser.add_argument('--providers', default='deepseek,dashscope')
    parser.add_argument('--default-publication', action='store_true')
    asyncio.run(main(parser.parse_args()))
