# 样例05 结构参考.xml 独立审查留痕（审查员#1）

依据：源 docx + 映射规格02 + 评测体系设计。不以上线版本为准。

## 覆盖
front: article-type/subject/journal-meta/article-id/title/authors(4)/aff(2)/editor/history/permissions/abstract/keywords 全查。
body: 章节树/正文逐段/图4/表10+图下计数表4/xref(bibr/fig/table) 全查。
back: 5声明块/ack/ref-list(27)/fn-group 全查。

## 结论
- title 与 docx[2] 程序diff=True；abstract 与 docx[34] 程序diff=True。逐字一致。
- authors 顺序/姓名/上标→aff 正确；无ORCID(docx亦无)。aff1/aff2逐字一致。
- history 三日期与 docx[27-29] 一致。keywords 一致。
- 图 caption 与 docx 一致；href=HSF49106/fig-0N.png，zip内fig-01..04.png存在。
- 27条参考逐条核对element-citation字段，忠实docx含原瑕疵(b10 doi空格/b17 year201818/b25 vol1/b26长串/b27 tab)。无丢/替换/漏。
- xref程序核对：仅 b21 无入链。

## 疑点
A[HIGH] 区间[9,20-22]未展开，b21悬空(映射规格§4.3 line130)。
B[LOW] back-matter Funding与Acknowledgment顺序与docx相反。
C[LOW] T9表头/表体列错位。
D[LOW] T1表头拆列致数据错位；Age行p/X²落入错误列。
E[LOW] article-id publisher-id用完整DOI而非稿号HSF49106。
