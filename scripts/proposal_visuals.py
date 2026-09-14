"""生成说明书中的架构图与任务依赖图（工程示意，不表示测量时长）。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '决赛提交/assets'
FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
TEAL, INK, PALE, GREY = '#16676b', '#173b3d', '#edf4f1', '#667771'


def drawing(size):
    im = Image.new('RGB', size, 'white')
    return im, ImageDraw.Draw(im)


def text(d, x, y, value, size=31, fill=INK, anchor='mm'):
    d.text((x, y), value, font=ImageFont.truetype(FONT, size), fill=fill, anchor=anchor)


def box(d, bounds, title, lines=()):
    x1, y1, x2, y2 = bounds
    d.rounded_rectangle(bounds, radius=16, fill=PALE, outline='#c7ddd6', width=2)
    mid = (x1 + x2) / 2
    text(d, mid, y1 + 42, title, 36, TEAL)
    for i, line in enumerate(lines):
        text(d, mid, y1 + 100 + 43*i, line, 29)


def arrow(d, points, fill=TEAL):
    d.line(points, fill=fill, width=4, joint='curve')
    x, y = points[-1]
    px, py = points[-2]
    if abs(x-px) > abs(y-py):
        a = 1 if x > px else -1
        d.polygon([(x,y),(x-16*a,y-9),(x-16*a,y+9)], fill=fill)
    else:
        a = 1 if y > py else -1
        d.polygon([(x,y),(x-9,y-16*a),(x+9,y-16*a)], fill=fill)


def architecture():
    im, d = drawing((2200, 1000))
    nodes = [
        ((40,80,490,365),'原稿解析',('文字与格式、自动编号','表格网格、图片、公式','统一记录地址与对象身份')),
        ((590,80,1040,365),'结构理解',('文首、正文、图表','文献边界、字段与引文','结构角色 + 原文定位线索')),
        ((1140,80,1590,365),'原文定位与组装',('摘抄匹配到字符范围','归并内容角色和实体关系','从 Word 原稿取回内容')),
        ((1690,80,2140,365),'JATS 与媒体生成',('文首、正文、文末结构','统一 ID 与交叉引用','MathML 与原始图片资源')),
    ]
    for bounds,title,lines in nodes:
        box(d,bounds,title,lines)
    for x in (490,1040,1590):
        arrow(d,[(x,220),(x+95,220)])
    arrow(d,[(265,365),(265,445),(1365,445),(1365,365)])
    text(d,510,417,'原文、格式与对象资源',29,GREY)
    arrow(d,[(815,365),(815,530),(1915,530),(1915,365)])
    text(d,1430,502,'文首元信息：局部 JATS 生成与专门核对',29,GREY)
    box(d,(1690,650,2140,890),'自动检查',('JATS 结构、链接与媒体','源文覆盖、输出内容来源'))
    box(d,(895,650,1455,890),'校样与字段修订',('内容预览、原稿对照、问题定位','文章与出版信息修改、版本恢复'))
    box(d,(100,650,660,890),'成果导出',('当前 JATS XML + 图片资源包','转换用量 + 修改记录'))
    arrow(d,[(2140,220),(2175,220),(2175,770),(2140,770)])
    arrow(d,[(1690,770),(1460,770)])
    arrow(d,[(895,770),(665,770)])
    text(d,1100,960,'语义判断、原文取回与编辑处理分别承担明确职责',30,GREY)
    im.save(OUT / '总体架构.png')


def dependencies():
    im,d = drawing((2200,1000))
    # 横向体现依赖先后；纵向为可以同时推进的任务。
    box(d,(35,390,325,590),'原稿清单',('文字、格式与对象',))
    ys = (85,285,485,685)
    labels = [('文首处理',('边界 → 元信息 / 摘要',)),
              ('正文理解',('角色、层级、图表',)),
              ('文献边界',('两路识别 → 分歧裁决',)),
              ('引文识别',('引用位置与目标线索',))]
    for y,(title,lines) in zip(ys,labels):
        box(d,(425,y,925,y+165),title,lines)
        arrow(d,[(325,490),(370,490),(370,y+82),(425,y+82)])
    box(d,(1100,140,1560,330),'角色归并',('文首、正文与边界结果齐备',))
    box(d,(1680,140,2140,330),'文本表格恢复',('按源片段组装行列',))
    box(d,(1100,485,1560,675),'文献字段提取',('各条文献并行处理',))
    box(d,(1680,655,2140,845),'最终组装与检查',('汇合所有必要结果',))
    arrow(d,[(925,165),(1010,165),(1010,200),(1100,200)])
    arrow(d,[(925,367),(1040,367),(1040,245),(1100,245)])
    arrow(d,[(925,567),(1070,567),(1070,290),(1100,290)])
    arrow(d,[(925,600),(1100,600)])
    arrow(d,[(1560,235),(1680,235)])
    arrow(d,[(1910,330),(1910,655)])
    arrow(d,[(1560,580),(1630,580),(1630,720),(1680,720)])
    arrow(d,[(925,767),(1680,767)])
    text(d,1100,935,'箭头表示实际数据依赖；同一转换进程内的模型请求共用并发上限',30,GREY)
    im.save(OUT / '并行依赖.png')


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True, parents=True)
    architecture()
    dependencies()
