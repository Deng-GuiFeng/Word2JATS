# JATS 标准与开源工具调研报告

> 本文为**实现前的技术调研与选型建议**（约 2026-06-17），属调研原始材料；其推荐路线与最终落地架构可能有出入，**最终实现以 `../01-设计/01-架构设计.md` 与 `../01-设计/02-智能循环架构.md` 为准**。
> 术语（OMML、OOXML、DTD、ORCID、CSL、TEI、MathML 等）首次出现处尽量已给中文释义；JATS、ORCID 的基础定义见 `../00-调研/01-竞赛理解.md`。

> 调研日期：2026-06-17 ｜ 所有结论均经联网查证并附来源链接。下文按竞赛方案的 7 个技术要点组织。

---

## 1. JATS Journal Publishing DTD v1.3（ANSI/NISO Z39.96-2021）核心结构

### 1.1 标准与官方文档定位

- 标准全称：**ANSI/NISO Z39.96-2021, JATS: Journal Article Tag Suite, version 1.3**，于 **2021-06-10 批准**。该标准免费开放下载。
- JATS 是一套"标签套件"，包含 3 个 Tag Set：**Journal Archiving (Green)**、**Journal Publishing (Blue)**、**Article Authoring (Pumpkin/Orange)**。竞赛"生成发布用 XML"应锁定 **Journal Publishing Tag Set v1.3**。
- 官方资源（**首选权威来源**）：
  - Tag Library（人类可读、逐元素文档）：https://jats.nlm.nih.gov/publishing/tag-library/1.3/index.html
  - Tag Set 总览页：https://jats.nlm.nih.gov/publishing/1.3/
  - **可直接下载的 DTD**：https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd
  - 全部 schema（DTD/XSD/RNG）匿名 FTP：https://public.nlm.nih.gov/projects/jats/publishing/1.3/
  - NISO 标准发布页（免费 PDF）：https://www.niso.org/publications/z3996-2021-jats
  - 1.2→1.3 变更历史：https://jats.nlm.nih.gov/archiving/tag-library/1.3/chapter/version-1.3-chg.html

### 1.2 文档骨架：`<article>` → front / body / back

JATS 文章根元素为 `<article>`，三大块：
- **`<front>`** —— 元数据区。包含 `<journal-meta>`（期刊级元数据）和 `<article-meta>`（文章级元数据，必备）。
- **`<body>`** —— 正文区。由 `<sec>`、段落 `<p>`、`<fig>`、`<table-wrap>`、`<disp-formula>` 等构成。
- **`<back>`** —— 后置区。含 `<ref-list>`（参考文献）、`<ack>`（致谢）、`<app-group>`（附录）、`<fn-group>`（脚注）等。

来源：JATS Guide / Taylor & Francis 对结构的说明 https://jats.taylorandfrancis.com/jats-guide/topics/content-presentation/ ；Tag Library section 元素 https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/sec.html

### 1.3 `<article-meta>` 常见/必备子元素

经 Tag Library 查证，`<article-meta>` 典型子元素（注意 JATS 对多数元素是"可选但强烈建议"，真正"必备"很少，硬性约束多由下游 Schematron 而非 DTD 决定）：

| 元素 | 作用 |
|---|---|
| `<article-id>` | 文章标识（DOI 等，配 `pub-id-type="doi"`） |
| `<title-group>` | 含 `<article-title>`、`<subtitle>` |
| `<contrib-group>` | 作者/贡献者组，内含多个 `<contrib>` |
| `<aff>` | 机构隶属（可在 contrib-group 内或其后） |
| `<pub-date>` | 出版日期 |
| `<volume>` / `<issue>` / `<fpage>` / `<lpage>` 或 `<elocation-id>` | 卷期页 |
| `<abstract>` | 摘要（可多个，多语种用 `xml:lang`） |
| `<kwd-group>` | 关键词组，内含 `<kwd>` |
| `<permissions>` | 版权/许可 |

来源：https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/article-meta.html

### 1.4 作者 + ORCID：`<contrib>` 与 `<contrib-id>`

ORCID 在 JATS 中用 `<contrib-id contrib-id-type="orcid">` 表示。标准做法是放完整 URL 形式：

```xml
<contrib-group>
  <contrib contrib-type="author">
    <contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-1825-0097</contrib-id>
    <name>
      <surname>Carberry</surname>
      <given-names>Josiah</given-names>
    </name>
    <xref ref-type="aff" rid="aff1"/>
  </contrib>
  <aff id="aff1">Department of Psychoceramics, Brown University</aff>
</contrib-group>
```

来源：contrib-id 元素文档 https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/contrib-id.html ；ORCID/JATS4R 推荐用完整 URI 形式见 JATS4R citations 规则 https://jats4r.niso.org/citations/

### 1.5 正文与后置区关键元素

- `<sec>`：章节，可嵌套；可含 `<fig>`、`<fig-group>`、`<table-wrap>`、`<table-wrap-group>`、`<disp-formula>`。
- **`@id` 在 `<fig>`、`<table-wrap>`、`<disp-formula>` 上是必需的**（供 `<xref>` 交叉引用）。
- JATS 1.3 新增：`<disp-formula>` 模型中加入了 `<caption>`。
- `<ref-list>`：参考文献列表，每条文献用 `<ref>` 包裹，内含 `<element-citation>` 或 `<mixed-citation>`。

来源：JATS Schematron 文档（`@id` 要求、disp-formula caption）https://jats.taylorandfrancis.com/jats-schematron/ ；ref-list 文档 https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/ref-list.html ；1.3 变更 https://jats.nlm.nih.gov/archiving/tag-library/1.3/chapter/version-1.3-chg.html

### 1.6 `<element-citation>` vs `<mixed-citation>`（关键设计决策）

JATS 提供 3 种引文模型：`<mixed-citation>`、`<element-citation>`、`<nlm-citation>`（后者已弃用，勿用）。核心区别：

- **`<element-citation>`**：纯结构化，**不允许穿插标点/自由文本**，每个字段（作者、标题、来源、年、卷、页）独立标记，依赖下游软件按 CSL 等样式重排版显示。适合"数据干净、要机器可重排版"的场景。
- **`<mixed-citation>`**：半结构化，**标记元素 + 未标记文本/标点混排**，元素顺序即显示顺序。适合"保留原始引用串外观"或来源杂乱的转换/legacy 场景。

**对 Word→JATS 工程的建议**：从 Word 纯文本引用串解析时，若解析置信度高且要求机器可重排 → 用 `element-citation`；若希望"原样保形 + 尽量标记"作为容错兜底 → 用 `mixed-citation`。两者**不要在同一文档混用**（JATS4R 建议全篇统一）。

`<element-citation publication-type="journal">` 典型示例（已查证）：

```xml
<ref id="bib13">
  <element-citation publication-type="journal">
    <person-group person-group-type="author">
      <name><surname>Bradley</surname><given-names>RK</given-names></name>
      <name><surname>Roberts</surname><given-names>A</given-names></name>
    </person-group>
    <year iso-8601-date="2009">2009</year>
    <article-title>Fast statistical alignment</article-title>
    <source>PLOS Computational Biology</source>
    <volume>5</volume>
    <elocation-id>e1000392</elocation-id>
    <pub-id pub-id-type="doi">10.1371/journal.pcbi.1000392</pub-id>
  </element-citation>
</ref>
```

要点：`@publication-type="journal"`；期刊名放 `<source>`；4 位年份放 `<year iso-8601-date>`；`<person-group person-group-type="author">` 包作者，`<name>` 内 `<surname>`/`<given-names>`。

来源：mixed-citation 文档 https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/mixed-citation.html ；element-citation https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/element-citation.html ；person-group https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/person-group.html ；JATS4R citations（含完整示例与"勿混用"建议）https://jats4r.niso.org/citations/ ；JATS Guide references https://jats.taylorandfrancis.com/jats-guide/topics/references/

---

## 2. 开源 Word→JATS / docx→JATS 工具调研

| 工具 | 语言/技术 | 路径 | License | 评价（是否可借鉴） |
|---|---|---|---|---|
| **pandoc + JATS writer** | Haskell | docx → 内部 AST → JATS（双向 ↔ 支持） | GPL-2.0+ | 见 §2.1。可借鉴其 docx 解析 + texmath 数学转换思路；直接产物需大量手工 front/参考文献清理 |
| **meTypeset** | Python 3 + Java + XSLT | docx → **TEI 中间格式** → JATS（fork 自 OxGarage 栈） | MIT/GPL 混合 | 思路成熟（用 TEI 做中介可换皮），但依赖 unoconv/Java/unzip 重，已较少维护 |
| **DOCX2JATS (Vitaliy-1)** | **XSLT 89% + Java**（Saxon9he + TEIC Stylesheets） | 解包 OOXML → JATS | **GPL-3.0**（含 CC-BY-SA-3.0、MPL-1.0 片段） | 处理 `[1]`/`[3,4,5]` 方括号引文、AMA/Vancouver 文献表、图表题注与正文交叉引用。XSLT 规则可直接参考 |
| **docxToJats (PHP)** | PHP 7.3+ | OOXML → JATS | GPL（项目内） | DOCX2JATS 的 PHP 重写版；测试覆盖 Word/LibreOffice/Google Docs 产出 |
| **docxConverter（OJS 插件）** | 纯 PHP | docx → JATS，喂给 Texture 编辑器 | （README 未明示，随 OJS 生态） | OJS 3.1+ 插件。**支持公式、JPEG/PNG 图、带 row/colspan 的表、脚注**；引文支持 Zotero/原始引文，但**结构化解析尚未实现**（重要：它也卡在引文结构化这一关） |
| **Texture / Substance / Stencila** | JavaScript | JATS 可视化所见即所得编辑器 | MIT/类似 | Substance 库 + Texture 编辑器，**JATS 为原生交换格式**。**注意：Texture 项目已停止维护，不兼容最新 OJS**。可作"人工校对环节"参考，不建议作核心依赖 |
| **OJS (Open Journal Systems)** | PHP | 期刊生产工作流平台 | GPL | 是 docxConverter/Texture 的宿主平台，定义了 docx→JATS→排版的完整生产链路 |

底层 docx 库（非端到端，但常被引用作组件）：
- **Apache POI**（Java，Apache-2.0）：读写 OOXML，可拿到 `word/document.xml` 全部内容含数学。
- **python-docx**（MIT）：见 §4，**不直接暴露 OMML 公式**。
- **docx4j**（Java，Apache-2.0）：完整 OOXML 对象模型，适合需要精细控制的 Java 方案。

**借鉴结论**：
- 若用 Python 主栈 → 借鉴 **meTypeset 的 "docx→中介→JATS" 分层** + **DOCX2JATS 的 XSLT 映射规则**，但 docx 解析改用 lxml 直读 `document.xml`（§4）。
- **pandoc 可作快速基线/兜底**，但其 front 元数据与参考文献结构化几乎都要手工补，不适合作为唯一引擎。
- 所有现成工具的**共同短板都是参考文献结构化**（docxConverter 明确"未实现"），这正是 LLM/专用解析器（§5、§6）的差异化机会点。

来源：pandoc 主页与 JATS writer https://pandoc.org/ ／ https://hackage.haskell.org/package/pandoc-2.2/docs/Text-Pandoc-Writers-JATS.html ；mfenner/pandoc-jats Lua writer https://github.com/mfenner/pandoc-jats ；meTypeset https://github.com/MartinPaulEve/meTypeset ；DOCX2JATS https://github.com/Vitaliy-1/DOCX2JATS ；docxToJats https://github.com/Vitaliy-1/docxToJats ；docxConverter README https://github.com/Vitaliy-1/docxConverter/blob/main/README.md ；Texture（含"已停维护"）https://github.com/pkp/texture/releases ／ https://www.ncbi.nlm.nih.gov/books/NBK540950/ ；Texture@eLife https://elifesciences.org/labs/8de87c33/texture-an-open-science-manuscript-editor

### 2.1 pandoc 实操限制（PKP 论坛社区经验，已查证）

- **优点**：`--from=docx+citations` 可直接读取 Word 内嵌的 **EndNote/Zotero 引文域**；结构、标题、段落转换稳。
- **缺点/坑**：
  - 参考文献表本身**不自动结构化**（需 `ref-extractor` 等辅助，或作者提供 Zotero/Mendeley 数据库）。
  - **表格与数学转换"会乱"**，需要 `-N` 参数或换路径。
  - bibtex key **不能含冒号或斜杠**，否则生成非法 JATS（pandoc bug）。
  - **社区推荐两步法：docx→HTML→JATS，结果更好**。
  - front 元数据需在 XML 编辑器里手工补进 `<front>`。

来源：PKP 社区论坛 https://forum.pkp.sfu.ca/t/pandoc-to-convert-docx-to-jats/41922 ；Zagreb JATS XML Converter Service（基于 pandoc 的在线服务）https://lab.operas-eu.org/2024/11/21/enhancing-machine-readability-in-scientific-publishing-introducing-the-jats-xml-converter-service/

---

## 3. Word 公式 OMML → MathML 转换方案（可落地路径）

### 3.1 三条主路径

1. **微软官方 OMML2MML.xsl（XSLT 1.0）—— 最权威、最常用**
   - 这是 Office 自带、Word"复制为 MathML"背后用的样式表，源出 **TEI XML 的 XSL stylesheets** 项目。
   - 实务上 `OMML2MML.xsl` 完成主体转换，余下由 David Carlisle 2007 年的 `xhtml-mathml.xsl` 收尾。
   - 文件随 Office 安装（如 Word 2016 在 `...\Office16`）。**注意分发许可**：直接随产品分发 MS 的 xsl 有合规问题，**推荐用下方开源移植版**。
   - 在 Python 中：`lxml.etree.XSLT` 加载 OMML2MML.xsl，对从 `word/document.xml` 抽出的 `<m:oMath>` 节点做转换即可得 MathML（presentation MathML）。
   - 开源移植：**plurimath/omml2mathml**（Ruby gem）、**npm omml2mathml**（Office xsl 的 JS 移植，修了若干 bug）、scienceai/omml2mathml。meTypeset 仓库里也直接带了一份 `docx/utils/maths/omml2mml.xsl` 可复用。

2. **pandoc / texmath 库**
   - pandoc 的 **texmath**（Haskell 库）支持 TeX math、Presentation MathML、**OMML** 三者互转，并能输出 eqn/typst/native。pandoc 读 docx 时即用它处理 `m:oMath`，AST 层保留 inline/display 区分。
   - 适合作为"调用 pandoc 即顺带得 MathML"的省事路径，但受 §2.1 的整体 docx 转换缺陷牵连。

3. **latex2mathml 等 Python 库（间接路径）**
   - 仅当公式已是 LaTeX（如作者用 MathType→LaTeX，或 OCR）时，用 `latex2mathml` 直接 TeX→MathML。**对原生 Word OMML 公式不适用**——OMML 不是 LaTeX。

### 3.2 落地建议（Python 栈，推荐组合）

```
word/document.xml --(lxml 抽取 m:oMath 节点)--> OMML 片段
   --(lxml.etree.XSLT 加载开源 OMML2MML.xsl)--> Presentation MathML
   --(包进 <disp-formula>/<inline-formula> 的 <mml:math>)--> JATS
```

- JATS 1.3 内嵌 MathML 用 **MathML3**（命名空间 `xmlns:mml="http://www.w3.org/1998/Math/MathML"`）；行间公式 `<disp-formula>`，行内 `<inline-formula>`。
- 区分 inline/display：看原 OMML 是 `<m:oMath>`（行内）还是 `<m:oMathPara>`（独立段落=display）。注意 pandoc 的已知 issue：仅含单个行内公式的段落会被 Word 视觉上当 display 渲染——自研可在解析段落时直接据 `oMathPara` 判定，规避此坑。

来源：plurimath/omml2mathml https://github.com/plurimath/omml2mathml ；npm omml2mathml（"Office xsl 的移植，修了 bug"）https://www.npmjs.com/package/omml2mathml ；OMML2MML.xsl 来历与 xhtml-mathml.xsl https://learn.microsoft.com/en-us/answers/questions/5240471/how-to-convert-omml-into-mathml-in-word-or-by-thir ；MS 关于 xsl 分发 https://learn.microsoft.com/en-us/answers/questions/5286296/redistrubution-of-omml2mml-xsl-from-ms-office ；meTypeset 自带 omml2mml.xsl https://github.com/MartinPaulEve/meTypeset/blob/master/docx/utils/maths/omml2mml.xsl ；texmath https://github.com/jgm/texmath ／ https://hackage.haskell.org/package/texmath ；pandoc inline/display OMML issue https://github.com/jgm/pandoc/issues/11674

---

## 4. docx 解析的 Python 生态：python-docx 能做/不能做什么

### 4.1 python-docx 能做

- 段落、run（含粗斜体/字号/颜色等直接格式与样式名）、标题（通过 `paragraph.style.name`）、表格（`doc.tables`，含合并近似处理）、列表、图片关系、节/页设置。
- 适合抽取**正文文本结构 + 段落样式 → 映射到 JATS 的 `<sec>`/`<p>`/标题层级**。

### 4.2 python-docx 不能做（关键限制，已查证）

- **不解析 OMML 数学公式**：`<m:oMath>` 不在 python-docx 对象模型内，需绕过它直接处理 XML（GitHub issue #213）。
- **不直接插入/读取 MathML/OMML 公式**：要手工对 XML 做 XSL 变换并把 etree 节点塞进段落（issue #320、#290）。
- 对某些样式、字段域（field codes，如 Zotero/EndNote 引文域 `w:fldSimple`/`w:instrText`）、批注、修订（track changes）、脚注/尾注等**暴露不全或不暴露**。

### 4.3 何时直接解析 `word/document.xml`（lxml）

**结论：数学公式、字段域引文、脚注/尾注、修订批注、精细样式 → 必须用 lxml 直读 OOXML。** 实务做法：
- `python-docx` 拿对象模型做主体结构；需要时通过 `paragraph._p`（底层 lxml element）下钻到原始 XML。
- 或直接 `zipfile` 解 .docx，`lxml.etree.parse('word/document.xml')`，按 OOXML 命名空间（`w:`、`m:`、`r:`）XPath 抽取 `m:oMath`、`w:fldSimple`、`w:footnoteReference` 等。
- 脚注/尾注在 `word/footnotes.xml`、`word/endnotes.xml`；图片关系在 `word/_rels/document.xml.rels`。

来源：python-docx OMML issue #213 https://github.com/python-openxml/python-docx/issues/213 ；插入 MathML/OMML 限制 issue #320 https://github.com/python-openxml/python-docx/issues/320 ；MML2OMML.XSL 反向变换 + lxml 注入 etree 的官方建议见同上 issue 串。

---

## 5. 参考文献解析（docx 纯文本引用串 → 结构化 citation）

### 5.1 候选方案对比（已查证评测）

| 方案 | 技术 | 输出 | 优劣 |
|---|---|---|---|
| **GROBID** | CRF（Wapiti，默认）+ 可选深度模型（BidLSTM-CRF、BERT-CRF/DeLFT） | 默认 **TEI XML**（需自行转 CSL/JATS） | **精度/召回在引文字段标注上高于 anystyle**；有 REST API（`processCitationList` 收每行一条 raw string 的 txt）、Docker、Java/Python 客户端、批处理。适合服务化 |
| **anystyle** | CRF | **直接输出 CSL-JSON / BibTeX** | 在更广语料（56 篇、27 学科）综合得分最佳；输出格式对接 CSL 生态最省事。Ruby 工具 |
| **CERMINE / ParsCit** | ML/CRF | 各异 | 评测中次于上两者（GROBID 开箱 F1≈0.89，CERMINE≈0.83，ParsCit≈0.75） |
| **规则/正则** | 手写规则 | 自定义 | 对单一固定文献样式（如全刊统一 Vancouver/AMA）快且可控；跨样式泛化差 |
| **LLM 抽取** | DeepSeek/Qwen 等 | 任意结构化 JSON | 见 §6，泛化最好但需控成本/幻觉 |

**评测要点**：GROBID 与 anystyle 综合最强，文献多建议**二者配合使用**；不同学科各有胜负，应按目标语料选型。两者均为 CRF 系。

来源：综合评测（arXiv 2205.14677）https://arxiv.org/abs/2205.14677 ／ https://link.springer.com/chapter/10.1007/978-3-031-16802-4_42 ；ML vs Rules 评测 https://www.researchgate.net/publication/325492402 ；GROBID 文档（processCitationList、TEI 输出、CRF/DeLFT）https://grobid.readthedocs.io/en/latest/References/ ／ https://grobid.readthedocs.io/en/latest/Grobid-service/ ；GROBID 在线工具 https://grobid.org/ ；anystyle 输出 CSL-JSON 与对比 https://github.com/kermitt2/grobid/issues/335

### 5.2 对本竞赛的落地建议

- Word 内若有 **Zotero/EndNote 域**（pandoc `+citations` 或自行解析 `w:instrText`）→ 优先直取结构化数据，最稳。
- 纯文本引用串 → **GROBID `processCitationList` 或 anystyle** 做主解析，得到字段后映射为 `<element-citation>`；解析失败/低置信的条目降级为 `<mixed-citation>` 保形兜底。
- TEI→JATS 字段映射需自写（GROBID 不直接出 JATS），但字段名相近，工作量可控。

---

## 6. 用 LLM（DeepSeek/Qwen）做语义结构化抽取：可行性与混合架构

### 6.1 可行性（有近期实证）

- 已有研究用 **Qwen3-8B 抽取学术摘要的原子维度**（研究问题/方法/发现），`temperature=0.0` 求确定性输出——表明开源中文系模型在学术文本结构化抽取上可用。
- 大规模评测覆盖 **Qwen、DeepSeek、Gemma、GLM、Mistral**，参数 0.5B–72B；7B 级别在抽取精度/推理成本上性价比突出，14B（如 Qwen2.5:14b）跨场景最稳定。

### 6.2 混合架构是最佳实践（关键结论）

实证显示**纯 LLM 不是最优**，**确定性规则 + LLM 兜底**的混合管线在准确率与效率上都更好：
- 一项学术 PDF 抽取评测对比 LLM-only / 正则+LLM 混合 / Camelot+LLM 兜底三策略，**Camelot+LLM 兜底准确率达 0.99–1.00 且多数 PDF <1 秒**，优于 LLM-only。
- 另有专门的 "Hybrid LLM-Rule-based Data Extraction" 工作系统化论证规则与 LLM 结合的收益。

### 6.3 对 Word→JATS 的推荐架构

```
确定性层（先跑、能定就定）：
  样式/OOXML 规则 → sec 层级、图表题注、公式(OMML→MathML)、Zotero 域引文
        │  低置信 / 规则未覆盖
        ▼
LLM 层（兜底 + 语义判定）：
  作者-机构-ORCID 归属、摘要/关键词边界、纯文本引文串结构化、章节语义类型
        │
        ▼
Schema 约束输出：用 JSON Schema / 函数调用约束 LLM 直接产出 JATS 字段，再模板化拼 XML
```

**典型 prompt 策略**：`temperature=0`；给定**目标 JSON Schema/字段定义**强约束输出；**few-shot 给 1–2 个标注样例**；**分维度拆解**（作者块、参考文献逐条、章节）而非整篇一次性抽；对每条引文要求模型同时回**置信度**以决定走 element- 还是 mixed-citation。**务必规则法做后校验**（ORCID 格式、DOI 正则、年份范围、XML 合法性），消解幻觉。

来源：Hybrid LLM-Rule-based Data Extraction https://arxiv.org/pdf/2404.15604 ；Qwen3-8B 摘要结构化（temp=0）https://arxiv.org/pdf/2601.08901 ；Hybrid 确定性-LLM 学术 PDF 抽取评测 https://arxiv.org/pdf/2604.00003 ；多模型规模评测 https://arxiv.org/html/2510.10138v1 ；病理报告结构化抽取（方法论参考）https://www.sciencedirect.com/science/article/pii/S2153353925001075

> 模型与 prompt 的具体 API 参数（如 DeepSeek/Qwen 的上下文窗口、function calling 语法）属各厂商文档，落地前应查对应官方 API 文档确认，不在本调研已查证范围内。

---

## 7. JATS 校验：DTD + Schematron（如何验证生成的 XML 合规）

### 7.1 两层校验（必须都做）

1. **DTD 校验**（语法/结构合法性）：验证文档符合其 `<!DOCTYPE>` 声明的 JATS DTD（结构、元素嵌套、属性枚举）。
2. **Schematron 校验**（业务规则/最佳实践）：DTD 管不到的"内容与风格约束"由 Schematron 用 XPath 规则断言。**JATS4R Schematron 是事实标准的合规规则集**。

### 7.2 JATS4R 验证工具链

- **JATS4R 在线/服务版校验器**：对输入做 DTD 校验（按 doctype 选 NISO JATS 1.0/1.1/1.2 …，返回 JSON）+ JATS4R Schematron 校验，给出报告。
  - 在线：https://jats4r.niso.org/jats4r-validator/
  - Web service（含 Schematron）：https://github.com/JATS4R/jats-validator ／ Docker 版 https://github.com/JATS4R/jats-validator-docker
  - 客户端校验器（Saxon-CE）：https://github.com/JATS4R/validator
- **master Schematron 文件** `jats4r.sch` 汇总全部主题、仅含 error-level 测试，决定 conformance。可"挑选规则组合成自己的 Schematron"。
- 校验脚本 `validate.sh`：处理 schematron 并构建 flattened JATS DTD，用 SaxonProcessor 跑 Schematron。

> 注意：JATS4R 校验器明确支持 JATS **1.0/1.1/1.2**；本方案目标是 **1.3**，落地时需确认所用校验器/规则集对 1.3 的覆盖，必要时直接用 1.3 DTD 做 DTD 层校验、再叠加 JATS4R Schematron（规则多数向后兼容，但应实测）。

### 7.3 Python 本地集成（自研推荐）

`lxml` 同时支持两层校验，便于集成进转换流水线做 CI：
- **DTD**：`lxml.etree.DTD('JATS-journalpublishing1-3.dtd').validate(tree)`。
- **Schematron**：`lxml.isoschematron.Schematron(sch_tree)`，`.validate()` 返回 True/False，可取详细报告。
- 重型/官方一致性可用 **Saxon** 跑 ISO Schematron（XSLT2/3），与 JATS4R 官方一致。
- 商业编辑器 **oXygen XML** 内置 JATS 框架，适合人工终校环节。

来源：JATS4R validator https://jats4r.niso.org/jats4r-validator/ ；jats-validator（DTD+Schematron 双校验、返回 JSON）https://github.com/JATS4R/jats-validator ／ README https://github.com/JATS4R/jats-validator/blob/master/README.md ；Docker https://github.com/JATS4R/jats-validator-docker ；jats4r.sch 组织方式 https://jats4r.niso.org/combine-your-tests-with-open-source-rules-to-build-your-ideal-schematron/ ；Schematron 入门 https://jats4r.niso.org/schematron-a-handy-xml-tool-thats-not-just-for-villains/ ；lxml validation（DTD + isoschematron）https://lxml.de/validation.html ；客户端校验器架构（Saxon-CE）https://www.balisage.net/Proceedings/vol15/html/Beck01/BalisageVol15-Beck01.html

---

## 综合架构建议（给竞赛方案的一句话收口）

**Python 主栈推荐**：`zipfile + lxml 直读 word/document.xml`（结构/样式/公式/字段域）+ `python-docx` 辅助拿对象模型 → **规则层**搭骨架（sec/fig/table-wrap/disp-formula，OMML 经开源 OMML2MML.xsl→MathML）→ **GROBID/anystyle + LLM 兜底**做作者-ORCID-机构与参考文献结构化（高置信走 `element-citation`，低置信走 `mixed-citation`）→ 按 **JATS Publishing 1.3 DTD** 模板拼 XML → **lxml DTD 校验 + JATS4R Schematron** 双重校验闭环。可借鉴 meTypeset 的分层与 DOCX2JATS 的 XSLT 映射规则，但避免直接依赖已停维护的 Texture 作核心。

---

### 全部来源汇总（核心）

- JATS 1.3 Tag Library https://jats.nlm.nih.gov/publishing/tag-library/1.3/index.html ｜ DTD https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd ｜ NISO 标准 https://www.niso.org/publications/z3996-2021-jats
- 工具：pandoc https://pandoc.org/ ｜ meTypeset https://github.com/MartinPaulEve/meTypeset ｜ DOCX2JATS https://github.com/Vitaliy-1/DOCX2JATS ｜ docxConverter https://github.com/Vitaliy-1/docxConverter ｜ Texture https://github.com/pkp/texture
- OMML→MathML：https://github.com/plurimath/omml2mathml ｜ texmath https://github.com/jgm/texmath
- python-docx 限制 https://github.com/python-openxml/python-docx/issues/213
- 引文解析：GROBID https://grobid.readthedocs.io/en/latest/References/ ｜ 评测 https://arxiv.org/abs/2205.14677
- LLM 混合 https://arxiv.org/pdf/2404.15604 ｜ https://arxiv.org/pdf/2604.00003
- 校验：JATS4R https://jats4r.niso.org/jats4r-validator/ ｜ jats-validator https://github.com/JATS4R/jats-validator ｜ lxml https://lxml.de/validation.html