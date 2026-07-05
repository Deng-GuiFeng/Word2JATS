# S01 结构参考.xml 审查留痕 — 审查员 #1

依据:docx(word/document.xml)+ 映射规格(02-数据与映射规格.md)+ 评测体系设计。上线版本未参与。

## 覆盖情况(逐区)

### front
- article-type=research-article ✓;subject=Original Research ✓(docx P001 Articletype)
- journal-meta:journal-id CEOG ✓;issn ppub 0390-6663/epub 2709-0094 ✓;publisher IMR Press ✓
  - ⚠ journal-title 用 "&"(Clinical and Experimental Obstetrics & Gynecology),B真值写作 "and"。abbrev(publisher)Clin. Exp. Obstet. Gynecol. ✓;另有 abbrev-type="pubmed"=全称(异常,pubmed 惯例应为缩写形)。
- article-id doi 10.31083/CEOG48513 ✓;⚠ publisher-id=10.31083/CEOG48513(含前缀),规格 line183 "article-id=DOI去前缀"→应为 CEOG48513。
- 标题 P002 逐字一致(保留小写 "area",未 Title Case) ✓
- 作者 5 人 Zhang Min/Guan Jing/Liang Fei/Shen Huiyi/Li Xiaoze;上标1→aff1;Li 星号→cor1+email ✓;无 dagger/共一标记(docx 也无) ✓
- ORCID 0009-0005-6068-9537 归 Li Xiaoze,加 https://orcid.org/ 前缀+authenticated="true" ✓(规格 line61/65 明确要求)
- aff1 逐字 ✓;editor Carlucci Stefania/Academic Editor ✓
- 日期 received 24/11/2025、rev-recd 2/2/2026、accepted 27/2/2026 vs docx Submitted/Revised/Accepted ✓
- permissions © 2026 IMR Press + CC BY 4.0 ✓
- 摘要:docx 单段无粗体标签,参考拆 4 结构化 sec(Background/Methods/Results/Conclusions),文本逐字(保留 "to characterized" 语病、χ² 字面、大写 P) ✓ 内容忠实,未取上线扩写版
- 关键词 5 个 ✓

### body
- 章节树 1/2(2.1-2.4,2.2.1-2.2.5)/3(3.1-3.5)/4/5/6 与 docx 完全一致;保留 "lnformation""lnitial" 小写L语病 ✓
- 段落逐字(含 P080 "Among the 338 pregnant Among pregnant women" 语病保留) ✓
- 行内:italic SMN1/SMN2/p、sup(χ²、Z²、d²)均与 docx 一致
- 图 3 张,caption 逐字,label,graphic href=CEOG48513/fig-0N.jpg;figures.zip 有 fig-01/02/03.jpg ✓;docx image4.png 为页眉logo(header2.rels),正确排除
- 表 5 张,真表还原(colgroup/thead/tbody/scope/style);单元格逐字;T001 保留 docx 自身 SMN 斜体不一致(前3行 SMN斜体+1正体,第4行 SMN1全斜体) ✓;表脚注 T001-fn1/T004-fn1 ✓
- 公式:docx 无 OMML(oMath=0),参考无 MathML,正确 ✓
- xref:fig 3、table 5、bibr 32 处;⚠ 区间 [13-16]/[17–19] 未展开(见疑点)

### back
- 声明小节顺序 Availability→Author Contributions→Ethics→Acknowledgment(ack)→Funding→Conflicts,与 docx + 规格 §10 一致;内容逐字(AuthorContrib 保留 docx 已有缩写) ✓
- fn-group Publisher's Note 模板 ✓(B)
- 参考文献 30 条 b1-b30,label [1]-[30];element-citation 各字段逐条核对与 docx 一致;试剂命名 Wushishi/Wuseshi/Five-Color Stone/five-color 各处语病忠实保留 ✓

## 疑点
1. [HIGH rule-violation] S4.p1 引用区间 [13-16]、[17–19] 未展开为独立 xref,仅链端点。规格 line130 "[8-15]展开成8个独立xref"。后果:b14、b18 全篇 0 处 in-text xref(悬空)。
2. [MEDIUM wrong-value] article-id publisher-id=10.31083/CEOG48513(含前缀),规格 line183 应为 CEOG48513(与 graphic href 用的 CEOG48513 自相矛盾)。
3. [LOW wrong-value] journal-title 用 "&",B真值作 "and";abbrev-type="pubmed" 用全称异常。
