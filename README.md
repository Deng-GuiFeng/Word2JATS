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

用一套自建的三层评测（合法 / 忠实 / 对位）数出剩余缺陷条数（0 = 完美）。

**成绩正在重算中。** 2026-08 做了两件事：14 例金标准经外部独立审查逐例裁决后修订（图片包换回 docx 原字节、表格列模型重做、MathML 按源文重建），评测器本身也做了一轮判别力修复——此前对金标准做 17 个单点破坏（调换作者、地址改挂别人名下、表格单元格对调、`rowspan` 改错、图片字节掉包）只能报出 5 个，现在全部报出，同时消掉了 97 条 id 重命名造成的假阳。旧的"80 条"是老金标准配老评测器的数，已不成立；新数需要重跑转换器才有。

拿现存输出（2026-07-10 生成）配新金标准 + 新评测器重新打分是 277 条，**这只证明旧数失效，不是新成绩**——那批输出是老的，转换器此后未重跑。抽查过的例子：05 有 25 条参考文献的 DOI 没抽出来（核实过 docx 原文确实写了），02 漏了 10 个小节、还把 `bioprosthesis` 写成了 `bioprostheses`，01 整块通讯地址没输出。这些都是过去看不见的真缺陷。

评测口径、判别力验证与成绩解读见 [`docs/06-评测与成绩`](docs/06-评测与成绩.md)；换模型、换温度、逐个拆模块的消融验证见 [`docs/07-消融与方法验证`](docs/07-消融与方法验证.md)（消融结论同样需在新口径下重跑）。

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

# 5) 跑转换 + V1 评测（10 例全量并发，默认后端 dashscope；会调用云端模型）
.venv/bin/python -m scripts.eval_v1
#    两套评测器一起评已有输出（不调模型、零成本）：
.venv/bin/python -m scripts.evalsuite

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
├── scripts/              两套评测器平级并存，同时评估同一批转换输出，判分逻辑互不引用
│   ├── eval_v1/              三层评测（L0 合法 / L1 忠实 / L2 对位）→ 缺陷清单；含消融 ablation
│   ├── eval_v2/              完整语义树严格比对 → 通过判定 + 八维质量向量
│   ├── evalsuite.py          编排层：一条命令跑完两器，出并排小结（本身不含任何评分逻辑）
│   └── package_submission.py 打包提交物
├── tests/                pytest：渲染单元 + 评测不变量与灵敏度 + 端到端集成 + 网页应用 + 守恒/并发
├── 样例数据/            14 例统一布局（docx + 结构参考.xml + figures.zip；上线版本.xml 仅 01–05）
│                        01–05 主样例、S01–S05 补充样例、X01–X04 外部投稿件；口径与来源见 说明.md
│                        样例登记.json = 14 例名单的唯一来源，两套评测器各自读取
├── 消融分析/            消融实验的审计留痕（逐臂原始数据 + 汇总 + 结论.md）
├── docs/                完整中文文档体系（十篇）；导航见 docs/README.md
├── Dockerfile           网页应用容器镜像（API Key 运行时注入，不打进镜像）
├── 初赛提交材料.zip     2026-07-31 提交的初赛材料快照（技术方案 + 当时的可运行原型）
├── references/          主办方给的参考材料：基线代码 baseline-develop/（Java）、JATS 手册、赛事介绍
│                        —— 均非本队作品，体积大且不参与构建，不入库
├── requirements.txt  pyproject.toml   依赖清单 / 打包配置
└── .env.example  .gitignore  LICENSE  配置模板 / 忽略规则（.env 存密钥，不入库）/ 许可证
```

`reports/` 下的分工：`outputs/<标签>/` 是转换输出，**两套评测器共同的评测对象、不挂在任何一方名下**；`eval_v1/<标签>/`、`eval_v2/<标签>/` 各放自己的报告；`evalsuite/<标签>/` 放并排小结；`_llm_cache/` 是转换器的模型缓存（复现靠它）。

> `reports/`（评测输出）、`.venv/`、`webapp/_runs`、各类缓存都是**可再生产物**，已列入 `.gitignore`、不纳入版本库，跑相应命令即重新生成。各样例目录里的 `figures.zip` 是**金标准的图片部分**（字节逐字取自 docx 内嵌媒体）：转换器不读它（图片一律从 docx 内嵌媒体提取），但评测按语义槽位拿它比 SHA-256 —— 图的身份是它的字节，不是文件名。

## 许可

MIT License，全文见 [`LICENSE`](LICENSE)。核心代码与算法为参赛队原创；JATS DTD 来自 NISO/NLM，`OMML2MML.XSL` 为微软样式表的开源移植（见 `src/word2jats/resources/`），网页预览样式表来自 NCBI 公有领域（见 `webapp/vendor/jats/SOURCE.md`）。
