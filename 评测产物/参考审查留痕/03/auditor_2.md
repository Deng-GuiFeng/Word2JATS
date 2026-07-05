# 样例03 结构参考.xml 审查留痕 — 审查员#2

依据:docx (word/document.xml) + 映射规格 §1-11 + 评测体系设计。判据只看 docx+规则,不看上线版本。

## 覆盖矩阵

### front
- article-type=research-article / subject=Original Research:docx[0]"Original Research" ✓ 合B真值
- journal-meta:JIN / Journal of Integrative Neuroscience / J. Integr. Neurosci. / ISSN 0219-6352,1757-448X / IMR Press ✓ 全合B真值
- article-id doi=10.31083/JIN49347 ✓;**publisher-id 也=10.31083/JIN49347(疑点:应为 JIN49347)**
- 标题:docx[2] 与 XML 逐字一致,无截断/无Title改写 ✓
- 作者9人:Yang/Zhou/Chen/Jing/Xie/Yu/Yao/Jiang/Li,surname=末词,given逐字 ✓;aff角标:Chen→aff2,Jiang→aff3,余aff1 ✓;dagger:仅Yang+Zhou(docx"^1†""^1^†")→fn-1 ✓;通讯*:Li→cor1+email ✓
- ORCID:7人有(Yang/Zhou/Chen/Jing/Xie/Jiang/Li),Yu/Yao无——与docx[10-17]完全一致 ✓;全4-4-4-4 + https://orcid.org/ + authenticated=true ✓
- aff1/2/3 文本 docx[4-6] 逐字 ✓
- editor:Platt/Bettina Academic Editor(docx[18]) ✓
- history:received 22/12/2025 + rev-recd 24/2/2026 ✓;docx"Accepted:待接收"占位符正确未产出 ✓
- permissions:© 2026 / CC BY 4.0 模板(B) ✓
- abstract:**docx[22]为单段(4个粗体label内联),XML拆成4个sec(疑点,§3.7单段应转单p)**;文本逐字 ✓
- kwd:5个,docx[24]分号切分逐字 ✓

### body
- 章节树:S1-S5 + S2.SS1-7 + S2.SS5.SSS1-3 + S3.SS1-3 + S4.SS1,与docx标题一一对应,无凭空建节/无漏 ✓;保留docx编号不一致(3.1无点) ✓
- 段落逐字:全body prose 与docx[26-102]比对一致 ✓
- 公式:6条 disp-formula(E001-E006)= docx (1)-(6),无丢式 ✓;OMML源缺陷(VP malformed LaTeX、SE的P_i)忠实保留 ✓
- 图:F001-F005,caption逐字,graphic href=JIN49347/fig-0N.jpg;figures.zip 实含 fig-01..05.jpg ✓
- 表:3张 T001/T002/T003,单元格与docx w:tbl逐格一致(含全角逗号"，"、en-dash/hyphen不一致 均忠实保留) ✓
- xref:bibr 65(prose 53 + Table3行内 12);fig 8;table 3。prose引用序列与docx逐一对齐,无错链/漏链/悬空 ✓ **但5处区间未展开(疑点)**

### back
- S6 Availability / S7 Author Contributions / S8 Ethics / S9 ack Acknowledgment / S10 Funding / S11 Conflict of Interest / S12 AI声明:标题+内容 docx[104-117] 逐字,顺序一致,无补写C样板 ✓;S12"takes"语法错忠实保留 ✓
- 参考文献:56条 = docx[120-175] 56条 ✓;逐条核对 surname/given/year/volume/fpage/lpage/etal/source/DOI 全部一致;b16无DOI+comment"discussion A54-60"正确;b49 book:author(Jones)+editor person-group(6人)+publisher-loc"Berlin, Heidelberg"+publisher"Springer" 结构正确;刊名保持docx缩写(未外部扩写=忠实) ✓

## 疑点汇总
1. [medium] 5处引用区间 [11–13][21–23][28–30][35–45][47–50] 未展开为独立xref(§4.3/清单18);中间号 b12/b22/b29/b36-b44/b48/b49 在该处无链。
2. [low] abstract 单段被拆成4个sec(§3.7:单段→单p)。缓和:4个label在docx确为粗体。
3. [low] article-id publisher-id 取全DOI,按§9/§11应为文章号"JIN49347"(graphic href已用JIN49347,内部不一致)。

## 未发现问题的区
front除上述外全部忠实;body prose/公式/图/表全部忠实;back声明+参考文献(56条)全部忠实。
