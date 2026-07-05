# 样例01 结构参考.xml 审查留痕 — 审查员#2

判据: docx(样例数据/01/初始文件.docx 的 word/document.xml) + 设计文档/映射规格。不看上线版本。

## 覆盖
- front: article-type/subject/journal-meta/article-id/title/authors(3)/aff(2)/editor/dates/permissions/abstract/keywords 全查。
- body: 章节树(S1-S5 + SS/SSS)/正文逐段/inline格式/图1/表1-4/公式(10 oMath + 1 wmf 方程)/xref(bibr/table/fig, 含区间展开) 全查。
- back: S6-S9 声明/ref-list(b1-b39)/fn-group(Publisher's Note) 全查。

## 判为"正确/忠实"的关键点(排除)
- 标题逐字一致(含 DEPICT 首字母大小写)。
- 3 作者 surname/given/degrees/aff上标/通讯*/ORCID归属/4-4-4-4 URL/email 全对。ORCID 三人分别 0009-0006-1026-1505(Ji)/0000-0003-3315-7840(Dang)/0000-0002-5660-8897(Lv) 与 docx 一致。
- aff1/aff2 文本逐字。corresp 由通讯作者邮箱汇总(符合映射规格 3.5)。
- 日期: docx [25]2025/9/21→received, [26]2026/1/15→rev-recd, [27]2025/1/16→accepted。accepted 年份 2025 早于 received 属 docx 笔误, 参考按规则原样保留=正确。
- 结构化摘要 4 sec(Background/Methods/Results/Conclusion)标题+文本逐字; 关键词 5 条齐。
- 章节树与 docx 标题样式(1/2/3)完全吻合; [63]/[65]"Internal/External validation"docx 内为加粗普通段(非标题样式), 参考渲染成 <p><bold>..</bold></p> 忠实(已验证 docx run 加粗)。
- 表1-4 单元格值逐一比对一致(含 rowspan/colspan、上标、全角括号"）"、加粗句点"0**.**0003"等 docx 怪癖保留)。
- 公式: docx 10 个 oMath(χ²×4, p/β0/β1/β2/β3, y=x)全部转 MathML; p59 大分式在 docx 是 wmf 图(image2.wmf,267pt),参考重建为 MathML=良好覆盖。
- xref 全覆盖: 区间 [16-17]→16,17; [8-15]→8..15; [23-24]→23,24 均正确展开; Table/Fig 链接齐。
- 参考文献 b1-b39 逐条: 作者/article-title/source/year/volume/fpage/lpage/publisher-loc/collab/etal/publication-type 忠实, 含 b17"RLJN BMBM"、b25 collab 等怪值保留。
- docx 笔误一律保留(faithfulness 正确): "with and with"、"536 records of in"、"unexploreled"、"should are recommended"、"Agaston CACS"、"directed applied" 等。
- 声明 S6-S9 与 docx 一一对应, 无补写 C 样板、无漏。graphic href="RCM46777/fig-01.jpg" 符合映射规格(article-id 取自 DOI), 且 fig-01.jpg 真存在于 figures.zip。

## 疑点(见结构化输出)
1. article-id publisher-id 值=完整 DOI, 疑应为 RCM46777。
2. abbrev-journal-title pubmed 用全称, 疑应为 NLM 缩写 Rev Cardiovasc Med。
3. fig/table id 用 F1/T1, 与映射规格 F00{n}/T00{n} 不符(内部一致)。
4. Table 4 校准 CI 单元格在数字与"("间加了 docx 没有的空格。
5. copyright © 2025, 而修回在 2026-01, 版权年可能应为 2026(B 值不确定)。
6. p59 tiny wmf(image1.wmf, 9.5pt)未在参考体现(极可能为 Word 渲染碎片)。
