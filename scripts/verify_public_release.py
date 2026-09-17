"""正式发布前后只读核验；不修改已有任务。"""
import argparse
import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = Path('/home/denggf/学术期刊结构化技术创新大赛')
OUT = ROOT / 'reports/web-public-20260915'


def main(mode):
    OUT.mkdir(parents=True, exist_ok=True)
    baseline = OUT / 'release-baseline.json'
    url = 'http://127.0.0.1:8504' if mode == 'before' else 'https://word2jats.jianglab.work'
    with httpx.Client(base_url=url, timeout=60, trust_env=False) as client:
        if mode == 'before':
            if baseline.exists():
                raise SystemExit('基线已经存在，不覆盖。')
            rows = []
            for path in sorted((ORIGINAL / 'webapp/_runs').glob('*/task.json')):
                tid = path.parent.name
                r = client.get('/api/status/' + tid)
                r.raise_for_status()
                status = r.json()
                assert status['status'] in {'done', 'error'}, '存在活动任务，不能切换：' + tid
                row = {'task': tid, 'status': status['status']}
                if status['status'] == 'done':
                    r = client.get('/api/result/' + tid)
                    r.raise_for_status()
                    data = r.json()
                    row.update(xml_sha256=hashlib.sha256(data['xml'].encode()).hexdigest(), llm=data['stats']['llm'])
                rows.append(row)
            baseline.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(json.dumps({'tasks': len(rows), 'active': 0}))
        else:
            rows = json.loads(baseline.read_text())
            for row in rows:
                r = client.get('/api/status/' + row['task'])
                r.raise_for_status()
                assert r.json()['status'] == row['status']
                if row['status'] == 'done':
                    r = client.get('/api/result/' + row['task'])
                    r.raise_for_status()
                    data = r.json()
                    assert hashlib.sha256(data['xml'].encode()).hexdigest() == row['xml_sha256']
                    assert data['stats']['llm'] == row['llm']
            print(json.dumps({'public_existing_tasks_unchanged': len(rows)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['before', 'after'])
    main(parser.parse_args().mode)
