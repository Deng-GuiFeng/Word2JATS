"""按历史任务的原始调用缓存隔离重放；不发起新请求、不修改线上缓存。"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import shutil

from prepare_sample_cache import BASE, PROJECT, SAMPLES, replay, dump, digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--task',action='append',required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    candidates=json.loads((BASE/'candidates.json').read_text());jobs=[];manifest=[]
    for tid in args.task:
        row=next(r for r in candidates if r['task']==tid)
        folder=args.output/tid;cache=folder/'cache'
        shutil.copytree(BASE/'cache-v2',cache,ignore=shutil.ignore_patterns('_source_layout'))
        origin=PROJECT/'webapp/_runs'/tid/'llm-cache';replacements=[]
        for file in sorted(origin.glob('*.json')):
            shutil.copy2(file,cache/file.name)
            replacements.append({'key':file.stem,'source':str(file),'sha256':digest(file)})
        assert replacements,(tid,'历史任务没有独立原始缓存')
        manifest.append({'task':tid,'sample':row['sample'],'replacements':replacements})
        sample=next(s for s in SAMPLES if s['key']==row['sample'])
        jobs.append((sample,row['options']['provider'],cache,folder,False,False))
    dump(args.output/'manifest.json',manifest)
    with ProcessPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(replay,jobs))
    dump(args.output/'summary.json',results)
    if any('error' in r for r in results):raise SystemExit(1)


if __name__=='__main__':main()
