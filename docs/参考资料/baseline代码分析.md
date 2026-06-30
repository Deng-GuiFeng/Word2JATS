# Baseline 参考代码深度技术分析报告（选题一 word2xml）

分析对象目录：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop`
分析方法：逐文件实读源码 + 解压实测输入样本 `test2.docx`（真实 MDPI 期刊论文）验证启发式规则的实际命中情况。

> 本文只分析**委员会提供的基线参考代码**（Java），不是本队作品。术语（JATS、DTD、OMML→MathML、mixed-citation、OOXML、XSLT 等）的解释见 `../00-调研/02-数据与映射规格.md` 与 `JATS与开源工具调研.md`。

---

## 0. 一句话结论（先给判断）

baseline 是一个**能跑通、但只是“玩具级 demo”**的脚手架：两条 word2xml 路线都建立在「正则切 HTML 块 + 关键词分水岭 + 位置猜测」的脆弱启发式之上，对真实学术文档（带语义样式、图表公式、列表、参考文献）几乎全军覆没。它的价值在于**给出了一套可复用的工程骨架（FreeMarker JATS 模板 + POI 富文本抓取思路 + FOP/PDF 闭环）**，但**核心抽取逻辑必须重写**。直接交这套代码不可能拿奖。

---

## 1. 两个 word2xml converter 的技术路线、流程、输入输出

两个 converter 共用同一个后处理与渲染管线（`ArticleMetadataUtil.parseWordHtmlToRootMap` + FreeMarker 模板），区别只在**前端如何从 Word 取内容**。

### 方案 A：`Word2xmlTikaConverter`（Tika 抽取 + 启发式）
- 技术路线：Apache Tika 把 docx 整体解析为扁平 **XHTML 字符串**（`ToXMLContentHandler` + `AutoDetectParser`），不区分语义，只保留 `<p>/<h1-6>/<b>/<i>` 等粗粒度标签。
- 流程：`ResourceUtil.getStream("static/test2.docx")` → `parser.parse()` 得到 `rawXhtml` → 交给 `ArticleMetadataUtil.parseWordHtmlToRootMap()` 做启发式元数据提取 → 读 `article.xml.ftlh` 模板 → `processTemplateString()` 渲染 → 写 `article-output.xml`。
- 输入/输出：输入 classpath 里写死的 `static/test2.docx`；输出工作目录下 `article-output.xml`。
- 注意：类里那个 `convertTikaHtmlToJats()` 方法**根本没被调用**（main 里走的是 `ArticleMetadataUtil`），是一段死代码/旧实现残留。

### 方案 B：`Word2JatsFtlConverter`（POI 富文本抓取）
- 技术路线：Apache POI（`XWPFDocument`）**逐段遍历**，按 `paragraph.getStyle()` 判断是否标题，逐 `XWPFRun` 抓取 bold/italic/上标/下标，自己拼出 `<sec><title>…</title><p>…</p></sec>` 的 JATS body 片段字符串。
- 流程：POI 打开 docx → `extractBodyFromWord()` 生成 body XML 字符串 → **同样**喂给 `ArticleMetadataUtil.parseWordHtmlToRootMap()` → FreeMarker 渲染 → 写 `article-output.xml`。
- 输入/输出：同方案 A。

> 关键矛盾：方案 B 在 `extractBodyFromWord` 里已经把段落转成了带 `<sec>/<p>/<bold>` 的 JATS 片段，但接着又把这个片段当成「Word HTML」再丢进 `parseWordHtmlToRootMap` 用正则 `<(p|h[1-6])>` 重新切块。而 B 产出的是 `<sec>/<title>` 而非 `<h1>`，所以**标题块会被正则漏掉**——`<title>` 内的标题文本被整段丢弃（块正则只认 `<p>/<h1-6>`、不认 `<title>`，故既不进 body 也不进元数据）。两条路线的衔接是不自洽的——方案 B 实际上和 `ArticleMetadataUtil` 互相打架。

---

## 2. 用到的库与版本（依赖偏重）

| 用途 | 库 | 版本 | scope |
|---|---|---|---|
| 框架 | spring-boot-starter / -web | 3.5.9（parent） | compile |
| Word→XHTML | tika-core / tika-serialization / **tika-parsers-standard-package** | 2.9.2 | compile |
| Word 底层 | poi-ooxml | 5.4.1 | compile |
| 模板引擎 | **freemarker** | **2.3.31**（pom 写死，未走 BOM） | compile |
| 工具类 | hutool-all | 5.8.32 | compile |
| JATS→PDF | xmlgraphics fop + xmlgraphics-commons + Saxon-HE | 2.9 / 2.9 / 11.5 | compile |
| HTML→PDF | openhtmltopdf core/pdfbox/svg/mathml | 1.0.10 | compile |
| HTML 解析 | jsoup | 1.18.1 | compile |
| AI | spring-ai-markdown-document-reader（BOM 1.1.2） | — | compile |
| 测试 | flexmark-all 0.64.8 / mockito 5.14.2 | — | test |

**依赖很重，而且对 word2xml 这一题大量冗余**：
- `tika-parsers-standard-package` 会传递引入 PDFBox、整套 POI、各种格式解析器，是个数十 MB 的“全家桶”。
- FOP + Saxon-HE + openhtmltopdf(4 个) + jsoup + spring-ai 全是为「JATS→PDF」「HTML→PDF」另外两个 demo 服务的，**和 word2xml 选题无关**。
- Java 21 + Spring Boot 3.5.9 起步即重。
- 真正 word2xml 只用到 Tika 或 POI + FreeMarker + hutool 四样，其余都可裁掉。

---

## 3. FreeMarker 模板对 JATS 的覆盖情况（对照功能要求）

模板 `article.xml.ftlh` 本身写得**相当完整、是全套代码里最有价值的资产**。它声明了 JATS 1.3 DTD、mml/xlink 命名空间，front/body/back 三段齐全。

**已覆盖（模板层面有坑位）：**
- `journal-meta`（刊名、ISSN、出版商）
- `article-meta`：DOI/PII、article-categories、`title-group/article-title`
- `contrib-group`：作者 `name/surname/given-names`、`xref` 关联机构、`email`、学术编辑、`aff/institution`、`author-notes/corresp`
- 出版/采集/收稿/修回/接受日期（history）、卷期页码、`permissions/license`
- `abstract`、`kwd-group`（含 JEL 关键词）、`funding-group`
- body：`${content}` 直接注入
- back：publisher-note、`ref-list`（ref 循环）

**缺失 / 形同虚设（对照评分功能点）：**
| 功能要求 | 模板/数据是否覆盖 | 说明 |
|---|---|---|
| 标题层级（多级 sec 嵌套） | 部分 | 模板 body 只是 `${content}` 透传，层级完全取决于上游字符串，**实际只产出平铺单层 `<sec>`，无 sec/sec 嵌套** |
| 作者 ORCID | **缺** | 模板有 surname/given-names/email/xref，但**没有 `<contrib-id contrib-id-type="orcid">`**；而样本 docx 里明确含 `ORCID:0000-0002-1506-7836`，被白白丢弃 |
| 关键词 | 有坑位（`kwd-group`） | 模板 OK，但上游提取易错（见 §4） |
| 摘要 | 有坑位（`abstract`） | 同上 |
| 图片 | **完全缺** | 模板无 `<fig>/<graphic>`，数据模型无图片字段 |
| 列表 | **完全缺** | 模板/数据均无 `<list>/<list-item>`；样本里有 85 处列表项 |
| 公式 | **完全缺** | 声明了 mml 命名空间却**没有任何 `<disp-formula>/<inline-formula>/mml:math`** 处理 |
| 表格 | **完全缺** | 无 `<table-wrap>/<table>`；样本里有 5 张表 |
| 参考文献 | 有坑位但无数据 | `ref-list` 循环存在，但**没有任何代码生产 `refList`**，永远为空；也无 `mixed-citation/element-citation` 结构化 |

---

## 4. 启发式元数据提取的具体规则（`ArticleMetadataUtil`）及实测命中

规则（按 block 顺序的状态机，block = 正则 `<(p|h[1-6])>…</\1>` 切出的纯文本块）：

1. **正文分水岭**：某块纯文本以 `INTRODUCTION` 或 `引言`（`(?i)^`）开头 → 之后所有块全部进 body，元数据提取停止。
2. **标题**：第 1 个非空块直接当 `articleTitle`。
3. **作者**：第 2 个非空块（且不以数字开头）当作者行；去掉 `*†`，按 `,`/`，` 切分；**姓名切分极其粗暴——取 `substring(0,1)` 当 surname、其余当 givenNames**（对英文名 “Anis Ahmad Chaudhary” 会切成 surname="A"、given="nis Ahmad Chaudhary"，完全错误）。
4. **机构**：以数字开头的块 → `aff`，id=首字符、内容=去掉首字符的剩余。
5. **关键词**：块含 `INDEX TERMS|KEYWORDS|关键词` → 正则剥掉前缀和冒号，按 `,/，` 切。
6. **摘要**：块含 `ABSTRACT|摘要` → 开 `isParsingAbstract`，把同块剩余文本和后续块都包成 `<p>` 累加。

**用真实样本 `test2.docx` 实测，这套规则几乎处处踩雷：**
- 标题（第 1 段）确实是 article-title ✔（唯一好运的一条）。
- 第 2 段 `Anis Ahmad Chaudhary*,` → 被切成 surname="A"，姓名解析错误。
- 真实结构里**作者后面紧跟的是 affiliation、`*Corresponding author`、邮箱、ORCID**，规则没有“数字开头”，所以机构识别不到；ORCID/邮箱被当成普通块丢弃或误判。
- **致命点**：摘要正文段在 docx 里样式是 `MDPI18keywords`，正文里那段 “The skin microbiome is a diverse ecosystem…” 既不含 “ABSTRACT” 也不含 “Keywords”，而真正的 “Abstract” 字样单独成段。规则匹配到 `Abstract` 这一段时它后面是空内容，下一段才是摘要正文——状态机能续上，但 keyword 行 `Keywords: …` 又会被先匹配进 keyword 分支，顺序耦合极脆。
- **分水岭误判**：样本里引言段样式是 `MDPI21heading1` 文本 “1. Introduction”，正则 `^(INTRODUCTION|引言)` 是**大小写不敏感但要求顶格开头**，而文本是 “**1.** Introduction”（前面有编号），`^INTRODUCTION` **匹配不到** → 元数据提取永不结束 → 整篇正文全被吞进元数据状态机，body 基本为空。这是一个能让输出彻底崩坏的硬 bug。

一句话：**启发式规则是按某一篇特定排版臆想出来的，遇到带编号标题、英文人名、语义样式的真实 MDPI 论文直接失效。**

---

## 5. baseline 处理了图片/公式/表格/参考文献吗？

**基本都没处理。** 实测 `test2.docx` 含 1 个 `w:drawing`、5 个 `w:tbl`、85 处 `w:numPr` 列表项、若干超链接、ORCID，但：

- **图片**：方案 B 的 POI 遍历只走 `getParagraphs()` 取 `getText()`，drawing/图片对象既不读也不导出；模板无 `<fig>`。方案 A 的 Tika 会把图标成 `<img>`，但 `parseWordHtmlToRootMap` 的块正则只认 `<p>/<h>`，`<img>` 被忽略。**0 处理**。（注：样本的 1 个 drawing 在 rels 里没有 image/media 目标，本身就是空框/占位，但即便有也不会被处理。）
- **公式**：样本恰好 0 个 OMML，所以“看起来没崩”。但代码对 `m:oMath`/MathML **完全无逻辑**，真实带公式文档会丢失或乱码。模板声明了 mml 命名空间却没人用。
- **表格**：5 张表全部丢失。POI 的 `getParagraphs()` 不含表格单元格内容（表格在 `getTables()` 里），代码根本没遍历表格；Tika 的 `<table>` 又被块正则过滤。`MDPI41tablecaption` 这种语义样式也没利用。
- **参考文献**：docx 里有 `References:` 段（被错标成 `MDPI22heading2`），但**没有任何代码生产 `refList`**，模板的 `ref-list` 循环永远空跑。参考文献既不抽取也不结构化（无 element-citation/作者/年份/期刊拆分）。
- **列表**：85 个列表项无任何处理，会被当普通段落甚至丢失编号/层级。

---

## 6. baseline 的明显短板与局限（为什么不够拿奖）

1. **抽取范式错误**：把 docx 退化成扁平 HTML/纯文本再正则猜，主动丢弃了 docx 里最宝贵的**语义样式信息**（`MDPI13authornames/16affiliation/17abstract/18keywords/21heading1…`）。正确做法应优先吃这些样式名，而 baseline 完全无视。
2. **启发式过拟合单一样本且自带硬 bug**：分水岭 `^Introduction` 对“1. Introduction”失配会导致整篇 body 塌陷；人名 `substring(0,1)` 切姓对英文名系统性错误。
3. **结构覆盖严重不全**：图/表/公式/列表/参考文献/ORCID 六大要素几乎全缺，而这些正是评分功能点。
4. **JATS 保真度低**：只产出单层平铺 `<sec>`，无层级嵌套；正文富文本只保 bold/italic/sup/sub，xref（图表/文献交叉引用）、链接、上下标公式全丢。
5. **字符串拼 XML 风险**：方案 B 手写字符串拼 JATS，依赖 `XmlUtil.escape`，缺少 schema/DTD 校验，易产出非良构 XML（且模板注释明确说不能开 XMLOutputFormat 转义，等于放弃了引擎级转义保护，注入正文 HTML 时有 XML 合法性隐患）。
6. **两条路线互相打架**：方案 B 产 `<sec>` 又被当 `<h>` 重切，衔接不自洽（见 §1）。
7. **工程冗余**：拉了 FOP/openhtmltopdf/spring-ai 一堆与本题无关的重依赖。
8. **死代码**：`Word2xmlTikaConverter.convertTikaHtmlToJats` 未被调用。

---

## 7. 可借鉴复用 vs 必须重写/新增

**可直接借鉴/复用（资产）：**
- `article.xml.ftlh` 这套 **JATS 1.3 front/body/back 模板骨架**——结构正确、命名空间齐全，是全代码库最有价值的部分，扩几个块（fig/table/list/formula/ref/ORCID）即可用。
- `ArticleMetadataUtil.processTemplateString`（StringTemplateLoader 动态渲染 + 不开 XMLOutputFormat 的踩坑注释）——FreeMarker 接入范式可留。
- 方案 B 的 **POI run 级富文本抓取思路**（bold/italic/`STVerticalAlignRun` 判 sup/sub）——方向正确，可作为正文 run 处理的基础，但要扩展。
- “数据模型 Map + 模板渲染”的**解析与渲染解耦**架构思想值得保留。
- README 里那条 JATS→PDF（FOP）闭环可作为**自验证手段**（生成 XML 后渲 PDF 肉眼比对）。

**必须重写：**
- 整个 `parseWordHtmlToRootMap` 启发式状态机——推倒，改为**基于 docx 样式名（pStyle）+ styles.xml 的样式语义映射**驱动，而非位置/关键词猜测。
- 人名拆分逻辑（surname/given-names）。
- 分水岭/章节切分逻辑（用样式层级 heading1/2/3 建真正的嵌套 `<sec>`）。
- 方案 A、B 二选一并打通（建议留 POI 路线，丢弃 Tika 扁平化）。

**必须新增（当前 0 覆盖）：**
- 图片：POI 读 drawing/blip → 导出 media 文件 → `<fig><graphic xlink:href>`，并关联 `MDPI51figurecaption`。
- 表格：遍历 `doc.getTables()` → `<table-wrap><table>`（thead/tbody/tr/td），关联 `MDPI41tablecaption`。
- 列表：读 `numbering.xml` + `w:numPr` → `<list list-type><list-item>`，保留层级。
- 公式：解析 OMML（`m:oMath`），用 OMML→MathML XSLT（OOXML 自带 `OMML2MML.XSL`）转 `mml:math`，包进 `<disp-formula>/<inline-formula>`。
- 参考文献：抽 `References` 段 → 结构化 `<ref><element-citation>`（作者/年/题名/刊名/卷期页/DOI 拆分，可正则起步、必要时 LLM 兜底）。
- ORCID/邮箱/通讯作者：从 affiliation/联系块解析 `<contrib-id contrib-id-type="orcid">`、`corresp="yes"`。
- 交叉引用（xref）：正文中“table 3 / Figure 1 / [12]”→ `<xref ref-type>`。

---

## 8. 对我们方案的具体建议

**8.1 是否继续用 Java？——建议主体改用 Python，Java 仅作可选验证。**
- 决策依据（第一性原理：docx 本质是 OOXML zip，抽取质量取决于对结构的解析深度，而非语言）：
  - **Python 生态对本题更顺手**：`python-docx`（段落/run/表格/样式/numbering 一把梭）、`lxml` 直接 XPath 啃 `document.xml`/OMML、`pandoc`（docx→JATS 现成，可作强基线）、OMML→MML 用官方 XSLT + `lxml`、参考文献/版面理解可无缝接 LLM（Anthropic SDK / 本地模型）做兜底，迭代速度远快于 Java。
  - baseline 的 Java 资产里**唯一值钱的是 FreeMarker 模板**，它是纯文本，可被任意语言的模板引擎复用，不构成留在 Java 的理由。
  - 若团队 Java 储备强、或赛方明确要求 Java/Spring 交付，则保留 Java：用 **POI（深度遍历 body 元素，而非只 getParagraphs）** 重写抽取，POI 5.x 对 OMML/drawing/table 都有 API。
- 折中推荐：**Python 做核心 word2xml**（解析 + 抽取 + 渲染），如需 PDF 自检再复用 baseline 的 Java FOP 链路，或直接 Python 端用 `weasyprint`/`fop` CLI。

**8.2 模板引擎是否保留？——保留模板化思想，模板内容直接复用并扩展。**
- 强烈建议**保留 `article.xml.ftlh` 的 JATS 结构**作为目标 schema 蓝本：它已对齐 JATS 1.3，省去从零设计 front/back 的成本。
- 若转 Python：把 ftlh 平移到 **Jinja2**（语法几乎等价，`<#if>`→`{% if %}`、`${x!''}`→`{{ x or '' }}`），并新增 fig/table/list/formula/ref/ORCID 块。
- 但**正文 body 不要再走“拼字符串塞进模板”**：body 结构复杂（嵌套 sec、表、图、公式、列表），建议**用 lxml/ElementTree 构建 DOM 节点**保证良构与可校验，front 元数据这类扁平字段才用模板。即“元数据走模板、正文走 DOM 构建”混合方案。

**8.3 抽取范式（最关键的方法论纠偏）：**
- **以 docx 样式名为第一信息源**：先解析 `styles.xml` 建立 styleId→语义角色映射（title/author/affiliation/abstract/keywords/heading1-3/figcaption/tablecaption/reference），按样式驱动而非关键词猜测。对 MDPI/IEEE/Elsevier 等常见模板各建一份样式词典。
- **样式缺失时再降级**到启发式 + 可选 LLM 辅助分类（把“疑似作者行/机构行/参考文献条目”交给模型判别），杜绝 baseline 那种顶格关键词硬匹配。
- **务必用真实样本回归**：`test2.docx` 已暴露“1. Introduction 失配、英文名错切、表/图/列表丢失”等问题，把它当第一条回归用例，再扩充多刊样本。
- **输出做 JATS schema/DTD 校验**（模板已声明 JATS 1.3 DTD），保证良构与可验证，作为答辩亮点。

---

### 附：关键文件路径（绝对路径）
- 方案 A：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop/src/main/java/com/example/demo/Word2xmlTikaConverter.java`
- 方案 B：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop/src/main/java/com/example/demo/Word2JatsFtlConverter.java`
- 启发式核心：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop/src/main/java/com/example/demo/util/ArticleMetadataUtil.java`
- JATS 模板（最值钱资产）：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop/src/main/resources/static/article.xml.ftlh`
- 示例 JATS：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop/src/main/resources/static/jats.xml`
- 依赖：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop/pom.xml`
- 真实输入样本（建议设为回归用例）：`/home/denggf/学术期刊结构化技术创新大赛/baseline-develop/src/main/resources/static/test2.docx`（MDPI 论文，含 1 drawing / 5 表 / 85 列表项 / ORCID）
- JATS→PDF 自检链路：`SimpleJATSToPDFConverter.java` + `jats-to-fo.xsl` + `fop.xconf`（同目录 static 下）