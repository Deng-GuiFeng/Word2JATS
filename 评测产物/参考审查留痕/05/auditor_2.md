# 样例05 结构参考.xml 审查留痕(审查员#2)

依据:docx = 样例数据/05/初始文件.docx(word/document.xml);规则 = 数据与映射规格.md + 评测体系设计.md。上线版本未看。

## front
- article-type=research-article ✓;subject=Article(docx 首行" Article ")✓。
- journal-meta:HSF / The Heart Surgery Forum / Heart Surg. Forum / issn 1098-3511,1522-6662 / doi 10.31083/HSF49106 —— 全部与 B 真值一致 ✓。
- article-id publisher-id = 10.31083/HSF49106,但 graphic href 用前缀 HSF49106/,publisher-id 疑应为 HSF49106(见疑点)。
- 标题逐字一致 ✓(含结尾句号,无 Title Case 改写)。
- 4 位作者顺序/姓名/aff 上标(1,2,2,2)与 docx 一致;Mistiaen 通讯(cor1)由"Address for correspondence"块正确推得;无 dagger/#/ORCID(docx 无)✓。
- aff1/aff2 逐字 ✓;编辑 George Isaac(docx"学编:Isaac George")✓。
- 日期 received 15/12/2025、rev-recd 26/03/2026、accepted 30/03/2026 与 docx 三行一致 ✓。
- permissions 2026 / CC BY 4.0 模板 B ✓;摘要单 p 逐字 ✓;关键词 4 个 ✓。

## body
- 5 章:Introduction/Methods/Results/Discussion/Conclusions,与 docx 标题一致 ✓,无凭空建/漏节。
- 正文段落逐字比对 ✓;docx 笔误全部保留(cross-camp、Cohens' D、<0/001、± /2.4%、elurgent)✓。
- 表 T1–T10 全部在;RT1–RT4 (KM 风险人数表)数值逐格核对与 docx 一致 ✓,脚注逐字(含 elurgent 笔误)✓。
- 图 F1–F4 caption 逐字 ✓,graphic 指向 figures.zip 内真实存在的 fig-01..04.png ✓。
- 交叉引用:Table one..ten → T1..T10 ✓(大小写随 docx);figures 1&2、3&4 → F1F2、F3F4 ✓。
- bibr xref 全表核对:唯一区间 [9,20-22] 未展开 → 只有 b20、b22,**b21 缺失**(见疑点,规则 130 行明确要求展开)。其余逗号列表均逐一链接。
- T1 "Age" 汇总行 p=<0.001、X²=22.3 被放进了 elective/SAVR(%) 列(见疑点,低)。

## back
- 声明小节:Author Contributions(docx 有显式标签)、Ethics/ Funding/ Acknowledgment/ Conflicts(docx 为裸句)全部捕获,内容逐字 ✓;Publisher's Note fn-group 为 B 模板 ✓。给裸句补标准标题属结构化标签,内容未编造,不判错。
- 参考文献 27 条 b1–b27,label [1]–[27];逐条核对作者名/article-title/source/year/volume/fpage/lpage/doi/pmid 与 docx 一致,typo(201818、vol 1、before\tand、10.1136/ bmjph)均保留 ✓。b21 定义存在但无任何 xref 引用(悬空,源于上面区间未展开)。

## 结论
- 唯一高置信错误:引用区间 [20-22] 未展开,b21 漏链(back/body 交叉引用同时受影响)。
- 低置信:publisher-id 取全 DOI;T1 Age 行 p/X² 错列。
