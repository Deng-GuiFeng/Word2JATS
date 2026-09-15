"""连续帧的无损差分审查：保留每帧映射，逐像素重建验证，不抽帧、不自动判通过。"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from PIL import Image, ImageChops, ImageDraw, ImageFont

from scripts.web_public_evidence import ROOT, write_json


def delta(previous, current, full=False):
    """返回包含全部差异像素的矩形；贴回后必须逐像素等于原帧。"""
    if previous is None or previous.size != current.size or full:
        box=(0,0,*current.size)
    else:
        box=ImageChops.difference(previous,current).getbbox()
        if box is None:
            return None,None
    patch=current.crop(box)
    if previous is not None and previous.size==current.size:
        restored=previous.copy()
        restored.paste(patch,box)
        assert ImageChops.difference(restored,current).getbbox() is None
    else:
        assert box==(0,0,*current.size)
    return box,patch


def spatial_delta(previous, current, gap=24, padding=12):
    """沿完全不变的空带拆分差异；保留上下文像素并验证整帧精确重建。"""
    if previous is None or previous.size != current.size:
        return [((0, 0, *current.size), current.copy())]
    difference = ImageChops.difference(previous, current)
    channels = difference.split()
    mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    if mask.getbbox() is None:
        return []

    def spans(projection):
        changed = [i for i, yes in enumerate(projection) if yes]
        if not changed:
            return []
        result = []
        start = last = changed[0]
        for position in changed[1:]:
            if position - last > gap:
                result.append((start, last + 1))
                start = position
            last = position
        return result + [(start, last + 1)]

    width, height = current.size
    pieces = []
    for top, bottom in spans(mask.getprojection()[1]):
        band = mask.crop((0, top, width, bottom))
        for left, right in spans(band.getprojection()[0]):
            box = (max(0, left-padding), max(0, top-padding),
                   min(width, right+padding), min(height, bottom+padding))
            pieces.append((box, current.crop(box)))
    restored = previous.copy()
    for box, patch in pieces:
        restored.paste(patch, box)
    assert ImageChops.difference(restored, current).getbbox() is None
    return pieces


def category(size):
    width,height=size
    if width<=128 and height<=128:return 'micro'
    if width<=850 and height<=180:return 'strip'
    return 'full'


LAYOUT={'micro':(10,10,180,170),'strip':(2,8,900,240),'full':(2,2,900,680)}


def build(args,out):
    manifest=out/'manifest.json'
    if manifest.exists():
        return json.loads(manifest.read_text())
    patches=[]; known={}; frames=[]; streams=[]
    paths=sorted({path for prefix in args.prefix for path in (args.root/prefix).rglob('events.jsonl')})
    for path in paths:
        previous=None; previous_file=None; last_action=None; by_file={}; count=0
        stream=str(path.relative_to(args.root))
        for line in path.open():
            row=json.loads(line)
            if row['kind']!='frame':continue
            count+=1
            file=path.parent/row['file']
            rel=str(file.relative_to(args.root))
            source=file.read_bytes()
            assert hashlib.sha256(source).hexdigest()==row['sha256'],rel
            if rel in by_file:
                frames.append({'stream':stream,'number':row['frame'],'file':rel,'same_as':by_file[rel],
                               'source_sha256':row['sha256'],'action':row['action']})
                with Image.open(file) as opened:previous=opened.convert('RGB')
                previous_file=rel;last_action=row['action']
                continue
            with Image.open(file) as opened:current=opened.convert('RGB')
            spatial = getattr(args, 'spatial', False)
            if spatial:
                pieces = spatial_delta(previous, current)
            else:
                box, patch = delta(previous,current,full=row['action']!=last_action)
                pieces = [] if patch is None else [(box, patch)]
            regions = []
            for box, patch in pieces:
                fingerprint=hashlib.sha256(str(patch.size).encode()+patch.tobytes()).hexdigest()
                if fingerprint not in known:
                    patch_id=len(patches);known[fingerprint]=patch_id
                    target=out/'patches'/f'{patch_id:07d}.png'
                    patch.save(target)
                    patches.append({'id':patch_id,'file':str(target.relative_to(out)),'size':patch.size,
                                    'pixel_sha256':fingerprint,'category':category(patch.size),
                                    'example_frame':rel,'example_box':box,'action':row['action'],
                                    'reviewed_at':None})
                patch_id=known[fingerprint]
                regions.append({'box':box,'patch':patch_id})
            item={'stream':stream,'number':row['frame'],'file':rel,'previous_file':previous_file,
                  'source_sha256':row['sha256'],'pixel_sha256':hashlib.sha256(current.tobytes()).hexdigest(),
                  'size':current.size,'action':row['action']}
            if spatial:
                item['regions'] = regions
            else:
                item.update(regions[0] if regions else {'box':None,'patch':None})
            by_file[rel]=len(frames);frames.append(item)
            previous=current;previous_file=rel;last_action=row['action']
        streams.append({'file':stream,'frames':count})
        print(json.dumps({'stream':stream,'frames':count,'patches':len(patches)}),flush=True)
    sheets={}
    for kind,(cols,rows,_,_) in LAYOUT.items():
        selected=[p for p in patches if p['category']==kind]
        for index,p in enumerate(selected):p['sheet']=index//(cols*rows)+1;p['tile']=index%(cols*rows)
        sheets[kind]=(len(selected)+cols*rows-1)//(cols*rows)
    data={'created':time.time(),'streams':streams,'frames':frames,'patches':patches,'sheets':sheets,
          'spatial':getattr(args,'spatial',False),
          'method':'每一连续帧源文件哈希校验；差分贴回后逐像素一致；完全相同的变化图块共享审查，所有原帧保留。生成不代表视觉通过。'}
    write_json(manifest,data)
    return data


def main(args):
    out=args.root/'visual-deltas'/args.name
    (out/'patches').mkdir(parents=True,exist_ok=True)
    data=build(args,out)
    if args.mark:
        for patch in data['patches']:
            if patch['category']==args.category and patch['sheet'] in args.mark:
                with Image.open(out/patch['file']) as im:
                    assert hashlib.sha256(str(im.size).encode()+im.tobytes()).hexdigest()==patch['pixel_sha256']
                patch['reviewed_at']=time.time()
        write_json(out/'manifest.json',data)
        print(json.dumps({'reviewed_patches':sum(bool(p['reviewed_at']) for p in data['patches']),
                          'total_patches':len(data['patches']),'frames':len(data['frames'])}))
        return
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',14)
    kinds=[args.category] if args.category else list(LAYOUT)
    for kind in kinds:
        cols,rows,width,height=LAYOUT[kind]
        for number in range(args.start,min(args.start+args.count,data['sheets'][kind]+1)):
            filename=out/f'{kind}-{number:05d}.png'
            if filename.exists():continue
            canvas=Image.new('RGB',(cols*width,rows*height),'#e6ebe9');draw=ImageDraw.Draw(canvas)
            for patch in data['patches']:
                if patch['category']!=kind or patch['sheet']!=number:continue
                x=(patch['tile']%cols)*width;y=(patch['tile']//cols)*height
                with Image.open(out/patch['file']) as im:
                    im.thumbnail((width-12,height-40))
                    canvas.paste(im,(x+6+(width-12-im.width)//2,y+34))
                draw.text((x+6,y+4),f"P{patch['id']} {patch['size'][0]}x{patch['size'][1]}",font=font,fill='#142923')
                draw.text((x+6,y+18),Path(patch['example_frame']).name,font=font,fill='#142923')
            canvas.save(filename)
    print(json.dumps({'frames':len(data['frames']),'patches':len(data['patches']),'sheets':data['sheets']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'reports/web-public-20260915/round-01')
    parser.add_argument('--name',required=True)
    parser.add_argument('--prefix',nargs='+',default=[])
    parser.add_argument('--category',choices=list(LAYOUT))
    parser.add_argument('--start',type=int,default=1)
    parser.add_argument('--count',type=int,default=20)
    parser.add_argument('--mark',nargs='+',type=int)
    parser.add_argument('--spatial',action='store_true',help='沿不变空带拆分差异；跨动作沿用已关联的前帧上下文')
    main(parser.parse_args())
