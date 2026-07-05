# S01 结构参考.xml 审查留痕 — 独立审查员 #2

依据:源 docx(word/document.xml) + 映射规格(docs/00-调研/02-数据与映射规格.md) + 评测体系设计。不参照上线版本。

## front
- article-type=research-article ✓;subject=Original Research ✓(docx P1)。
- journal-meta:id=CEOG ✓;issn 0390-6663/2709-0094 ✓;doi 10.31083/CEOG48513 ✓;publisher IMR Press ✓。
  journal-title 用 "Obstetrics & Gynecology"(&),任务 B 真值为 "and" → 记疑点(low)。
- 标题逐字("Changzhi area" 小写保留)✓;作者 5 人+aff1+Li Xiaoze 通讯*/email/ORCID(4-4-4-4)✓;
  aff1 逐字 ✓;编辑 Carlucci Stefania ✓;日期 received/rev-recd/accepted 24-11-2025/2-2-2026/27-2-2026 ✓;
  permissions 2026+CC BY 4.0 ✓;摘要拆 4 sec 文本逐字("2.5%")✓;关键词 5 个 ✓。

## body
- 章节树 1–6/2.1–2.4/2.2.1–2.2.5/3.1–3.5 全对;笔误 lnformation/lnitial 保留 ✓。
- 段落逐字一致(含 P80 docx 重复短语忠实保留)。
- italic 与 docx run 边界一致(表1 前3行 SMN 斜体+1正、第4行 SMN1 全斜体 = docx 原样)✓。
- 图 3(F001-3)href=CEOG48513/fig-0N.jpg 对 figures.zip ✓;表 5(T001-5)结构/脚注 ✓;地区名怪空格保留 ✓。
- 公式:docx m:oMath=0,参考无 MathML ✓;文本上标 Z^2/χ^2 已 sup ✓。
- xref:Fig/Table 全链 ✓;文献区间仅 [13-16]、[17–19] 两处,**参考未展开**:S4.p1 仅 b13/b16、b17/b19,
  漏 b14/b15/b18 独立 xref → 违反规格 4.3(HIGH)。单条/逗号列表全链 ✓。

## back
- 声明 S7–S12+ack 全对,标题+正文逐字,无补写/漏。fn-group Publisher's Note = B 模板 ✓。
- footnotes/endnotes 空。参考文献 30 条逐条核对字段全一致(In Chinese/collab 顺序/2026 年/"Hao S J"空格等忠实),label 对。

## 结论
极忠实。确证:两处区间 xref 未展开(漏 b14/b15/b18)。journal-title & vs and 记 low。
