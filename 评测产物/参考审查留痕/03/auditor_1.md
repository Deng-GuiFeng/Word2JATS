# 样例03 结构参考.xml 独立审查留痕 (auditor_1)

依据: 初始文件.docx (word/document.xml) + 映射规格 + 评测体系设计。上线版本未参考。

## front (逐项)
- article-type=research-article / subject=Original Research (docx P0 "Original Research") ✓
- journal-meta 全字段=B真值 (JIN / Journal of Integrative Neuroscience / J. Integr. Neurosci. / 0219-6352 / 1757-448X) ✓
- article-id doi/publisher-id = 10.31083/JIN49347 ✓
- 标题逐字=docx P2 ✓ (无截断/无Title Case改写)
- 9 作者, surname/given/aff xref/通讯*/共同贡献† 全部对 docx P3 ✓; ORCID 7 人(Yu/Yao无, docx确无) 归属+URL(https://orcid.org/ + authenticated) 全对 P11-P17 ✓; Li email ✓
- 3 单位逐字=docx P4-P6 (aff1 用 docx 形态 "Shanghai 200433" 未邮编前置) ✓
- editor Platt Bettina = docx P18 ✓
- history: received 22/12/2025, rev-recd 24/2/2026 = docx P19; accepted "待接收" 占位→未产出 ✓ (符合规则)
- permissions © 2026 + CC BY 4.0 = B模板 ✓
- 摘要: docx P22 单段但含 4 个粗体标签(Background:/Methods:/Results:/Conclusions:, 已验证均为 bold run)→结构化4 sec, 文本逐字 ✓
- 5 关键词 = docx P24 ✓

## body (逐项)
- 章节树: 全部标题匹配 docx (含 2.5.1-2.5.3 三级嵌套; 3.1 无句点保留; "Limitations and Future Directions" 作 S4.SS1 — docx 为独立短行, 与全文其它无 heading 样式的小标题一致, 可接受) ✓
- 段落逐字: 抽查多段 (含 docx 重复词 "feature extraction, feature extraction" 保留) ✓
- 6 块公式 E001-E006 (OMML→MathML) 无丢式 ✓; 行内变量以 italic 呈现(符合 house-style §4.2), 内容无丢
- 5 图 F001-F005, caption 逐字, graphic href JIN49347/fig-0N.jpg, figures.zip 存在 fig-01..05 ✓
- 3 表 T001-T003, 真表结构(colgroup/thead/tbody/th td scope/break/rowspan), 表脚注 T001-fn1/T002-fn1 ✓
- 交叉引用: fig/table xref 全链(F001-F005, T001-T003) ✓
  ⚠ bibr 区间未展开: docx 5 处区间 [11–13][21–23][28–30][15,35–45][47–50] 均只链端点。
    → b12,b22,b29,b48,b49 全篇无任何 xref 指向(纯漏链); [35–45] 中 b36-b44 在别处被链但此处漏。

## back (逐项)
- 7 声明小节全在且顺序=docx: Availability/Author Contributions/Ethics/Acknowledgment(<ack>)/Funding/Conflict of Interest/Declaration of AI; 标题与内容逐字, 无补写C, Conflict 用 docx "conflict"(非模板复数) ✓
- Publisher's Note fn-group = B模板 ✓
- 参考文献 56 条 element-citation: 逐条核对作者 surname/given+etal 全部=docx(含 Van Dongen/Ben Khalifa/Wan Masri/Mohamad Zulkufli/Di Fazio/di Pellegrino/Della Villa/Mihai (Ungureanu) 等复合姓, 无丢名/无加名), article-title/source/year/volume/fpage/lpage 抽查一致; b16 comment/b49 book+editor+publisher-loc 结构正确 ✓

## 结论
唯一确定性缺陷: 交叉引用区间未展开(omission)。其余 front/body/back 均忠实、完整、合规。
