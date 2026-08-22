为两个相互独立的文首处理任务确定输入范围。只判断范围，不提取字段，不改写原文，也不生成 XML。

user 消息中是从 Word 主文档开头连续提取的记录。每行开头的 `[doc/p1]` 一类字符串是记录位置；后面的数组是 Word 文本片段。按顺序连接一条记录中的所有 `text`，就能得到该记录在 Word 中的完整可见文字。

任务一处理文首元信息，包括文章类别、标题、作者和编辑、单位、地址、联系方式、作者注释以及收稿、修回、接受等稿件日期。

任务二处理文首中具有正文式文字结构的部分，包括各种摘要及关键词。结构化摘要、非结构化摘要、翻译摘要、短摘要和多个摘要都属于同一个任务，不要在定位阶段把它们拆成不同范围。摘要内部的段落、分节和公式不改变范围的数量。图文摘要中的图片由同时运行的全文对象任务识别，不用为了图片扩大这个文字范围。

论文正文、作者贡献声明、致谢、资助、利益冲突、伦理声明、数据声明、人工智能声明和参考文献不属于这两个任务。

只返回一个符合给定 schema 的 JSON 对象，不要返回其他文字：

- `metadata_range`：任务一的一个连续输入范围；`first_node` 和 `last_node` 分别是范围内第一条和最后一条非空记录的位置。稿件没有文首元信息时返回 null。
- `front_content_range`：任务二的一个连续输入范围；`first_node` 和 `last_node` 含义相同。稿件没有摘要和关键词时返回 null。
- `issues`：无法按上述定义唯一确定边界时，简要写明原因；没有问题时返回空数组。

记录位置必须从 user 消息中原样复制。空白记录不能作为范围端点。每个任务只有一个范围；从该任务第一条相关记录到最后一条相关记录之间的记录均作为这个任务下一步的输入，后续任务自行识别其中的具体结构。两个范围可以相邻，也可以因 Word 中内容交错而重叠。不要因为稿件后部再次出现作者姓名，就把后部内容纳入文首任务。如果给出的记录在范围结束前已经截断，不能确定 `last_node`，应在 `issues` 中说明，不能猜测。

示例：
[doc/p1] [{"text":"Research Article","styles":[]}]
[doc/p2] [{"text":"A study of tidal marshes","styles":[]}]
[doc/p3] [{"text":"Lina Hart","styles":[]}]
[doc/p4] [{"text":"Coastal Institute","styles":[]}]
[doc/p5] [{"text":"","styles":[]}]
[doc/p6] [{"text":"Abstract","styles":["bold"]}]
[doc/p7] [{"text":"Marshes were surveyed ...","styles":[]}]
[doc/p8] [{"text":"Keywords: marsh; tide","styles":[]}]
[doc/p9] [{"text":"Introduction","styles":["bold"]}]
示例输出：{"metadata_range":{"first_node":"doc/p1","last_node":"doc/p4"},"front_content_range":{"first_node":"doc/p6","last_node":"doc/p8"},"issues":[]}
