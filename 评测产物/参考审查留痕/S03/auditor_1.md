# S03 结构参考.xml 审查留痕 — 审查员 #1

依据:docx(初始文件.docx word/document.xml) + 映射规格(02-数据与映射规格.md) + 评测体系设计.md。不参考上线版本。

## 覆盖
front: article-type/subject/journal-meta/article-id/title/authors×6/aff×3/editors×2/dates×3/permissions/abstract(结构化4节)/precis/keywords 全比对。
body: 章节树(S1-S5含子节)/正文逐段/行内italic/4图caption+graphic/Table1(47行逐格)/无公式/xref。
back: 5声明节/ack无/参考文献22条(b1-b22逐字段)/Publisher's Note模板。

## 已确认正确(略)
- 作者姓名/surname-given、ORCID归属(Zhang/Han/Li/Tan有,Hu/Zhou无)、4-4-4-4 URL、dagger共同一作、通讯*、email全对。
- aff全文逐字(含全角逗号保留)。dates全对(占位符无)。permissions B值对。
- 摘要结构化忠实;precis(Capsule)忠实含"Claude-3/ChatGPT-4"原样。keywords 4个对。
- Table1: 47行全在,逐格文本忠实(仅段落断行处空格差异,非内容差异),表脚注含"MRI, ;""VTE, "空定义忠实保留。
- 正文忠实保留docx笔误:"Claude  Sonnet"(缺3双空格)、"Gemini Gynecologyand"、双"supported by"、缺A.选项等。
- bibr xref: [1][2][3,4]...[9-10][20,21][22] 全展开正确。
- 参考文献b1-b22字段基本忠实,b12 collab保留,b7/b20 etal保留。graphic href指向figures.zip真实存在的fig-01..04.tif。

## 疑点
1. [HIGH 规则违反/omission] 全篇缺 fig/table 的 xref。grep: ref-type="fig"=0, ref-type="table"=0。正文"Table 1 and Appendix""Fig. 1A-C""Fig. 2A".."Fig. 4D-F""Figure 1D-F"全为<bold>纯文本,未生成<xref ref-type="fig" rid="F00N">/<xref ref-type="table" rid="T001">。违反映射规格§4.3("识别并生成<xref ref-type=fig|table|bibr>")。
2. [HIGH omission] b15 丢URL。docx[15]:"...Retrieved from http://www.readabilityformulas.com/flesch-reading-ease-readability-formula.php";参考b15 mixed-citation止于"Retrieved from",URL被删(对比b14同URL有ext-link)。
3. [LOW omission] 作者学位M.Sc/M.B/M.D在docx作者行,参考未产出(可入<degrees>)。金标准可能亦省,低置信。
4. [LOW omission] docx[24]"Article type: Observational Study"未在参考任何处体现(subject取Original Research)。
5. [LOW wrong-value] journal-title用"&"("Obstetrics & Gynecology"),任务给定B真值为"...and Gynecology";期刊真实名用&,存在张力,低置信。
6. [LOW rule-nuance] 子节id用S2.1而非规格§10的S{n}.SS{m}(S2.SS1);back节id用B1-B5(规格未定义)。无section xref故不影响解析。
