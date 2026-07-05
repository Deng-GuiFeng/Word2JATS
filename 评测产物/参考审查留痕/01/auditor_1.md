# 样例01 结构参考.xml 独立审查留痕 — 审查员#1

判据：源 docx（word/document.xml）+ 评测规则 + 映射规格。不看上线版本。

## 覆盖与结论

### FRONT
- article-type=research-article、subject="Original Research"：与 docx para[0]"Original Research" 及 B 真值一致 ✓
- journal-meta：RCM / Reviews in Cardiovascular Medicine / Rev. Cardiovasc. Med. / issn 1530-6550,2153-8174 / IMR Press，全部符合 B 真值 ✓
  - 存疑：abbrev-journal-title abbrev-type="pubmed" 填的是全称"Reviews in Cardiovascular Medicine"，而非 PubMed 缩写形式。低。
- article-id doi/publisher-id = 10.31083/RCM46777 ✓
- 标题：与 docx para[1] 逐字一致，未 Title Case、未润色、未截断 ✓
- 作者：3 人 Ji Yinze / Dang Aimin / Lv Naqiang，surname/given、degrees(M.D., Ph.D.)、aff 上标(Ji=1,2；Dang=1；Lv=1)、通讯星号(Dang/Lv corresp)、email(jiyz3/amdangfw/lvnaqiang)、ORCID(0009-0006-1026-1505 / 0000-0003-3315-7840 / 0000-0002-5660-8897, 4-4-4-4, 归属正确) 全部与 docx para[3][9-21] 一致 ✓
- aff1/aff2：与 docx para[5][6] 逐字一致 ✓
- 编辑：De Rosa, Salvatore / Academic Editor（docx para[29]"学编：Salvatore De Rosa"）✓
- author-notes：corresp cor1 由通讯作者信息重构（"Correspondence:" 为标准模板标签）；<p>"Aimin Dang will handle correspondence..." = docx para[23] ✓
- history：docx para[25-27] 为 3 个裸日期 2025/9/21、2026/1/15、2025/1/16，无显式标签。参考按文档顺序映射 received/rev-recd/accepted，日期 Y/M/D 逐字忠实（accepted 年份 2025 = docx 笔误，已保留不改，正确）。**映射本身无 docx 标签佐证** → 低置信存疑。
- permissions：Copyright © 2025 IMR Press / CC BY 4.0，B 模板 ✓
- abstract：结构化 4 sec（Background:/Methods:/Results:/Conclusion:），文本逐字忠实，含 docx 原错"with and with"(未改)、"from 2009 to 2023"(与正文不一致但忠实 docx) ✓
- keywords：5 个，与 docx para[38] 分号列表一致 ✓

### BODY
- 章节树：S1 Introduction / S2 Materials and Methods(SS1..SS3, SSS1..SSS7) / S3 Results / S4 Discussions / S5 Conclusions，层级与 docx 样式 1/2/3 完全对应；"Internal validation"/"External (temporal) validation" 为 docx 无样式粗体段，参考正确作 <p><bold> 未凭空建节 ✓
- 段落：逐段核对 Introduction/Methods/Results/Discussion/Conclusions，无丢段/加段；docx 语病(如"records of in"、"data-driven of")均保留 ✓
- 行内格式：italic("in the future"/"now"/期刊名/P/c/t/loess 等)、bold、sup/sub 与 docx 用法一致；docx 杂散粗体(0<bold>.</bold>0003、1<bold>.</bold>73)亦保留 ✓
- 图：docx 仅 1 图（para[744] 内嵌 10 张 jpeg = 5 个校准面板×2），figures.zip 提供合并图 fig-01.jpg；参考 F1 graphic href="RCM46777/fig-01.jpg" 符合映射规格 line140/145（{article-id}/fig-0N.jpg）✓；caption title/正文逐字 ✓
- 表：4 表(T1-T4) 均为真 w:tbl，单元格、colspan/rowspan、thead/tbody、表脚注(<fn>) 全部还原，含 docx 原样脚注"(Continued)(Continued)"、"CHD indicates" 等，忠实 ✓
- 公式：docx 共 10 个 OMML + para[59] 的 2 个 wmf 内嵌公式图(逻辑回归大分式)。参考 22 个 inline-formula 标签(11 个元素)：para52 χ²、para59 大分式(由 wmf 图重建为 MathML)+p+β0..β3、3 处表脚注 χ²、Fig 题 y=x —— 数量与 docx 一一对应，无整条丢式 ✓
- xref：区间已展开为独立 xref([16-17]→b16,b17；[23-24]→b23,b24；[8-15]→b8..b15)；正文所有 [n]、Table N、Figure N 均链且无悬空/漏链/多链，逐段核对 ✓

### BACK
- 声明小节：Funding(S6)/Conflict of interest(S7)/Author contributions(S8)/Ethics Approval(S9)，均 docx 实有(para774/776/778/780)，标题+内容逐字 ✓；保留 docx 顺序（未按映射规格 line188 的规范序重排，属忠实 docx，不判错）；docx 无"Availability of Data"/"Acknowledgment"(para[747] ac 样式为空)，参考未补占位语，符合本任务 C 规则(不补写) ✓
- fn-group：Publisher's Note (IMR Press) B 模板 ✓
- 参考文献：39 条(b1-b39)，逐条核对 surname/given/article-title/source/year/volume/fpage/lpage、作者数与 et al(<etal/>)、书/报告/网页类型(b20/b21/b25/b28 有 publisher-name/loc/edition；b17 保留 docx 乱码作者"RLJN BMBM"；b25 collab)全部与 docx [785-823] 逐字一致，无丢作者/丢 loc/替换/漏条，label 对 ✓

## 疑点（均低置信，供辩论团核）
1. id 命名：fig=F1、table=T1..T4、graphic 无 id、table-wrap-foot fn 无 id —— 与映射规格 line191 的 fig=F00{n}/graphic=F00{n}.g1/table=T00{n}/表脚注=T00{n}-fn1 不符。但 sec(S{n})、文献(b{n})、aff/cor 均非补零且与规格一致，参考内部自洽；规格本身 S{n} 与 T00{n} 不一致，存在张力。低。
2. history 三日期的 received/rev-recd/accepted 映射无 docx 标签佐证（仅按序推断）。日期值忠实。低。
3. pubmed abbrev-journal-title 填全称而非缩写。低。

总体：该参考质量很高，未发现 fabrication/omission/wrong-value 级别的内容错误，忠实性把控到位（多处 docx 原错均保留未改）。
