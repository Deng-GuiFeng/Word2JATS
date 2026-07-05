# S04 结构参考.xml 审查留痕 — 审查员 #2

依据:docx (word/document.xml) + 评测规则。上线版本未用。

## front
- article-type=research-article / subject=Original Research：docx 稿件类型 para[0]=\"Original Research\" ✓；符合 B 真值。
- journal-meta：journal-id RCM ✓，title Reviews in Cardiovascular Medicine ✓，abbrev Rev. Cardiovasc. Med. ✓，issn 1530-6550/2153-8174 ✓，publisher IMR Press。abbrev pubmed=全名(非缩写)——B 模板范畴，非 docx 可判，不报。
- article-id doi=10.31083/RCM49717 ✓（含 publisher-id 同值）。
- 标题：逐字与 docx para[1] 一致，无截断/改写 ✓。
- 作者：6 人，顺序 Jiang/Feng/Liu/Gong/Peng/Wang，surname/given 逐字 ✓。aff 归属:Jiang123,Feng1,Liu1,Gong123,Peng123+*,Wang123+* —— 与 docx 上标一致 ✓。**问题**:docx Jiang 上标含 \"#\"(raw run: '1,2,3, #' superscript),XML 中 Jiang 只有 aff1/2/3 三个 xref,\"#\" 贡献标记完全丢失(无 author-note、无 fn xref)。见 suspects。
- corresp:author-notes 构造 \"Correspondence: Xiaoping Peng; Xiang Wang\"——docx 无 Correspondence/email 文本,但 * 明确指向 Peng/Wang,属 B 合理重建,不报。ORCID/email:docx 无,XML 无,未编造 ✓。
- 单位:aff1/2/3 三条逐字与 docx para[3-5] 一致 ✓,label 上标数字 ✓。
- 编辑:docx \"Academic editor: Giuseppe Boriani, Boyoung Joung\" → 两 editor Boriani/Joung role=Academic Editor ✓。
- 日期(D/M/Y):Submitted5/1/2026→received d5m1y2026 ✓;Revised9/2→rev-recd d9m2 ✓;Accepted26/2→accepted d26m2 ✓。无占位符。
- permissions:copyright ©2026 IMR Press + CC BY 4.0——B 模板,copyright-year2026 合理 ✓。
- 摘要:docx 4 段(Background/Methods/Results/Conclusion)→ 4 个结构化 sec,标题带冒号,正文逐字(含 \"vs 0.93\" 无点、italic P)✓。
- 关键词:5 个,与 docx para[18] 一致 ✓。

## body
- 章节树:S1 Introduction / S2 Materials and Methods(SS1-4)/ S3 Results(SS1-5)/ S4 Discussion / S5 Conclusion。数字前缀 2.1..3.5 保留 ✓。无凭空建/漏建节。段落计数逐一核对(Intro4,方法各1/1/1/2,结果3/1/1/2/1,讨论6,结论1)全部对齐 ✓。
- 段落逐字:抽查多段含保留笔误——\"within within 24 h\"(S4.p3)✓,结论前导空格 ✓,\"≥125 p g/mL\" 空格 ✓,\"dat.\"(S2.SS1)✓,\"R²\"/\"3500×g\"/\"-80°C\" ✓。讨论 p3 \"OR = 2.420, 95% CI: 1.531–3.826\" 与 docx 一致(与前文 2.273 不同系 docx 自身,保留)✓。
- italic:P 值均 <italic>P</italic> ✓。
- 图:6 图 F001-F006,label \"Figure. N.\" 保留,caption 逐字(含 \"LAD,SIRI\" 无空格、F006 双 caption)✓。graphic href RCM49717/fig-0N.{tif,png,jpg} 与 word/media 实际格式一一对应(image1-3/5=tiff→tif,image4=png,image6=jpeg→jpg)✓。
- 表:3 表 T001(42行6列)/T002(7行)/T003(13行)与 docx w:tbl 结构一致;单元格值逐格核对,保留 docx 错误 \"O.847\"(字母O)、\"0.49\"、\"0.57\"、HMGB1+SIRI 的 (0.718,0.818) 重复 CI、脚注 \"ng/m\" 缺 L ✓。BMI kg/m<sup>2</sup> ✓。表脚注 T001-fn1/T002-fn1 逐字 ✓。
- 公式:docx oMath 计数=0,无公式,XML 无 MathML ✓,无整条丢式。
- xref:bibr 引用全部为逗号列表(如 [2,3][9,10][14,15][22,23][24,25][37,38]),均展开为独立 xref;无区间(n–m)型;Fig/Table 引用 F001-006/T001-003 全链 ✓。无悬空/漏链。

## back
- 声明小节 S6-S12:Availability/Author Contributions/Ethics/Funding/Conflict/AI Declaration/Supplementary——docx 均实有,标题+正文逐字(Ethical Number IIT2024849-1 ✓,Grant Nos.82400462 ✓)。无补写 C 样板、无漏。
- fn-group Publisher's Note:B 模板 ✓。ack/glossary:docx 无,未编造 ✓。
- 参考文献:41 条(b1-b41)全在;逐条核对 surname/given/article-title/source/year/volume/fpage/lpage 与 docx para[136-176] 一致;et al 触发正确(≤6 全列如 b8/b10/b19/b20/b28/b30/b33/b34/b36/b37,≥7 用 etal);suffix Jr(b8)✓;土耳其语/重音字符(Hayıroğlu/Çınar/López/Böhm/Tschöpe/β-Catenin)保留 ✓;无卷末页缺失(b3/b10/b23/b34/b36/b37/b39/b41 本就无 lpage,docx 亦无)。label [N] 对齐 ✓。

## 结论
唯一疑点:front 作者 Jiang 的 \"#\" 上标贡献标记被丢弃。其余 front/body/back 全区忠实,无 fabrication/wrong-value/faithfulness 违规。
