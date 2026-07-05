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
| 参考文献 | ✅ `mixed-citation`（默认）/ `element-citation`（用 LLM 进一步结构化） |
| 交叉引用 | ✅ 正文里的 `Fig. N`/`Table N`/`[n]` → 自动生成 `<xref>` 链接 |

**核心方法 = LLM 主导的修复闭环**

正确性的真值只有一个：**忠实于源 docx**。围绕这一点，本作品的质量来自一个闭环，而不是更全的死规则：

1. **热启动出草稿（加速器）**：规则解析 + 一次性模型抽取，单遍快速搭出一版 JATS 草稿（含图片抽取、表格重建、参考文献结构化）。它只是给闭环一个好起点，不是最终保证。
2. **取证**：把输入 Word 渲染成页面图（`soffice` + `pdftoppm`，纯 CPU，dpi=120，实测 OCR 可读）作**人眼真值**，再配上 docx 的标题样式与源文本，作为判断依据。
3. **四个 LLM 主导修复器就地修复**：是否要改、改成什么，主要由 LLM 据上述证据判断（作者归属是 LLM + 确定性混合，见下条），逐项就地修复：
   - **章节结构修正**：据 docx 标题样式重排层级，移动已有内容块（不重写）。
   - **作者归属修正**：LLM 只判定**单位**的多单位关联；ORCID 用确定性解析按“姓名:号”精确关联，通讯/共同贡献/邮箱用热启动阶段对 `*`/`†` 标记的确定性检测（同姓作者下 LLM 会误判——实测三个 Wang 曾被 LLM 全标通讯且挂同一邮箱，故这几项不用 LLM，详见 `docs/01-设计/02-智能循环架构.md` §2.B）。
   - **后置声明拆分**：把源里无标题的裸声明句识别归入命名 `<back>` 小节。
   - **内容补全（多轮）**：漏作者→看首页图重抽；漏表→看页面图重建为 `<table-wrap>`；多轮至无可补。
4. **循环收敛**：每步都做 DTD 校验 + 内容守恒校验，任一不过即回滚；全过程留痕。

> **去掉了早期"结构问题查出却丢弃"的白名单行为**。早期版本把章节层级、作者归属、声明这类视觉/语义查出的问题一律“仅记录、不修”——R15 重构纠正了它：这些改由 LLM 修复器（阶段 A/B/C）**实际修复**。内容补全阶段（D）目前实现了**作者、表格两类补全器**（`REPAIRABLE={extract_authors, rebuild_table}`）；漏图/漏式暂仅记录到 trace、不自动修（尚无对应补全器，实测多为视觉噪声或解析问题），作为诚实的能力边界。系统的硬约束只有两条：
> 1. **最终必须 DTD 合法**（JATS Journal Publishing 1.3）——赛题硬要求；
> 2. **不编造源文档没有的内容**——正确性底线，配合内容守恒校验兜底。

**其它特点**：
- **本地多模态模型**：本机 GPU 上跑本地 Qwen 多模态模型（VLM），驱动闭环的取证与看图重建，全程本地、不花外网钱；另支持云端 LLM（DeepSeek/DashScope）与 CrossRef 联网补文献（补 DOI/刊名全称）。
- **优雅降级 = 兜底底线，不是卖点**：模型/渲染不可用时退回热启动草稿，仍产出 DTD 合规的 XML——这是**以质量下降为代价的底线**，不是优点。
- **不依赖固定的 Word 样式**：根据内容本身判断每段是什么角色，投稿文档样式名缺失或乱用也能处理。

> 完整质量档命令：`python -m word2jats convert <docx> --llm local --agent`（见 §2.4）。
> 方法体系详见 [`docs/01-设计/02-智能循环架构.md`](docs/01-设计/02-智能循环架构.md)，评测见 [`docs/03-评测/评测体系设计.md`](docs/03-评测/评测体系设计.md)。

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
| `--llm` | `off`(默认,纯热启动草稿) / `local`(本机 Qwen VLM) / `deepseek` / `dashscope` |
| `--agent` | **开启 LLM 主导修复闭环(质量核心)**:渲染页取证 → 四修复器就地修复 → 收敛。需配合 `--llm`(如 `--llm local --agent`) |
| `--agent-rounds` | Agent 闭环最大轮数(默认 3) |
| `--crossref` | 联网用 CrossRef 给参考文献补 DOI / 刊名全称 |
| `--no-validate` | 跳过 DTD 校验 |

> `--journal`/`--doi` 对应出版系统分配的外部元数据（docx 里通常没有）。不提供这两项时程序照样会生成结构完整的 XML，只是对应字段留空或填占位符。

### 2.4 完整质量档:开启 LLM 主导修复闭环(推荐)

本作品的主路径——先用本地模型做热启动抽取,再进入 **LLM 主导的修复闭环**:渲染取证 → 四修复器就地修复 → 循环收敛:
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
`--llm off`(或不加 `--agent`)退回纯热启动草稿——这是**降级兜底,质量会下降**,不是推荐用法。

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

### 3.4 系统性根因(下一轮转换器修复清单)

跨样例一致复现、优先修的结构性差异:

- **参考文献几乎全用 `mixed-citation`、未做 `element-citation`**——最大根因,级联拖累作者拆分、刊名、卷页。
- 公式 MathML 结构差异;DOI / 链接未补(B 档);章节层级与编号;通讯作者结构;表脚注未归入 `table-wrap-foot` 等。

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
│   ├── enrich/                 外部数据(journals 查表 / CrossRef / LLM 文献结构化)
│   ├── agent/                  ★ LLM 主导修复闭环(质量核心):取证 + 四修复器 + loop 编排
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

**残留真缺陷**(三层评测据源确认、诚实披露,均为热启动/构建层质量,逐条见 `reports/eval/<tag>/` 的缺陷清单):

- **公式**(样例1):正文一处核心 logistic 回归显示公式(源为 OMML)未抽出,留下文字空洞。
- **表格结构**(样例1/5):个别多层表头的第三层被放进 `<tbody>`;少数表脚注作为表后独立 `<p>` 而非 `table-wrap-foot`。
- **参考文献粒度**(多样例):`element-citation` 个例丢失 edition/publisher-loc/PMID/姓名后缀/“(In Chinese)”注记;个别姓名转写讹误;偶发臆造期号。
- **章节 @id 一致性**(样例1):结构修复移动内容块后,段落 `@id` 沿用旧编号,内部不一致(不影响 DTD/xref)。

**设计边界**(非缺陷):

- **图片型表格**(整张表就是一张图,如样例2)和**用制表符拼出来的表格**(如样例5):纯热启动档不重建;`--llm local` 用本地多模态模型看图/读文本重建。模型未能结构化的少数制表符表现以**无损兜底**保留为段落(不静默丢、不串号),内容不丢但暂未升级为 `<table-wrap>`。真正的 `<w:tbl>` 表格在任何档位都转得很好。
- 外部元数据(DOI/journal-meta/收发日期)以 docx 里实际有没有为准——这些是出版层信息、无法从 docx 推导,缺的部分靠 `--doi`/`--journal`、期刊查表或 `--crossref` 补上。
- 参考文献默认用 `mixed-citation`(安全、尽量保持原样);加 `--crossref`/`--llm` 后大多升级成 `element-citation`(带 DOI/刊名全称/卷期页)。

---

## 6. 许可

MIT License。核心代码和算法是参赛队原创；JATS DTD 来自 NISO/NLM，OMML2MML.XSL 是微软样式表的开源移植（见 `src/word2jats/resources/`）。
