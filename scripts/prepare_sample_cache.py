"""隔离准备与只读核查 14 例缓存；不修改生产代码、线上任务或原始回答。"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent.parent
RELEASE = Path(os.environ.get('W2J_REVIEW_CODE_ROOT', PROJECT / 'tmp/web-release-r02')).resolve()
sys.path.insert(0, str(RELEASE))
sys.path.insert(0, str(RELEASE/'src'))
BASE = ROOT/'reports/sample-cache-20260915'
SAMPLES = json.loads((ROOT/'样例数据/样例登记.json').read_text())['样例']


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(stats):
    return {k:stats.get(k) for k in ('authors','affiliations','abstract_sections','keywords',
        'body_sections','tables','figures_exported','formulas','references','refs_structured','xrefs')}


def prepare():
    from word2jats.llm.cache import cache_key
    target = BASE/'cache-v1'
    target.mkdir(parents=True, exist_ok=False)
    sources = [ROOT/'reports/web-public-20260915/round-01/cache', PROJECT/'webapp/_cache_targeted_20260915']
    manifest, conflicts = {}, []
    for source in sources:
        for file in sorted(source.glob('*.json')):
            raw = json.loads(file.read_text())
            assert cache_key(raw['payload']) == file.stem
            assert isinstance(raw['response'], str) and raw.get('usage') is not None, file
            dest = target/file.name
            if dest.exists() and digest(dest) != digest(file):
                conflicts.append({'key':file.stem,'old':manifest[file.stem], 'selected':str(file)})
            shutil.copy2(file, dest)
            manifest[file.stem] = {'source':str(file),'sha256':digest(file),
                                  'provider':raw['payload']['provider'],'model':raw['payload']['model']}
    source_hashes = {digest(ROOT/'样例数据'/s['key']/'初始文件.docx'):s['key'] for s in SAMPLES}
    candidates = []
    for file in sorted((PROJECT/'webapp/_runs').glob('*/task.json')):
        task = json.loads(file.read_text())
        source = file.parent/'input.docx'
        if not source.is_file() or digest(source) not in source_hashes or task['status'] != 'done':
            continue
        result = task['result']
        candidates.append({'sample':source_hashes[digest(source)],'task':task['task_id'],
            'options':task['options'],'xml':result['xml_path'],
            'validation':result['validation'],'counts':compact(result['stats']),
            'calls':result['stats'].get('llm',{}).get('calls'),
            'edited':bool(result.get('edit_history'))})
    dump(BASE/'cache-v1-manifest.json', {'sources':list(map(str,sources)), 'entries':manifest,'conflicts':conflicts})
    dump(BASE/'candidates.json', candidates)
    dump(BASE/'inputs.json', [{'sample':s['key'],'sha256':digest(ROOT/'样例数据'/s['key']/'初始文件.docx'),
                             'journal':s['journal'],'doi':s['doi']} for s in SAMPLES])
    print('Prepared', len(manifest), 'responses;',len(conflicts),'explicit replacements;',len(candidates),'candidate tasks',flush=True)


def replay(job):
    sample, provider, cache, out, network, web_defaults = job
    from word2jats.pipeline import ConvertOptions, convert
    from word2jats.llm.cache import DiskCache, cache_key
    from webapp.usage import public_usage
    from word2jats.parse.docx_reader import read_source_docx
    from word2jats.validate.checks import run_checks
    from lxml import etree
    folder = Path(out)/f"{sample['key']}-{provider}"
    folder.mkdir(parents=True, exist_ok=True)
    if (folder/'result.json').is_file():
        raise FileExistsError(f'{folder}: 请使用新的输出目录，避免把旧产物当作本次复现')
    used, missing = set(), set()
    original = DiskCache.get_entry
    def get_entry(self, payload):
        key = cache_key(payload)
        used.add(key)
        entry = original(self,payload)
        if entry is None:
            missing.add(key)
            if not network:
                raise RuntimeError('Missing recorded model response: '+key)
        return entry
    def no_network(*args, **kwargs):
        raise RuntimeError('Model network calls prohibited during cache verification')
    source = ROOT/'样例数据'/sample['key']/'初始文件.docx'
    journal,doi=sample['journal'],sample['doi']
    if web_defaults:
        shutil.copy2(source,folder/'input.docx')
        source=folder/'input.docx'
        journal=doi=None
    try:
        with patch.object(DiskCache,'get_entry',get_entry):
            if network:
                result = convert(ConvertOptions(docx_path=str(source),out_dir=str(folder/'output'),
                    journal_id=journal,doi=doi,llm=provider,llm_cache_dir=str(cache)))
            else:
                with patch.object(socket.socket,'connect',no_network), patch.object(socket,'create_connection',no_network):
                    result = convert(ConvertOptions(docx_path=str(source),out_dir=str(folder/'output'),
                        journal_id=journal,doi=doi,llm=provider,llm_cache_dir=str(cache)))
        dump(folder/'full-result.json',asdict(result))
        llm = result.stats['llm']
        if not network:
            assert not missing and llm['calls'] == 0 and llm['cache_misses'] == 0
        usage = public_usage(llm)
        assert usage['result_usage']['complete'] and usage['result_usage']['available']
        xml = Path(result.candidate_xml)
        tree = etree.parse(str(xml), etree.XMLParser(resolve_entities=False,no_network=True))
        ref = etree.parse(str(ROOT/'样例数据'/sample['key']/'结构参考.xml'),etree.XMLParser(resolve_entities=False,no_network=True))
        def text(node):
            return ' '.join(''.join(node.itertext()).split())
        def inventory(tree):
            return {'title':[text(n) for n in tree.xpath('/article/front/article-meta/title-group/article-title')],
                'authors':[text(n) for n in tree.xpath('/article/front/article-meta/contrib-group/contrib')],
                'affiliations':[text(n) for n in tree.xpath('/article/front/article-meta/aff')],
                'abstracts':[text(n) for n in tree.xpath('//abstract')],
                'keywords':[text(n) for n in tree.xpath('//kwd')],
                'headings':[text(n) for n in tree.xpath('//sec/title')],
                'figures':[text(n) for n in tree.xpath('//fig/label|//fig/caption')],
                'tables':[{'label':text(n.find('label')) if n.find('label') is not None else '',
                           'rows':len(n.xpath('.//tr')),'text':text(n)} for n in tree.xpath('//table-wrap')],
                'references':[text(n) for n in tree.xpath('//ref')],
                'formulas':[etree.tostring(n,encoding='unicode') for n in tree.xpath('//disp-formula')],
                'counts':dict(Counter(etree.QName(n).localname for n in tree.iter() if isinstance(n.tag,str)))}
        source_doc = read_source_docx(str(source))
        (folder/'source.txt').write_text('\n\n'.join(f"[{n.node_id}] {n.text}" for n in source_doc.nodes if n.text))
        (folder/'output.txt').write_text('\n\n'.join(f"[{tree.getpath(n)}] {text(n)}" for n in tree.xpath('//article-title|//contrib|//aff|//title|//p|//table-wrap|//ref') if not n.xpath('ancestor::p|ancestor::ref|ancestor::table-wrap')))
        dump(folder/'content.json',{'output':inventory(tree),'reference':inventory(ref)})
        stats = result.stats
        row = {'sample':sample['key'],'provider':provider,'xml':str(xml),'version':digest(xml),
            'candidate_dir':result.candidate_dir,'cache':str(cache),'network_allowed':network,
            'web_defaults':web_defaults,
            'used_keys':sorted(used),'initial_missing':sorted(missing),'calls':llm['calls'],
            'cache_hits':llm['cache_hits'],'usage':usage,'validation':asdict(result.validation),
            'counts':compact(stats),'gates':stats['verify']['gates'],
            'checks':[asdict(c) for c in run_checks(xml.read_bytes())],
            'understanding':stats['understanding'],
            'coverage_issues':stats['verify']['source_coverage'].get('issues',[]),
            'conservation':stats['verify']['conservation'],
            'media':stats['media_gate']}
        dump(folder/'result.json',row)
        print(sample['key'],provider,'DTD',row['validation'].get('dtd_valid'),'cache',llm['cache_hits'],'calls',llm['calls'],flush=True)
        return row
    except Exception as error:
        row = {'sample':sample['key'],'provider':provider,'error':repr(error),'missing':sorted(missing),'used_keys':sorted(used)}
        dump(folder/'failure.json',row)
        print('FAILED',sample['key'],provider,repr(error),flush=True)
        return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','replay'])
    parser.add_argument('--cache',type=Path,default=BASE/'cache-v1')
    parser.add_argument('--output',type=Path,default=BASE/'replay-v1')
    parser.add_argument('--samples',default='all')
    parser.add_argument('--providers',default='deepseek,dashscope')
    parser.add_argument('--network',action='store_true')
    parser.add_argument('--web-defaults',action='store_true',help='按网页默认空出版设置和 input.docx 文件名转换')
    parser.add_argument('--workers',type=int,default=2)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
        return
    samples = [s for s in SAMPLES if args.samples == 'all' or s['key'] in args.samples.split(',')]
    assert samples and all(p in ['deepseek','dashscope'] for p in args.providers.split(','))
    jobs = [(s,p,args.cache,args.output,args.network,args.web_defaults) for s in samples for p in args.providers.split(',')]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(replay,jobs))
    dump(args.output/'summary.json',results)
    if any('error' in r for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
