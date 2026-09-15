"""将小于 49×49 像素的未审查差分原尺寸排列，不省略或自动审核。"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from PIL import Image, ImageChops, ImageDraw, ImageFont

from scripts.web_public_evidence import ROOT, write_json

COLS, ROWS, CELL = 24, 20, 64


def fingerprint(im):
    return hashlib.sha256(str(im.size).encode()+im.tobytes()).hexdigest()


def make_sheet(folder, selected):
    canvas=Image.new('RGB',(COLS*CELL,ROWS*CELL),'#e6ebe9')
    draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',10)
    for index,patch in enumerate(selected):
        x=(index%COLS)*CELL; y=(index//COLS)*CELL
        with Image.open(folder/patch['file']) as im:
            assert max(im.size)<=48
            assert fingerprint(im)==patch['pixel_sha256']
            left=x+(CELL-im.width)//2; top=y+16
            canvas.paste(im,(left,top))
            assert ImageChops.difference(canvas.crop((left,top,left+im.width,top+im.height)),im).getbbox() is None
        draw.text((x+3,y+2),f"P{patch['id']}",font=font,fill='#142923')
    return canvas


def mark_sheets(folder, ledger, numbers):
    manifest=folder/'manifest.json'
    data=json.loads(manifest.read_text())
    by_id={p['id']:p for p in data['patches']}
    for number in numbers:
        entry=ledger['sheets'][str(number)]
        sheet=folder/'tiny'/entry['file']
        assert hashlib.sha256(sheet.read_bytes()).hexdigest()==entry['sha256']
        for item in entry['patches']:
            patch=by_id[item['id']]
            assert patch['pixel_sha256']==item['pixel_sha256']
            with Image.open(folder/patch['file']) as im:
                assert fingerprint(im)==item['pixel_sha256']
            patch['reviewed_at']=time.time()
    write_json(manifest,data)
    return sum(bool(p['reviewed_at']) for p in data['patches'])


def main(args):
    folder=args.root/'visual-deltas'/args.name
    out=folder/'tiny'; out.mkdir(exist_ok=True)
    ledger_path=out/'index.json'
    if ledger_path.exists():
        ledger=json.loads(ledger_path.read_text())
    else:
        data=json.loads((folder/'manifest.json').read_text())
        pending=[p for p in data['patches'] if not p['reviewed_at'] and max(p['size'])<=48]
        ledger={'created':time.time(),'sheets':{},'method':'全部入选小图块原尺寸贴入；像素校验一致；仅明确登记已看的图版。'}
        for start in range(0,len(pending),COLS*ROWS):
            selected=pending[start:start+COLS*ROWS]
            number=start//(COLS*ROWS)+1
            file=f'tiny-{number:05d}.png'
            make_sheet(folder,selected).save(out/file)
            ledger['sheets'][str(number)]={'file':file,'sha256':hashlib.sha256((out/file).read_bytes()).hexdigest(),
                'patches':[{'id':p['id'],'pixel_sha256':p['pixel_sha256']} for p in selected]}
        write_json(ledger_path,ledger)
    if args.mark:
        print(json.dumps({'reviewed_patches':mark_sheets(folder,ledger,args.mark)}))
    else:
        print(json.dumps({'sheets':len(ledger['sheets']),'patches':sum(len(s['patches']) for s in ledger['sheets'].values())}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'reports/web-public-20260915/round-01')
    parser.add_argument('--name',required=True)
    parser.add_argument('--mark',nargs='+',type=int)
    main(parser.parse_args())
