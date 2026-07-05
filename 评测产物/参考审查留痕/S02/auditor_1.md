# S02 结构参考.xml 审查留痕 — 审查员 #1

依据:初始文件.docx(word/document.xml)+ 映射规格.md + 评测体系。上线版本未参考。

## 覆盖与核对方法
- docx 解压 → 219 段落逐段提取(含上标/斜体/粗体标记),存 docx_paras.txt。
- XML 全 2308 行读毕;脚本核对 ref-list ids、bibr/fig/table 全部 rid、tables、affs、authors、ORCID。

## front(逐项)
- article-type=review-article、subject=Review:docx P1 "Review" ✓,B真值 ✓。
- journal-meta:journal-id=CEOG ✓,issn 0390-6663/2709-0094 ✓,doi 10.31083/CEOG51386 ✓。**journal-title 用 "&"(Clinical and Experimental Obstetrics & Gynecology),任务 B真值写 "and"** → 见疑点。docx 参考文献(b5/b52/b53)自身用 "&"。
- 标题:逐字 ✓(含 "Signs o' the Times",无 Title Case 改写)。
- 10 位作者:surname/given、上标 aff、通讯*、10 个 ORCID 就近回填 + https 前缀 + authenticated,全部逐一核对 = 完全一致 ✓(Roberta Musso 0009-0004-5024-2486、Gambaudo 0009-0005-5422-2463 等 4-4-4-4 规范 ✓)。email 两个 ✓。
- 8 个 aff:逐字 ✓(含 "Department Of" 大写 O 保留)。
- 编辑:Iavazzo Christos / Dahan Michael H.,Academic Editor ✓(docx P27)。
- 日期:received 2/3/2026、rev-recd 30/3/2026、accepted 14/4/2026 ✓(docx P29-31)。
- permissions:2026 + CC BY 4.0 模板 ✓(B)。
- 摘要:**docx 为单段(P34),内含 4 个内联粗体标签;参考拆成 4 个结构化 sec** → 见疑点(规则 §3.7 "是一段就转成一个 p")。文本逐字 ✓。
- 关键词:5 个 ✓。

## body(逐项)
- 章节树:S1-S9 及子节层级与 docx 编号完全对应(含无编号子节 S2.SS1)✓,无凭空建节/漏节。
- 段落:逐段抽样比对,内容逐字 ✓(保留 "(ProMisE, )as well as" 笔误、双 "exclusively" 等)。
- 行内:italic 忠实 docx(Signs o' the Times、PALB2 等基因斜体)✓。
- 图:2 图(F001/F002),caption 逐字 ✓,graphic id F00n.g1 ✓。**href 用 .tif** → 见疑点(规格 §5/§11 写 .jpg;figures.zip 实为 fig-01.tif/fig-02.tif,故指向真实文件)。media image1.png=页眉 logo 正确排除。
- 表:T001(12 行)、T002(5 行)结构+单元格+表脚注(T001-fn1/T002-fn1)逐字 ✓。
- 公式:docx oMath=0,XML formula=0 ✓,无丢式。
- xref:除下述范围外,全部 [n]/Fig/Table 均正确链接、无悬空、无漏链。**[13-15] 未展开**(仅 b13、b15,漏 b14)→ 见疑点(规格 §4.3 区间必须展开)。ref-list 50 条 id 与 docx 跳号(缺16/31/32)完全一致,无 dangling。

## back(逐项)
- 声明小节:Author Contributions/Ethics/Acknowledgment(ack)/Funding/Conflicts/Declaration of AI 六节全部来自 docx,顺序内容 ✓;**未虚构** Availability of Data and Materials 等 C 模板,未漏 docx 实有声明。S10.p1 "." 为 docx P184 原样保留(忠实)。
- 参考文献:逐条核对字段(person-group/etal/article-title/source/year/volume/fpage/lpage/doi),忠实 docx(保留 b36 "PI3KCA" 笔误+斜体、b41 无 doi 无 etal、b47 "Front Oncol"缩写异格式)✓。

## 结论
高置信疑点 1(xref 范围漏 b14);中 1(摘要结构);低 2(journal-title & vs and;图 .tif vs .jpg)。整体转换质量高、忠实度好。
