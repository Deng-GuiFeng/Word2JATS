# word2jats — 学术论文 Word → JATS XML 智能结构化转换

> **学术期刊结构化技术创新大赛**（杭州电子科技大学计算机学院 × IMR Press 联合主办）**选题一** 参赛作品。
> 把学术论文的 Word 投稿稿（`.docx`）自动转换成符合 **JATS Journal Publishing DTD v1.3** 的结构化 XML，图片按要求外部存储。

**在线体验：[`https://word2jats.jianglab.work`](https://word2jats.jianglab.work)** —— 浏览器上传 Word 即可拿到 JATS XML，无需本地安装。部署架构与运维见 [`docs/10-部署上线`](docs/10-部署上线.md)。

这份 README 是快速上手的门面。完整的背景、领域知识、设计与评测，见 **[`docs/`](docs/README.md)**。

术语小抄：**JATS** = Journal Article Tag Suite，学术出版界通用的期刊全文 XML 标准；**DTD** = 文档类型定义，规定 XML 的合法结构（赛题要求输出必须通过校验）；**OOXML** = Word `.docx` 底层的 XML 格式；**OMML → MathML** = 把 Word 的公式格式转成通用数学标记。这些概念在 [`docs/02-领域基础`](docs/02-领域基础.md) 从零讲起。

## 一句话讲清方法

**大模型只负责"判断结构"（这一段是标题还是正文、这条参考文献各字段的边界在哪），从不负责"生成文字"；所有正文文字都按源块编号从 Word 原文原样取回。**

这样内联格式（斜体/上标/加粗）零损失、内容也不会被模型改写或编造——本项目把"转换后正文与源稿逐字一致、不增不减不改"这条铁律叫**内容守恒**，它是**结构上就做不到改**（文字压根不流经模型），而不是靠事后比对补救。图片和公式只以占位符喂给模型、不喂图像字节，从物理上杜绝"看图造字"。这是本项目区别于"纯模板规则"和"纯大模型改写"两条常见路线的核心。详见 [`docs/04-系统设计`](docs/04-系统设计.md)。

转换管线：

```
docx ─parse─▶ 中间表示 ─serialize─▶ 内容流 ─understand(三个大模型 pass)─▶ 结构判定
     ─assemble─▶ SemanticDoc ─render(机械)─▶ JATS XML ─verify─▶ 内容守恒 + DTD + 结构自洽自检
```

## 当前成绩

用一套自建的三层评测（合法 / 忠实 / 对位）数出剩余缺陷条数（0 = 完美）。最近一次全量评测，10 个样例合计 **80 条缺陷，全部通过 JATS 1.3 DTD 校验**，温度为 0、同输入靠磁盘缓存逐字节复现：

| 样例 | 01 | 02 | 03 | 04 | 05 | S01 | S02 | S03 | S04 | S05 |
|---|---|---|---|---|---|---|---|---|---|---|
| 缺陷 | 15 | 11 | 17 | **0** | 13 | **0** | 4 | 13 | 4 | 3 |

其中 04、S01 零缺陷；S01–S05 是主办方只给 docx、没给上线版本的泛化测试样例。剩下的缺陷大多是"结构参考自身的取舍空间"或"评测口径造成的分词伪差"，继续磨会掉进过拟合——这套评测口径、成绩解读与残余分析见 [`docs/06-评测与成绩`](docs/06-评测与成绩.md)；换模型、换温度、逐个拆模块的消融验证见 [`docs/07-消融与方法验证`](docs/07-消融与方法验证.md)。

> 这个"缺陷数"是我们自建的、用来指导迭代的**代理指标**，不是评委的分。竞赛评审从落地性、创新性等多维度打分（[`docs/01-竞赛与任务`](docs/01-竞赛与任务.md)）。

## 快速开始

需要 Python 3.9+ 和一个大模型 API Key（本项目方法必须用到大模型，默认阿里云百炼 DashScope）。

```bash
# 1) 建虚拟环境装依赖（DTD 校验要求 lxml ≥ 6.0，别用系统 Python）
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 2) 配密钥
cp .env.example .env          # 填入 DASHSCOPE_API_KEY

# 3) 命令行转换一篇论文
PYTHONPATH=src .venv/bin/python -m word2jats convert \
    样例数据/03/初始文件.docx \
    --journal JIN --doi 10.31083/JIN49347 -o output/

# 4) 或起网页应用「校样工作台」：浏览器上传 docx → 拿到 JATS + 交付前自检报告
.venv/bin/python -m webapp          # 打开 http://127.0.0.1:8000

# 5) 跑评测复现成绩（10 例全量并发，默认后端 dashscope）
PYTHONPATH=scripts .venv/bin/python -m eval.run

# 6) 跑测试
.venv/bin/python -m pytest
```

单篇转换耗时约 **2–4 分钟**（默认 `qwen3.7-plus`，10 例实测 109–252 秒，中位数 139 秒），绝大部分花在等云端模型判结构上。

命令细节、参数、Docker 部署与常见问题见 [`docs/09-安装与使用`](docs/09-安装与使用.md)；挂到公网、大文件分片上传、日常运维见 [`docs/10-部署上线`](docs/10-部署上线.md)。

## 项目结构

```
学术期刊结构化技术创新大赛/
├── src/word2jats/        转换器源码（参赛核心作品）：docx → JATS 1.3 XML
│   ├── cli.py pipeline.py    命令行入口与转换编排
│   ├── parse/                docx 解析 → 中间表示（IR），只搬物理结构
│   ├── understand/           理解层：内容流 + 三个大模型 pass + 组装（判结构、不生成文字）
│   ├── semantic/             SemanticDoc 语义模型（理解层与渲染层之间的契约）
│   ├── render/               渲染：SemanticDoc → JATS 树（front/body/back/表/参考文献）
│   ├── build/                机械构件：图片外部化 / OMML→MathML / JATS 元素 / 交叉引用
│   ├── enrich/               期刊元数据查表（journals.yaml）
│   ├── verify/               出口自检：内容守恒 + 结构自洽
│   ├── validate/             DTD 校验 + 结构自洽诊断 + 机械兜底修复
│   ├── llm/  model/          大模型客户端 + 磁盘缓存 / 中间表示 IR
│   └── resources/            JATS 1.3 DTD + OMML2MML.XSL + journals.yaml
├── webapp/               网页应用「校样工作台」：FastAPI 单服务，上传 docx → JATS + 自检报告
│   ├── app.py                HTTP 端点、进程内任务表、线程池调度（进程内直接调 pipeline.convert）
│   ├── render.py fidelity.py 服务端预览渲染（NCBI 公有领域 XSLT） / 忠实核对口径
│   └── static/  vendor/      原生 JS 前端 / NCBI 预览样式表
├── scripts/
│   ├── eval/                 三层评测（L0 合法 / L1 忠实 / L2 对位 + run/report + 消融 ablation）
│   └── package_submission.py 打包提交物
├── tests/                pytest：渲染单元 + 评测不变量 + 端到端集成 + 网页应用 + 守恒/并发等专项
├── 样例数据/            01–05、S01–S05：10 个赛题样例（docx + 结构参考.xml + scope.json + 上线版本.xml〔仅 01–05〕）
│                        X01–X04：4 份外部真实稿件，只有 docx、无参考，用于兼容性调试；布局见 说明.md
├── 消融分析/            消融实验的审计留痕（逐臂原始数据 + 汇总 + 结论.md）
├── docs/                完整中文文档体系（十篇）；导航见 docs/README.md
├── Dockerfile           网页应用容器镜像（API Key 运行时注入，不打进镜像）
├── 初赛提交材料.zip     2026-07-31 提交的初赛材料快照（技术方案 + 当时的可运行原型）
├── references/          主办方给的参考材料：基线代码 baseline-develop/（Java）、JATS 手册、赛事介绍
│                        —— 均非本队作品，体积大且不参与构建，不入库
├── requirements.txt  pyproject.toml   依赖清单 / 打包配置
└── .env.example  .gitignore  LICENSE  配置模板 / 忽略规则（.env 存密钥，不入库）/ 许可证
```

> `reports/`（评测输出）、`.venv/`、`webapp/_runs`、各类缓存都是**可再生产物**，已列入 `.gitignore`、不纳入版本库，跑相应命令即重新生成。各样例目录里的 `figures.zip` 是当年构建结构参考时的历史中间产物，转换器与评测都已不用它（图片一律从 docx 内嵌媒体提取）。

## 许可

MIT License，全文见 [`LICENSE`](LICENSE)。核心代码与算法为参赛队原创；JATS DTD 来自 NISO/NLM，`OMML2MML.XSL` 为微软样式表的开源移植（见 `src/word2jats/resources/`），网页预览样式表来自 NCBI 公有领域（见 `webapp/vendor/jats/SOURCE.md`）。
