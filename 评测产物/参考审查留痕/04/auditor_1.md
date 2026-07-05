# 样例04 结构参考.xml 审查留痕 — 审查员#1

判据:内容权威=初始文件.docx(mitochondrial ASM review);规则=评测体系设计.md + 02-数据与映射规格.md。不参照上线版本。

## 覆盖与结论(逐区)
- 数据规模:docx body 372个直接w:p;3张真表(w:tbl);2张图(rId7=image2.png, rId8=image3.png;image1.png为页眉logo,非正文);0个OMML公式;参考文献[1]-[145]共145条。
- **front**:article-type=review-article✓;subject=Review✓(docx p0);journal-meta(JIN/Journal of Integrative Neuroscience/J. Integr. Neurosci./0219-6352/1757-448X/IMR Press)✓与B真值一致;article-id doi=10.31083/JIN52316✓;标题逐字✓(小写impact未Title Case);7作者✓,surname/given逐字✓(含Héctor/Ángel/Moisés/Pérez-重音,Serrano-Garcia取byline写法);上标1-6→aff xref✓;#→fn(equal,Carmen+Norma)✓;*→cor1(Moisés)+email✓;6个ORCID全部正确4-4-4-4展开(Ricardo 0000000242784814→0000-0002-4278-4814等)且加https://orcid.org/✓;Carmen无ORCID(docx确无)✓;aff1-6逐字✓;editor Hsu Kuei-Sen/Academic Editor✓;日期received27/3/rev27/4/accepted30/4 2026✓;permissions 2026/CC BY 4.0✓;abstract单p逐字✓(⁺²⁺→sup);8关键词✓。
- **body**:9章节树(S1-S9)与docx完全对应,段落数逐节吻合(S1=6,S2=3,S3=3,S4=5,S5=9,S6=2,S7=2,S8=4,S9=1),空段p72正确忽略;图2张caption逐字(bold标题句、italic"Created in BioRender"、BioRender URL、sub/sup均保留;"Figure N."移入label);表3张 cell逐字(含Ethosuximide格 docx重复冗余"...overload mitochondrial Ca2+ homeostasis..."被忠实保留);无公式;xref:所有Figure/Table在文提及均已链;逗号引用列表均展开为独立xref。**发现3处漏链(见疑点)**。
- **back**:声明5节(Author Contributions缩写全名C.R./N.S.-G...docx原样、Funding、Data Availability、Conflicts、Ethics)逐字✓;glossary(List of abbreviations)62项按docx顺序✓;ref-list 145条element-citation,首作者姓/页码/DOI全对齐docx(批量核验0差异);反向DOI核验无凭空新增/篡改/丢失;b10=Brodie(未采纳上线版Singh编辑改写,忠实docx)✓;145条数量未减(未采纳上线版145→144删改)✓。

## 疑点
1. 漏链[76](S5.p1,line283):"apoptotic signaling [76]."纯文本未生成xref rid=b76(同句后半[76,77]却已链)。
2. 漏链[76](S5.p2,line291):"regulate VDAC1 function [76]."纯文本未生成xref rid=b76。
3. 漏链[19,99,114](S6.p2,line303):"generalized epilepsies [19,99,114]."整组未展开为b19/b99/b114三个xref。
4. (低)graphic href="JIN52316/fig1.jpg"/"fig2.jpg" 用了figures.zip原名,规格7节要求重命名fig-0N并 href="{id}/fig-01.jpg"。
5. (低)图label"Figure 1./2."与规格约定"Fig. N."不一致(但docx原文即"Figure",忠实性可辩)。
6. (低)fn id="fn1"与规格命名fn-{n}(应fn-1)不一致。
7. (低)abbrev-journal-title pubmed填了全称"Journal of Integrative Neuroscience"而非缩写;article-id publisher-id=完整DOI(与doi重复)。
