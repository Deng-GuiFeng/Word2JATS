# 样例04 结构参考.xml 审查留痕 — 审查员 #2

判据:源 docx(word/document.xml,372 段)+ 映射规格/评测规则。不使用上线版本。

## 覆盖与方法
用 lxml 解析 docx 与结构参考.xml,逐区自动比对。

## front(全部核对)
- article-type=review-article,subject=Review;docx 首段[0]="Review" ✓
- journal-meta:JIN / Journal of Integrative Neuroscience / J. Integr. Neurosci. / 0219-6352 / 1757-448X ✓ 与 B 真值一致
- article-id doi=10.31083/JIN52316 ✓
- 标题:与 docx[2] 逐字一致,无 Title Case 改写、无截断 ✓
- 作者 7 人:surname/given、上标→aff xref、通讯*、#共同贡献、email 全部与 docx[4] 一致 ✓
- ORCID 归属:Carmen Rubio 无(docx 确无);其余 6 人 orcid 与 docx[18-23] 一一对应,含 0000000242784814→0000-0002-4278-4814、0000000231846522→0000-0002-3184-6522 的补连字符,4-4-4-4 规范 ✓。注:参考未加 authenticated="true"(见疑点)
- 单位 6 条:与 docx[6-11] 逐字一致(aff3 末尾句号、aff5 无邮编均忠实保留) ✓
- 编辑:Hsu / Kuei-Sen / Academic Editor ✓(docx[25])
- 日期:received 27/3/2026、rev-recd 27/4/2026、accepted 30/4/2026,与 docx[27-29] 一致 ✓
- permissions:© 2026 / IMR Press / CC BY 4.0 模板 ✓
- 摘要:单 p,与 docx[33] 逐字一致(docx 本就是一段)✓
- 关键词:8 个,与 docx[36] 一致 ✓

## body(全部核对)
- 章节树 S1–S9,标题与 docx[38/47/57/67/76/95/105/114/123] 逐字一致,层级/编号正确,无凭空建节/漏节 ✓
- 正文段落:所有 sec/p itertext 归一化后均逐字命中 docx(0 例外)✓
- 图:2 图,label "Figure 1./2." 与 docx 一致(忠实 docx,而非套用 "Fig.");caption 逐字命中 docx;graphic href=JIN52316/fig1.jpg、fig2.jpg,figures.zip 内确有 fig1.jpg/fig2.jpg ✓
- 表:docx 3 张真 w:tbl,T001/T002/T003 单元格(36/40/48)全部命中 docx;T003 Ethosuximide 单元格的重复口误"...overload mitochondrial Ca2+ homeostasis..."忠实保留(docx 原样)✓
- 公式:docx 无 OMML,参考无公式 ✓
- xref:fig/table 无悬空;bibr 145 个 rid 全部有对应 ref,145 ref 全部被引;docx 无区间式[n-m]引用,无展开问题。**但发现 3 处正文数字引用未生成 xref(见疑点)**

## back(全部核对)
- 声明小节顺序 = docx:Author Contributions/Funding/Data Availability/Conflicts of interest/Ethics,标题与内容逐字一致("declared"过去式、"Not applicable"无句点均保留)✓,无补写 C 样板
- glossary "List of abbreviations":62 term = docx 62 条,逐条 term+def 一致(仅上标归一)✓
- 参考文献:145 条 = docx 145 条([1]..[145] 连续),全部 element-citation;year/volume/fpage/lpage、article-title、source、所有 surname 均命中 docx 原文,无丢条/替换/copyedit ✓
- **未见 fn-group / Publisher's Note(见疑点,低置信)**

## 疑点
1. [high] S5.p1 正文 "[76]" 为纯文本,未生成 xref(同段其他 [76,77] 已链)
2. [high] S5.p2 正文 "[76]" 为纯文本,未生成 xref
3. [high] S6.p2 正文 "[19,99,114]" 为纯文本,未展开为 3 个 xref(b19/b99/b114 均存在)
4. [low] 缺 fn-group Publisher's Note(设计文档 §10 列为固定模板 B)
5. [low] 每条参考文献 DOI 以 <ext-link> 承载,而非规格 §7 的 <pub-id pub-id-type="doi">
6. [low] ORCID 未加 authenticated="true"(规格 §3.2 要求;但属外部认证信息,省略亦可辩)
