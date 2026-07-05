# S02 结构参考.xml 审查留痕 — 审查员 #2

判据:初始文件.docx + 映射规格 + 评测体系设计。绝不拿上线版本当标准。

## front（逐项已查）
- article-type=review-article✓;subject Review✓;journal-meta CEOG/ISSN/IMR/DOI 对齐B真值✓
  疑点:journal-title 用"&"而B真值"and"(docx参考文献支持"&",低);abbrev pubmed用全名(低)
- 标题与docx[1]逐字✓;10作者surname/given+aff上标+corresp*+ORCID逐人回填+email 全对✓
- aff8条逐字(含"Department Of")✓;编辑2人✓;日期received/rev/accepted✓;permissions✓
- abstract:docx[33]单段含4粗体标签→结构化4sec(符§3.7),文本逐字✓;关键词5✓

## body（逐项已查）
- 章节树S1..S9与docx heading(1/2/3)完全对应,无凭空/漏建节✓
- 逐节段落计数与docx完全一致,无丢/加段✓
- italic:docx仅[37]标题+[168]/[179]基因名为斜体,参考逐一吻合✓
- 图2(F001/F002)caption逐字,graphic指向figures.zip真实fig-01/02.tif✓
- 表2真表单元格逐字+footnote归位✓;无公式(docx无oMath)✓
- xref逐条比对:发现1处区间未展开(疑点1);其余全命中,无悬空/漏/多链;缺16/31/32与docx一致

## back（逐项已查）
- 声明6小节与docx[245-257]一一对应/顺序/逐字(含孤立"."、缺句号保留);未补Availability✓
- fn-group Publisher's Note✓
- 参考50条全element-citation,字段逐条忠实(含笔误PI3KCA、缩写Front Oncol保留)✓
  疑点:DOI用ext-link且href含尾随句点(低,疑点2)

## 疑点
1. HIGH S3.SS3.p1 区间[13-15]未展开(缺b14)
2. LOW 参考DOI xlink:href含尾随句点
3. LOW journal-title"&"vs B真值"and"
4. LOW abbrev pubmed用全名
