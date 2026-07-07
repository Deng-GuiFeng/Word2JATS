# word2jats — 学术论文 Word → JATS XML 智能结构化转换

> **学术期刊结构化技术创新大赛**（杭州电子科技大学计算机学院 × IMR Press 联合主办）**选题一** 参赛作品。
> 把学术论文的 Word 投稿稿（`.docx`）自动转换成符合 **JATS Journal Publishing DTD v1.3** 的结构化 XML，图片按要求外部存储。

这份 README 是快速上手的门面。完整的背景、领域知识、设计与评测，见 **[`docs/`](docs/README.md)**。

术语小抄：**JATS** = Journal Article Tag Suite，学术出版界通用的期刊全文 XML 标准；**DTD** = 文档类型定义，规定 XML 的合法结构（赛题要求输出必须通过校验）；**OOXML** = Word `.docx` 底层的 XML 格式；**OMML → MathML** = 把 Word 的公式格式转成通用数学标记。这些概念在 [`docs/02-领域基础`](docs/02-领域基础.md) 从零讲起。

## 一句话讲清方法

**大模型只负责"判断结构"（这一段是标题还是正文、这条参考文献各字段的边界在哪），从不负责"生成文字"；所有正文文字都按源块编号从 Word 原文原样取回。**

这样内联格式（斜体/上标/加粗）零损失、内容不会被模型改写或编造——"内容守恒"是**结构性成立**的（文字压根不流经模型），而不是靠事后比对补救。图片和公式只以占位符喂给模型、不喂图像字节，从物理上杜绝"看图造字"。这是本项目区别于"纯模板规则"和"纯大模型改写"两条常见路线的核心。详见 [`docs/04-系统设计`](docs/04-系统设计.md)。

转换管线：

```
docx ─parse─▶ 中间表示 ─serialize─▶ 内容流 ─understand(三个大模型 pass)─▶ 结构判定
     ─assemble─▶ SemanticDoc ─render(机械)─▶ JATS XML ─verify─▶ 内容守恒 + DTD + 结构自洽自检
```

## 当前成绩

用一套自建的三层评测（合法 / 忠实 / 对位）数出剩余缺陷条数（0 = 完美）。10 个样例合计 **80 条缺陷，全部通过 JATS 1.3 DTD 校验**，且温度为 0、同输入逐字节复现：

| 样例 | 01 | 02 | 03 | 04 | 05 | S01 | S02 | S03 | S04 | S05 |
|---|---|---|---|---|---|---|---|---|---|---|
| 缺陷 | 15 | 11 | 17 | **0** | 13 | **0** | 4 | 13 | 4 | 3 |

其中 04、S01 零缺陷；S01–S05 是主办方只给 docx、没给"正确答案"的泛化测试样例。剩下的缺陷大多是"金标准自身不一致"或"评测口径造成的伪差"，继续磨会掉进过拟合——这套评测口径、成绩解读与残余分析见 [`docs/06-评测与成绩`](docs/06-评测与成绩.md)。

> 这个"缺陷数"是我们自建的、用来指导迭代的**代理指标**，不是评委的分。竞赛评审从创新性、落地性等多维度打分（[`docs/01-竞赛与任务`](docs/01-竞赛与任务.md)）。

## 快速开始

需要 Python 3.9+ 和一个大模型 API Key（本项目方法必须用到大模型，默认阿里云百炼 DashScope）。

```bash
# 1) 装依赖（项目自带 .venv，也可新建）
.venv/bin/python -m pip install -r requirements.txt

# 2) 配密钥
cp .env.example .env          # 填入 DASHSCOPE_API_KEY

# 3) 转换一篇论文
PYTHONPATH=src .venv/bin/python -m word2jats convert \
    样例数据/03/初始文件.docx \
    --journal JIN --doi 10.31083/JIN49347 \
    --figures 样例数据/03/figures.zip -o output/

# 4) 跑评测复现成绩（务必用项目 .venv，DTD 校验需要）
PYTHONPATH=scripts .venv/bin/python -m eval.run --llm dashscope

# 5) 跑测试
.venv/bin/python -m pytest
```

命令细节、参数、常见问题见 [`docs/07-安装与使用`](docs/07-安装与使用.md)。

## 项目结构

```
学术期刊结构化技术创新大赛/
├── src/word2jats/        转换器源码（参赛核心作品）：docx → JATS 1.3 XML
│   ├── cli.py pipeline.py    命令行入口与转换编排
│   ├── parse/                docx 解析 → 中间表示（IR），只搬物理结构
│   ├── understand/           理解层：序列化内容流 + 三个大模型 pass + 组装（判结构、不生成文字）
│   ├── semantic/             SemanticDoc 语义模型（理解层与渲染层之间的契约）
│   ├── render/               渲染：SemanticDoc → JATS 树（front/body/back/表/参考文献）
│   ├── build/                机械构件：图片外部化 / OMML→MathML / JATS 元素 / 交叉引用
│   ├── enrich/               期刊元数据查表（journals.yaml）
│   ├── verify/               出口自检：内容守恒 + 结构自洽
│   ├── validate/             DTD 校验 + JATS4R 检查 + 机械兜底修复
│   ├── llm/  model/          大模型客户端+磁盘缓存 / 中间表示 IR
│   └── resources/            JATS 1.3 DTD + OMML2MML.XSL + journals.yaml
├── scripts/
│   ├── eval/                 三层评测（L0 合法 / L1 忠实 / L2 对位 + run/report）
│   └── package_submission.py 打包提交物
├── tests/                pytest：渲染单元 + 评测不变量 + 10 例端到端集成
├── 样例数据/            10 个样例（docx + 结构参考.xml + scope.json + figures）；布局见 说明.md
├── docs/                完整中文文档体系；导航见 docs/README.md
├── baseline-develop/    主办方基线参考代码（Java，非本队作品，仅供对照）
├── requirements.txt  pyproject.toml   依赖清单 / 打包配置
└── .env.example  .gitignore           配置模板 / 忽略规则（.env 存密钥，不入库）
```

> `reports/`（评测输出）、`.venv/`、各类缓存都是**可再生产物**，已列入 `.gitignore`、不纳入版本库，跑相应命令即重新生成。

## 许可

MIT License。核心代码与算法为参赛队原创；JATS DTD 来自 NISO/NLM，`OMML2MML.XSL` 为微软样式表的开源移植（见 `src/word2jats/resources/`）。
