"""汇总公网逐例执行及实际视觉审查进度；不把已尝试、已截图当作通过。"""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.web_public_evidence import ROOT, write_json


CHAPTERS = ('read_content', 'checks_and_downloads', 'editing', 'exceptional_paths',
            'responsive', 'restore_and_recent', 'reconvert', 'remaining_entries', 'upload_paths',
            'metadata_persistence')
PROVIDERS = ('deepseek', 'dashscope')


def reviewed_delta_frames(data):
    """只有整页或完整的已审查前帧+全部变化区域，才覆盖该帧。"""
    patches = {p['id']: p for p in data['patches']}
    covered, by_file, hashes = [], {}, set()
    for index, frame in enumerate(data['frames']):
        if 'same_as' in frame:
            assert 0 <= frame['same_as'] < index
            other = data['frames'][frame['same_as']]
            assert frame['source_sha256'] == other['source_sha256']
            yes = covered[frame['same_as']]
        else:
            regions = frame.get('regions', [{'patch':frame.get('patch'),'box':frame.get('box')}])
            full = any(region['box'] == [0, 0, *frame['size']] for region in regions)
            base = full or by_file.get(frame['previous_file'], False)
            yes = bool(base and all(region['patch'] is None or
                       patches[region['patch']].get('reviewed_at') for region in regions))
        covered.append(yes)
        by_file[frame['file']] = yes
        if yes:
            hashes.add(frame['source_sha256'])
    return hashes


def review_hashes(root):
    hashes = set()
    for path in (root/'visual-review').glob('*/manifest.json'):
        data = json.loads(path.read_text())
        hashes.update(row['sha256'] for row in data['items'] if row.get('reviewed_at'))
    for path in (root/'visual-deltas').glob('*/manifest.json'):
        hashes.update(reviewed_delta_frames(json.loads(path.read_text())))
    return hashes


def audit(root):
    reviewed = review_hashes(root)
    cases, read_errors = [], []
    samples = json.loads((ROOT/'样例数据/样例登记.json').read_text())['样例']
    for sample in (row['key'] for row in samples):
        for provider in PROVIDERS:
            folder = root/(sample+'-'+provider)
            initial = folder/'convert/result.json'
            record = json.loads(initial.read_text()) if initial.exists() else {}
            llm = record.get('llm', {})
            fresh = bool(llm.get('calls', 0) > 0 and llm.get('usage', {}).get('total_tokens', 0) > 0
                         and llm.get('cache_hits') == 0 and llm.get('reused_responses') == 0)
            chapters, issues, legacy = {}, [], {}
            observed_controls, operated_controls = {}, set()
            frames = frame_reviewed = shots = shot_reviewed = 0
            for path in sorted(folder.rglob('events.jsonl')):
                for number, line in enumerate(path.open(), 1):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        # 采集仍进行中时，最后一条可能尚未写完；下次核算会重新读取。
                        read_errors.append({'file': str(path.relative_to(root)), 'line': number})
                        continue
                    kind = event['kind']
                    if kind == 'user-event' and event['event'] in {'click','dblclick','input','change','keydown'}:
                        for key in ('id', 'field'):
                            if event.get(key):
                                operated_controls.add(key+':'+event[key])
                    if kind == 'chapter-end':
                        name = event['chapter']
                        if event['time'] > chapters.get(name, {}).get('time', 0):
                            chapters[name] = {k: event[k] for k in ('status', 'time')}
                    elif kind == 'issue':
                        issues.append({'file': str(path.relative_to(root)),
                                       **{k: event[k] for k in ('time', 'action', 'message')}})
                    elif kind == 'frame':
                        frames += 1
                        frame_reviewed += event['sha256'] in reviewed
                    elif kind == 'screenshot':
                        for control in event.get('controls', []):
                            if control.get('disabled') or control.get('inert'):
                                continue
                            for key in ('id', 'field'):
                                if control.get(key):
                                    observed_controls[key+':'+control[key]] = control
                        shots += 1
                        file = path.parent/event['file']
                        shot_reviewed += hashlib.sha256(file.read_bytes()).hexdigest() in reviewed
            for path in sorted(folder.glob('actions/**/progress.json')):
                progress = json.loads(path.read_text())
                for action in progress['actions']:
                    if action['name'] in CHAPTERS and action['name'] not in chapters:
                        # 早期记录器没有 chapter-end；保留已完成步骤的原记录，
                        # 不将整个章节的外层 passed 冒充所有子步骤通过。
                        legacy.setdefault(action['name'], []).append({
                            'file': str(path.relative_to(root)), **action})
            cases.append({'sample': sample, 'provider': provider, 'task': record.get('task'),
                          'fresh_initial_conversion': fresh, 'chapters': chapters,
                          'legacy_chapter_records': legacy,
                          'identified_visible_controls': len(observed_controls),
                          'identified_operated_controls': len(set(observed_controls) & operated_controls),
                          'controls_without_recorded_operation': [
                              {'key': key, **control} for key, control in observed_controls.items()
                              if key not in operated_controls],
                          'missing_chapters': [name for name in CHAPTERS if name not in chapters and name not in legacy],
                          'chapters_with_issues': [name for name in CHAPTERS
                                                  if chapters.get(name, {}).get('status') == 'issues-found'],
                          'issues': issues, 'frames': frames, 'reviewed_frames': frame_reviewed,
                          'screenshots': shots, 'reviewed_screenshots': shot_reviewed})
    return {'note': '仅进度核算；章节末状态不能替代逐步验收。旧问题不因补跑而删除，截图生成不等于审查。',
            'cases': cases, 'evidence_read_errors': read_errors,
            'fresh_initial_conversions': sum(c['fresh_initial_conversion'] for c in cases),
            'frames': sum(c['frames'] for c in cases),
            'reviewed_frames': sum(c['reviewed_frames'] for c in cases),
            'screenshots': sum(c['screenshots'] for c in cases),
            'reviewed_screenshots': sum(c['reviewed_screenshots'] for c in cases)}


def main(args):
    result = audit(args.root)
    write_json(args.root/'audit-progress.json', result)
    for case in result['cases']:
        print(json.dumps({k: v for k, v in case.items() if k not in {'issues', 'chapters'}}, ensure_ascii=False))
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT/'reports/web-public-20260915/round-01')
    main(parser.parse_args())
