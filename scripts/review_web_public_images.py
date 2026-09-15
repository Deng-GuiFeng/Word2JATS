"""为本轮全部截图/连续帧建立逐图审查清单；不会自动标记视觉通过。"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFont

from scripts.web_public_evidence import ROOT, write_json


def main(args):
    out=args.root/'visual-review'/args.name
    out.mkdir(parents=True,exist_ok=True)
    manifest=out/'manifest.json'
    if args.mark:
        data=json.loads(manifest.read_text())
        for number in args.mark:
            assert 1<=number<=data['sheets']
            for row in data['items']:
                if row['sheet']==number:
                    assert hashlib.sha256((args.root/row['file']).read_bytes()).hexdigest()==row['sha256']
                    row['reviewed_at']=time.time()
        write_json(manifest,data)
        print(json.dumps({'reviewed':sum(bool(r.get('reviewed_at')) for r in data['items']),
                          'total':len(data['items'])}))
        return
    if not manifest.exists():
        rows=[];unique={}
        for prefix in args.prefix:
            base=args.root/prefix
            assert base.is_dir(),base
            files=[]
            if args.kind in {'all','screenshots'}: files.extend(base.rglob('screenshots/*.png'))
            if args.kind in {'all','frames'}: files.extend(base.rglob('frames/*.jpg'))
            for path in sorted(files):
                sha=hashlib.sha256(path.read_bytes()).hexdigest()
                if sha not in unique: unique[sha]=len(unique)
                with Image.open(path) as im: size=im.size
                tile=unique[sha]
                rows.append({'file':str(path.relative_to(args.root)),'sha256':sha,'size':size,
                             'tile':tile,'sheet':tile//args.tiles+1,'reviewed_at':None})
        write_json(manifest,{'items':rows,'unique':len(unique),'tiles':args.tiles,
                    'sheets':(len(unique)+args.tiles-1)//args.tiles,'created':time.time(),
                    'note':'只登记实际打开查看的图版；生成不表示通过。'})
    data=json.loads(manifest.read_text())
    representatives={row['tile']:row for row in data['items']}
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',15)
    for number in range(args.start,min(args.start+args.count,data['sheets']+1)):
        filename=out/f'sheet-{number:05d}.jpg'
        if filename.exists(): continue
        cols=2 if data['tiles']<=4 else 3
        width=900 if cols==2 else 600
        height=660 if cols==2 else 440
        canvas=Image.new('RGB',(cols*width,((data['tiles']+cols-1)//cols)*height),'#e6ebe9')
        draw=ImageDraw.Draw(canvas)
        for i in range(data['tiles']):
            tile=(number-1)*data['tiles']+i
            if tile not in representatives: continue
            row=representatives[tile]
            x,y=(i%cols)*width,(i//cols)*height
            with Image.open(args.root/row['file']) as im:
                im.thumbnail((width-12,height-35))
                canvas.paste(im,(x+6+(width-12-im.width)//2,y+30))
            draw.text((x+8,y+8),f"{tile+1} | {row['file'].split('/')[0]} | {Path(row['file']).name[:40]}",font=font,fill='#172a26')
        canvas.save(filename,quality=94)
    print(json.dumps({'files':len(data['items']),'unique':data['unique'],'sheets':data['sheets'],
                      'built_from':args.start,'built_to':min(args.start+args.count-1,data['sheets'])}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'reports/web-public-20260915/round-01')
    parser.add_argument('--name',required=True)
    parser.add_argument('--prefix',nargs='+',default=[])
    parser.add_argument('--kind',choices=['all','screenshots','frames'],default='all')
    parser.add_argument('--tiles',type=int,choices=[4,9,12],default=4)
    parser.add_argument('--start',type=int,default=1)
    parser.add_argument('--count',type=int,default=20)
    parser.add_argument('--mark',nargs='+',type=int)
    main(parser.parse_args())
