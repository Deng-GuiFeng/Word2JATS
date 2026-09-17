"""本轮四份真实任务的计量与重启持久化验收，不操作服务生命周期。"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/web-next'
URL='http://127.0.0.1:18640'
FIELDS=('input_tokens','output_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('before','after'))
    args=parser.parse_args()
    path=OUT/'restart-before.json'
    with httpx.Client(base_url=URL,timeout=60) as client:
        def get(route):
            response=client.get(route); response.raise_for_status(); return response.json()
        if args.action=='before':
            rows=[]
            for folder in ('real-deepseek','real-qwen','reuse-deepseek','reuse-qwen'):
                summary=json.loads((OUT/folder/'验收.json').read_text())
                tid=summary['task_id']
                assert get('/api/status/'+tid)['status']=='done'
                stored=json.loads((ROOT/'webapp/_runs'/tid/'task.json').read_text())['result']['stats']['llm']
                raw=stored['usage_records']+stored['reused_usage_records']
                assert raw and len({r['request_id'] for r in raw})==len(raw)
                # 独立从调用响应核对，不能直接以同一汇总函数自证。
                actual=dict.fromkeys(FIELDS,0)
                for record in raw:
                    usage=record['usage']; inp=usage['prompt_tokens']; output=usage['completion_tokens']
                    hit=usage.get('prompt_cache_hit_tokens',usage.get('prompt_tokens_details',{}).get('cached_tokens',0))
                    miss=usage.get('prompt_cache_miss_tokens',inp-hit)
                    assert inp==hit+miss and usage['total_tokens']==inp+output
                    for key,value in zip(FIELDS,(inp,output,usage['total_tokens'],hit,miss)): actual[key]+=value
                data=get('/api/result/'+tid)
                public=data['stats']['llm']
                assert all(public['result_usage'][key]==actual[key] for key in FIELDS)
                if folder.startswith('reuse'):
                    assert public['calls']==0 and public['incremental_usage']['total_tokens']==0
                    original=json.loads((OUT/folder.replace('reuse-','real-')/'验收.json').read_text())
                    assert data['version']==original['xml_sha256']
                    assert all(original['llm']['result_usage'][key]==actual[key] for key in FIELDS)
                work=get('/api/workbench/'+tid)
                work['fields']['title']+='（重启保留验证）'
                response=client.post('/api/edit/'+tid,json={'version':work['version'],'fields':work['fields']}); response.raise_for_status()
                data=get('/api/result/'+tid)
                rows.append({'task':tid,'version':data['version'],'llm':data['stats']['llm'],'title':work['fields']['title'],'response_totals':actual,'folder':folder})
            assert len(rows)==4
            path.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        else:
            rows=json.loads(path.read_text())
            keys=[value for key,value in dotenv_values(ROOT/'.env').items() if key.endswith('_API_KEY') and value]
            assert len(keys)>=2
            for route in ('/.env','/static/.env','/static/%2e%2e/.env'):
                response=client.get(route)
                assert response.status_code==404 and all(key.encode() not in response.content for key in keys)
            for row in rows:
                tid=row['task']; data=get('/api/result/'+tid)
                assert data['version']==row['version'] and data['stats']['llm']==row['llm']
                assert get('/api/workbench/'+tid)['fields']['title']==row['title']
                response=client.get('/api/download/'+tid); response.raise_for_status()
                assert all(key.encode() not in response.content for key in keys)
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    assert archive.read(data['xml_filename']).decode()==data['xml']
                    for name in archive.namelist(): assert all(key.encode() not in archive.read(name) for key in keys)
                xml=client.get('/api/xml/'+tid); xml.raise_for_status()
                assert hashlib.sha256(xml.content).hexdigest()==row['version']
                response=client.post('/api/restore/'+tid,json={'version':row['version']}); response.raise_for_status()
                original=json.loads((OUT/row['folder']/'验收.json').read_text())
                assert get('/api/result/'+tid)['version']==original['xml_sha256']
            (OUT/'restart-result.json').write_text(json.dumps({'tasks':4,'checks':['原始响应 Token 总量独立核算一致','两模型复用原始用量不归零且新增为零','重启保留修订与用量','ZIP 与 XML 版本一致','恢复原始版本','配置密钥不经网页及交付文件暴露']},ensure_ascii=False,indent=2))
        print(args.action+' passed: 4 tasks')


if __name__=='__main__': main()
