"""从同批实际转换结果生成说明书实验表与完整工作表，不内置实验数字。"""
from __future__ import annotations
import argparse
import csv
from decimal import Decimal
import io
import json
from pathlib import Path

from scripts.eval_v1.samples import get
from scripts.proposal_metrics import aggregate

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'reports/proposal-experiments'
METRICS = [
    ('title', '题名'), ('authors', '作者姓名'), ('author_order', '作者顺序'),
    ('affiliations', '作者单位'), ('author_affiliations', '作者—单位关系'),
    ('correspondence', '通讯作者—邮箱关系'), ('orcid', '作者—ORCID 关系'),
    ('dates', '稿件日期'), ('abstract_text', '摘要内容'),
    ('abstract_structure', '摘要分段与分节'), ('keywords', '关键词'),
    ('sections', '章节层级与标题'), ('section_order', '章节顺序'),
    ('definition_items', '术语—释义条目'), ('back_sections', '文末声明与附属章节'),
    ('figures', '图对象'), ('figure_captions', '图注内容'),
    ('tables', '表对象'), ('table_captions', '表题内容'), ('table_notes', '表注内容'),
    ('table_cells', '表格网格位置与内容'), ('media', '图片资源及图表归属'),
    ('native_math', '原生公式数学树'), ('equation_image_resources', '公式图片资源'),
    ('references', '参考文献条目覆盖'), ('reference_fields', '文献著录字段'),
    ('citations', '正文—文献引用关系'), ('figure_table_links', '正文—图表引用关系'),
]
TOKEN_COLUMNS = [('input_tokens','输入'), ('output_tokens','输出'),
                 ('cache_hit_tokens','缓存命中'), ('cache_miss_tokens','缓存未命中'),
                 ('total_tokens','总量')]
# 2026-09-14 报告计费口径：Qwen 全部任务统一按用户指定的 Plus 费率；
# DeepSeek 使用官方高峰单价。均为元 / 百万 Token，不代表账户实扣金额。
RATES = {
    'Qwen': tuple(map(Decimal, ('1.6', '0.32', '6.4'))),
    'DeepSeek': tuple(map(Decimal, ('2', '0.04', '8'))),
}


def usage_cost(usage, configuration):
    keys = ('cache_miss_tokens', 'cache_hit_tokens', 'output_tokens')
    for key in (*keys, 'input_tokens', 'total_tokens'):
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise ValueError('用量必须是接口返回的非负整数：' + key)
    if usage['input_tokens'] != usage['cache_miss_tokens'] + usage['cache_hit_tokens']:
        raise ValueError('输入总量与缓存分项不相等')
    if usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']:
        raise ValueError('总量与输入输出之和不相等')
    return sum(Decimal(usage[key]) * rate for key, rate in zip(keys, RATES[configuration])) / Decimal(1000000)


def number(value):
    return f'{value:,}'


def percent(value):
    return f'{value * 100:.2f}%' if value is not None else '—'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '|' + '|'.join('---' for _ in headers) + '|',
                      *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]]) + '\n'


def sections(qrows, drows):
    q, d = aggregate(qrows), aggregate(drows)
    qt, dt = q['time_seconds'], d['time_seconds']
    qc, dc = usage_cost(q['usage'], 'Qwen'), usage_cost(d['usage'], 'DeepSeek')
    parts = ['### 6.2 稿件组成与转换质量', '',
             f'本组共 {len(qrows)} 篇稿件。表1列出各篇的内容规模；图与表按论文中的逻辑对象计数，'
             '公式栏分别列出原生公式数与图像载体部件数。Word 文件中的图片大小、文献数量与排表方式各不相同，'
             '因此同时保留逐篇结果与整体汇总。', '', '表1　实验稿件的内容组成', '']
    def count(row, metric):
        return row['metrics'].get(metric, {}).get('expected', 0)
    parts += [table(['编号','文件 / MiB','词项数','作者 / 章节','图 / 表','公式 / 图像部件','文献'], [
        [r['sample'], f"{Path(get(r['sample']).docx).stat().st_size / 1024**2:.2f}",
         number(count(r,'text_retention')), f"{count(r,'authors')} / {count(r,'sections')}",
         f"{count(r,'figures')} / {count(r,'tables')}",
         f"{count(r,'native_math')} / {count(r,'equation_image_resources')}", count(r,'references')]
        for r in qrows]), '',
        '表2　全文分项结构结果（单元格为“正确匹配数 / 输出数；召回率”）', '']
    quality = []
    for key, label in METRICS:
        qm, dm = q['metrics'].get(key, {}), d['metrics'].get(key, {})
        expected = qm.get('expected', 0)
        if not expected and not qm.get('actual') and not dm.get('actual'):
            continue
        assert expected == dm.get('expected', 0), (key, expected, dm)
        def cell(m):
            return f"{number(m.get('correct',0))} / {number(m.get('actual',0))}；{percent(m.get('recall'))}"
        quality.append([label, number(expected), cell(qm), cell(dm)])
    parts += [table(['评价项','应有数','Qwen','DeepSeek'], quality), '',
              '正确匹配数与输出数之比即精确率；召回率反映应有内容中得到正确结构化表示的比例。'
              '文献字段包括作者名单、题名、刊名、年份、卷期、页码或文章位置、DOI、PMID及出版者；'
              '同一语义字段的标签分拆与等价写法先统一，再进行匹配。引用关系在对齐的可见段落和字符位置上核对实际目标，'
              '区间引用展开为各条目标关系。', '',
              '表3　整体内容保留与标准符合性', '',
              table(['指标','Qwen','DeepSeek'], [
                  ['全文词项保留率', percent(q['metrics']['text_retention']['recall']),
                   percent(d['metrics']['text_retention']['recall'])],
                  ['JATS DTD 合法文档', f"{q['dtd_valid']} / {len(qrows)}", f"{d['dtd_valid']} / {len(drows)}"],
                  ['已导出媒体的原字节一致性',
                   f"{sum(r['media_from_source'] for r in qrows)} / {sum(r['media_references'] for r in qrows)}",
                   f"{sum(r['media_from_source'] for r in drows)} / {sum(r['media_references'] for r in drows)}"],
              ]), '',
              '两种配置在细粒度结构加工上各有侧重：DeepSeek 的表格网格召回率为 '
              f"{percent(d['metrics']['table_cells']['recall'])}，Qwen 为 {percent(q['metrics']['table_cells']['recall'])}；"
              f"Qwen 的文献字段召回率为 {percent(q['metrics']['reference_fields']['recall'])}，"
              f"DeepSeek 为 {percent(d['metrics']['reference_fields']['recall'])}。"
              '网格指标要求单元格内容与行列位置同时正确，文献字段指标要求条目身份与字段内容同时对应，'
              '因此能够进一步区分对象识别之后的加工质量。', '',
              '### 6.3 转换耗时', '',
              '表4　逐篇转换耗时及实际模型请求次数', '',
              table(['编号','Qwen / 秒','DeepSeek / 秒','Qwen 请求数','DeepSeek 请求数'], [
                  [a['sample'], f"{a['wall_seconds']:.2f}", f"{b['wall_seconds']:.2f}", a['requests'], b['requests']]
                  for a,b in zip(qrows,drows)
              ] + [['平均',f"{qt['mean']:.2f}",f"{dt['mean']:.2f}",'—','—'],
                   ['中位数',f"{qt['median']:.2f}",f"{dt['median']:.2f}",'—','—'],
                   ['合计', f"{sum(r['wall_seconds'] for r in qrows):.2f}",
                    f"{sum(r['wall_seconds'] for r in drows):.2f}",
                    sum(r['requests'] for r in qrows),sum(r['requests'] for r in drows)]]), '',
              f"在本组稿件及运行条件下，DeepSeek 的平均转换时间为 Qwen 的 "
              f"{dt['mean'] / qt['mean'] * 100:.1f}%，平均耗时缩短 {(1 - dt['mean']/qt['mean']) * 100:.1f}%。"
              '这一结果比较的是相同转换流程和并发上限下的完整处理时间，并非单次模型响应速度。'
              '逐篇数据保留稿件内容规模、通信等待与重试共同造成的时间差异；中位数反映本组稿件的典型处理时间。', '',
              '### 6.4 Token 用量与调用成本', '',
              '以下用量均来自模型服务实际响应中的 usage 字段，'
              '包含全部任务与重试中已返回的用量。'
              '输入总量等于缓存命中与缓存未命中之和，总量为输入与输出之和；'
              '缓存命中已包含在输入中，不再重复相加。接口未单列缓存未命中量时，以输入总量减缓存命中量取得，'
              '两种配置使用相同统计口径。表中用量单位为 Token，费用单位为元。', '',
              '表5　调用成本折算单价（元 / 百万 Token）', '',
              table(['配置','未命中输入','缓存命中输入','输出'], [
                  [name] + list(map(str, rates)) for name, rates in RATES.items()]), '',
              '成本按“未命中输入量 × 未命中单价 + 缓存命中输入量 × 命中单价 + 输出量 × 输出单价”计算，'
              '再除以 1,000,000。Qwen 各任务采用表5所列的统一折算单价；DeepSeek 采用'
              '[官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)的高峰时段单价，'
              '价格口径截至 2026 年 9 月 14 日。所列费用是按实测用量折算的模型调用成本，不含本地部署与人工校样费用。'
              '逐篇金额保留四位小数，合计先汇总未舍入金额再取舍。', '']
    for i, (name, rows, agg) in enumerate([('Qwen',qrows,q),('DeepSeek',drows,d)], 6):
        assert agg['usage_complete'], '用量不完整，不能生成完整用量表'
        parts += [f'表{i}　{name} 配置逐篇用量与调用成本', '',
                  table(['编号'] + [label for key,label in TOKEN_COLUMNS] + ['成本 / 元'], [
                      [r['sample']] + [number(r['usage'][key]) for key,label in TOKEN_COLUMNS] +
                      [f"{usage_cost(r['usage'], name):.4f}"] for r in rows
                  ] + [['合计'] + [number(agg['usage'][key]) for key,label in TOKEN_COLUMNS] +
                       [f"{usage_cost(agg['usage'], name):.4f}"]]), '']
    costs = [r['usage'].get('costs_returned') for r in qrows + drows]
    if any(costs):
        parts += ['接口返回的费用金额：' + json.dumps(costs,ensure_ascii=False), '']
    parts += [f"按表5口径，{len(qrows)} 篇稿件的 Qwen 调用成本合计 {qc:.4f} 元，篇均 {qc/len(qrows):.4f} 元；"
              f"DeepSeek 合计 {dc:.4f} 元，篇均 {dc/len(drows):.4f} 元，合计较 Qwen 减少 {(1-dc/qc)*100:.1f}%。"
              '这些费用包含本次真实命中缓存所带来的价格差异；不同配置的 Token 分词与缓存机制并不相同，'
              '因此采用各自实测用量及明示单价计算，不以 Token 总量单独判断成本高低。', '']
    return '\n'.join(parts)


def save_working(qrows, drows):
    DEST.mkdir(exist_ok=True,parents=True)
    report = '# 双模型实验结果工作表\n\n' + sections(qrows, drows)
    report += '\n## 公式载体与输出容器\n\n'
    report += '原生公式与图像载体已按源内容单独核对。下表补充列出公式容器数量，用于查看不同载体的去向；'
    report += '行内与行间组织可能不同，容器数量本身不作为公式正确率。\n\n'
    report += table(['编号','原稿原生公式','原稿公式图片部件','结构参照公式容器','Qwen 输出容器','DeepSeek 输出容器'],[
        [a['sample'],a['formula_inventory']['native_math'],
         a['metrics']['equation_image_resources']['expected'],
         a['formula_inventory']['reference_formula_containers'],
         a['formula_inventory']['output_formula_containers'],b['formula_inventory']['output_formula_containers']]
        for a,b in zip(qrows,drows)])
    for name, rows in [('Qwen',qrows),('DeepSeek',drows)]:
        report += '\n## ' + name + '：逐篇分项结果\n\n'
        for start in range(0,len(METRICS),6):
            selected = METRICS[start:start+6]
            report += table(['编号']+[label for key,label in selected], [
                [r['sample']] + [f"{r['metrics'].get(key,{}).get('correct',0)} / "
                                f"{r['metrics'].get(key,{}).get('expected',0)} / "
                                f"{r['metrics'].get(key,{}).get('actual',0)}" for key,label in selected]
                for r in rows]) + '\n'
        report += '各项依次为正确匹配数 / 应有数 / 输出数。\n'
    (DEST/'双模型实验结果工作表.md').write_text(report,encoding='utf-8')
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['模型','编号','评价项','正确匹配','应有','输出','召回率','精确率'])
    for name,rows in [('Qwen',qrows),('DeepSeek',drows)]:
        for r in rows:
            for key,label in METRICS+[('text_retention','全文文字保留')]:
                m=r['metrics'].get(key,{})
                writer.writerow([name,r['sample'],label,*[m.get(k,0) for k in ('correct','expected','actual')],
                                 m.get('recall'),m.get('precision')])
    (DEST/'逐篇内容指标.csv').write_text(buffer.getvalue(),encoding='utf-8-sig')
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['配置', '编号', '耗时/秒'] + [label for key,label in TOKEN_COLUMNS] + ['折算成本/元'])
    for name, rows in [('Qwen',qrows),('DeepSeek',drows)]:
        for row in rows:
            writer.writerow([name,row['sample'],row['wall_seconds']] +
                            [row['usage'][key] for key,label in TOKEN_COLUMNS] + [usage_cost(row['usage'],name)])
    (DEST/'逐篇用量与成本.csv').write_text(buffer.getvalue(),encoding='utf-8-sig')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qwen',required=True)
    p.add_argument('--deepseek',required=True)
    p.add_argument('--final',action='store_true')
    args=p.parse_args()
    if args.final:
        batches=[json.loads((ROOT/'reports/outputs'/tag/'timing.json').read_text())
                 for tag in (args.qwen,args.deepseek)]
        assert all(not b['failures'] and b.get('source_unchanged_during_run') for b in batches)
        assert batches[0]['source_sha256']==batches[1]['source_sha256'], '两配置的转换方法版本不同'
        assert all(r['mode']=='cold' for b in batches for r in b['samples'].values()), '最终表须来自真实调用'
    q=json.loads((DEST/(args.qwen+'.json')).read_text())['samples']
    d=json.loads((DEST/(args.deepseek+'.json')).read_text())['samples']
    assert len(q)==len(d)==14 and [r['sample'] for r in q]==[r['sample'] for r in d]
    for a,b in zip(q,d):
        assert a['input_sha256']==b['input_sha256']
    save_working(q,d)
    template=ROOT/'决赛提交/技术方案说明书.md'
    text=template.read_text()
    if args.final:
        q=[r for r in q if not r['sample'].startswith('X')]
        d=[r for r in d if not r['sample'].startswith('X')]
    content=sections(q,d)
    start='<!-- EXPERIMENT_RESULTS -->'
    end='<!-- END_EXPERIMENT_RESULTS -->'
    before,sep,tail=text.partition(start)
    assert sep
    if end in tail:
        tail=tail.partition(end)[2]
    updated=before+start+'\n\n'+content+'\n'+end+tail
    dest=template if args.final else ROOT/'docs/11-重构工程/技术方案说明书-双模型结果工作稿.md'
    if args.final:
        assert not any(k in updated for k in ('X01','X02','X03','X04'))
    dest.write_text(updated,encoding='utf-8')
    if args.final:
        method=text.split('### 6.1 实验设置与评价方法\n',1)[1].split(start,1)[0].strip()
        (ROOT/'docs/06-评测与成绩.md').write_text('# 06 · 质量、效率与模型用量\n\n'+method+'\n\n'+content+
            '\n## 复现实验\n\n使用 `scripts/finals_run.py` 分别以 dashscope 和 deepseek 运行同一批稿件，'
            '再用 `scripts/proposal_metrics.py` 核对全文分项内容。实际输出、媒体、逐篇用量与运行说明随原型提供。'
            '安装及命令示例见 [安装与使用](09-安装与使用.md)。\n',encoding='utf-8')
    print(dest)
    print(DEST/'双模型实验结果工作表.md')


if __name__=='__main__':
    main()
