"""恢复定稿 PDF 中两处漏绘的句号，不重新排版、不改 Word 原稿。"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ByteStringObject, ContentStream, FloatObject, NameObject

BASELINE = '7474f3ad0b56899f22338549430f60eeee50634daf5eafffeb69a63dabdd684f'
TARGETS = [(4, '/F3', '公式结构', 495.5, 467.089),
           (8, '/F7', '看', 528.2, 760.389)]


def cmap(font):
    result = {}
    for block in re.findall(rb'beginbfchar(.*?)endbfchar', font['/ToUnicode'].get_data(), re.S):
        for code, value in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            result[bytes.fromhex(code.decode())] = bytes.fromhex(value.decode()).decode('utf-16-be')
    return result


def text(path):
    raw = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
    return re.sub(r'\s+', '', raw)


def restore(source, output):
    assert hashlib.sha256(source.read_bytes()).hexdigest() == BASELINE, '仅处理已确认的定稿版本'
    assert not output.exists(), '输出路径已存在'
    reader = PdfReader(source)
    writer = PdfWriter(clone_from=reader)
    for page_number, expected_font, expected_text, x, y in TARGETS:
        page = reader.pages[page_number]
        maps = {name: cmap(ref.get_object()) for name, ref in page['/Resources']['/Font'].items()}
        period = next(code for code, char in maps['/F3'].items() if char == '。')
        stream = ContentStream(page['/Contents'], reader)
        font, matrix, font_size, hits = None, None, None, []
        for index, (args, op) in enumerate(stream.operations):
            if op == b'Tf':
                font, font_size = args
            elif op == b'Tm':
                matrix = args
            elif op in (b'Tj', b'TJ') and font == expected_font and matrix:
                if abs(float(matrix[-2]) - x) > .001 or abs(float(matrix[-1]) - y) > .001:
                    continue
                strings = args[:1] if op == b'Tj' else args[0]
                decoded = ''
                for value in strings:
                    if isinstance(value, (str, bytes)):
                        raw = value.original_bytes if hasattr(value, 'original_bytes') else bytes(value)
                        decoded += ''.join(maps[font][bytes([b])] for b in raw)
                if decoded == expected_text:
                    assert float(font_size) == 10.5
                    assert stream.operations[index + 1][1] == b'ET'
                    hits.append(index)
        assert len(hits) == 1
        # 在现有行末文本对象中复用同一正文字体的句号；之后立即 ET，其他文字位置不变。
        stream.operations[hits[0] + 1:hits[0] + 1] = [
            ([NameObject('/F3'), FloatObject(10.5)], b'Tf'),
            ([ByteStringObject(period)], b'Tj'),
        ]
        writer.pages[page_number][NameObject('/Contents')] = writer._add_object(stream)
        writer.pages[page_number].compress_content_streams()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer.write(output)
    expected = text(source)
    for before, after in [('数学对象进入公式结构Word', '数学对象进入公式结构。Word'),
                          ('用户查看各入口', '用户查看。各入口')]:
        assert expected.count(before) == 1
        expected = expected.replace(before, after)
    assert text(output) == expected, '文本差异必须恰好为两个句号'
    result = PdfReader(output)
    assert len(result.pages) == 15
    assert set(result.named_destinations) == set(reader.named_destinations)
    return {'pages_changed': [5, 9], 'inserted_characters': '。。',
            'other_text_unchanged': True, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(restore(args.source, args.output), ensure_ascii=False))
