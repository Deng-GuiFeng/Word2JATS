# word2jats — 学术论文 Word → JATS XML 智能结构化转换

> 学术期刊结构化技术创新大赛 **组别一（选题一：word2xml）** 参赛作品
> 把学术论文 Word(.docx) 自动转换成符合 **JATS Journal Publishing DTD v1.3** 的结构化 XML，并把图片单独抽出来另存。

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
3. **四个 LLM 主导修复器就地修复**：是否要改、改成什么，全部由 LLM 据上述证据判断，逐项就地修复：
   - **章节结构修正**：据 docx 标题样式重排层级，移动已有内容块（不重写）。
   - **作者归属修正**：LLM 只判定**单位**的多单位关联；ORCID 用确定性解析按「姓名:号」精确关联，通讯/共同贡献/邮箱用热启动阶段对 `*`/`†` 标记的确定性检测（同姓作者下 LLM 会误判——实测三个 Wang 曾被 LLM 全标通讯且挂同一邮箱，故这几项不用 LLM，详见 `docs/01-设计/02-智能循环架构.md` §2.B）。
   - **后置声明拆分**：把源里无标题的裸声明句识别归入命名 `<back>` 小节。
   - **内容补全（多轮）**：漏作者→看首页图重抽；漏表→看页面图重建为 `<table-wrap>`；多轮至无可补。
4. **循环收敛**：每步都做 DTD 校验 + 内容守恒校验，任一不过即回滚；全过程留痕。

> **去掉了早期"结构问题查出却丢弃"的白名单行为**。早期版本把章节层级、作者归属、声明这类视觉/语义查出的问题一律「仅记录、不修」——R15 重构纠正了它：这些改由 LLM 修复器（阶段 A/B/C）**实际修复**。内容补全阶段（D）目前实现了**作者、表格两类补全器**（`REPAIRABLE={extract_authors, rebuild_table}`，成员与早期 `ACTIONABLE` 逐字相同）；漏图/漏式暂仅记录到 trace、不自动修（尚无对应补全器，实测多为视觉噪声或解析问题），作为诚实的能力边界。系统的硬约束只有两条：
> 1. **最终必须 DTD 合法**（JATS Journal Publishing 1.3）——赛题硬要求；
> 2. **不编造源文档没有的内容**——正确性底线，配合内容守恒校验兜底。

**其它特点**：
- **本地多模态模型**：本机 GPU 上跑本地 Qwen 多模态模型（VLM），驱动闭环的取证与看图重建，全程本地、不花外网钱；另支持云端 LLM（DeepSeek/DashScope）与 CrossRef 联网补文献（补 DOI/刊名全称）。
- **优雅降级 = 兜底底线，不是卖点**：模型/渲染不可用时退回热启动草稿，仍产出 DTD 合规的 XML——这是**以质量下降为代价的底线**，不是优点。
- **不依赖固定的 Word 样式**：根据内容本身判断每段是什么角色，投稿文档样式名缺失或乱用也能处理。

> 完整质量档命令：`python -m word2jats convert <docx> --llm local --agent`（见 §2.4）。
> 方法体系详见 [`docs/01-设计/02-智能循环架构.md`](docs/01-设计/02-智能循环架构.md)，评测见 [`docs/03-评测/01-评测报告.md`](docs/03-评测/01-评测报告.md)。

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
    样例/样例3/第一组/初始文件.docx \
    --journal JIN \
    --doi 10.31083/JIN49347 \
    --figures 样例/样例3/第一组/figures.zip \
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
- 闭环全过程留痕落盘 `{article-id}.agent-trace.json`(逐轮:每个修复器看了什么证据、判断、是否生效、为何不生效),便于复盘。评测产物归档于 `reports/eval_l1_v2/`(Layer-1)与 `reports/eval_judge/`(Layer-2 judge)。

**云端 LLM(无本地 GPU 时)**:
```bash
cp .env.example .env          # 填入 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY
PYTHONPATH=src python -m word2jats convert <docx> --llm deepseek ...
```
所有模型调用结果**按内容哈希缓存到磁盘**,重复运行不再额外花钱、便于复现。
`--llm off`(或不加 `--agent`)退回纯热启动草稿——这是**降级兜底,质量会下降**,不是推荐用法。

---

## 3. 评测

**正确性的真值是「忠实于源 docx」,不是「通过我们自己定义的几个指标」。** 委员会金标准是**已发表**的 JATS,含大量**出版层编辑**——分配 DOI、补收发/发表日期、把无编号标题排成「1./2.1」、新增源里没有的声明节、给参考文献补 DOI、把整表拆成多张并重编号、给 ORCID 加认证标记……这些**无法从 docx 推导**。因此「与金标准每处差异都算缺陷」会误报,「只比几个计数都对就算正确」会漏报。评测据此分两层(详见 [`docs/03-评测/01-评测报告.md`](docs/03-评测/01-评测报告.md))。

### Layer-1:全面可定义指标(对照权威标签)

逐维度(章节树/标题/作者/单位/摘要/关键词/图/表/公式/参考文献/xref/back 声明节)对照外部权威标签算分。参考目标永远是**外部权威标签**(原始样例 1–5 = 委员会金标准 XML;补充样例 S1–S5 = 独立三方核验伪标签),绝不拿本系统旧输出当目标。

```bash
cd scripts && python -m eval.run        # 逐维度对照标签,产出 JSON + HTML
cd scripts && python -m eval.ablation   # 消融:逐个关闭组件,量化各自贡献
```

- **结果**:Layer-1 宏平均综合分 **0.967**;样例 3 满分 1.000。少数维度低分(如样例 2 的 back=0、样例 1/5 的表)经 Layer-2 源核对**确认为出版层差异、非我方缺陷**(金标准发表时新增了源里没有的声明节、把整表拆分重编号)。
- **消融**:逐个关闭组件用 Layer-1 量化贡献——闭环整体 +0.020;章节结构修正在样例 1 上 0.905→0.946;声明拆分在样例 5 上 0.833→0.870;内容补全在这批规范样例上几乎不触发(它是缺内容时才用的安全网,如实标注)。

### Layer-2:每个输出派独立 subagent 逐字审查(评测核心创新)

对**每个样例的每一次输出**单独派一个**全新** subagent,读入「输出 XML + 标签 + 源 docx」逐字比对;再派一个对抗性「完整性批判」二审找漏报。每处差异据**源**判为**真缺陷**(源确有、我方没忠实呈现)或**出版层差异**(标签的出版加工,不计缺陷)。归档于 `reports/eval_judge/judge_v2.json`。

- **结果**:各样例对源**忠实度 0.80–0.96(宏均 0.93)**,与标签吻合度 0.72–0.98。正是这一层(及对抗二审)抓出了 Layer-1 覆盖不到的真缺陷(章节层级错位、核心公式漏抽、ORCID 张冠李戴等)并促成修复。
- **为什么不可或缺**:早期「三方验收」失灵,是因为审查口径只覆盖同一批可定义计数、没人拿输出去和标签/源逐字比;本层从机制上补上了这一点。

### 复现性与残留

- **复现性**:LLM 温度 0 仍可能微抖,但每个修复器都带保守安全校验(集合不变/内容守恒/源支撑/DTD),抖动至多导致「不生效/回滚」,不会改坏;ORCID 等结构化数据走确定性解析,渲染按内容哈希缓存。全套单测通过(`tests/test_agent_fixers.py` 守护栏)。
- **残留真缺陷**已在 `reports/eval_judge/judge_v2.json` 逐条留档(含严重度与源证据),作为后续优化清单透明交付,详见 [`docs/03-评测/01-评测报告.md` §5](docs/03-评测/01-评测报告.md)。

---

## 4. 项目结构

```
src/word2jats/
├── cli.py  pipeline.py        # 入口与编排
├── agent/                     # ★ LLM 主导修复闭环(质量核心):render/inspect + structure/authors_fix/declarations/reconcile/repair + loop 编排
├── model/                     # 中间表示 IR / 语义模型
├── parse/                     # OOXML 解析（docx_reader/runs/ooxml）
├── classify/                  # 语义识别（document/frontmatter/sections/references/patterns）
├── build/                     # JATS 构建（metadata/body/figures/formulas/tables/references/xref）
├── enrich/                    # 外部数据（journals 查表 / CrossRef / LLM 参考文献）
├── llm/                       # LLM/VLM 客户端 + 缓存
├── validate/                  # DTD 校验
└── resources/                 # OMML2MML.XSL / journals.yaml / JATS 1.3 DTD
tests/                         # pytest(test_agent_fixers.py 修复器护栏 + test_agent.py 闭环 + test_table_fallback.py 表格无损兜底)
scripts/                       # eval/(Layer-1 对照标签 run.py + 消融 ablation.py);Layer-2 审查归档于 reports/eval_judge/
docs/                          # 完整中文文档体系
```

详细架构见 [`docs/01-设计/01-架构设计.md`](docs/01-设计/01-架构设计.md)。

---

## 5. 已知限制

**残留真缺陷**(Layer-2 据源确认、诚实披露,均为热启动/构建层质量,逐条留档于 `reports/eval_judge/judge_v2.json`):

- **公式**(样例1):正文一处核心 logistic 回归显示公式(源为 OMML)未抽出,留下文字空洞。
- **表格结构**(样例1/5):个别多层表头的第三层被放进 `<tbody>`;少数表脚注作为表后独立 `<p>` 而非 `table-wrap-foot`。
- **参考文献粒度**(多样例):`element-citation` 个例丢失 edition/publisher-loc/PMID/姓名后缀/「(In Chinese)」注记;个别姓名转写讹误;偶发臆造期号。
- **章节 @id 一致性**(样例1):结构修复移动内容块后,段落 `@id` 沿用旧编号,内部不一致(不影响 DTD/xref)。

**设计边界**(非缺陷):

- **图片型表格**(整张表就是一张图,如样例2)和**用制表符拼出来的表格**(如样例5):纯热启动档不重建;`--llm local` 用本地多模态模型看图/读文本重建。模型未能结构化的少数制表符表现以**无损兜底**保留为段落(不静默丢、不串号),内容不丢但暂未升级为 `<table-wrap>`。真正的 `<w:tbl>` 表格在任何档位都转得很好。
- 外部元数据(DOI/journal-meta/收发日期)以 docx 里实际有没有为准——这些是出版层信息、无法从 docx 推导,缺的部分靠 `--doi`/`--journal`、期刊查表或 `--crossref` 补上。
- 参考文献默认用 `mixed-citation`(安全、尽量保持原样);加 `--crossref`/`--llm` 后大多升级成 `element-citation`(带 DOI/刊名全称/卷期页)。

---

## 6. 许可

MIT License。核心代码和算法是参赛队原创；JATS DTD 来自 NISO/NLM，OMML2MML.XSL 是微软样式表的开源移植（见 `src/word2jats/resources/`）。
