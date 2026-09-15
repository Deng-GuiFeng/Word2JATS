"""发布前后核对旧任务、旧缓存和提交包不变；不修改服务配置。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import httpx

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT.parent.parent
BASE=ROOT/'reports/sample-cache-20260915'
BASELINE=BASE/'release-safety-before.json'
OLD_CACHE=PROJECT/'webapp/_cache_targeted_20260915'
NEW_CACHE=PROJECT/'webapp/_cache_samples_20260916'
OVERRIDE=Path('/etc/systemd/system/word2jats-web.service.d/zzzz-public-20260915.conf')


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def files(directory):
    return {str(p.relative_to(directory)):sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}


def main(mode,expected_code=None):
    baseline=None
    if mode=='before':
        assert not BASELINE.exists(),'已有发布基线，不覆盖'
        assert files(NEW_CACHE)==files(BASE/'cache-v2'),'生产缓存副本不一致'
        rows=[]
        with httpx.Client(base_url='http://127.0.0.1:8504',timeout=30,trust_env=False) as client:
            for p in sorted((PROJECT/'webapp/_runs').glob('*/task.json')):
                t=json.loads(p.read_text());r=client.get('/api/status/'+p.parent.name);r.raise_for_status()
                assert r.json()['status'] in {'done','error'},'活动任务：'+p.parent.name
                paths=[p,p.parent/'input.docx']
                if t['status']=='done':paths.append(Path(t['result']['xml_path']))
                rows.append({'task':p.parent.name,'status':t['status'],
                             'files':{str(f):sha(f) for f in paths if f.is_file()}})
        baseline={'tasks':rows,'old_cache':files(OLD_CACHE),'new_cache':files(NEW_CACHE),
                  'submitted_zip':sha(PROJECT/'dist/JiangLab.zip'),
                  'override':OVERRIDE.read_text(),
                  'code':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT/'tmp/web-release-r03',text=True).strip()}
        BASELINE.write_text(json.dumps(baseline,ensure_ascii=False,indent=2)+'\n')
        print('Preflight:',len(rows),'existing tasks; active 0; cache files',len(baseline['new_cache']))
    else:
        baseline=json.loads(BASELINE.read_text())
        for row in baseline['tasks']:
            for path,digest in row['files'].items():assert sha(Path(path))==digest,path
        assert files(OLD_CACHE)==baseline['old_cache'],'旧缓存被修改'
        for path,digest in baseline['new_cache'].items():assert sha(NEW_CACHE/path)==digest,path
        assert sha(PROJECT/'dist/JiangLab.zip')==baseline['submitted_zip'],'提交包被修改'
        assert OVERRIDE.read_text()==(ROOT/'scripts/deployment/web-samples-20260916.conf').read_text()
        configured=OVERRIDE.read_text()
        release=Path(next(line.split('=',1)[1] for line in configured.splitlines() if line.startswith('WorkingDirectory=')))
        actual_code=subprocess.check_output(['git','rev-parse','HEAD'],cwd=release,text=True).strip()
        assert expected_code and actual_code==expected_code,'必须指定已测试的完整发布提交摘要'
        assert subprocess.check_output(['systemctl','show','word2jats-web.service','-p','WorkingDirectory','--value'],text=True).strip()==str(release)
        assert not subprocess.check_output(['git','status','--porcelain'],cwd=release,text=True).strip()
        result={'unchanged_tasks':len(baseline['tasks']),'old_cache_unchanged':True,
                'original_response_files_unchanged':True,'submitted_zip_unchanged':True,
                'release_code':actual_code,'release_worktree_clean':True}
        (BASE/'release-safety-after.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['before','after'])
    parser.add_argument('--code')
    args=parser.parse_args()
    main(args.mode,args.code)
