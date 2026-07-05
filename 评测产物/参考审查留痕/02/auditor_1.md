# 样例02 结构参考.xml 审查留痕 — 审查员#1

判据:docx(初始文件.docx / word/document.xml)+ 规则文档。上线版本.xml 仅用于反证"内容来自C版"。

## front
- article-type=review-article ✓ subject=Review ✓(docx 稿件类型行 para0 "Review")
- journal-meta:journal-id RCM / title Reviews in Cardiovascular Medicine / abbrev Rev. Cardiovasc. Med. / issn 1530-6550,2153-8174 / publisher IMR Press —— 全部符合 B 真值 ✓
- article-id doi 10.31083/RCM46175 ✓(与 B 真值一致)
- 标题:与 docx para1 逐字一致,无截断/无 Title Case 改写 ✓
- 作者:5 人,surname/given 逐字 ✓;上标 1/2 转 aff xref ✓;Bin Wang * 通讯 ✓;Shuang/Aili Wang † 共同贡献→fn1 ✓
- ORCID:仅 Shuang Wang(0009-0004-8148-7152)、Bin Wang(0000-0003-0201-9154),与 docx para13/14 归属一致,URL 4-4-4-4 ✓
- email wangbin87098429@126.com ✓
- aff1/aff2 逐字 ✓(含 corresp 中 "Wuhan University, Wuhan University" 冗余,忠实保留 ✓)
- 无 received/accepted 日期 —— docx 亦无,未编造 ✓
- permissions:copyright 2026 + CC BY 4.0 模板(B/模板;docx 无版权文本,2026 与 2026 出版年相符,低风险)
- 摘要:单 p,与 docx para18 逐字 ✓
- 关键词:4 个,与 docx para21 一致 ✓

## body
- 章节树 S1..S6 与 docx 加粗/编号标题一致;编号内嵌 title、label 空 —— 与映射规格 line115 示例一致 ✓
- 章节 id 用 S2.1 / S2.1.1 而非规格 line116/191 的 S2.SS1 / SSS —— id 命名偏离规格(低)
- 正文 54 个叙述段全部能在 docx 找到(逐段 containment 通过);docx 叙述段无遗漏(仅 ● Key Updates 因 ● 前缀差异被提示,实为 S2.1.1.1 标题)✓
- 图:4 个(Fig1-4),caption 与 docx 逐字一致,label Fig. N. ✓,graphic 指向 figures.zip 真实 fig-01..04.jpg ✓
- 交叉引用 xref:bibr 74 个,区间/逗号列表全部展开为独立 xref(含 docx 混用全角逗号的 [53,54，55...61] 9 连引)✓;fig xref F001×2(docx "Figure 1 (Fig. 1)")/F002-4 各1 ✓;table xref T001-8 各1 ✓;全部 rid 可解析,无悬空/漏链/多链 ✓
- 无 OMML 公式,docx 亦无 oMath ✓
- 表:见下(重大问题)

## 表格(重大)
- docx 顶层 w:tbl = 0;8 张"表"在 docx 中均为**扁平位图**(para45/123/131/141/144/147/149/153 等 w:drawing)。
- 结构参考把 8 张表全部重建为**带文本单元格 + 内联 CSS 的 <table>**(如 T001 col width 10.9%/31.3%、border:0.5pt solid #000000),并嵌入 InnerTable-Graphic1..15。
- T001 单元格文本 "EROA"、"LVEF 30%-50%" 在 docx 全文(71609 字)中**ABSENT**;而在 上线版本.xml 中 FOUND,连同 10.9%/31.3%/0.5pt solid #000000/InnerTable-Graphic 全部 FOUND。
- 结论:表格文本/样式/切图**取自 C 版上线版本,非 docx 可得**。docx 图片表应作为图片(graphic 指向表位图)处理,当前为 fabrication/faithfulness 违规。

## back
- glossary(Abbreviations and Acronyms)与 docx para25 逐字一致(含 "CE,European"、"RVol:"、"3D:" 等原样)✓
- ref-list 69 条:docx [1]-[68] + 无编号的 Freitas-Ferraz 条(→b69,补 label [69],借位合理,低)。逐条核 person-group/article-title/source/year/volume/fpage/lpage,作者数与 docx 一致(抽核 [24][37][39][54][63][7][12][11][19] 均符);[54] 与 [63] 为 docx 自身重复条,忠实保留 ✓
  - [39] 文末 article-id "S1936-8798(23)01358-4" 放入 <comment>,而同型 [7] "S0022-5223(25)00670-1" 放入 <fpage> —— 内部不一致(低)
- fn-group:Publisher's Note 模板(B)✓;fn id "fn1" vs 规格 "fn-{n}"(低)
- docx 无 funding/conflict/ack/ethics/data 等声明 —— 结构参考未编造声明小节 ✓

## caption 精校
- 除 Table 3 外全部逐字一致。Table 3:docx "China's"(U+2019 后直接 s),参考 "China' s"(插入空格)—— faithfulness 违规。
