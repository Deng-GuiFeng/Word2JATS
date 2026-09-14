"""旧 Web 服务停机前，把仍可查询的已结束任务保存成可恢复快照。"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from webapp import editor, task_store


def collect(url, runs):
    tasks, active, unavailable=[],[],[]
    with httpx.Client(base_url=url,timeout=20) as client:
        for folder in sorted(runs.iterdir()):
            if not folder.is_dir() or len(folder.name)!=16 or any(c not in '0123456789abcdef' for c in folder.name):
                continue
            response=client.get('/api/status/'+folder.name)
            if response.status_code==404:
                unavailable.append(folder.name); continue
            response.raise_for_status(); status=response.json()
            if status['status'] not in {'done','error'}:
                active.append(folder.name); continue
            if (folder/'task.json').exists():
                continue
            created=(folder/'input.docx').stat().st_mtime
            task={'task_id':folder.name,'filename':status['filename'],'workdir':str(folder.resolve()),
                  'status':status['status'],'stage':status['stage'],'stage_key':status.get('stage_key',''),
                  'created_at':created,'started_at':created,'finished_at':created+status['elapsed'],
                  'error':status.get('error'),'result':None,'options':{}}
            if status['status']=='done':
                response=client.get('/api/result/'+folder.name); response.raise_for_status(); result=response.json()
                matches=[file for file in (folder/'output').rglob('*.xml') if file.read_text(encoding='utf-8')==result['xml']]
                preferred=[file for file in matches if file.parent.name=='candidate']
                candidates=preferred or matches
                if not candidates:
                    raise ValueError('未找到与服务结果一致的 XML：'+folder.name)
                xml=candidates[0]; publication=editor.extract(xml.read_bytes())['publication']
                task['options']={'provider':result['stats'].get('llm',{}).get('provider','dashscope'),
                                 'doi':publication['doi'],'journal':publication['journal_id']}
                task['result']={k:v for k,v in result.items() if k not in {'xml','task_id','filename'}}
                task['result'].update(xml_path=str(xml.resolve()),candidate_xml=str(xml.resolve()),
                                      candidate_dir=str(xml.parent.resolve()),out_dir=str((folder/'output').resolve()))
            tasks.append(task)
    return tasks,active,unavailable


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8504')
    parser.add_argument('--runs',type=Path,default=ROOT/'webapp/_runs')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--report',type=Path,default=ROOT/'reports/web-product-20260914/旧任务迁移.json')
    args=parser.parse_args()
    if not args.url.startswith(('http://127.0.0.1:','http://localhost:')):
        parser.error('仅允许访问本机服务')
    tasks,active,unavailable=collect(args.url,args.runs)
    if active:
        raise SystemExit(f'还有 {len(active)} 个任务正在执行，不进行迁移或重启。')
    if args.apply:
        for task in tasks:
            task_store.persist(task)
    summary={'mode':'apply' if args.apply else 'dry-run','timestamp':time.time(),'tasks':[
        {'task_id':task['task_id'],'status':task['status'],'xml_sha256':hashlib.sha256(Path(task['result']['xml_path']).read_bytes()).hexdigest() if task['result'] else None}
        for task in tasks],'active':active,'already_unavailable_count':len(unavailable)}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'mode':summary['mode'],'ready_tasks':len(tasks),'active':len(active),'already_unavailable':len(unavailable)},ensure_ascii=False))


if __name__=='__main__':
    main()
