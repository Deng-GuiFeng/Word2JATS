"""只对本轮隔离任务做重启前后的修改、计量和下载一致性验收。"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile
import httpx


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('action',choices=('before','after'))
    ap.add_argument('--url',required=True)
    ap.add_argument('--machine',choices=('本机','A800'),required=True)
    args=ap.parse_args()
    out=Path(__file__).resolve().parents[1]/'reports/finals-directory-20260914'
    path=out/(args.machine+'重启前.json')
    with httpx.Client(base_url=args.url,timeout=60) as client:
        def get(route):
            r=client.get(route); r.raise_for_status(); return r.json()
        if args.action=='before':
            rows=[]
            for p in sorted(out.glob(args.machine+'-*/验收.json')):
                tid=json.loads(p.read_text())['task_id']
                assert get('/api/status/'+tid)['status']=='done'
                editor=get('/api/workbench/'+tid)
                editor['fields']['title']+='（重启保留验证）'
                r=client.post('/api/edit/'+tid,json={'version':editor['version'],'fields':editor['fields']})
                r.raise_for_status()
                data=get('/api/result/'+tid)
                rows.append({'task':tid,'xml_sha256':hashlib.sha256(data['xml'].encode()).hexdigest(),
                             'llm':data['stats']['llm'], 'title':editor['fields']['title']})
            assert len(rows)==4
            path.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        else:
            rows=json.loads(path.read_text())
            for row in rows:
                tid=row['task']; data=get('/api/result/'+tid)
                assert hashlib.sha256(data['xml'].encode()).hexdigest()==row['xml_sha256']
                assert data['stats']['llm']==row['llm']
                assert get('/api/workbench/'+tid)['fields']['title']==row['title']
                r=client.get('/api/download/'+tid); r.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    assert z.read(data['xml_filename']).decode()==data['xml']
                editor=get('/api/workbench/'+tid)
                r=client.post('/api/restore/'+tid,json={'version':editor['version']}); r.raise_for_status()
                assert '重启保留验证' not in get('/api/workbench/'+tid)['fields']['title']
            (out/(args.machine+'重启验收.json')).write_text(json.dumps({
                'tasks':len(rows),'checks':['已有任务可打开','人工修改保留','XML 与用量不变','下载当前版本','重启后恢复原版本']},ensure_ascii=False,indent=2))
            print(args.machine+' 4 份任务重启恢复、计量和下载验收通过')


if __name__=='__main__':
    main()
