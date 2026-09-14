"""提交目录验收辅助；报告不记录密钥。测试材料始终在提交目录外。"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import zipfile


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()
            and p.relative_to(root).parts[0] != 'build'
            and not any(n.startswith(('_cache', '_runs', '_uploads', '.llm_cache', '.crossref_cache')) or n in
                        {'.venv', '__pycache__', '.pytest_cache', 'word2jats.egg-info'}
                        for n in p.relative_to(root).parts)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=('snapshot', 'probe', 'audit'))
    ap.add_argument('--prototype', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    root = args.prototype.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.action == 'snapshot':
        report = {'python':sys.version, 'files':hashes(root)}
    elif args.action == 'audit':
        from lxml import etree
        from word2jats.validate.validator import Validator
        from word2jats.llm.usage import summarize_usage
        tasks = []
        for path in sorted((root/'webapp/_runs').glob('*/task.json')):
            task = json.loads(path.read_text())
            if task['status'] != 'done':
                continue
            result = task['result']; llm = result['stats']['llm']
            raw = Path(result['xml_path']).read_bytes()
            assert Validator().validate_bytes(raw).ok
            for record in llm.get('reused_usage_records', []):
                cache = json.loads((root/'webapp/_cache'/(record['cache_key']+'.json')).read_text())
                assert record['usage'] == cache['usage']
                assert record['request_id'] == cache['response_meta']['request_id']
            xml = etree.fromstring(raw)
            refs = xml.xpath('//@xlink:href', namespaces={'xlink':'http://www.w3.org/1999/xlink'})
            with zipfile.ZipFile(path.parent/'input.docx') as original:
                media = {original.read(n) for n in original.namelist() if n.startswith('word/media/')}
            count = 0
            for ref in refs:
                if ':' in ref or ref.startswith('#'):
                    continue
                assert (Path(result['candidate_dir'])/ref).read_bytes() in media
                count += 1
            tasks.append({'task':task['task_id'], 'xml_sha256':hashlib.sha256(raw).hexdigest(),
                          'media_checked':count, 'reused_records_checked':len(llm.get('reused_usage_records', [])),
                          'dtd_valid':True})
        assert len(tasks) >= 4
        report = {'python':sys.version, 'tasks':tasks, 'files':hashes(root)}
    else:
        from dotenv import dotenv_values
        from openai import OpenAI
        values = dotenv_values(root/'.env')
        report = {'checks':[]}
        for provider, model in [('DASHSCOPE','qwen3.7-plus'), ('DASHSCOPE','qwen3.8-max'),
                                ('DEEPSEEK','deepseek-flash')]:
            row = {'provider':provider, 'model':model}
            try:
                with OpenAI(api_key=values[provider+'_API_KEY'],
                            base_url=values[provider+'_BASE_URL'], max_retries=0, timeout=60) as client:
                    r = client.chat.completions.create(model=model, max_tokens=128,
                        messages=[{'role':'user','content':'Return the JSON object {"ok":true} only.'}])
                    row.update(ok=bool(r.choices[0].message.content), returned_model=r.model,
                               usage=r.usage.model_dump() if r.usage else None)
            except Exception as error:
                row.update(ok=False, error_type=type(error).__name__,
                           status=getattr(error,'status_code',None))
            report['checks'].append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        assert all(r['ok'] for r in report['checks']), '提交配置存在不可调用模型'
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
