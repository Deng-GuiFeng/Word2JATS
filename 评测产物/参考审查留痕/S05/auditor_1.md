# S05 结构参考.xml 审查留痕 — auditor_1

依据:docx `word/document.xml`(530 段)+ 映射规格 `02-数据与映射规格.md`。上线版本未用。

## front(逐项)
- article-type=research-article、subject=Original Research(docx[000]"Original Research"),journal-meta 全字段=RCM/Reviews in Cardiovascular Medicine/Rev. Cardiovasc. Med./1530-6550/2153-8174,DOI=10.31083/RCM49651 —— 全部与 B 真值一致。
- 标题:与 docx[001] 逐字一致,无截断/改写。
- 作者 9 人,surname/given、上标 aff、通讯 * 均与 docx[002]一致。ORCID 9 人逐一核对 docx[026-034]:Wang C/Ji/Wang A/Kang/Zhao 有,Hao/Zhang/Zhou/Wang W = NA→无 contrib-id,全部正确;URL 均 4-4-4-4。email 两名通讯正确。
- aff1-6 与 docx[005-010] 逐字一致。editor Bolignano Davide(docx[042]"学编：Davide Bolignano")。
- 日期 received 2/1/2026 rev 27/2/2026 accepted 28/2/2026 = docx[038-040],非占位符。
- permissions 2026/CC BY 4.0 模板(B)。摘要拆 4 sec,文本=docx[046]。关键词 5 个=docx[049]。
- 疑点:corresp 仅保留两 email,docx[013-024] 的 phone/address/MD 未纳入(low)。equal-contribution fn-1 未被作者 xref 关联(low)。

## body
- 章节树 S1-S5 + SS 与 docx 标题(1./2.1-2.5/3.1-3.5/4./5.)一一对应,无增删。
- 正文段落、Table1-3、Fig1-4 caption 与 docx 逐字核对一致;表结构为真 w:tbl 还原。
- 无 OMML(docx oMath=0),ref 无 MathML,正确。
- **交叉引用 xref:7 处区间全部未展开**(见下),违反映射规格 4.3 line130“区间[8-15]展开成8个独立xref”。

## back
- 参考文献 46 条(b1-b46)全在,element-citation 字段逐条核对 docx[548-593] 一致:全大写姓名(b14 NELSON/GOBEL、b18 WHITE)、b31 suffix Jr、b12/b28/b34 单页无 lpage、etal 位置均忠实。
- 无 docx 不存在的声明小节被补写(docx 无 Availability/Funding/COI 等,ref 也无)。fn-group Publisher's Note = B 模板。

## 关键疑点:区间 xref 未展开(high)
Python 统计:bibr xref 从不指向的 ref = b2,b16,b19,b25,b40,b45(全为三连号区间中间项)。
逐个区间:[1–3]缺b2;[4–6]缺b5;[15–17]缺b16;[18–21]缺b19,b20;[24–26]缺b25;[39–41]缺b40;[44–46]缺b45。
渲染为 `<xref>lo</xref>–<xref>hi</xml>` 仅端点,中间项无独立 xref,b2/b16/b19/b25/b40/b45 成为无任何引用指向的悬空文献。

## 次要
- graphic href 用 `RCM49651/fig-0N.tif`;figures.zip 实为 fig-01..04.tif(存在),但建规范 5 节要求转 JPG/`fig-0N.jpg`(low,资产本身是 tif)。
