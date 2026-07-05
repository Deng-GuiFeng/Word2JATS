# S05 结构参考.xml 审查留痕 — 审查员 #2

依据：源 docx（word/document.xml）+ 映射规格(02) + 评测体系设计(03)。不参照上线版。

## 逐区覆盖

### front
- article-type=research-article、subject=Original Research（docx[0] "Original Research"）✓
- journal-meta：RCM / Reviews in Cardiovascular Medicine / Rev. Cardiovasc. Med. / issn 1530-6550,2153-8174 / IMR Press，全部与 B 真值一致 ✓
- article-id doi=10.31083/RCM49651 ✓；publisher-id 也填全 DOI（B 级，未指定格式，未计）
- 标题逐字与 docx[1] 完全一致，无截断/无 Title Case 改写 ✓
- 作者 9 人，surname/given 逐字、上标数字→aff xref 映射全部正确（Wang1,2 / Hao1,2,3 / Ji1,2 / A.Wang1,2,3,4 / Zhang1,2,3,4 / Zhou5 / Kang1,2* / Zhao1,2,6* / W.Wang1,2）✓
- ORCID 9 人逐一核对 docx[26-34]：4 个 NA 者无 contrib-id，5 个有 ORCID 且归属正确，URL 4-4-4-4 规范 ✓
- email：Kang/Zhao 两通讯 email 与 docx 一致 ✓
- 6 条 aff 逐字与 docx[5-10] 一致，label 上标正确 ✓
- 编辑 Bolignano Davide / Academic Editor（docx[42] "学编：Davide Bolignano"）✓
- 日期 received 2/1/2026、rev-recd 27/2/2026、accepted 28/2/2026（docx[38-40]）✓
- permissions：版权年 2026、CC BY 4.0 链接 ✓（模板 B）
- 摘要拆成 4 个 sec（Background/Methods/Results/Conclusions），docx[46] 实为单段带内联标签；文本逐字一致，italic p 正确
- 关键词 5 个与 docx[49] 一致 ✓

### body
- 章节树 S1–S5 + 子节 2.1–2.5、3.1–3.5，与 docx 一致；2.4 标题 docx 无 heading 样式(斜体)仍被正确识别为 sec 标题 ✓
- 正文各段逐字比对（含 "XXX [This information has been edited and hidden]" 原样保留、l/大小写未动）✓
- 3 张表 cell 内容与 docx w:tbl 逐格多重集比对：Table1 182格、Table2 148格、Table3 32格，全部 MATCH ✓
- 表脚注 T001/T002(Model1-4+Abbrev)/T003 与 docx 一致 ✓
- 4 张图 F001-F004，caption 逐字、label、graphic href 指向 figures.zip 内真实 fig-01..04.tif（前缀 RCM49651/）✓
- OMML：docx 无 oMath，参考无 MathML ✓（无公式可丢）
- italic/sup：docx 18 个 italic（16 个 "p" + 斜体标题2.4）、18 个 superscript（角标/χ²），参考对应无误 ✓
- 交叉引用 Fig1-4、Table1-3、Table S1(supplementary 不链)覆盖正确
- **bibr xref：区间只链端点，未展开** → 见疑点1

### back
- 无 ack（docx 无致谢）✓
- ref-list 46 条，element-citation 字段逐条核对：作者集/title/source(italic)/year/vol/fpage/lpage/doi(ext-link)全部与 docx[140-185] 一致
- **et al.**：docx 13 处（ref5,7,15,19,21,23,26,30,38,41,43,44,45），参考有 13 个 `<etal>` ✓（初查因 XPath 误判，复核确认无误）
- **suffix**：ref31 "Welch GH Jr" → 参考有 `<suffix>Jr</suffix>` ✓
- fn-group Publisher's Note 模板 ✓（B）
- author-notes corresp/fn-1 → 见疑点2、3

## 疑点

1. **[高] 引用区间未展开为独立 xref**：docx 的 [1–3]/[15–17]/[18–21]/[24–26]/[39–41]/[44–46] 只链两端点，中间 b2、b16、b19、b25、b40、b45 从未被任何 xref 引用（grep 确认 0 次）。映射规格 §4.3 明确"[8-15] 展开成 8 个独立 xref"，评测体系 §3(322 行)将"区间展开正确性"列为评分轴，且 03 样例同类缺陷（漏 b12/b22/b29）已被记为已知参考侧待补。

2. **[低] corresp 丢失 docx 通讯作者明细**：docx[13-24] 有 "Corresponding authors" 完整块（MD 学位、科室、No.119 South 4th Ring West Road 地址、phone +861059975701）。参考 corresp 仅保留 email+姓名，标签改写为 "Correspondence:"。属 A 档内容的省略。

3. **[低] fn-1 共同贡献脚注悬空**：docx 单一 "*" 同时表示通讯+共同贡献。参考把 * 只映射到 corresp cor1，另建 fn-1（"These authors contributed equally…"）但无任何作者 xref 指向 fn-1，脚注悬空未闭合。
