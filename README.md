# word2jats — 学术论文 Word → JATS XML 智能结构化转换

> 学术期刊结构化技术创新大赛 **组别一（选题一：word2xml）** 参赛作品
> 把学术论文 Word(.docx) 自动转换成符合 **JATS Journal Publishing DTD v1.3** 的结构化 XML，并把图片单独抽出来另存。
>
> 术语小抄（首次出现，下文不再逐一解释）：**JATS** = Journal Article Tag Suite，出版界通用的期刊文章全文 XML 标签标准；**DTD** = 文档类型定义，规定 XML 的合法结构（赛题要求输出必须通过校验）；**OMML → MathML** = 把 Word 内置的公式格式转成通用数学标记；**mixed-citation / element-citation** = JATS 两种参考文献写法（混排文本 / 结构化字段）；**ORCID** = 研究者的全球唯一数字身份码；**front / back** = JATS 文章的前置元数据区 / 后置区（致谢、声明、参考文献）。

---

## 1. 这是什么

把一篇 Word 论文一键转成出版级 JATS XML，覆盖竞赛要求的全部模块：

| 模块 | 支持情况 |
|------|----------|
| 标题层级 | ✅ 嵌套 `<sec>` 树 + 章节号 + 层级 id（S1/S1.SS1/…） |
| 作者信息（姓名/单位/ORCID） | ✅ `contrib`+`name`、`aff`、`contrib-id[orcid]`、通讯作者、共同贡献 |
| 关键词 | ✅ `kwd-group` |
| 摘要 | ✅ 结构化（Background/Methods/…）与非结构化 |
| 图片 | ✅ **单独抽出另存**：从文档里抽出内嵌图 → 转 JPG → `{article-id}/fig-0N.jpg` → 用 `<graphic>` 引用 |
| 列表 | ✅ 段落/列表渲染 |
| 数学公式 | ✅ **OMML → MathML3**（`disp-formula`/`inline-formula`） |
| 参考文献 | ✅ **搜索式确定性切分**结构化为 `element-citation`（10 样例 492/522 条），对不齐整条落回 `mixed-citation`；难例可交 LLM 守恒兜底 |
| 交叉引用 | ✅ 正文里的 `Fig. N`/`Table N`/`[n]` → 自动生成 `<xref>` 链接 |

**核心方法 = 内容守恒不变量下的"确定性优先 + LLM 只判结构"**

正确性的真值只有一个：**忠实于源 docx**。本赛题是结构化技术——文字本来就在 docx 里，任务是给已有文字判定正确的 JATS 结构标签，而不是产生文字。因此**绝不用模型从图片里"读"字（OCR），绝不扩写编造**，并由此推出分工：

1. **确定性管线承担质量主体**：解析 + 分类 + 构建，只搬运原文、天然守恒。参考文献**搜索式确定性切分**（在 DOI 前搜最靠右的著录尾部 `年;卷(期):页`，尾部之前按句读切作者/标题/刊名——每个字段都是原文子串，对不齐整条落回 mixed-citation）；图片表**确定性外部化**为 `<table-wrap><graphic>`（不 OCR 重建）；制表符表**确定性切 Tab** 还原。确定性档（`--llm off`）10 样例总缺陷 4316→356（降 91.8%，见 `docs/02-开发/03-迭代日志.md` R17）。
2. **LLM 只判结构、不生成文字**（可选增强）：
   - **管线内难例兜底**：书籍/会议录/APA 等确定性切不动的参考文献交 LLM 从原文切分，带**内容守恒护栏**——返回的 title/source/作者姓名归一化后必须是原文子串，否则整条拒绝。
   - **`--agent` 核对/修复闭环**：渲染页面图（`soffice` + `pdftoppm`，纯 CPU，dpi=120）作**人眼真值**，配上 docx 标题样式与源文本为证据，修复器就地修复——**章节结构**（据样式重排层级，只移动已有块、不重写）、**作者归属**（LLM 只判单位；ORCID/通讯/共同贡献/邮箱走确定性解析，同姓不混，详见 `docs/01-设计/02-智能循环架构.md` §2.B）、**后置声明拆分**（裸声明句归入命名 `<back>` 小节）、**内容补全**（漏作者重抽，新列表须涵盖既有作者；漏表/漏图/漏式仅记录 trace）。每步 DTD 校验 + 内容守恒校验，任一不过即回滚；全过程留痕。
   - 实测 +LLM+agent **只好不坏**：10 样例总缺陷 356→228，逐样例只降不升（样例1 93→43、样例2 53→28 等），且无新增编造（`reports/eval/llm_agent`）。

> **交付口径**：提交的输出用 **+LLM+agent 档**（总缺陷 356→228，更好且无新增编造）；**确定性档**（356，零 LLM/GPU 依赖、秒级）作离线兜底——落地（稳定可交付）与创新（LLM 只判结构、收难例）兼顾。

> **两轮重构的关键纠正**：R15 去掉了早期"结构问题查出却丢弃"的白名单行为（章节/作者/声明由修复器实际修复）；R17 删除了"VLM 看图重建表"的 OCR 路径（从图片读字必造字——样例2 实测编造 617 个源文档没有的词），内容补全现只保留漏作者重抽（`REPAIRABLE={extract_authors}`）。系统的硬约束只有两条：
> 1. **最终必须 DTD 合法**（JATS Journal Publishing 1.3）——赛题硬要求；
> 2. **不编造源文档没有的内容**（内容守恒）——正确性底线，配守恒校验兜底。

**其它特点**：
- **本地多模态模型**：本机 GPU 上跑本地 Qwen 多模态模型（VLM），驱动闭环的取证（渲染页清点），全程本地、不花外网钱；另支持云端 LLM（DeepSeek/DashScope）与 CrossRef 联网补文献（补 DOI/刊名全称）。
- **优雅降级**：模型/渲染不可用时直接交确定性管线的输出，仍 DTD 合规、可交付（质量主体在确定性，只是难例得不到 LLM 的进一步收敛）。
- **不依赖固定的 Word 样式**：根据内容本身判断每段是什么角色，投稿文档样式名缺失或乱用也能处理。

> 完整档命令：`python -m word2jats convert <docx> --llm local --agent`（见 §2.4）。
> 方法体系详见 [`docs/01-设计/01-架构设计.md`](docs/01-设计/01-架构设计.md)（闭环细节见 [`docs/01-设计/02-智能循环架构.md`](docs/01-设计/02-智能循环架构.md)），评测见 [`docs/03-评测/评测体系设计.md`](docs/03-评测/评测体系设计.md)。

---

## 2. 快速开始

### 2.1 环境准备（Python ≥ 3.9）

```bash
# 1) 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 2) 安装依赖
pip install -r requirements.txt
# 或作为包安装（提供 word2jats 命令）：pip install -e .
```

### 2.2 转换一篇论文

```bash
PYTHONPATH=src python -m word2jats convert \
    样例数据/03/初始文件.docx \
    --journal JIN \
    --doi 10.31083/JIN49347 \
    --figures 样例数据/03/figures.zip \
    -o output
```

输出：
- `output/JIN49347.xml`     —— JATS 1.3 XML
- `output/JIN49347/fig-0N.jpg` —— 抽出来另存的图片

终端会打印各模块的统计和 **DTD 校验结果**。

### 2.3 参数说明

| 参数 | 说明 |
|------|------|
| `docx`（位置参数） | 输入 Word 文件路径（必填） |
| `-o, --out-dir` | 输出目录（默认 `output`） |
| `--journal` | 期刊 id（RCM/JIN/HSF…）；不填时尝试从 `--doi` 推断 |
| `--doi` | 文章 DOI，如 `10.31083/JIN49347`（决定 article-id 和图片路径前缀） |
| `--figures` | 外部图片包（zip 或目录）；不填时从 docx 内嵌图片提取 |
| `--llm` | `off`(默认,纯确定性档——质量主体,合规可交付) / `local`(本机 Qwen VLM) / `deepseek` / `dashscope` |
| `--agent` | **开启 LLM 修复闭环(收敛结构难例)**:渲染页取证 → 修复器就地修复 → 收敛。需配合 `--llm`(如 `--llm local --agent`) |
| `--agent-rounds` | Agent 闭环最大轮数(默认 3) |
| `--crossref` | 联网用 CrossRef 给参考文献补 DOI / 刊名全称 |
| `--no-validate` | 跳过 DTD 校验 |

> `--journal`/`--doi` 对应出版系统分配的外部元数据（docx 里通常没有）。不提供这两项时程序照样会生成结构完整的 XML，只是对应字段留空或填占位符。

### 2.4 完整档:确定性 + LLM 难例兜底 + 修复闭环

在确定性管线的基础上,难例参考文献走 LLM 守恒兜底,并进入**修复闭环**:渲染取证 → 修复器就地修复 → 循环收敛:
```bash
# 先把本地推理服务起起来(sglang,端口 30000),再:
PYTHONPATH=src python -m word2jats convert <docx> --llm local --agent --crossref ...
```
- 默认连本机 `http://localhost:30000/v1`(可用环境变量 `LOCAL_LLM_BASE_URL` 改);渲染用系统 `soffice` + `pdftoppm`(纯 CPU,dpi=120)。
- 闭环全过程留痕落盘 `{article-id}.agent-trace.json`(逐轮:每个修复器看了什么证据、判断、是否生效、为何不生效),便于复盘。评测产物(逐样例逐类的缺陷清单 + 转换输出)落在 [`reports/eval/<tag>/`](reports/eval/),方法见 §3。

**云端 LLM(无本地 GPU 时)**:
```bash
cp .env.example .env          # 填入 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY
PYTHONPATH=src python -m word2jats convert <docx> --llm deepseek ...
```
所有模型调用结果**按内容哈希缓存到磁盘**,重复运行不再额外花钱、便于复现。
`--llm off`(默认)纯确定性档即产出合规、可交付的 XML(质量主体);`--llm local --agent` 在其上进一步收敛难例(实测只好不坏,见 `docs/02-开发/03-迭代日志.md` R17)。

---

## 3. 评测

**评测不给项目打一个分数,而是完整、可重复、每条有源头地,找出"输出 XML"离"把这篇 docx 正确结构化"还差在哪,产出一份逐类的缺陷清单,作为修复迭代的依据。** 评测回路里**没有 AI**、**不出加权总分**——单一数字会掩盖具体差异、无法指导修复;唯一的"分数"是剩余缺陷条数(0 = 满分)。权威设计见 [`docs/03-评测/评测体系设计.md`](docs/03-评测/评测体系设计.md)。

### 3.1 三个对象:先把"在评什么"钉死

正确性的真值只有一个:**忠实于源 docx**。据此区分三个对象(不再用"金标准"):

- **源 docx**(`初始文件.docx`):内容的唯一权威。
- **上线版本**(委员会给 01–05 的发表 XML):= 忠实结构化 **+ 出版层编辑加工**(分配 DOI、补收发日期、把无编号标题排成"1./2.1"、新增源里没有的声明节、整表拆分重编号、给 ORCID 加认证标记……)。**它不是评测目标**,只作 house-style 模板 / 抽全性核对 / B 档旁证。
- **结构参考**(`样例数据/<key>/结构参考.xml`):= "把 docx 忠实结构化 + 补 B 档"的理想产物(**A+B、不含 C**),是评测唯一的对照物。它经多代理起草、逐疑点法官辩论后**人工冻结**进库,冻结后评测只读、不改。同目录 `scope.json` 记录该样例的 B 档清单与已知编辑加工。

判据是 **A/B/C 三档**,不是逐字节对上线版:

- **A 档**:docx 里直接就有的内容,该抽对(标题、作者、单位、ORCID、正文、真表、公式、文献原文)。
- **B 档**:docx 没有,但靠查表 / 外部库 / 出版惯例能得到、就该做(期刊元数据、文献 DOI/刊名全称补全、章节编号、声明拆分)。
- **C 档**:从输入得不到的纯编辑加工(改写标题摘要、整条替换文献),**保留原文、不算缺陷**。
- **A、B 没做对 = 真缺陷;C 档 = 人为修订,从对照物里剔除。**

### 3.2 三层确定性评测(全确定性,同输入同结果)

一个输出是否正确,拆成三件互相独立、各有各对照物的事:

- **L0 合法**(`scripts/eval/validity.py`,不看参考):DTD 校验(JATS Journal Publishing 1.3)+ 一批 JATS4R 出版规范规则,分 error / warning / info 三级。规则如:公式须含机读式(`mml:math`/`tex-math`)、ORCID 须完整 URL、`id` 全局唯一、`xref/@rid` 全部闭合(无悬空)、license 须带链接、裸表不应携多余 `frame`/`rules` 属性等。
- **L1 忠实**(`fidelity.py`,对源 docx):守"只加结构、不改内容"。对 docx 与输出各取**词多重集**比对,抓真丢失(docx 有、输出无)与真编造(输出有、docx 无)——允许清单由结构参考确定性推出(参考同样不承载/同样补的一律不计),不靠手编;外加图片逐字节 md5 比对。
- **L2 对位**(`structure.py`,对结构参考):比对前先归一化(C14N + 等价折叠)消除假差异,再按**语义键**(非位置、非编号)分类对齐,逐类(front / 作者 / 单位 / 摘要 / 关键词 / 章节 / 图 / 表 / 公式 / 参考文献 / 交叉引用 / back)产出命中 / 漏标 / 多标计数 + 缺陷清单。**覆盖守门**(`_coverage_gate`)遍历"参考 ∪ 输出"两棵树,凡不在元素全集内的类型即报"未覆盖",杜绝整类静默漏检(10 例现零未覆盖)。参考文献**按字段比**(作者集 / title / source / year / vol / fpage / lpage),容器用 mixed 还是 element 不单独当评分轴;**B 档补全覆盖率**(DOI/ORCID/链接)单列、不计入结构分。

### 3.3 运行与产物

```bash
# 评测(默认确定性档 --llm off,不吃 GPU;对照物永远是冻结的 结构参考.xml)
PYTHONPATH=scripts .venv/bin/python -m eval.run --llm off

# 消融:逐个关闭转换器组件(热启动 / 各修复器 / Agent 视觉闭环),用"剩余缺陷条数"量化贡献
PYTHONPATH=scripts .venv/bin/python -m eval.ablation --llm dashscope --samples 03,05

# 评测器自身的单元测试(72 通过):test_eval.py 锁评测器不变量 + test_integration.py 遍历 10 例真跑
.venv/bin/python -m pytest -q
```

产物落在 `reports/eval/<tag>/eval.{json,txt,html}`(逐样例、逐类,无总分)。参考核定与审查留痕归档于 [`评测产物/参考审查留痕/`](评测产物/参考审查留痕/) 和 [`评测产物/结构参考留痕/`](评测产物/结构参考留痕/)。

### 3.4 当前残差(R17 后)

原最大根因"参考文献几乎全用 mixed-citation"已在 R17 修复(搜索式确定性切分,492/522 条结构化)。当前确定性档剩 **356 条缺陷**(`reports/eval/deterministic`),按处置分三类(逐条见 `docs/02-开发/04-优化清单.md`):

- **LLM territory**:参考文献难例(书籍/会议录/APA,已接守恒兜底)、章节/声明长尾;
- **确定性可做**:表格多余 `frame`/`rules` 属性、`<aff>` 放置位置、交叉引用长尾;
- **度量伪差 / 金标准人工判断,不宜追**:公式表示差异(display 括号已折 mfenced,残余为语义键展开的 near-miss,数学内容一致)、制表符表列数(金标准系人工取舍)、个别 xref id 命名(金标准自身不一致)。

现行评测每轮都会把这些重新量化出来,供修复迭代追踪。

---

## 4. 项目结构

顶层每一个目录/文件的作用(目录整洁,无临时/过时产物):

```
学术期刊结构化技术创新大赛/
├── src/word2jats/          ★ 转换器源码(参赛核心作品):docx → JATS 1.3 XML
│   ├── cli.py  pipeline.py     入口与编排
│   ├── parse/                  OOXML 解析(docx 读取 / run / 样式)
│   ├── classify/               语义识别(每段是什么:标题 / 作者 / 正文 / 文献…)
│   ├── build/                  JATS 构建(front / body / 图 / 表 / 公式 / 文献 / xref)
│   ├── enrich/                 外部数据(journals 查表 / CrossRef / 难例文献 LLM 守恒兜底)
│   ├── agent/                  LLM 修复闭环(只判结构):取证 + 修复器 + loop 编排
│   ├── llm/  model/            LLM/VLM 客户端+缓存 / 中间表示 IR
│   ├── validate/               DTD 校验
│   └── resources/              JATS 1.3 DTD + OMML2MML.XSL + journals.yaml
├── scripts/
│   ├── eval/                   三层确定性评测(L0 validity + L1 fidelity + L2 structure + run/ablation/report)
│   └── package_submission.py   打包提交物到 dist/
├── tests/                  pytest 72:转换器端到端+DTD+修复器护栏 + 评测器 4 条不变量
├── 样例数据/              10 个样例(输入 docx + 结构参考.xml + scope.json + figures);布局见 说明.md
├── 评测产物/              结构参考的核定与审查辩论留痕(证明参考做扎实、可审计)
├── docs/                   完整中文文档体系(00 调研 / 01 设计 / 02 开发 / 03 评测);导航见 docs/README.md
├── baseline-develop/      委员会提供的基线参考代码(Java,非本队作品,仅供对照)
├── ppt_build/  *.pptx  报名海报.jpg   竞赛演示材料(项目介绍/决赛答辩 PPT + 海报 + PPT 构建源)
├── JATSv1.3 完整手册.pdf   JATS 1.3 标签手册(离线查阅参考)
├── requirements.txt  pyproject.toml   依赖清单 / 打包配置
└── .env.example  .gitignore   配置模板 / 忽略规则(.env 存密钥,不入库)
```

> 说明:`reports/`(评测输出)、`dist/`(打包产物)、`.venv/`、各类缓存均为**可再生产物**,已列入 `.gitignore`、不纳入版本库,运行相应命令即重新生成。

详细架构见 [`docs/01-设计/01-架构设计.md`](docs/01-设计/01-架构设计.md)。

---

## 5. 已知限制

**残留真缺陷**(三层评测据源确认、诚实披露,确定性档共 356 条,逐条见 `reports/eval/deterministic` 缺陷清单、处置归类见 `docs/02-开发/04-优化清单.md`):

- **参考文献难例**(最大项):书籍/会议录/APA 等体例确定性锚点切不动,按设计落回 `mixed-citation`(可开 `--llm` 走守恒兜底进一步结构化)。
- **公式**:样例1 一处核心 logistic 回归显示公式(源为 OMML)未抽出;样例3 残余公式表示差异(display 括号已折 mfenced,余为语义键展开的 near-miss,数学内容一致,属度量口径)。
- **制表符表列数**:切 Tab 还原保证不丢表不丢字,但列数/表头合并与金标准的人工取舍不完全一致。
- **零星长尾**:词级丢失/编造残余(样例1、S03 图面板号 4a/4d 等零星词;R17 后续把通讯块改为忠实保留 docx 原文,样例2 通讯区块此前丢词已归零)、个别 xref id 命名(金标准自身不一致)、表格多余 `frame`/`rules` 属性(L0 警告)。
- **章节 @id 一致性**(样例1):结构修复移动内容块后,段落 `@id` 沿用旧编号,内部不一致(不影响 DTD/xref)。

**设计边界**(非缺陷):

- **图片型表格**(整张表就是一张图,如样例2):**确定性外部化**为 `<table-wrap><graphic>`——图片就是图片,不 OCR 重建(图中文字不在 docx 文本流里,读图必造字,与金标准处理一致)。**用制表符拼的表**(如样例5):确定性切 Tab 还原为 HTML 表,失败时无损兜底为段落(不静默丢、不串号)。真正的 `<w:tbl>` 表格在任何档位都转得很好。
- 外部元数据(DOI/journal-meta/收发日期)以 docx 里实际有没有为准——这些是出版层信息、无法从 docx 推导,缺的部分靠 `--doi`/`--journal`、期刊查表或 `--crossref` 补上。
- 参考文献纯确定性档即结构化 492/522 条为 `element-citation`,其余落 `mixed-citation`(安全、保持原样);加 `--llm` 后难例进一步结构化(守恒护栏:字段须为原文子串),加 `--crossref` 可联网补 DOI/刊名全称。

---

## 6. 许可

MIT License。核心代码和算法是参赛队原创；JATS DTD 来自 NISO/NLM，OMML2MML.XSL 是微软样式表的开源移植（见 `src/word2jats/resources/`）。
