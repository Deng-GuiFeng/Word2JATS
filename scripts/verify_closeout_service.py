"""内部发布核验：保存已有任务状态，切换后核对原 XML 与计量、下载命名。"""
from pathlib import Path
import argparse
import hashlib
import json

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'reports/finals-closeout-20260914'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['before','after'])
    args = parser.parse_args()
    with httpx.Client(base_url='http://127.0.0.1:8504', timeout=60) as client:
        if args.mode == 'before':
            rows = []
            for path in sorted((ROOT/'webapp/_runs').glob('*/task.json')):
                task = json.loads(path.read_text()); tid = task['task_id']
                response = client.get('/api/status/'+tid); response.raise_for_status()
                assert response.json()['status'] in {'done','error'}, '存在活动任务，停止切换'
                if response.json()['status'] != 'done':
                    continue
                result = client.get('/api/result/'+tid); result.raise_for_status(); result=result.json()
                rows.append({'task':tid,'xml_sha256':hashlib.sha256(result['xml'].encode()).hexdigest(),
                             'incremental_usage':result['stats']['llm']['usage']})
            (OUT/'线上切换前.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
            print('活动任务为 0；已保存 %d 个已完成任务的 XML 哈希与新增计量。' % len(rows))
        else:
            rows = json.loads((OUT/'线上切换前.json').read_text()); recovered=[]
            for row in rows:
                response = client.get('/api/result/'+row['task']); response.raise_for_status(); result=response.json()
                assert hashlib.sha256(result['xml'].encode()).hexdigest() == row['xml_sha256']
                llm = result['stats']['llm']; assert llm['usage'] == row['incremental_usage']
                assert llm['result_usage']['complete'] and llm['result_usage']['available']
                assert llm['result_usage']['total_tokens'] > 0
                assert result['download_filename'].startswith('Word2JATS-')
                assert result['download_filename'] != '初始文件.zip'
                recovered.append({'task':row['task'],'xml_sha256':row['xml_sha256'],
                                  'download_filename':result['download_filename'],
                                  'usage':llm['result_usage']})
            (OUT/'线上切换后.json').write_text(json.dumps(recovered,ensure_ascii=False,indent=2))
            print('%d 个旧任务均可打开，XML 与原新增计量不变；原始用量完整、成果命名正确。' % len(rows))


if __name__ == '__main__':
    main()
