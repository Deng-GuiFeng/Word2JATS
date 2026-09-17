"""从已重放候选中选择原始响应，生成独立缓存和可核对的来源清单。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/sample-cache-20260915'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path,default=BASE/'cache-v2')
    parser.add_argument('--base',type=Path,default=BASE/'cache-v1')
    parser.add_argument('--selection',type=Path,action='append',required=True)
    args=parser.parse_args()
    args.target.mkdir(parents=True,exist_ok=False)
    entries={}
    selections=[]
    for file in sorted(args.base.glob('*.json')):
        shutil.copy2(file,args.target/file.name)
        entries[file.stem]={'source':str(file.resolve()),'sha256':digest(file),'selection':'base'}
    for path in args.selection:
        result=json.loads(path.read_text())
        assert 'error' not in result and result['validation']['dtd_valid']
        assert not result['coverage_issues'] or all(
            i['source_id']=='doc/p28' and 'Received' in i['detail'] for i in result['coverage_issues'])
        name=f"{result['sample']}-{result['provider']}"
        for key in result['used_keys']:
            source=Path(result['cache'])/(key+'.json')
            raw=json.loads(source.read_text())
            assert raw['payload']['provider']==result['provider'] and raw.get('usage') is not None
            old=entries.get(key)
            shutil.copy2(source,args.target/source.name)
            entries[key]={'source':str(source.resolve()),'sha256':digest(source),
                          'selection':name,'previous':old}
        selections.append({'sample':name,'result':str(path.resolve()),'keys':result['used_keys']})
    manifest={'base':str(args.base.resolve()),'target':str(args.target.resolve()),
              'selections':selections,'entries':entries}
    args.target.with_name(args.target.name+'-manifest.json').write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print('Selected',len(entries),'original responses;',len(selections),'candidate overrides')


if __name__=='__main__':
    main()
