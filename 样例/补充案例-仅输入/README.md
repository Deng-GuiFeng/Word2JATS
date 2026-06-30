# 补充案例（仅输入，委员会 2026-06 提供）

> 来源：官方上传的 `补充案例（只有输入）.zip`（已解压并删除原压缩包）。
> **与 `样例/样例1..5/` 的根本区别：这里只有输入，没有金标准输出。**

## 目录结构

```
补充案例-仅输入/
├── 选题一/                      ← 我们的赛题(word2xml):5 篇全新论文 docx,仅输入
│   ├── 样例1.docx  Screening and Prenatal Diagnosis of Spinal Muscular Atrophy…
│   ├── 样例2.docx  Signs o' the Times. The Quiet Revolution of Molecular Pathology…
│   ├── 样例3.docx  Evaluating the Performance of Large Language Models GPT-4, Claude 3…
│   ├── 样例4.docx  Elevated Circulating HMGB1 Levels as a Potential Biomarker…
│   └── 样例5.docx  Early Cardiac Workload and Long-Term Prognosis After Intracerebral Hemorrhage…
└── 选题二/                      ← 另一个赛道(xml2pdf,非本队赛题):XML 输入 + 图片,仅留存备查
    └── 样例1..5/  (初始文件.xml + figure(s)/...)
```

## 用途与处理原则

- **选题一是我们的赛题。这 5 篇是"全新、未见过"的论文**(与原始 5 样例 md5 不同、标题各异),正是检验泛化能力的held-out测试集。
- 因为**没有金标准输出**,优化分两条线:
  1. **泛化压力测试(主线、无需标签、零风险)**:对 5 篇跑我们的转换器,看 DTD 校验 / 结构一致性检查 / 出版规范检查是否通过,定位在未见论文上暴露的、**能泛化的**缺陷并修复。
  2. **谨慎生成参考标签(辅线)**:用多代理交叉核验生成高质量参考 XML,用于量化质量、发现自动检查漏掉的问题。**标签只作核对依据,不作盲目拟合目标**,以免循环自证、entrench 错误。
- 选题二不是我们的赛题,仅完整留存,不投入优化。

## 数据管理约定

- 原始 docx 一字未改(已用 md5 校验搬运无损);本目录只读。
- 生成的参考标签、评测产物放在 `output/` 或 `docs/03-评测/`,不污染本输入目录。
