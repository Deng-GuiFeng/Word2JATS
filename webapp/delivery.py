"""从同一份已保存结果生成下载说明与文件，下载后清理临时 ZIP。"""
import io
import json
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from .editor import fingerprint
from .export import names, media_files


def snapshot(task, version=None):
    """任务更新使用新字典；本地快照中的路径与检查属于同一版本。"""
    result = task['result']
    try:
        xml = Path(result['xml_path']).read_bytes()
    except OSError as error:
        raise HTTPException(409, '当前 XML 暂时无法读取，请重新载入结果后重试。') from error
    current = fingerprint(xml)
    if version and version != current:
        raise HTTPException(409, '结果已在其他页面更新，请重新载入后下载最新版本。')
    return xml, current


def resources(task, xml):
    result = task['result']
    try:
        return list(media_files(result.get('candidate_dir') or result['out_dir'], xml))
    except (ValueError, OSError) as error:
        raise HTTPException(409, '配套图片暂时无法完整打包。请重试；也可先单独下载 XML 和 Word 原稿。') from error


def describe(task, version=None):
    xml, current = snapshot(task, version)
    media = resources(task, xml)
    result = task['result']
    file_names = names(task)
    entries = [
        {'name': file_names['xml_filename'], 'description': '当前保存版本的 JATS XML', 'bytes': len(xml)},
        {'name': 'figures.zip', 'description': f'{len(media)} 个配套图片资源，保留原文件与目录结构', 'count': len(media)},
        {'name': '文件说明.txt', 'description': '文件用途与图片解压方法'},
        {'name': '检查摘要.json', 'description': '当前版本的 XML 格式与结构检查'},
        {'name': '转换用量.json', 'description': '原始模型用量与本次新增用量'},
    ]
    if result.get('edited'):
        entries.append({'name': '修改记录.json', 'description': '已保存的文章或出版信息修改'})
    if (Path(task['workdir']) / 'review.json').is_file():
        entries.append({'name': '人工复核记录.json', 'description': '此前版本保留的复核记录，不改变 XML'})
    return {**file_names, 'version': current, 'edited': bool(result.get('edited')),
            'saved_at': result.get('saved_at'), 'files': entries}


def xml_response(task, version=None):
    xml, current = snapshot(task, version)
    filename = names(task)['xml_filename']
    return Response(xml, media_type='application/xml', headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename),
        'ETag': '"' + current + '"', 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff'})


def archive_response(task, stats, version=None):
    xml, current = snapshot(task, version)
    media = resources(task, xml)
    result = task['result']
    file_names = names(task)
    figures_data = io.BytesIO()
    download_path = None
    try:
        with zipfile.ZipFile(figures_data, 'w', zipfile.ZIP_DEFLATED) as figures:
            for media_path, relative in media:
                figures.write(media_path, relative)
        download_path = Path(task['workdir']) / ('download-' + uuid.uuid4().hex + '.zip')
        with zipfile.ZipFile(download_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(file_names['xml_filename'], xml)
            archive.writestr('figures.zip', figures_data.getvalue())
            archive.writestr('文件说明.txt', 'Word2JATS 转换成果\n\n' + file_names['xml_filename'] +
                '：当前保存版本的 JATS XML。\nfigures.zip：XML 引用的配套图片；请解压到 XML 所在目录，保留图片包内的目录结构。\n' +
                '检查摘要.json：当前版本的结构检查结果。\n转换用量.json：生成分析结果的模型用量及本次新增用量。\n' +
                ('修改记录.json：已保存的文章或出版信息修改。\n' if result.get('edited') else ''))
            archive.writestr('检查摘要.json', json.dumps({
                'validation': result.get('validation'), 'edited': result.get('edited', False),
                'checks': result.get('checks', []), 'xml_sha256': current}, ensure_ascii=False, indent=2))
            if result.get('edited'):
                archive.writestr('修改记录.json', json.dumps(result.get('edit_history', []), ensure_ascii=False, indent=2))
            review = Path(task['workdir']) / 'review.json'
            if review.is_file():
                archive.write(review, '人工复核记录.json')
            archive.writestr('转换用量.json', json.dumps({
                'elapsed_seconds': stats.get('elapsed_sec'), 'model_usage': stats.get('llm', {})}, ensure_ascii=False, indent=2))
    except OSError as error:
        if download_path is not None:
            download_path.unlink(missing_ok=True)
        raise HTTPException(503, '下载文件暂时无法生成，请稍后重试。已保存的转换结果不受影响。') from error
    return FileResponse(download_path, media_type='application/zip', filename=file_names['download_filename'],
                        headers={'Cache-Control': 'no-store', 'ETag': '"' + current + '"'},
                        background=BackgroundTask(download_path.unlink, missing_ok=True))


def install_routes(app, get_task):
    def completed(task_id):
        task = get_task(task_id)
        if task is None:
            raise HTTPException(404, '未找到这次转换，请重新上传文件。')
        if task['status'] != 'done':
            raise HTTPException(409, '转换尚未完成。')
        return task

    @app.get('/api/delivery/{task_id}')
    def delivery(task_id: str, version: str | None = None):
        return describe(completed(task_id), version)

    @app.get('/api/xml/{task_id}')
    def xml(task_id: str, version: str | None = None):
        return xml_response(completed(task_id), version)
