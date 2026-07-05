# S03 结构参考.xml 审查留痕 — 审查员 #2

依据:docx(word/document.xml)+ 评测体系设计.md + 02-数据与映射规格.md(§4.3 交叉引用、§4 命名)。

## 覆盖
- front:article-type/subject、journal-meta、article-id、标题、6作者(名/上标/ORCID/通讯/共一)、3单位、2编辑、3日期、permissions、结构化摘要+precis、关键词 — 全部逐字比对 docx[0..49]。
- body:章节树(S1..S5+子节)、逐段、bibr xref、Table1(48行+表脚注)、4图(href+placement)、fig/table 交叉引用 — 比对 docx[53..145]。docx 无 OMML 公式(oMath=0)。
- back:6声明小节(B1..B5)、22条参考文献逐条、Publisher's Note — 比对 docx[16..37,147..169]。

## 已确认忠实(无疑点)
标题逐字含尾点;6作者姓名/顺序;ORCID 归属(Zhang/Han/Li/Tan 4人)与 4-4-4-4 URL;通讯*;共一†(fn-1);3单位(全角逗号保留);2编辑;3日期;precis(Capsule 去标签);关键词;章节树与编号;正文各段逐字(含 supported by Supported by、Gynecologyand 原样错误);bibr xref(区间[9-10]已展开);Table1 47题全转并保留缺选项字母等原样错误;4图 href 顺序 fig-01..04.tif 与 docx placement 一致;22条 element/mixed-citation 字段(l869、172.e1、Tayebi Arasteh 双词姓、collab)。

## 疑点
1. 系统性缺 fig/table xref:正文 Table 1(→T001)、Fig.1..4(→F001..F004)全部仅 <bold> 加粗未生成 xref。全文 ref-type 统计 fig=0 table=0。违反 §4.3。补充材料 Fig S1/Table S1-S3 无对应对象,纯文本合理。
2. ref b15 丢 URL:docx[15] 含 http://www.readabilityformulas.com/...php,XML b15 仅"...Retrieved from"后无 ext-link。omission。
3. journal-title 用"&"而 B 真值给定"and"。低置信。
4. abbrev pubmed 填全称,应为缩写。低置信。
5. article-id publisher-id 填完整 DOI,通常应为稿号 CEOG50327。低置信。
6. 作者学位 M.Sc/M.B/M.D 丢弃。低置信。
7. Rui Hu/Xiao Zhou 上标 docx 实为"12,3"(笔误),XML 归一 aff1,2,3。低置信。
