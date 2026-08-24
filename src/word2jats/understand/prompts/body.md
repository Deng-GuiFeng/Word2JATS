通读一份英文学术稿件，判定它的逻辑结构：在块一级认定每条 Word 记录承担什么角色，确定章节的层次，确定图、表、公式等对象各自的归属，识别 Word 原生表格与以制表符排版的表格，以及图题、表题、声明和注释。只判定角色和相互关系，不撰写稿件文字。

## 一、输入

user 消息中是 Word 稿件的一段记录清单，每一条都带有地址。`[doc/p1]` 一类字符串是一条 Word 记录的地址，其后是这条记录的完整可见文字；`[doc/p1.2]` 一类带编号的续行是同一条记录在软换行之后续下的部分；`[doc/tbl1|表]` 是一张 Word 原生表格，其后 `[doc/tbl1.r1]`、`[doc/tbl1.r2]` 是它的各行，行内单元格之间用 `⇥` 分隔。`⟦图#o1⟧`、`⟦公式#o2⟧` 和 `⟦对象#o3⟧` 是 Word 原始对象的可见占位。

部分段落记录之后还附有一行 `WORD_FACTS(记录地址)`。这一行是直接从 Word 中读出的结构与格式事实，不是稿件文字：段落样式的编号与名称、实际的提纲级别、编号属性，以及带有实际文字格式的字符范围。它本身不构成稿件文字，不能摘抄，也不能作为一条记录返回。

其中各个键的含义如下：`o` 是 Word 实际的提纲级别，以 0 为起点；`n` 是 `[numbering_id, numbering_level]`；`s` 是 `[style_id, style_name]`；`f` 的每一项是 `[start, end, "effective_format_name+..."]`，其中的字符范围以 0 为起点、左闭右开。

## 二、源指针 Q

schema 中类型标为 Q 的字段，都要填写一段从可见文字中逐字摘抄的原文，并附上 `node_hint`。Q 的完整形式为：

{"quote":"从原文逐字摘抄的片段","node_hint":"doc/pN","left_context":"","right_context":""}

- 实际输出的 JSON 中，绝不能出现单独的字母 Q，也绝不能把某个 Q 的位置替换为普通字符串。
- `quote` 必须保留拼写、大小写、标点，以及原文中的错误。
- 不要展开期刊名，不要改正日期，不要补出原文没有的信息，不要转录图片中的文字，也不要把解释写入摘抄。
- 记录地址和对象出现的编号必须原样复制。
- `node_hint` 不可省略：同样的印刷文字可能出现在多个实际位置上。
- 同一段摘抄在该记录中出现不止一次时，把紧邻的原文抄入 `left_context` 和／或 `right_context`，抄写至足以唯一定位所指的那一处为止。
- 上下文只用于定位，不会进入最终文章。
- 只有摘抄在本条记录中已经唯一时，上下文才留空字符串。
- 上下文不能改写原文，也不能与摘抄本身重叠。
- 上下文可以越出该摘抄所属的那个更小的语义单位，但必须留在 `node_hint` 指明的同一条记录之内。
- 不能从另一条方括号记录中抄取文字，不能从 `[doc/pN]` 跨到 `[doc/pN+1]`，也不能把印出的记录地址或人为的换行放入上下文。
- `[doc/pN.2]` 这类带编号的续行与 `[doc/pN]` 属于同一条记录，除此之外没有别的记录属于它。

## 三、输出

只返回一个 JSON 对象，不要返回其他文字：

{"blocks":[{"nodes":["..."],"role":"body-paragraph|section-title|figure-caption|table-caption|table|table-footnote|display-formula|declaration|glossary|definition-list|footnote|blank|decorative","level":1|null,"kind":"funding|conflict|ethics|consent|acknowledgments|author-contributions|data-availability|supplementary|glossary|other|null","title_quote":Q|null,"content_nodes":["..."]}],"objects":[{"occurrence_id":"oN","role":"figure|graphical-abstract|inline-graphic|display-formula|inline-formula|table-image|ole-formula|preview-superseded|fallback-superseded|decorative","owner_node":"...","title_quote":Q|null}],"figures":[{"caption_nodes":["..."],"label_quote":Q|null,"caption_title_quote":Q|null,"caption_paragraph_quotes":[Q],"graphics":["oN"],"group_key":null}],"figure_groups":[{"caption_nodes":["..."],"label_quote":Q|null,"caption_title_quote":Q|null,"caption_paragraph_quotes":[Q],"members":[{"caption_nodes":["..."],"caption_title_quote":Q|null,"caption_paragraph_quotes":[Q],"graphics":["oN"]}]}],"tables":[{"caption_nodes":["..."],"label_quote":Q|null,"caption_title_quote":Q|null,"caption_paragraph_quotes":[Q],"table_node":"..."|null,"flattened_row_nodes":[],"header_rows":1,"row_header_cells":[{"row":2,"column":1}],"graphic":"oN"|null,"footnote_nodes":["doc/pN"],"footnotes":[{"kind":"other|equal"|null,"paragraphs":[{"content_quotes":[Q]}]}]}],"formulas":[{"occurrence_id":"oN","display":true,"label_quote":Q|null}],"special_blocks":[{"role":"glossary|definition-list","container":"body|back","nodes":["..."],"title_quote":Q|null,"paragraph_quotes":[Q],"items":[{"term_quote":Q,"definition_quotes":[Q]}]}],"issues":[]}

无法确定时使用 null 或空数组，并把不确定之处写入 `issues`；不要猜测。

## 四、块的角色与章节层次

`blocks` 必须覆盖正文范围内的每一条可见记录——文首之后、参考文献之前的全部内容。文首与参考文献都由另外的任务负责，不要为它们建立块。连续若干条记录角色相同时，可以合并为一项列出。

在图、表或 `special_blocks` 的说明中列出的记录，在 `blocks` 中必须具有相应的角色。不要把一张表格、一条图题表题或一条注释并入一个宽泛的 `body-paragraph` 项。

只有一条记录中不含任何稿件内容时，才能判为 `blank` 或 `decorative`。

判断块的角色与章节层次时，应把 `WORD_FACTS` 给出的事实、文字本身与前后结构结合起来考虑。

- 样式的编号与名称是由文档生成方定义的证据，不构成到 JATS 层级的固定映射，作者也可能误用样式。某一行加粗或使用斜体，不足以说明它是章节标题。
- 反过来同样不可取：不能弃用 Word 中已有的明确结构，仅凭标题的措辞推测。
- 任何单独一条事实都不能推翻整份稿件的完整上下文。

文首内容不属于本任务，不要为它建立块：文章标题、作者与编者等署名人、单位、摘要的容器标题与正文、关键词的标题与文字，都由另一项任务处理。但仍要认出文首在哪里结束，才能确定正文从哪一条记录开始。

`section-title` 仅指在 JATS 正文中开启一节的标题。文章标题、摘要的容器标题在外观上也是标题，但不因此成为正文中的一节。

不要假定文首内容必定位于稿件开头的固定一段，也不要假定参考文献必定位于结尾的固定一段。

## 五、Word 原始对象

判为 `figure`、`table-image` 或公式的对象，都必须同时出现在对应的 `figures`／`figure_groups`、`tables` 或 `formulas` 说明中。

`decorative` 仅用于不含内容的装饰物，例如分隔线、出版方标识。正文之前用于概括全文的图属于 `graphical-abstract`，不是装饰。

`objects[].title_quote` 仅用于明确为 `graphical-abstract` 充当标题的文字，以便该对象与其标题一并确定。其余各种对象角色一律填 null，因为图题、表题和公式编号各有专门的说明。不要依据对象的角色编造标题。

## 六、图与图组

一张图由若干小图拼合而成时，这些小图都应留在这张图内，不得分置别处。同一对象的另一份存储形式或预览图不属于这类小图。

只有稿件在一个共用的图编号与图题之下，为各成员图分别配有独立图题时，才使用 `figure_groups`；由若干小图拼合、各小图没有独立图题的，作为 `figures` 中的一项。

## 七、图题与表题

每张图、每张表的 `caption_nodes` 列出其图题或表题所在的记录。

`label_quote` 只填印出的编号，例如 `Figure 1` 或 `Table 2`；`caption_title_quote` 与 `caption_paragraph_quotes` 均不包含该编号。

编号之后的一般图题或表题文字写入 `caption_paragraph_quotes`。不要仅因 Word 中该行为粗体，就把整条图题或表题判为标题。只有稿件把一段独立的标题文字与其后解释性的图题或表题段落分开时，才使用 `caption_title_quote`。

连贯的一句图题或表题算作一个段落。

## 八、表格与表注

一张 Word 原生表格由其确切的表格记录标识。表格块中只列出 `doc/tblN|表` 这条记录，不要逐条列出 `.rN` 显示行；程序会从这条原生表格记录出发，把归属传播到所有单元格。

以制表符排版的表格，须在 `flattened_row_nodes` 中列出显示出来的每一条实际行地址；一条源段落因软换行分出多行时，`.2`、`.3` 等也都要列入。这些片段的编号与归位由后续的专门任务完成，本步的块分类中不要凭空产生单元格。

Word 中没有明确的重复表头标记时，`header_rows` 填写开头有几行属于表头行。`row_header_cells` 以从 1 起计的实际行号与列号，逐个列出语义上作为行头的单元格。第一列的单元格不一定是行头，不要因其位于第一列就判为行头。

每张表的 `footnote_nodes` 只列出属于该表表注的实际记录。

`footnotes` 指语义上的注：一条注可以包含若干段落，一个段落又可以通过 `content_quotes` 把若干段原文片段连接起来。

- 一条印出的注被分置于多个文本框、跨页续排或多个段落，但仍属同一语义段落时，使用多个 `content_quotes`。
- 只有稿件确实在同一条注中分了段，才使用多个 `paragraphs`。
- 不要把每一条实际记录都拆成单独的一条注，不要把不同表格的注合并在一起，所有原文片段都按印出的先后顺序排列。

## 九、声明

一条声明构成一个块，其标题记录与全部内容记录都在这个块内：`title_quote` 是原文标题的逐字摘抄，`content_nodes` 只列出标题之后的那些段落。

声明的段落不必再作摘抄，给出其记录地址即可。

不要把声明的标题作为声明的段落输出，也不要把声明的内容留作普通正文段落。不要把原文的标题替换为规范化的同义说法。

## 十、术语表与定义列表

`special_blocks` 必须保持稿件中的先后顺序，并指向该术语表或定义列表用到的每一条记录。

只有稿件明确把术语与释义分开时才使用 `items`；否则将术语表原样保留为 `paragraph_quotes` 中的段落摘抄。
