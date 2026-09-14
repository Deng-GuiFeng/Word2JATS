"""将 DOCX 的链接与书签同步到导出的 PDF，验证目标后原位保存。

部分 LibreOffice 版本导出保留了链接文字却未生成链接注释。本步骤以
Word 的实际链接为依据，用 Poppler 的文本坐标定位，不改变 PDF 正文和排版。
仅用于交付文档，额外依赖 pypdf 与 pdftotext，不是转换原型的运行依赖。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from lxml import etree as E
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.generic import ArrayObject, FloatObject, NameObject, NullObject, Fit

NS = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'wp':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}


def compact(text):
    return re.sub(r'[\s\u00ad\u200b]', '', text)


def text_locations(pdf):
    xml = subprocess.check_output(['pdftotext','-bbox-layout',str(pdf),'-'])
    root = E.fromstring(xml)
    result = []
    for page in root.findall('.//{*}page'):
        chars, boxes = '', []
        # Poppler 的 block 顺序可能把页眉穿插进图表标题；按真实行坐标阅读。
        lines = []
        for word in sorted(page.findall('.//{*}word'),key=lambda w:float(w.get('yMin'))):
            y = float(word.get('yMin'))
            if not lines or y - float(lines[-1][0].get('yMin')) >= 3:
                lines.append([])
            lines[-1].append(word)
        words = [word for line in lines for word in sorted(line,key=lambda w:float(w.get('xMin')))]
        for word in words:
            token = compact(word.text or '')
            box = tuple(float(word.get(k)) for k in ('xMin','yMin','xMax','yMax'))
            chars += token
            boxes.extend([box] * len(token))
        result.append((chars, boxes))
    return result


def find_text(pages, label):
    needle = compact(label)
    if not needle:
        raise ValueError('链接或书签名称为空')
    matches = []
    for index,(text,boxes) in enumerate(pages):
        for match in re.finditer(re.escape(needle),text):
            lines = {}
            for box in boxes[match.start():match.end()]:
                # 同一行的西文、中文基线可能相差约 1 pt。
                key = next((k for k in lines if abs(k-box[1]) < 3), box[1])
                lines.setdefault(key, []).append(box)
            rects = [(min(b[0] for b in bs),min(b[1] for b in bs),
                      max(b[2] for b in bs),max(b[3] for b in bs)) for bs in lines.values()]
            matches.append((index,rects))
    return matches


def finalize(docx, pdf):
    pages = text_locations(pdf)
    reader = PdfReader(pdf)
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        if '/Annots' in page:
            page[NameObject('/Annots')] = ArrayObject(
                a for a in page['/Annots'] if a.get_object().get('/Subtype') != '/Link')
    source_bookmarks, source_links = {}, set()
    with zipfile.ZipFile(docx) as archive:
        parts = ['word/document.xml'] + [n for n in archive.namelist() if re.fullmatch(r'word/footer\d+\.xml',n)]
        for part in parts:
            root = E.fromstring(archive.read(part))
            relpath = str(Path(part).parent / '_rels' / (Path(part).name + '.rels'))
            rels = ({r.get('Id'):r.get('Target') for r in E.fromstring(archive.read(relpath))}
                    if relpath in archive.namelist() else {})
            for link in root.findall('.//w:hyperlink',NS):
                label = ''.join(link.xpath('.//w:t/text()',namespaces=NS))
                anchor = link.get('{'+NS['w']+'}anchor')
                target = '#' + anchor if anchor else rels[link.get('{'+NS['r']+'}id')]
                source_links.add((label,target))
            for bookmark in root.findall('.//w:bookmarkStart',NS):
                name = bookmark.get('{'+NS['w']+'}name')
                paragraph = bookmark.getparent()
                label = ''.join(paragraph.xpath('.//w:t/text()',namespaces=NS))
                image_height = 0
                if not label:
                    extent = paragraph.find('.//wp:extent',NS)
                    if extent is not None:
                        image_height = int(extent.get('cy')) / 12700
                    label = ''.join(paragraph.getnext().xpath('.//w:t/text()',namespaces=NS))
                matches = find_text(pages,label)
                if not matches:
                    raise ValueError('PDF 中未找到书签：' + name + ' / ' + label)
                page,rects = matches[-1] if name.startswith('section_') else matches[0]
                top = float(reader.pages[page].mediabox.height) - max(0,rects[0][1] - image_height - 12)
                source_bookmarks[name] = page,top
                destination = ArrayObject([writer.pages[page].indirect_reference,NameObject('/XYZ'),
                                           FloatObject(0),FloatObject(top),NullObject()])
                if name not in reader.named_destinations:
                    writer.add_named_destination_array(name,destination)
    added = 0
    covered = set()
    for label,target in sorted(source_links):
        matches = find_text(pages,label)
        if not matches:
            raise ValueError('PDF 中未找到链接文字：' + label)
        for page,rects in matches:
            height = float(reader.pages[page].mediabox.height)
            for x0,y0,x1,y1 in rects:
                rect = (x0,height-y1,x1,height-y0)
                signature = (page, tuple(round(v,2) for v in rect),target)
                if signature in covered:
                    continue
                covered.add(signature)
                if target.startswith('#'):
                    dest,top = source_bookmarks[target[1:]]
                    annotation = Link(rect=rect,target_page_index=dest,fit=Fit.xyz(left=0,top=top))
                else:
                    annotation = Link(rect=rect,url=target)
                inserted = writer.add_annotation(page,annotation)
                if target.startswith('#'):
                    # 本地跳转必须引用 PDF 页对象，不能使用仅适用于远程跳转的页序号。
                    inserted[NameObject('/Dest')] = ArrayObject([
                        writer.pages[dest].indirect_reference,NameObject('/XYZ'),
                        FloatObject(0),FloatObject(top),NullObject()])
                added += 1
    # 写入临时文件并检查全部链接；通过后才替换导出的 PDF。
    with tempfile.NamedTemporaryFile(dir=pdf.parent,suffix='.pdf',delete=False) as temp:
        temporary = Path(temp.name)
    try:
        writer.write(temporary)
        checked = PdfReader(temporary)
        assert len(checked.pages) == len(reader.pages)
        assert all(a.extract_text() == b.extract_text() for a,b in zip(reader.pages,checked.pages))
        assert set(source_bookmarks) <= set(checked.named_destinations)
        page_ids = {page.indirect_reference.idnum for page in checked.pages}
        for page in checked.pages:
            for item in page.get('/Annots',[]):
                annotation = item.get_object()
                if annotation.get('/Subtype') == '/Link' and '/Dest' in annotation:
                    assert annotation['/Dest'][0].idnum in page_ids
        assert added > 0
        temporary.replace(pdf)
    finally:
        temporary.unlink(missing_ok=True)
    return {'pages':len(reader.pages),'bookmarks':len(source_bookmarks),
            'source_links':len(source_links),'annotations_added':added}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('docx',type=Path)
    parser.add_argument('pdf',type=Path)
    parser.add_argument('--page-map', type=Path, help='将实测书签页码保存为目录回填数据')
    args = parser.parse_args()
    print(finalize(args.docx,args.pdf))
    if args.page_map:
        reader = PdfReader(args.pdf)
        args.page_map.write_text(json.dumps({name:reader.get_destination_page_number(destination) + 1
                                for name,destination in reader.named_destinations.items()},
                               ensure_ascii=False,indent=2), encoding='utf-8')
