# S04 结构参考.xml 审查留痕 — 审查员 #1

依据:初始文件.docx(word/document.xml)+ 映射规格 02 + 评测体系设计。只报 docx 有证据的疑点。

## 覆盖矩阵

### front
1. article-type=research-article / subject=Original Research —— docx para0 "Original Research"(斜体稿件类型)。✓ 与 B 真值一致。
2. journal-meta:RCM / Reviews in Cardiovascular Medicine / Rev. Cardiovasc. Med. / issn 1530-6550,2153-8174 / IMR Press。✓ 全部对上 B 真值。
3. article-id doi=10.31083/RCM49717 ✓;另一条 pub-id-type="publisher-id" 取了**完整 DOI** 而非文章号 RCM49717(§9/§11 约定文章号=DOI去前缀,graphic href 用的正是 RCM49717/)。→ 低置信疑点。
4. 标题:逐字与 docx para1 完全一致,无截断/无 Title-Case 改写。✓
5. 作者:6 人,surname=末词全部正确(Jiang/Feng/Liu/Gong/Peng/Wang);aff xref Jiang123 Feng1 Liu1 Gong123 Peng123 Wang123 ✓;通讯 * = Peng、Wang ✓;**Jiang 的上标 `#`(共同贡献标记)在 XML 中被丢弃**(唯一真实 # 见 document.xml 文本节点 '1,2,3, #';其余 9 个 # 均为 EndNote 元数据里的 `&#xD;`)→ 疑点。ORCID:docx 全文 orcid=0,XML 无,✓ 未臆造。email:docx 无(@ 仅出现在参考文献元数据里),XML corresp 无 email,✓。
6. aff:3 条,文本逐字=docx para3/4/5,带 sup 标号 ✓。
7. 编辑:Boriani Giuseppe、Joung Boyoung,role=Academic Editor ✓(docx para7 "Academic editor: Giuseppe Boriani, Boyoung Joung")。
8. 日期:received 5/1/2026、rev-recd 9/2/2026、accepted 26/2/2026(D/M/Y,26 只能是日→格式确认)✓=docx para9/10/11。
9. permissions:© 2026 / CC BY 4.0(B 模板,年份=录用年)✓。
10. abstract:4 个结构化 sec(Background/Methods/Results/Conclusion),文本逐字=docx para14-17 ✓。
11. kwd:5 个 ✓=docx para18。

### body
12. 章节树:S1 Introduction / S2(2.1-2.4)/ S3(3.1-3.5)/ S4 Discussion / S5 Conclusion —— 与 docx heading 完全对应,无凭空建节/漏节,节号(2.1 等)保留 ✓。
13. 段落文本:抽查 methods、discussion、conclusion 均逐字;docx 笔误 "≥125 p g/mL" 在 XML line200 原样保留 ✓。
14. 行内格式:统计量 <italic>P</italic>、期刊名 italic ✓。
15. 图:6 张 F001-F006;caption 逐字=docx;label "Figure. N." 与 docx 一致;graphic href fig-01.tif/02.tif/03.tif/04.png/05.tif/06.jpg **全部命中 figures.zip 真实文件** ✓。
16. 表:3 张;docx tbl 42/7/13 行,XML 结构与单元格逐格核对一致;笔误 "O.847"(字母O)、"0.49"、footnote "HMGB1>1.472ng/m"(缺L)均原样保留 ✓;table-wrap-foot 脚注 ✓。
17. 公式:docx oMath=0,XML 无公式 ✓ 无整条丢式。
18. xref:docx 引用编号 1-41 全部,XML bibr 覆盖 1-41(43 处,含重复引用),无区间需展开;Fig1-6 各 1 处、Table1×2/2×1/3×1 全部对上;"Supplementary Fig.1 / Supplementary Table 1" 正确未建 xref。✓ 无悬空/漏链/多链。

### back
19. 声明小节:docx 7 节(Availability / Author Contributions / Ethics / Funding / Conflict of Interest / Declaration of AI / Supplementary Material)全部存在且逐字;Conflict of Interest 正文保留 "no competing interests"(未替换成模板 "no conflicts");未臆造 Acknowledgment。✓
20. fn-group:Publisher's Note(B 模板)✓;无 glossary 需求。
21. 参考文献:41=41;脚本逐条核对 year/volume/fpage/lpage/first-surname/article-title/DOI 全部与 docx 一致;作者姓名无遗漏、et al 一致(b8 suffix "Jr" 保留)。✓ 无替换/漏条/丢作者。

## 疑点结论
- [medium] front-authors:Jiang 的上标 `#`(共同贡献标记)被丢弃,无 fn/xref。
- [low] front:article-id publisher-id 用完整 DOI 而非文章号 RCM49717。

其余全区未发现 docx 有据的错误。参考整体忠实度高。
