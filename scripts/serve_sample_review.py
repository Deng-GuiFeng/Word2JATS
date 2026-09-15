"""仅本机展示已重放的实际转换产物，不重新调用模型，不改线上任务。"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
RELEASE = Path(os.environ.get('W2J_REVIEW_CODE_ROOT', ROOT.parent.parent/'tmp/web-release-r02')).resolve()
sys.path.insert(0,str(RELEASE))
sys.path.insert(0,str(RELEASE/'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--port',type=int,default=18643)
    args = parser.parse_args()
    args.directory = args.directory.resolve()
    os.environ['W2J_RUNS_DIR'] = str(args.directory/'runs')
    os.environ['W2J_UPLOADS_DIR'] = str(args.directory/'uploads')
    os.environ['W2J_WEBAPP_CACHE'] = str(args.directory/'unused-cache')
    import webapp.app as module
    from webapp.review import review_items
    from webapp.fidelity import summary
    from word2jats.validate.checks import run_checks
    def disabled_conversion(*args,**kwargs):
        raise RuntimeError('Read-only sample review server: conversion disabled')
    module.convert = disabled_conversion
    manifest = []
    samples = {s['key']:s for s in json.loads((ROOT/'样例数据/样例登记.json').read_text())['样例']}
    for number,file in enumerate(sorted(args.results.glob('*/full-result.json')),1):
        raw = json.loads(file.read_text())
        key,provider = file.parent.name.split('-',1)
        tid = f'{number:016x}'
        folder = module.RUNS_DIR/tid
        folder.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/'样例数据'/key/'初始文件.docx',folder/'input.docx')
        xml = Path(raw['candidate_xml']).read_bytes()
        task = {'task_id':tid,'workdir':str(folder),'filename':f'{key}.docx',
            'status':'done','stage':'完成','stage_key':'done','error':None,
            'created_at':time.time(),'started_at':time.time(),'finished_at':time.time(),
            'options':{'provider':provider,'journal':samples[key]['journal'],'doi':samples[key]['doi']},
            'result':{**raw,'xml_path':raw['candidate_xml'],'out_dir':str(file.parent/'output'),
                'checks':[asdict(c) for c in run_checks(xml)],'notice':None,
                'fidelity':summary(str(folder/'input.docx'),xml),
                'review_items':review_items(str(folder/'input.docx'),SimpleNamespace(**raw)),
                'review_decisions':{}}}
        module.TASKS[tid] = task
        module.task_store.persist(task)
        manifest.append({'sample':key,'provider':provider,'task':tid,'source_result':str(file)})
    (args.directory/'tasks.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print('Review server: actual replay outputs, model conversion disabled, tasks',len(manifest),flush=True)
    import uvicorn
    uvicorn.run(module.app,host='127.0.0.1',port=args.port,access_log=False)


if __name__ == '__main__':
    main()
