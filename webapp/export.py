"""以论文标识命名转换成果；XML 和配套图片是交付主体。"""
import re
from pathlib import Path

from lxml import etree


def safe_stem(value, limit=64):
    value = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', str(value))
    value = re.sub(r'\s+', ' ', value).strip(' ._')[:limit].rstrip(' .')
    if value.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        value = '_' + value
    return value


def names(task):
    result = task['result']
    article_id = safe_stem(result.get('article_id') or '')
    # 含数字的出版标识（如 JIN49347）比“初始文件”更能识别稿件。
    identifier = article_id if re.search(r'\d', article_id) else ''
    if not identifier:
        original = safe_stem(Path(task.get('filename', '').replace('\\', '/')).stem)
        if original.lower() not in {'', '初始文件', '稿件', '文档', 'document', 'input', 'upload', 'article'}:
            identifier = original
    if not identifier:
        try:
            root = etree.fromstring(Path(result['candidate_xml']).read_bytes(),
                                    etree.XMLParser(resolve_entities=False, no_network=True))
            identifier = safe_stem(''.join(root.xpath('./front/article-meta/title-group/article-title//text()')))
        except (OSError, etree.XMLSyntaxError, KeyError):
            pass
    identifier = identifier or ('稿件-' + task['task_id'][:8])
    return {'download_filename': 'Word2JATS-' + identifier + '.zip',
            'xml_filename': identifier + '.xml'}


def media_files(directory, xml_bytes):
    """只交付 XML 实际引用的本地资源，不递归塞入历史预览、报告与其他输出。"""
    directory = Path(directory).resolve()
    root = etree.fromstring(xml_bytes, etree.XMLParser(resolve_entities=False, no_network=True))
    paths = set(root.xpath('//@*[local-name()="href" and namespace-uri()="http://www.w3.org/1999/xlink"]'))
    for relative in sorted(paths):
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', relative) or relative.startswith('#'):
            continue
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError('图片资源缺失或路径无效：' + relative)
        yield path, path.relative_to(directory).as_posix()
