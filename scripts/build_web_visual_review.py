"""汇总当前界面截图为人工审阅图版；原截图不变，不自动宣称已审阅。"""
import hashlib
import json
import argparse
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]/'reports/web-next'
OUT=ROOT/'visual-review'


def main():
    global ROOT, OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT,help='本轮独立验收目录；不改写其他轮次证据')
    parser.add_argument('--folders',nargs='+',default=['regression','expanded','supplement','async-edges','real-current'])
    parser.add_argument('--reviewed-sheets',type=int,nargs='+',help='实际查看完图版后登记；只核对既有清单，不重新生成截图')
    args=parser.parse_args()
    ROOT=args.root; OUT=ROOT/'visual-review'
    if args.reviewed_sheets:
        path=OUT/'manifest.json'
        data=json.loads(path.read_text())
        assert set(args.reviewed_sheets)==set(range(1,data['sheets']+1)), '须逐张查看所有图版'
        for row in data['items']:
            assert hashlib.sha256((ROOT/row['file']).read_bytes()).hexdigest()==row['sha256'], '截图在检查后已改变'
            row['reviewed']=True
        data['review']=f"已逐图查看 {data['sheets']} 张图版，覆盖 {data['screenshots']} 张截图（{data['unique_screenshots']} 张不同图）。原尺寸细节复查与具体结论见本轮验收文档；此登记操作不等于自动图像判定。"
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2))
        print(f"Reviewed {len(data['items'])}/{data['screenshots']} screenshots")
        return
    OUT.mkdir(parents=True,exist_ok=True)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
    rows=[]; unique={}
    for folder in args.folders:
        for path in sorted((ROOT/folder).glob('*.png')):
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            with Image.open(path) as im: size=im.size
            row={'file':str(path.relative_to(ROOT)),'sha256':sha,'size':size,'reviewed':False}
            if sha not in unique: unique[sha]=len(unique)
            row['tile']=unique[sha]+1; row['sheet']=unique[sha]//4+1
            rows.append(row)
    representatives={row['tile']:row for row in reversed(rows)}
    for start in range(0,len(representatives),4):
        canvas=Image.new('RGB',(1840,1420),'#e7ecea'); draw=ImageDraw.Draw(canvas)
        for offset in range(4):
            row=representatives.get(start+offset+1)
            if not row: continue
            x=(offset%2)*920; y=(offset//2)*710
            with Image.open(ROOT/row['file']) as source:
                source.thumbnail((900,670))
                canvas.paste(source,(x+10+(900-source.width)//2,y+35))
            draw.text((x+12,y+8),f"#{row['tile']:03d}  {row['file'].split('/')[0]}  {row['size'][0]} x {row['size'][1]}",font=font,fill='#172a26')
        canvas.save(OUT/f'sheet-{start//4+1:02d}.jpg',quality=93)
    (OUT/'manifest.json').write_text(json.dumps({'screenshots':len(rows),'unique_screenshots':len(unique),'sheets':(len(unique)+3)//4,'items':rows,'review':'待人工逐图确认'},ensure_ascii=False,indent=2))
    print(f'{len(rows)} files; {len(unique)} unique; {(len(unique)+3)//4} sheets')


if __name__=='__main__': main()
