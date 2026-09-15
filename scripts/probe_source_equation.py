"""只在隔离副本中验证复杂 Word 公式的原生排版，不改原稿或转换产物。"""
import argparse
from copy import deepcopy
import io
from pathlib import Path
import subprocess
from zipfile import ZipFile,ZIP_DEFLATED

from lxml import etree
from PIL import Image,ImageChops

ROOT=Path(__file__).resolve().parents[1]
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample',default='X02')
    parser.add_argument('--paragraphs',default='42')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    folder=args.output.resolve();folder.mkdir(parents=True,exist_ok=False)
    original=ROOT/'样例数据'/args.sample/'初始文件.docx'
    with ZipFile(original) as source:
        root=etree.fromstring(source.read('word/document.xml'))
        body=root.find(f'{{{W}}}body')
        paragraphs=body.findall(f'{{{W}}}p')
        keep=[deepcopy(paragraphs[int(n)-1]) for n in args.paragraphs.split(',')]
        section=deepcopy(body.find(f'{{{W}}}sectPr'))
        for reference in list(section):
            if etree.QName(reference).localname in {'headerReference','footerReference'}:section.remove(reference)
        for child in list(body):body.remove(child)
        for child in keep:body.append(child)
        body.append(section)
        with ZipFile(folder/'fragment.docx','w',ZIP_DEFLATED) as out:
            for info in source.infolist():
                out.writestr(info,etree.tostring(root,xml_declaration=True,encoding='UTF-8')
                             if info.filename=='word/document.xml' else source.read(info.filename))
    subprocess.run(['libreoffice','-env:UserInstallation='+(folder/'profile').as_uri(),
                    '--headless','--convert-to','pdf','--outdir',str(folder),str(folder/'fragment.docx')],
                    check=True,timeout=30)
    subprocess.run(['pdftoppm','-png','-r','144',str(folder/'fragment.pdf'),str(folder/'raw')],check=True,timeout=20)
    for index,path in enumerate(sorted(folder.glob('raw-*.png'))):
        image=Image.open(path).convert('RGB')
        bounds=ImageChops.difference(image,Image.new('RGB',image.size,'white')).getbbox()
        if bounds:
            a,b,c,d=bounds
            image.crop((max(0,a-15),max(0,b-15),min(image.width,c+15),min(image.height,d+15))).save(folder/f'page-{index}.png')
    subprocess.run(['pdftotext',str(folder/'fragment.pdf'),'-'],check=True,timeout=10)


if __name__=='__main__':main()
