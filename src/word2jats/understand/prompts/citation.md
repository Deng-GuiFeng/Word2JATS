分析英文学术稿件的逻辑结构：找出参考文献表之外每一处指向参考文献的引用，并指明它所指向的参考文献。只判定哪一段文字是引用、它指向何处；不改写稿件文字，也不直接生成 XML。

## 一、输入

user 消息中是 Word 稿件的一段视图，其中每条记录都可按地址定位。每条 Word 记录以 `[doc/p1]` 一类记录地址开头，其后是这条记录的完整可见文字。`[doc/p1.2]` 一类带序号的续行是软换行之后续下的部分，与 `[doc/p1]` 同属一条记录；除此之外没有别的记录属于它。`[doc/tbl1|表]` 是一张表格，其后 `doc/tblN.rM` 一类地址是这张表格的行，行内单元格之间用 `⇥` 分隔。`⟦图#o1⟧`、`⟦公式#o2⟧` 和 `⟦对象#o3⟧` 是 Word 原始对象的可见占位。

引用可能出现在叙述性段落中，也可能出现在 Word 原生表格的行、以制表符分隔的普通段落、图题、注释，或其他任何一条 Word 记录之中；在本任务中它们地位相同。“正文里的引用”不等于“只看叙述性段落”。

本提示词末尾另有一段 `REFERENCE IDENTITIES`，逐条给出参考文献的身份信息：稳定的实体编号，以及印出的标号、作者姓氏、年份、年份后缀与标题证据；后面这些均从已经切分完毕的参考文献中复制而来。

## 二、引用的摘抄与定位

每一处引用都以一个 `citation_quote` 表示。它固定为下列四个字段构成的对象，不能写成单个字符串：

{"quote":"引用的可见原文","record_key":"doc/pN","left_context":"","right_context":""}

- `quote` 是此后将由一个 JATS `xref` 包住的那段可见原文。必须逐字摘抄，保留拼写、大小写、标点以及原文中的错误。不要展开期刊名，不要改正日期，不要补出原文没有的信息，不要转录图片中的文字，也不要把解释写入摘抄。
- `record_key` 是该记录开头印出的地址，须原样复制。同一串文字可能出现在稿件的多个位置，因此这个字段不可省略。记录地址和对象编号都必须逐字符照抄。
- `left_context` 与 `right_context` 只用于定位，不会进入 `xref`。从同一条记录中紧邻 `quote` 左右两侧的字符抄起，抄写至 `left_context + quote + right_context` 在该记录中只出现一次为止。
- 只有 `quote` 在该记录中本就只出现一次时，两侧上下文才留空字符串。`quote` 紧靠记录的开头或结尾时，该侧可以为空；但 `quote` 重复出现时，两侧不能同时为空。
- 上下文只能取自原文字符，不能改写，也不能与 `quote` 本身重叠。上下文可以越出 `quote` 所属的更小片段的边界，但不能越出 `record_key` 指定的这条记录：不能抄取另一条记录的文字，不能从 `[doc/pN]` 跨到 `[doc/pN+1]`，也不能把印出的记录地址或人为的换行放入上下文。

## 三、输出

只返回一个 JSON 对象，不要返回其他文字：

{"compact_range_citations":[{"citation_quote":{"quote":"一段紧凑的可见范围","record_key":"doc/pN","left_context":"紧挨它左边的原文","right_context":"紧挨它右边的原文"},"target_reference_ids":["reference:1","reference:2","reference:3"]}],"single_target_citations":[{"citation_quote":{"quote":"一处可见的引用单位","record_key":"doc/pN","left_context":"紧挨它左边的原文","right_context":"紧挨它右边的原文"},"target_reference_id":"reference:1"}],"issues":[]}

按以下顺序处理：先找出并写完 `compact_range_citations` 的每一项，再在这些已被占用的原文范围之外写 `single_target_citations`。

一段可见原文只指向一条参考文献时，使用 `single_target_citations`。逗号或分号列表中各自可见的每个成员、每一处普通的作者-年份引用，以及一个范围仅涉及两条参考文献时的那两个端点，都属于这种情形。它的目标字段是单数的 `target_reference_id`，只能填一个字符串，不能填数组。

只有当一段紧凑的可见范围代表多个目标，且其中至少有一个目标在原文中没有属于自己的字符时，才使用 `compact_range_citations`。它的复数字段 `target_reference_ids` 按原文顺序列出全部目标。不要仅仅为了把几个各自可见的列表成员合并在一起就使用这个数组。

两个数组在原文字符层面互斥：一个范围已经写入 `compact_range_citations` 之后，就不要再把它的可见端点或该范围内的任何一段文字写入 `single_target_citations`。不要为没有可见字符的目标凭空造出字符。把若干处引用括在一起或彼此隔开的共用标点，仍然是普通原文。

`issues` 用于记录无法确定之处。摘抄的确切范围与目标集合，只要其中一项无法确定，就不要猜测：略过该处对应关系，把不确定之处写入 `issues`。

## 四、返回前的检查

给出的每一条记录都要逐条检查，包括每一条 `doc/tblN.rM` 表格行，也包括每一条含制表符的 `doc/pN` 段落，不要把整类记录一并略过。带编号的引用按印出的标号对应参考文献，作者-年份引用按姓氏、年份与年份后缀对应参考文献。叙述式引用与括注式引用采用同一种表示方式。标题与邻近的语义线索只能用于判断目标是哪一条，不能用于造出可见文字。不要假定稿件只使用一种标点、大小写、编号或作者姓名写法。参考文献表中的条目本身不是引用，不要作为引用返回。

返回之前，把所有记录自首至尾重读一遍，核对有无遗漏：凡是能够唯一确定目标的正文引用，在两个数组中合计必须恰好出现一次。不要因为某条参考文献仅在此处被引用就略过它，也不要把同一段原文同时写入两个数组。

## 五、示例

以下示例均为虚构，只用于说明返回值须满足的契约，不是判断何者构成引用的范式。

### 示例一

**Word 记录**

[doc/p12] Several earlier trials reached the same conclusion [2,5].

**已知的参考文献**

reference:2 印出的标号是 [2]；reference:5 印出的标号是 [5]。

**输出**

{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"2","record_key":"doc/p12","left_context":"conclusion [","right_context":",5]"},"target_reference_id":"reference:2"},{"citation_quote":{"quote":"5","record_key":"doc/p12","left_context":"[2,","right_context":"]."},"target_reference_id":"reference:5"}],"issues":[]}

方括号与逗号都是普通原文。每个可见的标号各占一项。分隔符两侧有无空格，只影响上下文应当抄取哪些字符，不影响这些可见标号是否各自成项。

### 示例二

**Word 记录**

[doc/p27] The findings differ (Rivera and Chen, 2021; Okafor, 2023).

**已知的参考文献**

reference:8 的姓氏是 Rivera 和 Chen，年份 2021；reference:9 的姓氏是 Okafor，年份 2023。

**输出**

{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"Rivera and Chen, 2021","record_key":"doc/p27","left_context":"differ (","right_context":"; Okafor"},"target_reference_id":"reference:8"},{"citation_quote":{"quote":"Okafor, 2023","record_key":"doc/p27","left_context":"2021; ","right_context":")."},"target_reference_id":"reference:9"}],"issues":[]}

共用的圆括号与分号都是普通原文。两篇文献各自拥有一段连续可见的识别文字时，不要把它们合入同一个 `citation_quote`。

### 示例三

**Word 记录**

[doc/p40] Reed (2022) reported the first result; Reed (2022) later revised it.

**已知的参考文献**

reference:4 的姓氏是 Reed，年份 2022。

**输出**

{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"Reed (2022)","record_key":"doc/p40","left_context":"","right_context":" reported"},"target_reference_id":"reference:4"},{"citation_quote":{"quote":"Reed (2022)","record_key":"doc/p40","left_context":"result; ","right_context":" later"},"target_reference_id":"reference:4"}],"issues":[]}

同一段摘抄被定位了两次，依据的不是它出现的次序，而是紧邻的原文标明了所指的究竟是哪一处。

### 示例四

**Word 记录**

[doc/p55] The combined evidence supports this conclusion [1-3, 7, 9-10].

**已知的参考文献**

印出的标号是 1 到 3、7、9 和 10。

**输出**

{"compact_range_citations":[{"citation_quote":{"quote":"1-3","record_key":"doc/p55","left_context":"conclusion [","right_context":", 7"},"target_reference_ids":["reference:1","reference:2","reference:3"]}],"single_target_citations":[{"citation_quote":{"quote":"7","record_key":"doc/p55","left_context":"1-3, ","right_context":", 9-10"},"target_reference_id":"reference:7"},{"citation_quote":{"quote":"9","record_key":"doc/p55","left_context":"7, ","right_context":"-10]"},"target_reference_id":"reference:9"},{"citation_quote":{"quote":"10","record_key":"doc/p55","left_context":"9-","right_context":"]."},"target_reference_id":"reference:10"}],"issues":[]}

`1-3` 保持为一个整体，因为 reference:2 在原文中没有属于自己的字符。`9-10` 拆为两项，因为这两个目标各有互不重叠的可见标号。方括号、逗号，以及分隔 9 与 10 的那个连字符，都是普通原文。特别注意：不要另行为 `1` 或 `3` 添加单独一项，它们已包含在返回的 `1-3` 这一项之中。

### 示例五

**Word 记录**

[doc/tbl2.r3] Cohort A ⇥ Improved after treatment [4, 6]

**已知的参考文献**

印出的标号是 4 和 6。

**输出**

{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"4","record_key":"doc/tbl2.r3","left_context":"treatment [","right_context":", 6]"},"target_reference_id":"reference:4"},{"citation_quote":{"quote":"6","record_key":"doc/tbl2.r3","left_context":"[4, ","right_context":"]"},"target_reference_id":"reference:6"}],"issues":[]}

表格行地址与段落地址遵循同一套定位规则。对于同一份原文，下面这个答案是错误的：

{"compact_range_citations":[],"single_target_citations":[],"issues":["Skipped because the citations occur in a non-narrative table row."]}

引用出现在表格、对比矩阵、研究概要栏、图题或注释之中，都不构成遗漏它的理由。

### 示例六

**Word 记录**

[doc/p83] Catalyst A ⇥ Stable ⇥ Earlier work [11,13]

**已知的参考文献**

印出的标号是 11 和 13。

**输出**

{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"11","record_key":"doc/p83","left_context":"Earlier work [","right_context":",13]"},"target_reference_id":"reference:11"},{"citation_quote":{"quote":"13","record_key":"doc/p83","left_context":"[11,","right_context":"]"},"target_reference_id":"reference:13"}],"issues":[]}

普通段落也可能以制表符排成表格行的形式。它使用 `doc/pN` 地址，这不改变引用的判定：应当像检查其他任何一条记录那样检查它的可见内容，不必等待程序或原文格式先将它标记为表格。

### 示例七

**Word 记录**

[doc/p70] The first paragraph ends with supporting evidence [8].
[doc/p71] A new paragraph begins here.

**已知的参考文献**

reference:8 印出的标号是 [8]。

**输出**

{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"]."},"target_reference_id":"reference:8"}],"issues":[]}

`right_context` 抄写至 doc/p70 结束即止。不要跨越换行进入 doc/p71，也不要为了使两条记录的文字看似相邻，就略去中间印出的记录地址。下面两种写法都是错误的：

{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"].\n[doc/p71] A new paragraph"},"target_reference_id":"reference:8"}

{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"].\nA new paragraph"},"target_reference_id":"reference:8"}

第一种跨出了记录边界，并且把地址一并抄入。第二种隐去了地址，但同样跨出了记录边界。doc/p71 的文字永远不能用于定位 doc/p70 中的文字。
