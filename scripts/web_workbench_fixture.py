"""仅限本机的 Web 验收服务；使用冻结转换产物，不调用模型。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import argparse
import json
import os
import shutil
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8514)
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    directory = args.directory or Path(tempfile.mkdtemp(prefix='word2jats-web-test-'))
    os.environ['W2J_RUNS_DIR'] = str(directory / 'runs')
    os.environ['W2J_UPLOADS_DIR'] = str(directory / 'uploads')
    import webapp.app as module
    from webapp import editor, task_store
    from scripts.output_manifest import resolve_output
    from word2jats.validate.validator import Validator
    from word2jats.validate.checks import run_checks
    from scripts.eval_v1.samples import BY_KEY

    def converted(opts, key='01'):
        provider = 'deepseek' if opts.llm == 'deepseek' else 'qwen'
        root = ROOT / 'reports/outputs' / f'finals-{provider}-20260914-r4'
        location = resolve_output(root, key)
        target = Path(opts.out_dir) / 'original' / 'candidate'
        shutil.copytree(location.candidate_dir, target)
        shutil.copy2(location.candidate_dir.parent / 'report.json', target.parent / 'report.json')
        timing = json.loads((root / f'{key}.timing.json').read_text())
        xml = target / location.candidate_xml.name
        return SimpleNamespace(candidate_xml=str(xml), candidate_dir=str(target), article_id=xml.stem,
            delivered=location.delivered, validation=Validator().validate_bytes(xml.read_bytes()),
            stats={'llm':{**timing['llm'],'provider':opts.llm}, 'elapsed_sec':timing['wall_seconds']})

    def simulate(opts):
        # 测试文件损坏也必须真实失败，不能夹具一律返回成功。
        import zipfile
        if not zipfile.is_zipfile(opts.docx_path):
            raise zipfile.BadZipFile('not a zip')
        for phase in ('parse','understand','render','validate'):
            if opts.progress:
                opts.progress(phase, phase)
            time.sleep(.3)
        return converted(opts)

    module.convert = simulate
    fixtures = [('qwen','01'),('deepseek','01'),('qwen','S03'),('deepseek','X01')]
    for number, (provider,key) in enumerate(fixtures,1):
        tid = f'{number:016x}'; folder = module.RUNS_DIR / tid
        if (folder / 'task.json').exists():
            continue
        folder.mkdir(parents=True,exist_ok=True)
        shutil.copy2(BY_KEY[key].docx,folder / 'input.docx')
        options = module.ConvertOptions(docx_path=str(folder/'input.docx'),out_dir=str(folder/'output'),llm='deepseek' if provider == 'deepseek' else 'dashscope')
        module.TASKS[tid] = {'task_id':tid,'workdir':str(folder),'filename':f'学术稿件-{key}.docx','status':'pending',
                           'stage':'等待开始','stage_key':'queued','created_at':time.time(),'started_at':None,'finished_at':None,
                           'error':None,'result':None,'options':{'provider':options.llm,'journal':BY_KEY[key].journal,'doi':BY_KEY[key].doi}}
        old_convert = module.convert
        module.convert = lambda opts, key=key: converted(opts,key)
        module._run_conversion(tid,options)
        module.convert = old_convert
        assert module.TASKS[tid]['status'] == 'done', module.TASKS[tid]
    # 缺出版信息/零用量/不完整用量：在测试副本上构造明确状态。
    for number, kind in ((5,'missing'),(6,'zero'),(7,'partial')):
        tid = f'{number:016x}'
        if (module.RUNS_DIR / tid / 'task.json').exists():
            continue
        original = deepcopy(module._get('0000000000000001'))
        folder = module.RUNS_DIR / tid
        shutil.copytree(module.RUNS_DIR / '0000000000000001', folder)
        old_prefix = original['workdir']
        original = json.loads(json.dumps(original).replace(old_prefix,str(folder)))
        original['task_id'] = tid
        result = original['result']
        if kind == 'missing':
            xml = Path(result['xml_path']); root = editor.parse(xml.read_bytes()); root.find('front/journal-meta').clear()
            xml.write_bytes(__import__('lxml.etree',fromlist=['etree']).tostring(root))
            validation = Validator().validate_bytes(xml.read_bytes())
            result['validation'] = {'ok':validation.ok,'well_formed':validation.well_formed,'dtd_valid':validation.dtd_valid,'errors':validation.errors}
            result['checks'] = [{'code':c.code,'severity':c.severity,'detail':c.detail} for c in run_checks(xml.read_bytes())]
        elif kind == 'zero':
            llm = result['stats']['llm']
            llm['reused_usage_records'] = [{**record, 'status':'reused'} for record in llm['usage_records']]
            llm['cache_hits'] = len(llm['reused_usage_records'])
            llm['usage_records'] = []
            result['stats']['llm']['usage'] = {**dict.fromkeys(('input_tokens','output_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens','requests'),0),'complete':True}
            result['stats']['llm']['by_model'] = []
        else:
            result['stats']['llm']['usage_records'][0]['usage'] = None
            result['stats']['llm']['usage']['complete'] = False
        module.TASKS[tid] = original; task_store.persist(original)
    print(json.dumps({'directory':str(directory),'url':f'http://127.0.0.1:{args.port}','tasks':[f'{i:016x}' for i in range(1,8)]}),flush=True)
    import uvicorn
    uvicorn.run(module.app, host='127.0.0.1',port=args.port,access_log=False)


if __name__ == '__main__':
    main()
