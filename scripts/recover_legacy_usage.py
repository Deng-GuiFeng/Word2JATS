"""内部维护：在严格核对后，为旧任务补回磁盘缓存中已有的原始计量。

prepare 只写内部核验文件和临时转换目录，不改任务；apply 要求 Web 服务已停止。
全程禁用网络模型调用，只接受原转换结束前已存在、计量完整的缓存回答。
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from word2jats.llm.client import LLMClient
from word2jats.llm.cache import DiskCache
from word2jats.pipeline import ConvertOptions, convert
from webapp.usage import public_usage
from webapp.task_store import persist

OUT = ROOT/'reports/finals-closeout-20260914/历史用量恢复'


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def identity(record):
    return record.get('provider'), record.get('model'), record.get('request_id')


def prepare(shared_cache):
    OUT.mkdir(parents=True, exist_ok=True)
    LLMClient._load_env = staticmethod(lambda *_: None)
    for key in list(os.environ):
        if key.endswith('_API_KEY'):
            os.environ.pop(key)
    original_get = DiskCache.get_entry
    plan = {'prepared':[], 'skipped':[]}
    temporary = Path(tempfile.mkdtemp(prefix='word2jats-usage-recovery-'))
    for path in sorted((ROOT/'webapp/_runs').glob('*/task.json')):
        original = path.read_bytes(); task = json.loads(original)
        old = task.get('result',{}).get('stats',{}).get('llm',{})
        if task['status'] != 'done' or not old.get('cache_hits') or old.get('reused_usage_records'):
            continue
        try:
            def cached_before_completion(cache, payload):
                cache_path = Path(cache.dir)/(cache._key(payload)+'.json')
                assert cache_path.exists(), '原请求缓存缺失'
                assert cache_path.stat().st_mtime <= task['finished_at'], '缓存晚于原任务完成时间'
                entry = original_get(cache,payload)
                assert entry and entry.get('usage'), '缓存缺少原始计量'
                assert entry.get('response_meta',{}).get('request_id'), '缓存缺少请求身份'
                return entry
            DiskCache.get_entry = cached_before_completion
            opts = task['options']; folder = path.parent
            cache = folder/'llm-cache' if (folder/'llm-cache').exists() else shared_cache
            result = convert(ConvertOptions(docx_path=str(folder/'input.docx'),
                out_dir=str(temporary/task['task_id']), journal_id=opts.get('journal') or None,
                doi=opts.get('doi') or None, llm=opts['provider'],llm_cache_dir=str(cache)))
            assert Path(result.candidate_xml).read_bytes() == Path(task['result']['candidate_xml']).read_bytes(), '原始 XML 不一致'
            new = result.stats['llm']; assert new['calls'] == 0 and new['cache_misses'] == 0
            online_ids = {identity(r) for r in old.get('usage_records',[]) if r.get('request_id')}
            recovered = [r for r in new['reused_usage_records'] if identity(r) not in online_ids]
            assert len(recovered) == old['cache_hits'], '复用调用数与原任务不一致'
            updated = copy.deepcopy(task)
            llm = updated['result']['stats']['llm']; llm['reused_usage_records'] = recovered
            for row in llm.get('by_model',[]):
                rows = [r for r in recovered if r['provider']==row['provider'] and r['model']==row['model']]
                assert len(rows) == row['cache_hits'], '子模型复用数不一致'
                row['reused_usage_records'] = rows
            assert public_usage(llm)['result_usage']['complete'], '计量仍不完整'
            (OUT/(task['task_id']+'-原快照.json')).write_bytes(original)
            (OUT/(task['task_id']+'-补全快照.json')).write_text(json.dumps(updated,ensure_ascii=False))
            plan['prepared'].append({'task':task['task_id'],'original_sha256':digest(original),
                'original_xml_sha256':digest(Path(task['result']['candidate_xml']).read_bytes()),
                'reused_records':len(recovered), 'result_usage':public_usage(llm)['result_usage']})
        except Exception as error:
            plan['skipped'].append({'task':task['task_id'],'reason':str(error)})
    DiskCache.get_entry = original_get
    (OUT/'核验方案.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    print(json.dumps({'可恢复':len(plan['prepared']),'保留原状':plan['skipped']},ensure_ascii=False))


def apply():
    assert subprocess.run(['systemctl','is-active','--quiet','word2jats-web']).returncode != 0, '先停止 Web 服务'
    plan = json.loads((OUT/'核验方案.json').read_text())
    for item in plan['prepared']:
        path = ROOT/'webapp/_runs'/item['task']/'task.json'
        assert digest(path.read_bytes()) == item['original_sha256'], '任务在核验后发生变化，停止应用'
    for item in plan['prepared']:
        task = json.loads((OUT/(item['task']+'-补全快照.json')).read_text())
        assert digest(Path(task['result']['candidate_xml']).read_bytes()) == item['original_xml_sha256']
        persist(task)
    (OUT/'已应用.json').write_text(json.dumps({'tasks':[r['task'] for r in plan['prepared']]},indent=2))
    print('已补全 %d 份任务计量；未修改 XML、媒体、人工修订和原新增量。' % len(plan['prepared']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare','apply'])
    parser.add_argument('--cache-dir', type=Path, default=ROOT/'webapp/_cache')
    args = parser.parse_args()
    prepare(args.cache_dir) if args.mode == 'prepare' else apply()
