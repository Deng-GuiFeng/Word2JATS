"""保留不能拆开的源公式版式；只处理明确的独立复合公式与两行文字分式。

不识别或改写公式。源段落在隔离 Word 副本中按原格式排版，生成 PNG；
原字符仍写入 JATS alt-text，源对象与派生图的关系单独登记。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree

from . import model as sm
from ..model.source import OBJECT_REPLACEMENT, SourceDocument, SourceText

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
RECIPE='word-formula-layout-v1'


def candidate_groups(source):
    nodes=[n for n in source.nodes if n.kind=='para' and n.part=='document' and n.parent is None]
    groups=[]
    for index,node in enumerate(nodes):
        if (len(node.objects)>=2
                and re.fullmatch(r'\s*\(?\d+\)?\s*',node.text.replace(OBJECT_REPLACEMENT,''))):
            objects=[source.occurrence(a.occ_id) for a in node.objects]
            if (any('/anchor[' in o.properties.get('xml_path','') for o in objects)
                    and any(o.kind=='ole' for o in objects)):
                groups.append((node.node_id,))
        if node.objects or index+1>=len(nodes):
            continue
        following=nodes[index+1]
        if (following.objects or '=' not in node.text
                or not re.search(r'\bEquation\s+\d+\s*$',node.text)
                or not any(s.run.underline for s in node.run_spans)
                or not re.fullmatch(r'[ \t]{5,}[\wρμ() +−–*/-]{1,24}',following.text)
                or re.search(r'[A-Za-z]{4,}',following.text)
                or len(following.text.strip().split())>2
                or following.order!=node.order+1):
            continue
        groups.append((node.node_id,following.node_id))
    return groups


def fragment_docx(blob,node_ids,source):
    with ZipFile(io.BytesIO(blob)) as original:
        root=etree.fromstring(original.read('word/document.xml'),
            etree.XMLParser(resolve_entities=False,no_network=True))
        body=root.find(f'{{{W}}}body')
        paragraphs=body.findall(f'{{{W}}}p')
        keep=[]
        for node_id in node_ids:
            match=re.fullmatch(r'/document/body\[1\]/p\[(\d+)\]',source.node(node_id).properties.get('xml_path',''))
            if not match:
                raise ValueError('原生公式只支持明确的顶层段落地址')
            keep.append(deepcopy(paragraphs[int(match[1])-1]))
        section=deepcopy(body.find(f'{{{W}}}sectPr'))
        if section is not None:
            for child in list(section):
                if etree.QName(child).localname in {'headerReference','footerReference','lnNumType'}:
                    section.remove(child)
        for child in list(body):
            body.remove(child)
        body.extend(keep)
        if section is not None:
            body.append(section)
        output=io.BytesIO()
        with ZipFile(output,'w',ZIP_DEFLATED) as result:
            for info in original.infolist():
                data=original.read(info.filename)
                if info.filename=='word/document.xml':
                    data=etree.tostring(root,xml_declaration=True,encoding='UTF-8')
                # 排版副本不请求外部链接。媒体与嵌入公式仍用包内原字节。
                elif info.filename.endswith('.rels'):
                    rels=etree.fromstring(data,etree.XMLParser(resolve_entities=False,no_network=True))
                    for rel in list(rels):
                        if rel.get('TargetMode')=='External':rels.remove(rel)
                    data=etree.tostring(rels,xml_declaration=True,encoding='UTF-8')
                result.writestr(info,data)
        return output.getvalue()


def _run(command,timeout):
    process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        return process.wait(timeout=timeout)==0
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        process.wait()
        return False


def rasterize_fragment(fragment,cache_dir):
    """有界、隔离的原生排版；工具不存在或转换失败就保留既有结构。"""
    lo=shutil.which('libreoffice');pdftoppm=shutil.which('pdftoppm')
    if not lo or not pdftoppm or len(fragment)>64*1024*1024:
        return None
    cache_dir=Path(cache_dir);cache_dir.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(RECIPE.encode()+fragment).hexdigest()
    target=cache_dir/(key+'.png')
    if target.is_file():
        return target.read_bytes()
    try:
        with tempfile.TemporaryDirectory(prefix='formula-',dir=cache_dir) as tmp:
            folder=Path(tmp);(folder/'fragment.docx').write_bytes(fragment)
            if not _run([lo,'-env:UserInstallation='+(folder/'profile').as_uri(),
                    '--headless','--convert-to','pdf','--outdir',str(folder),
                    str(folder/'fragment.docx')],30):return None
            pdf=folder/'fragment.pdf'
            if not pdf.is_file() or pdf.stat().st_size>16*1024*1024:return None
            if not _run([pdftoppm,'-f','1','-l','2','-png','-r','144',str(pdf),str(folder/'page')],15):return None
            pages=list(folder.glob('page-*.png'))
            if len(pages)!=1 or pages[0].stat().st_size>16*1024*1024:return None
            from PIL import Image,ImageChops
            with Image.open(pages[0]) as image:
                if image.width*image.height>10_000_000:return None
                image=image.convert('RGB')
                bounds=ImageChops.difference(image,Image.new('RGB',image.size,'white')).getbbox()
                if not bounds:return None
                a,b,c,d=bounds
                image=image.crop((max(0,a-12),max(0,b-12),min(image.width,c+12),min(image.height,d+12)))
                buffer=io.BytesIO();image.save(buffer,format='PNG')
            staged=folder/'ready.png';staged.write_bytes(buffer.getvalue());os.replace(staged,target)
            return buffer.getvalue()
    except (OSError,ValueError,etree.XMLSyntaxError):
        return None


def _walk(value):
    yield value
    if isinstance(value,SourceDocument):return
    if is_dataclass(value):
        for field in fields(value):yield from _walk(getattr(value,field.name))
    elif isinstance(value,(tuple,list)):
        for child in value:yield from _walk(child)


def _block_sources(block,document):
    if not isinstance(block,(sm.Paragraph,sm.Formula)):return set()
    nodes=set()
    inline={f.entity_id:f for f in document.inline_formulas}
    for value in _walk(block):
        if isinstance(value,SourceText):nodes.update(r[0] for r in value.ranges)
        if isinstance(value,sm.InlineFormula):
            nodes.update(_block_sources(inline[value.formula_id],document))
        if isinstance(value,sm.InlineGraphic):
            nodes.add(document.source.occurrence(value.occurrence_id).node_id)
        if isinstance(value,sm.Formula):
            for occurrence in (value.image_occurrence,value.omml_occurrence):
                if occurrence:nodes.add(document.source.occurrence(occurrence).node_id)
    return nodes


def prepare_source_layout(document,docx_path,cache_dir,*,rasterizer=rasterize_fragment):
    groups=candidate_groups(document.source)
    if not groups:return []
    blob=Path(docx_path).read_bytes();digest=hashlib.sha256(blob).hexdigest()
    if digest!=document.source.metadata.get('source_sha256'):
        raise ValueError('公式排版文件与解析源文件摘要不符')
    records=[]
    protected={target for value in _walk(document) if isinstance(value,sm.CrossReference)
               for target in value.target_ids}
    removed_inline=set()

    def replace_blocks(blocks):
        blocks=[replace(b,blocks=replace_blocks(b.blocks)) if isinstance(b,(sm.Section,sm.Glossary)) else b for b in blocks]
        for group in groups:
            wanted=set(group)
            matches=[i for i,b in enumerate(blocks) if _block_sources(b,document)&wanted]
            if not matches or matches!=list(range(min(matches),max(matches)+1)):continue
            selected=blocks[min(matches):max(matches)+1]
            sets=[_block_sources(b,document) for b in selected]
            if set.union(*sets)!=wanted or any(not s or not s<=wanted for s in sets):continue
            identities={getattr(v,'entity_id',None) for b in selected for v in _walk(b)}
            inline_ids={v.formula_id for b in selected for v in _walk(b) if isinstance(v,sm.InlineFormula)}
            if (identities|inline_ids)&protected:continue
            # 一个行内实体在片段之外仍被引用时，不移动其唯一展示。
            outside=[b for i,b in enumerate(blocks) if i not in matches]
            if any(isinstance(v,sm.InlineFormula) and v.formula_id in inline_ids for v in _walk(outside)):continue
            fragment=fragment_docx(blob,group,document.source)
            image=rasterizer(fragment,cache_dir)
            if not image:continue
            ranges=[];occurrences=[]
            for node_id in group:
                node=document.source.node(node_id);start=0
                for position,char in enumerate(node.text):
                    if char==OBJECT_REPLACEMENT:
                        if position>start:ranges.append((node_id,start,position))
                        start=position+1
                if start<len(node.text):ranges.append((node_id,start,len(node.text)))
                occurrences.extend(a.occ_id for a in node.objects)
            graphic=sm.SourceLayoutGraphic(group,digest,image,SourceText(tuple(ranges)),tuple(occurrences))
            formula=sm.Formula('source-layout:'+':'.join(group),'source-layout',source_layout=graphic)
            blocks[min(matches):max(matches)+1]=[formula]
            removed_inline.update(inline_ids)
            records.append({'nodes':list(group),'source_sha256':digest,'png_sha256':hashlib.sha256(image).hexdigest(),'recipe':RECIPE})
        return tuple(blocks)

    document.body=replace_blocks(document.body)
    # inline_formulas 保存的是被正文引用的实体，不能留下已合并的孤立公式。
    still_used={v.formula_id for v in _walk(document.body) if isinstance(v,sm.InlineFormula)}
    document.inline_formulas=tuple(f for f in document.inline_formulas if f.entity_id not in removed_inline or f.entity_id in still_used)
    document.validate()
    return records
