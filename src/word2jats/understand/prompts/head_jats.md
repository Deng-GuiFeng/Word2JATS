把 user 消息中的 Word 文首信息区转换成 JATS Publishing 1.3 XML。

## 一、输入与输出

user 消息中只有已经确认的文首信息区。每行开头的 `[doc/p1]` 一类字符串是记录位置；后面的数组是 Word 文本片段。按顺序连接一条记录中的所有 `text`，就能得到该记录在 Word 中的完整可见文字。`styles` 记录加粗、斜体、下划线、上标和下标等 Word 格式。记录位置不能写入 XML。

按顺序读取 user 消息中的全部非空记录。文章类别、标题、作者、编辑、学位、作者或编辑标识、邮箱、单位、个人地址、通信说明、共享作者注释和稿件日期都属于本任务。每条属于本任务的记录都必须在 XML 中得到表示。容易遗漏的有：文章类别（`article-categories` 与 `article-type`）、编辑、稿件日期，输出前逐项核对。

只返回 XML，不要返回 Markdown 代码块、解释、XML 声明或 DOCTYPE。根结构为：
<article><front><article-meta>...</article-meta></front></article>

不要输出期刊元数据、DOI、出版信息、权限信息、摘要、图文摘要、短摘要、关键词、论文正文、作者贡献声明、致谢、资助、利益冲突、伦理声明、数据声明、人工智能声明或参考文献。Word 原文没有写明的信息和关系不得猜测，也不得使用外部知识补全。

## 二、DTD 合法性

XML 的元素、属性、内容结构和子元素顺序必须符合 JATS Publishing 1.3 官方 DTD。只有 DTD 允许且 Word 原文能够确定含义和取值的元素与属性才能输出。如果 Word 格式无法在当前内容结构中合法表示，保留可见文字，舍弃无法表示的格式；不得为了保留格式生成非法元素或属性。

本任务可能用到的元素，内容模型如下：

article-meta       (article-id*, (article-version | article-version-alternatives)?, article-categories?, title-group, (contrib-group | aff | aff-alternatives)*, author-notes?, (pub-date+ | pub-date-not-available?), volume*, volume-id*, volume-series?, issue*, issue-id*, issue-title*, issue-title-group*, issue-sponsor*, issue-part?, volume-issue-group*, isbn*, supplement?, ((fpage, lpage?, page-range?) | elocation-id)?, (email | ext-link | uri | product | supplementary-material)*, history?, pub-history?, permissions?, self-uri*, (related-article | related-object)*, abstract*, trans-abstract*, kwd-group*, funding-group*, support-group*, conference*, counts?, custom-meta-group?)
article-categories (subj-group*, series-title*, series-text*)
subj-group         ((subject | compound-subject)+, subj-group*)
title-group        (article-title, subtitle*, trans-title-group*, alt-title*, fn-group?)
contrib-group      (contrib+, (address | aff | aff-alternatives | author-comment | bio | email | ext-link | on-behalf-of | role | uri | xref)*)
contrib            (contrib-id*, (anonymous | collab | collab-alternatives | name | name-alternatives | string-name)*, degrees*, (address | aff | aff-alternatives | author-comment | bio | email | ext-link | on-behalf-of | role | uri | xref)*)
name               (((surname, given-names?) | given-names), prefix?, suffix?)
string-name        (#PCDATA | degrees | given-names | prefix | surname | suffix)*
surname            #PCDATA*
given-names        #PCDATA*
prefix             #PCDATA*
suffix             #PCDATA*
degrees            #PCDATA*
contrib-id         #PCDATA
aff                (#PCDATA | addr-line | city | country | fax | institution | institution-wrap | phone | postal-code | state | email | ext-link | uri | inline-supplementary-material | related-article | related-object | break | bold | fixed-case | italic | monospace | overline | roman | sans-serif | sc | strike | underline | ruby | label | fn | target | xref | sub | sup)*
xref               (#PCDATA | bold | fixed-case | italic | monospace | overline | roman | sans-serif | sc | strike | underline | ruby | named-content | styled-content | sub | sup)*
author-notes       (label?, title?, (corresp | fn | p)+)
corresp            (#PCDATA | addr-line | city | country | fax | institution | institution-wrap | phone | postal-code | state | email | ext-link | uri | bold | fixed-case | italic | monospace | overline | roman | sans-serif | sc | strike | underline | ruby | label | named-content | styled-content | sub | sup)*
fn                 (label?, p+)
email              #PCDATA*
uri                #PCDATA*
history            date+
date               (((day?, month?) | season)?, year, era?)
day                #PCDATA
month              #PCDATA
year               #PCDATA
season             #PCDATA
era                #PCDATA

姓名无法可靠拆分时使用 `string-name`，不得猜测拆法。通信说明中的人名保留为普通文字。`day`、`month` 和 `year` 使用数字，与 Word 原文中的书写顺序无关。

## 三、文章类别与稿件日期

Word 原文明确写出的每一项文章类别都要保留。类别的可见文字保留在 `article-categories` 中。

`article-type` 取与 Word 类别语义相符的一项：

abstract、addendum、announcement、article-commentary、book-review、books-received、brief-report、calendar、case-report、clinical-instruction、collection、correction、discussion、dissertation、editorial、in-brief、introduction、letter、meeting-report、news、obituary、oration、partial-retraction、product-review、rapid-communication、reply、reprint、research-article、retraction、review-article、translation

Word 类别文字的语义不足以确定属于哪一项时，不输出 `article-type`。

`date-type` 取与 Word 事件语义相符的一项：

received（收到稿件）、rev-request（要求修改）、rev-recd（收到修改稿）、resubmitted（重新投稿）、accepted（录用）、pub（出版）、preprint（预印本发布）、corrected（更正）、retracted（撤稿）

只输出 Word 原文实际写有的日期分量；无法形成合法 `date` 时，不输出空的 `date`。

## 四、作者、编辑与关系

作者和编辑使用符合其身份的 `contrib` 结构。Word 原文在文首信息区中明确给出某位人的稿件角色时，输出该人。`role` 保留 Word 原文的角色文字，不翻译、不改写；原文没有角色文字时不得添加。

姓名、学位、ORCID、邮箱和个人地址可以出现在不同记录中；只有 Word 原文明确写出或标出它们属于某位作者或编辑时，才归入该 `contrib`。ORCID 使用 `contrib-id-type="orcid"`，内容为标准 `https://orcid.org/` URI。

`surname`、`given-names` 和 `string-name` 里只放人名。作者名后印的 `*`、`†`、`#` 等是关系标记，写进对应的 `xref`，不得留在姓名中。

为需要被引用的单位、通信说明和共享作者注释分配唯一 `id`，用 `xref/@rid` 指向对应的 `id`。一个人对应多个目标时，每个目标分别使用一个 `xref`。多个人可以指向同一个 `corresp` 或共享注释。

`xref/@ref-type` 取与被引用元素类型相符的一项：

aff、app、author-notes、award、bibr、bio、boxed-text、chem、collab、contrib、corresp、disp-formula、fig、fn、kwd、list、plate、scheme、sec、statement、supplementary-material、table、table-fn、other、custom

## 五、忠实保留 Word 原文

除 XML 转义和必要的 JATS 结构转换外，不得改动写入 XML 的文字，包括字符、大小写、单复数、全角或半角标点、空格和原稿中的错误。不得翻译、润色、纠错、摘要或改写 Word 原文。标题整段加粗通常是段落样式，不因此给整个标题套上 `<bold>`。将含义明确的英文月份写成月份数字；不要改动 Word 原文已经写出的数字形式。

Word 中印有关系标记时，在相应 `xref` 中保留。标记在单位文字之前就保留在之前，在之后就保留在之后。Word 原文明确表达了关系但没有印出标记时，使用没有可见内容的 `xref`。

单位、个人地址和通信说明即使含有相同文字，也不得相互覆盖。一份完整的通信说明对应一个 `corresp`；不得拆分、合并、摘要、换序或改写通信说明。`corresp` 的文字只取自本身构成通信说明的 Word 记录；不得使用单位或其他个人地址记录扩写它。Word 中有完整文字的共享作者注释使用 `fn`，并只与原文标记指明的作者建立关系。Word 印了标记、但全文找不到任何解释该标记的文字时：不建注释，也不建指向它的 `xref`，该标记不写进 XML。不得为了安放标记而编造注释正文。

## 六、不得编造

写进 XML 的姓名、邮箱、单位、地址、电话、日期、标识号和注释正文，必须来自 Word 原稿。Word 里没有的，一律不写；宁可少一个元素，也不得填入原稿中不存在的事实。编造出来的邮箱和姓名，读者会当真去用。

以下是本任务规定的写法，不属于编造：ORCID 补上 `https://orcid.org/` 前缀；英文月份写成月份数字；角色文字按示例的固定写法输出。

## 七、示例

### 示例一

**Word 记录**

[doc/p1] [{"text":"Original Research","styles":["italic"]}]
[doc/p2] [{"text":"Development of mathematical models Evaluating Presence of coronary calcification Independent of Computed Tomography (DEPICT): Radiation-free evaluation of coronary atherosclerosis","styles":["bold"]}]
[doc/p3] []
[doc/p4] [{"text":"Yinze Ji","styles":[]},{"text":"1,2","styles":["superscript"]},{"text":", M.D., Ph.D., Aimin Dang","styles":[]},{"text":"1,*","styles":["superscript"]},{"text":", M.D., Ph.D. , Naqiang Lv","styles":[]},{"text":"1,*","styles":["superscript"]},{"text":", M.D., Ph.D.","styles":[]}]
[doc/p5] [{"text":"Affiliations:","styles":[]}]
[doc/p6] [{"text":"1","styles":["superscript"]},{"text":" Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences & Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, Beijing, China","styles":[]}]
[doc/p7] [{"text":"2","styles":["superscript"]},{"text":" School of Public Health and Emergency Management, School of Medicine, Southern University of Science and Technology, Shenzhen, China","styles":[]}]
[doc/p8] []
[doc/p9] [{"text":"Addresses & ORCID of the author:","styles":[]}]
[doc/p10] [{"text":"Yinze Ji (E-mail: ","styles":[]},{"text":"jiyz3@sustech.edu.cn","styles":["underline"]},{"text":"):","styles":[]}]
[doc/p11] [{"text":"1","styles":["superscript"]},{"text":" Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences & Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, No.167 North Lishi Road, Xicheng District, Beijing, China (Postal code: 100037, Tel: +86(10)88322131)","styles":[]}]
[doc/p12] [{"text":"2","styles":["superscript"]},{"text":" Taizhou Building, 1088 Xueyuan Avenue, Shenzhen, People’s Republic of China (Postal code: 518055)","styles":[]}]
[doc/p13] [{"text":"ORCID: 0009-0006-1026-1505","styles":[]}]
[doc/p14] []
[doc/p15] [{"text":"Address of corresponding authors:","styles":[]}]
[doc/p16] [{"text":"Aimin Dang:","styles":[]}]
[doc/p17] [{"text":"1","styles":["superscript"]},{"text":" Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences & Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, No.167 North Lishi Road, Xicheng District, Beijing, China (Postal code: 100037, Tel: +86(10)88322131, E-mail: ","styles":[]},{"text":"amdangfw@163.com","styles":["underline"]},{"text":")","styles":[]}]
[doc/p18] [{"text":"ORCID: 0000-0003-3315-7840","styles":[]}]
[doc/p19] []
[doc/p20] [{"text":"Naqiang Lv:","styles":[]}]
[doc/p21] [{"text":"1","styles":["superscript"]},{"text":" Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences & Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, No.167 North Lishi Road, Xicheng District, Beijing, China (Postal code: 100037, Tel: +86(10)88322131, E-mail: ","styles":[]},{"text":"lvnaqiang@gmail.com","styles":["underline"]},{"text":")","styles":[]}]
[doc/p22] [{"text":"ORCID: 0000-0002-5660-8897","styles":[]}]
[doc/p23] []
[doc/p24] [{"text":"Aimin Dang will handle correspondence at all stages of refereeing and publication, also post-publication.","styles":[]}]
[doc/p25] []
[doc/p26] [{"text":"2025/9/21","styles":[]}]
[doc/p27] [{"text":"2026/1/15","styles":[]}]
[doc/p28] [{"text":"2025/1/16","styles":[]}]
[doc/p29] []
[doc/p30] [{"text":"学编：Salvatore De Rosa","styles":[]}]

**输出**

<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Original Research</subject></subj-group></article-categories><title-group><article-title>Development of mathematical models Evaluating Presence of coronary calcification Independent of Computed Tomography (DEPICT): Radiation-free evaluation of coronary atherosclerosis</article-title></title-group><contrib-group><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0009-0006-1026-1505</contrib-id><name><surname>Ji</surname><given-names>Yinze</given-names></name><degrees>M.D., Ph.D.</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><email>jiyz3@sustech.edu.cn</email><address><addr-line><sup>1</sup> Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences &amp; Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, No.167 North Lishi Road, Xicheng District, Beijing, China</addr-line><postal-code>100037</postal-code><phone>+86(10)88322131</phone></address><address><addr-line><sup>2</sup> Taizhou Building, 1088 Xueyuan Avenue, Shenzhen, People’s Republic of China</addr-line><postal-code>518055</postal-code></address></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0003-3315-7840</contrib-id><name><surname>Dang</surname><given-names>Aimin</given-names></name><degrees>M.D., Ph.D.</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>amdangfw@163.com</email><address><addr-line><sup>1</sup> Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences &amp; Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, No.167 North Lishi Road, Xicheng District, Beijing, China</addr-line><postal-code>100037</postal-code><phone>+86(10)88322131</phone></address></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-5660-8897</contrib-id><name><surname>Lv</surname><given-names>Naqiang</given-names></name><degrees>M.D., Ph.D.</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>lvnaqiang@gmail.com</email><address><addr-line><sup>1</sup> Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences &amp; Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, No.167 North Lishi Road, Xicheng District, Beijing, China</addr-line><postal-code>100037</postal-code><phone>+86(10)88322131</phone></address></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>De Rosa</surname><given-names>Salvatore</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup> Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of Medical Sciences &amp; Peking Union Medical College, National Clinical Research Center for Cardiovascular Diseases, National Center for Cardiovascular Diseases, Beijing, China</aff><aff id="aff2"><sup>2</sup> School of Public Health and Emergency Management, School of Medicine, Southern University of Science and Technology, Shenzhen, China</aff><author-notes><corresp id="cor1"><sup>*</sup>Correspondence: <email>amdangfw@163.com</email> (Aimin Dang); <email>lvnaqiang@gmail.com</email> (Naqiang Lv)</corresp><p>Aimin Dang will handle correspondence at all stages of refereeing and publication, also post-publication.</p></author-notes><history><date date-type="received"><day>21</day><month>9</month><year>2025</year></date><date date-type="rev-recd"><day>15</day><month>1</month><year>2026</year></date><date date-type="accepted"><day>16</day><month>1</month><year>2025</year></date></history></article-meta></front></article>

### 示例二

**Word 记录**

[doc/p1] [{"text":"Review","styles":["italic"]}]
[doc/p2] [{"text":"Technological Innovations and Research Frontiers in Interventional Therapy for Mitral Regurgitation","styles":["bold"]}]
[doc/p3] [{"text":"Shuang Wang","styles":[]},{"text":"1†","styles":["superscript"]},{"text":",Aili Wang","styles":[]},{"text":"1†","styles":["superscript"]},{"text":",Yuna Huang","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Jinping Liu ","styles":[]},{"text":"2","styles":["superscript"]},{"text":", Bin Wang","styles":[]},{"text":"1*","styles":["superscript"]}]
[doc/p4] [{"text":"†","styles":["superscript"]},{"text":"The first two authors contributed equally to the study.","styles":[]}]
[doc/p5] []
[doc/p6] [{"text":"1","styles":["superscript"]},{"text":"Department of Cardiovascular Ultrasound, Zhongnan Hospital of Wuhan University, Wuhan 430071, China","styles":[]}]
[doc/p7] [{"text":"2","styles":["superscript"]},{"text":"Department of Cardiovascular Surgery, Zhongnan Hospital of Wuhan University, Wuhan 430071, China","styles":[]}]
[doc/p8] []
[doc/p9] [{"text":"Correspondences: ","styles":["bold"]}]
[doc/p10] [{"text":"Bin Wang, Department of Cardiovascular Ultrasound, Zhongnan Hospital of Wuhan University, Wuhan University, Wuhan 430071, China. ","styles":[]}]
[doc/p11] [{"text":"Email: wangbin87098429@126.com","styles":[]}]
[doc/p12] []
[doc/p13] [{"text":"ORCID:","styles":[]}]
[doc/p14] [{"text":"Shuang Wang: 0009-0004-8148-7152","styles":[]}]
[doc/p15] [{"text":"Bin Wang: 0000-0003-0201-9154","styles":[]}]

**输出**

<article article-type="review-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Review</subject></subj-group></article-categories><title-group><article-title>Technological Innovations and Research Frontiers in Interventional Therapy for Mitral Regurgitation</article-title></title-group><contrib-group><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0004-8148-7152</contrib-id><name><surname>Wang</surname><given-names>Shuang</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="fn" rid="fn1"><sup>†</sup></xref></contrib><contrib contrib-type="author"><name><surname>Wang</surname><given-names>Aili</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="fn" rid="fn1"><sup>†</sup></xref></contrib><contrib contrib-type="author"><name><surname>Huang</surname><given-names>Yuna</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Liu</surname><given-names>Jinping</given-names></name><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-0201-9154</contrib-id><name><surname>Wang</surname><given-names>Bin</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>wangbin87098429@126.com</email></contrib></contrib-group><aff id="aff1"><sup>1</sup>Department of Cardiovascular Ultrasound, Zhongnan Hospital of Wuhan University, Wuhan 430071, China</aff><aff id="aff2"><sup>2</sup>Department of Cardiovascular Surgery, Zhongnan Hospital of Wuhan University, Wuhan 430071, China</aff><author-notes><corresp id="cor1"><sup>*</sup>Correspondences: Bin Wang, Department of Cardiovascular Ultrasound, Zhongnan Hospital of Wuhan University, Wuhan University, Wuhan 430071, China. Email: <email>wangbin87098429@126.com</email></corresp><fn id="fn1"><p><sup>†</sup>The first two authors contributed equally to the study.</p></fn></author-notes></article-meta></front></article>

### 示例三

**Word 记录**

[doc/p1] [{"text":"Original Research","styles":["italic"]}]
[doc/p2] []
[doc/p3] [{"text":"Assessing Cumulative Mental Fatigue via EEG-Based Machine Learning in a Multiday High-Intensity Contest","styles":["bold"]}]
[doc/p4] [{"text":"Xiaodong Yang","styles":[]},{"text":"1†","styles":["superscript"]},{"text":", Jie Zhou","styles":[]},{"text":"1†","styles":["superscript"]},{"text":", Zhan Chen","styles":[]},{"text":"2","styles":["superscript"]},{"text":", Yufei Jing","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Yawei Xie","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Hao Yu","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Yongjie Yao","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Chunpeng Jiang","styles":[]},{"text":"3","styles":["superscript"]},{"text":" and Chuantao Li","styles":[]},{"text":"1,","styles":["superscript"]},{"text":"*","styles":[]}]
[doc/p5] [{"text":"1","styles":["superscript"]},{"text":"Naval Medical Center of PLA, Second Military Medical University, Shanghai 200433, China","styles":[]}]
[doc/p6] [{"text":"2","styles":["superscript"]},{"text":"School of Open Learning and Education, East China Normal University, Shanghai 200062, China","styles":[]}]
[doc/p7] [{"text":"3","styles":["superscript"]},{"text":"National Key Laboratory of Advanced Micro and Nano Manufacture Technology, Shanghai Jiao Tong University, Shanghai 200240, China","styles":[]}]
[doc/p8] [{"text":"*Correspondence: lichuantao@smmu.edu.cn (Chuantao Li)","styles":[]}]
[doc/p9] []
[doc/p10] [{"text":"†","styles":["superscript"]},{"text":"These authors contributed equally.","styles":[]}]
[doc/p11] [{"text":"ORCID:","styles":[]}]
[doc/p12] [{"text":"Xiaodong Yang: 0000-0003-2351-2343","styles":[]}]
[doc/p13] [{"text":"Jie Zhou: 0009-0000-4430-5461","styles":[]}]
[doc/p14] [{"text":"Zhan Chen: 0009-0001-1336-5637","styles":[]}]
[doc/p15] [{"text":"Yufei Jing: 0009-0006-2940-5652","styles":[]}]
[doc/p16] [{"text":"Yawei Xie: 0009-0009-9845-8725","styles":[]}]
[doc/p17] [{"text":"Chunpeng Jiang: 0000-0003-1171-4044","styles":[]}]
[doc/p18] [{"text":"Chuantao Li: 0000-0002-4917-7750","styles":[]}]
[doc/p19] [{"text":"Academic editor: Bettina Platt","styles":[]}]
[doc/p20] [{"text":"Submitted: 22 / 12 / 2025 ","styles":[]}]
[doc/p20.2] [{"text":"Revised: 24 / 02 / 2026","styles":[]}]
[doc/p20.3] [{"text":"Accepted: 待接收","styles":[]}]

**输出**

<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Original Research</subject></subj-group></article-categories><title-group><article-title>Assessing Cumulative Mental Fatigue via EEG-Based Machine Learning in a Multiday High-Intensity Contest</article-title></title-group><contrib-group><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-2351-2343</contrib-id><name><surname>Yang</surname><given-names>Xiaodong</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="fn" rid="fn-1"><sup>†</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0000-4430-5461</contrib-id><name><surname>Zhou</surname><given-names>Jie</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="fn" rid="fn-1"><sup>†</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0001-1336-5637</contrib-id><name><surname>Chen</surname><given-names>Zhan</given-names></name><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0006-2940-5652</contrib-id><name><surname>Jing</surname><given-names>Yufei</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0009-9845-8725</contrib-id><name><surname>Xie</surname><given-names>Yawei</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Yu</surname><given-names>Hao</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Yao</surname><given-names>Yongjie</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-1171-4044</contrib-id><name><surname>Jiang</surname><given-names>Chunpeng</given-names></name><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-4917-7750</contrib-id><name><surname>Li</surname><given-names>Chuantao</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>lichuantao@smmu.edu.cn</email></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>Platt</surname><given-names>Bettina</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Naval Medical Center of PLA, Second Military Medical University, Shanghai 200433, China</aff><aff id="aff2"><sup>2</sup>School of Open Learning and Education, East China Normal University, Shanghai 200062, China</aff><aff id="aff3"><sup>3</sup>National Key Laboratory of Advanced Micro and Nano Manufacture Technology, Shanghai Jiao Tong University, Shanghai 200240, China</aff><author-notes><corresp id="cor1"><sup>*</sup>Correspondence: <email>lichuantao@smmu.edu.cn</email> (Chuantao Li)</corresp><fn id="fn-1"><p><sup>†</sup>These authors contributed equally.</p></fn></author-notes><history><date date-type="received"><day>22</day><month>12</month><year>2025</year></date><date date-type="rev-recd"><day>24</day><month>2</month><year>2026</year></date></history></article-meta></front></article>

### 示例四

**Word 记录**

[doc/p1] [{"text":"Review","styles":["italic"]}]
[doc/p2] []
[doc/p3] [{"text":"Antiseizure Medications","styles":["bold"]},{"text":" ","styles":[]},{"text":"impact mitochondrial ion channels in novel bioenergetic and neural mechanisms","styles":["bold"]}]
[doc/p4] []
[doc/p5] [{"text":"Carmen Rubio1#, Norma Serrano-Garcia1#, Ricardo Pérez-Rubio1,2, Javier Pérez-Villavicencio1,3, Héctor Romo-Parra1,4, Ángel Lee5, Moisés Rubio-Osornio6*.","styles":[]}]
[doc/p6] []
[doc/p7] [{"text":"1","styles":["superscript"]},{"text":" Department of Neurophysiology, National Institute of Neurology and Neurosurgery, Mexico City, 14269 Mexico","styles":[]}]
[doc/p8] [{"text":"2","styles":["superscript"]},{"text":" Mexican Faculty of Medicine, La Salle University, Mexico City, 14000 Mexico  ","styles":[]}]
[doc/p9] [{"text":"3","styles":["superscript"]},{"text":" Department of Electrical Engineering, Basic Sciences and Engineering Division Metropolitan Autonomous University, Iztapalapa Campus, Mexico City, 09340 Mexico.","styles":[]}]
[doc/p10] [{"text":"4","styles":["superscript"]},{"text":" Department of Psychology, Ibero-American University, Santa Fe Campus, Mexico City, 01376 Mexico ","styles":[]}]
[doc/p11] [{"text":"5","styles":["superscript"]},{"text":" National Institute of Public Health, Cuernavaca, Morelos, Mexico  ","styles":[]}]
[doc/p12] [{"text":"6","styles":["superscript"]},{"text":" Department of Neurochemistry, National Institute of Neurology and Neurosurgery, Mexico City, 14269 Mexico","styles":[]}]
[doc/p13] []
[doc/p14] [{"text":"#contributed equally to this work","styles":[]}]
[doc/p15] []
[doc/p16] [{"text":"*Corresponding author ","styles":[]}]
[doc/p17] [{"text":"Moisés Rubio-Osornio, E-mail: moises.rubio@innn.edu.mx ","styles":[]}]
[doc/p18] [{"text":"ORCID:","styles":[]}]
[doc/p19] [{"text":"Moisés Rubio-Osornio: 0000-0001-9236-0609","styles":[]}]
[doc/p20] [{"text":"Norma Serrano-García 0000-0001-5859-2617","styles":[]}]
[doc/p21] [{"text":"Ricardo Perez-Rubio: 0000000242784814","styles":[]}]
[doc/p22] [{"text":"Javier Perez-Villavicencio: 0000000231846522","styles":[]}]
[doc/p23] [{"text":"Hector Romo-Parra: 0000-0002-4275-3161","styles":[]}]
[doc/p24] [{"text":"Angel Lee: 0000-0002-2301-3598","styles":[]}]
[doc/p25] []
[doc/p26] [{"text":"Academic Editor: Kuei-Sen Hsu","styles":["bold"]}]
[doc/p27] []
[doc/p28] [{"text":"Submitted: 27 / 03 / 2026","styles":["bold"]}]
[doc/p29] [{"text":"Revised: 27 / 04 / 2026","styles":["bold"]}]
[doc/p30] [{"text":"Accepted：30 / 04 / 2026","styles":["bold"]}]

**输出**

<article article-type="review-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Review</subject></subj-group></article-categories><title-group><article-title>Antiseizure Medications impact mitochondrial ion channels in novel bioenergetic and neural mechanisms</article-title></title-group><contrib-group><contrib contrib-type="author"><name><surname>Rubio</surname><given-names>Carmen</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="fn" rid="fn1"><sup>#</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0001-5859-2617</contrib-id><name><surname>Serrano-Garcia</surname><given-names>Norma</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="fn" rid="fn1"><sup>#</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-4278-4814</contrib-id><name><surname>Pérez-Rubio</surname><given-names>Ricardo</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-3184-6522</contrib-id><name><surname>Pérez-Villavicencio</surname><given-names>Javier</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-4275-3161</contrib-id><name><surname>Romo-Parra</surname><given-names>Héctor</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff4"><sup>4</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-2301-3598</contrib-id><name><surname>Lee</surname><given-names>Ángel</given-names></name><xref ref-type="aff" rid="aff5"><sup>5</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid">https://orcid.org/0000-0001-9236-0609</contrib-id><name><surname>Rubio-Osornio</surname><given-names>Moisés</given-names></name><xref ref-type="aff" rid="aff6"><sup>6</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>moises.rubio@innn.edu.mx</email></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>Hsu</surname><given-names>Kuei-Sen</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Department of Neurophysiology, National Institute of Neurology and Neurosurgery, Mexico City, 14269 Mexico</aff><aff id="aff2"><sup>2</sup>Mexican Faculty of Medicine, La Salle University, Mexico City, 14000 Mexico</aff><aff id="aff3"><sup>3</sup>Department of Electrical Engineering, Basic Sciences and Engineering Division Metropolitan Autonomous University, Iztapalapa Campus, Mexico City, 09340 Mexico.</aff><aff id="aff4"><sup>4</sup>Department of Psychology, Ibero-American University, Santa Fe Campus, Mexico City, 01376 Mexico</aff><aff id="aff5"><sup>5</sup>National Institute of Public Health, Cuernavaca, Morelos, Mexico</aff><aff id="aff6"><sup>6</sup>Department of Neurochemistry, National Institute of Neurology and Neurosurgery, Mexico City, 14269 Mexico</aff><author-notes><corresp id="cor1"><sup>*</sup>Corresponding author Moisés Rubio-Osornio, E-mail: <email>moises.rubio@innn.edu.mx</email></corresp><fn id="fn1" fn-type="equal"><p><sup>#</sup>contributed equally to this work</p></fn></author-notes><history><date date-type="received"><day>27</day><month>3</month><year>2026</year></date><date date-type="rev-recd"><day>27</day><month>4</month><year>2026</year></date><date date-type="accepted"><day>30</day><month>4</month><year>2026</year></date></history></article-meta></front></article>

### 示例五

**Word 记录**

[doc/p1] [{"text":" ","styles":[]},{"text":"Article ","styles":["italic"]}]
[doc/p2] []
[doc/p3] [{"text":"How do age and the development of need for urgent surgical aortic valve replacement affect hospital mortality and long-term survival? A stratified analysis.","styles":["bold"]}]
[doc/p4] []
[doc/p5] [{"text":"Wilhelm Mistiaen","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Karl Dossche², Anthony Vanermen², Ivo Deblier². ","styles":[]}]
[doc/p6] []
[doc/p7] []
[doc/p8] []
[doc/p9] [{"text":"Faculty of Medicine and Health Sciences, University of Antwerp, 2650 Antwerp","styles":[]}]
[doc/p10] [{"text":"Department of Cardiovascular Surgery, ZAS Middelheim, 2020 Antwerp","styles":[]}]
[doc/p11] []
[doc/p12] []
[doc/p13] []
[doc/p14] []
[doc/p15] []
[doc/p16] [{"text":"Address for correspondence","styles":[]}]
[doc/p17] []
[doc/p18] [{"text":"Wilhelm Mistiaen","styles":[]}]
[doc/p19] [{"text":"University of Antwerp","styles":[]}]
[doc/p20] [{"text":"Faculty of Medicine and Health Sciences","styles":[]}]
[doc/p21] [{"text":"Building R, 3","styles":[]},{"text":"rd","styles":["superscript"]},{"text":" floor","styles":[]}]
[doc/p22] [{"text":"Campus Drie Eiken ","styles":[]}]
[doc/p23] [{"text":"Universiteitsplein 1","styles":[]}]
[doc/p24] [{"text":"2610 Antwerp","styles":[]}]
[doc/p25] [{"text":"Belgium ","styles":[]}]
[doc/p26] [{"text":"Wilhelm.mistiaen@uantwerpen.be","styles":["underline"]},{"text":" ","styles":[]}]
[doc/p27] []
[doc/p28] [{"text":"Submission Date: ","styles":["bold"]},{"text":"15 / 12 / 2025","styles":[]},{"text":" | ","styles":["bold"]}]
[doc/p29] [{"text":"Last Revision Date: ","styles":["bold"]},{"text":"26 / 03 / 2026","styles":[]},{"text":" | ","styles":["bold"]}]
[doc/p30] [{"text":"Last Action Date: ","styles":["bold"]},{"text":"30 / 03 / 2026","styles":[]}]
[doc/p31] []
[doc/p32] [{"text":"学编：Isaac George","styles":[]}]

**输出**

<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Article</subject></subj-group></article-categories><title-group><article-title>How do age and the development of need for urgent surgical aortic valve replacement affect hospital mortality and long-term survival? A stratified analysis.</article-title></title-group><contrib-group><contrib contrib-type="author"><name><surname>Mistiaen</surname><given-names>Wilhelm</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref></contrib><contrib contrib-type="author"><name><surname>Dossche</surname><given-names>Karl</given-names></name><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib><contrib contrib-type="author"><name><surname>Vanermen</surname><given-names>Anthony</given-names></name><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib><contrib contrib-type="author"><name><surname>Deblier</surname><given-names>Ivo</given-names></name><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>George</surname><given-names>Isaac</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Faculty of Medicine and Health Sciences, University of Antwerp, 2650 Antwerp</aff><aff id="aff2"><sup>2</sup>Department of Cardiovascular Surgery, ZAS Middelheim, 2020 Antwerp</aff><author-notes><corresp id="cor1"><sup>*</sup> Address for correspondence: Wilhelm Mistiaen, University of Antwerp, Faculty of Medicine and Health Sciences, Building R, 3<sup>rd</sup> floor, Campus Drie Eiken, Universiteitsplein 1, 2610 Antwerp, Belgium, <email>Wilhelm.mistiaen@uantwerpen.be</email></corresp></author-notes><history><date date-type="received"><day>15</day><month>12</month><year>2025</year></date><date date-type="rev-recd"><day>26</day><month>03</month><year>2026</year></date><date date-type="accepted"><day>30</day><month>03</month><year>2026</year></date></history></article-meta></front></article>

### 示例六

**Word 记录**

[doc/p1] [{"text":"Original Research","styles":["italic"]}]
[doc/p2] [{"text":"Screening and Prenatal Diagnosis of Spinal Muscular Atrophy in 13,500 Pregnant Women in the Changzhi area","styles":["bold"]}]
[doc/p3] [{"text":"Min Zhang","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Jing Guan","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Fei Liang","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Huiyi Shen","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Xiaoze Li","styles":[]},{"text":"1,","styles":["superscript"]},{"text":"*","styles":[]}]
[doc/p4] []
[doc/p5] [{"text":"1","styles":["superscript"]},{"text":"Department of Medical Genetics, Changzhi Maternal and Child Health Hospital, 046000 Changzhi, Shanxi, China","styles":[]}]
[doc/p6] []
[doc/p7] [{"text":"*Correspondence: 13403554760@163.com (Xiaoze Li)","styles":[]}]
[doc/p8] []
[doc/p9] [{"text":"ORCID:","styles":[]}]
[doc/p10] [{"text":"0009-0005-6068-9537 (Xiaoze Li)","styles":[]}]
[doc/p11] []
[doc/p12] [{"text":"Academic Editor: Stefania Carlucci","styles":[]}]
[doc/p13] []
[doc/p14] [{"text":"Submitted: 24 November 2025","styles":[]}]
[doc/p15] [{"text":"Revised: 2 February 2026","styles":[]}]
[doc/p16] [{"text":"Accepted: 27 February 2026","styles":[]}]

**输出**

<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Original Research</subject></subj-group></article-categories><title-group><article-title>Screening and Prenatal Diagnosis of Spinal Muscular Atrophy in 13,500 Pregnant Women in the Changzhi area</article-title></title-group><contrib-group><contrib contrib-type="author"><name><surname>Zhang</surname><given-names>Min</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Guan</surname><given-names>Jing</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Liang</surname><given-names>Fei</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Shen</surname><given-names>Huiyi</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0005-6068-9537</contrib-id><name><surname>Li</surname><given-names>Xiaoze</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>13403554760@163.com</email></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>Carlucci</surname><given-names>Stefania</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Department of Medical Genetics, Changzhi Maternal and Child Health Hospital, 046000 Changzhi, Shanxi, China</aff><author-notes><corresp id="cor1"><sup>*</sup>Correspondence: <email>13403554760@163.com</email> (Xiaoze Li)</corresp></author-notes><history><date date-type="received"><day>24</day><month>11</month><year>2025</year></date><date date-type="rev-recd"><day>2</day><month>2</month><year>2026</year></date><date date-type="accepted"><day>27</day><month>2</month><year>2026</year></date></history></article-meta></front></article>

### 示例七

**Word 记录**

[doc/p1] [{"text":"Review","styles":["italic"]}]
[doc/p2] [{"text":"Signs o’ the Times. The Quiet Revolution of Molecular Pathology in Gynecologic Oncology: A Narrative Review","styles":["bold"]}]
[doc/p3] [{"text":"Valerio Gaetano Vellone","styles":[]},{"text":"1,2,","styles":["superscript"]},{"text":"*, Michele Paudice","styles":[]},{"text":"2,3","styles":["superscript"]},{"text":", Gabriele Gaggero","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Francesca Buffelli","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Katia Mazzocco","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Roberta Musso","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Maria Teresa Gambaudo","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Serafina Mammoliti","styles":[]},{"text":"4","styles":["superscript"]},{"text":", Simone Ferrero","styles":[]},{"text":"5,6","styles":["superscript"]},{"text":", Emanuela Marcenaro","styles":[]},{"text":"7,8","styles":["superscript"]}]
[doc/p4] []
[doc/p5] [{"text":"1","styles":["superscript"]},{"text":"Pathology Unit, IRCCS Istituto Giannina Gaslini, 16147 Genoa, Italy","styles":[]}]
[doc/p6] [{"text":"2","styles":["superscript"]},{"text":"Department Of Integrated Surgical and Diagnostic Sciences (DISC), University of Genoa, 16132 Genoa, Italy","styles":[]}]
[doc/p7] [{"text":"3","styles":["superscript"]},{"text":"Pathology Academic Unit, AOM IRCCS San Martino, 16132 Genoa, Italy","styles":[]}]
[doc/p8] [{"text":"4","styles":["superscript"]},{"text":"Oncology Unit, AOM Villa Scassi, 16149 Genoa, Italy","styles":[]}]
[doc/p9] [{"text":"5","styles":["superscript"]},{"text":"Gynecology Academic Unit, AOM IRCCS San Martino, 16132 Genoa, Italy","styles":[]}]
[doc/p10] [{"text":"6","styles":["superscript"]},{"text":"Department of Neuroscience, Rehabilitation, Ophthalmology, Genetics, Maternal and Child Health (DINOGMI), University of Genoa, 16132 Genoa, Italy","styles":[]}]
[doc/p11] [{"text":"7","styles":["superscript"]},{"text":"Department of Experimental Medicine (DIMES), University of Genoa, 16132 Genoa, Italy","styles":[]}]
[doc/p12] [{"text":"8","styles":["superscript"]},{"text":"AOM IRCCS San Martino, 16132 Genoa, Italy","styles":[]}]
[doc/p13] []
[doc/p14] [{"text":"*Correspondence: valerio.vellone@unige.it; valeriovellone@gaslini.org (Valerio Gaetano Vellone)","styles":[]}]
[doc/p15] [{"text":"ORCID:","styles":[]}]
[doc/p16] [{"text":"Valerio Gaetano Vellone: 0000-0002-5107-1584","styles":[]}]
[doc/p17] [{"text":"Michele Paudice: 0000-0003-4188-4247","styles":[]}]
[doc/p18] [{"text":"Gabriele Gaggero: 0000-0001-9098-563X","styles":[]}]
[doc/p19] [{"text":"Francesca Buffelli: 0000-0003-1086-2651","styles":[]}]
[doc/p20] [{"text":"Katia Mazzocco: 0000-0002-6599-5681","styles":[]}]
[doc/p21] [{"text":"Roberta Musso: 0009-0004-5024-2486","styles":[]}]
[doc/p22] [{"text":"Maria Teresa Gambaudo: 0009-0005-5422-2463","styles":[]}]
[doc/p23] [{"text":"Serafina Mammoliti: 0000-0002-6560-0061","styles":[]}]
[doc/p24] [{"text":"Simone Ferrero: 0000-0003-2225-5568","styles":[]}]
[doc/p25] [{"text":"Emanuela Marcenaro: 0000-0003-4103-7566","styles":[]}]
[doc/p26] []
[doc/p27] [{"text":"Academic Editors: Christos Iavazzo and Michael H. Dahan","styles":[]}]
[doc/p28] []
[doc/p29] [{"text":"Submitted: 2 March 2026","styles":[]}]
[doc/p30] [{"text":"Revised: 30 March 2026","styles":[]}]
[doc/p31] [{"text":"Accepted: 14 April 2026","styles":[]}]

**输出**

<article article-type="review-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Review</subject></subj-group></article-categories><title-group><article-title>Signs o’ the Times. The Quiet Revolution of Molecular Pathology in Gynecologic Oncology: A Narrative Review</article-title></title-group><contrib-group><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-5107-1584</contrib-id><name><surname>Vellone</surname><given-names>Valerio Gaetano</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>valerio.vellone@unige.it</email><email>valeriovellone@gaslini.org</email></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-4188-4247</contrib-id><name><surname>Paudice</surname><given-names>Michele</given-names></name><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0001-9098-563X</contrib-id><name><surname>Gaggero</surname><given-names>Gabriele</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-1086-2651</contrib-id><name><surname>Buffelli</surname><given-names>Francesca</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-6599-5681</contrib-id><name><surname>Mazzocco</surname><given-names>Katia</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0004-5024-2486</contrib-id><name><surname>Musso</surname><given-names>Roberta</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0005-5422-2463</contrib-id><name><surname>Gambaudo</surname><given-names>Maria Teresa</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-6560-0061</contrib-id><name><surname>Mammoliti</surname><given-names>Serafina</given-names></name><xref ref-type="aff" rid="aff4"><sup>4</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-2225-5568</contrib-id><name><surname>Ferrero</surname><given-names>Simone</given-names></name><xref ref-type="aff" rid="aff5"><sup>5</sup></xref><xref ref-type="aff" rid="aff6"><sup>6</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-4103-7566</contrib-id><name><surname>Marcenaro</surname><given-names>Emanuela</given-names></name><xref ref-type="aff" rid="aff7"><sup>7</sup></xref><xref ref-type="aff" rid="aff8"><sup>8</sup></xref></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>Iavazzo</surname><given-names>Christos</given-names></name><role>Academic Editor</role></contrib><contrib contrib-type="editor"><name><surname>Dahan</surname><given-names>Michael H.</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Pathology Unit, IRCCS Istituto Giannina Gaslini, 16147 Genoa, Italy</aff><aff id="aff2"><sup>2</sup>Department Of Integrated Surgical and Diagnostic Sciences (DISC), University of Genoa, 16132 Genoa, Italy</aff><aff id="aff3"><sup>3</sup>Pathology Academic Unit, AOM IRCCS San Martino, 16132 Genoa, Italy</aff><aff id="aff4"><sup>4</sup>Oncology Unit, AOM Villa Scassi, 16149 Genoa, Italy</aff><aff id="aff5"><sup>5</sup>Gynecology Academic Unit, AOM IRCCS San Martino, 16132 Genoa, Italy</aff><aff id="aff6"><sup>6</sup>Department of Neuroscience, Rehabilitation, Ophthalmology, Genetics, Maternal and Child Health (DINOGMI), University of Genoa, 16132 Genoa, Italy</aff><aff id="aff7"><sup>7</sup>Department of Experimental Medicine (DIMES), University of Genoa, 16132 Genoa, Italy</aff><aff id="aff8"><sup>8</sup>AOM IRCCS San Martino, 16132 Genoa, Italy</aff><author-notes><corresp id="cor1"><sup>*</sup>Correspondence: <email>valerio.vellone@unige.it</email>; <email>valeriovellone@gaslini.org</email> (Valerio Gaetano Vellone)</corresp></author-notes><history><date date-type="received"><day>2</day><month>3</month><year>2026</year></date><date date-type="rev-recd"><day>30</day><month>3</month><year>2026</year></date><date date-type="accepted"><day>14</day><month>4</month><year>2026</year></date></history></article-meta></front></article>

### 示例八

**Word 记录**

[doc/p1] [{"text":"Original Research","styles":["italic"]}]
[doc/p2] [{"text":"Evaluating the Performance of Large Language Models GPT-4, Claude 3 Sonnet, and Gemini Pro in Recurrent Pregnancy Loss.","styles":["bold"]}]
[doc/p3] [{"text":"Author information:","styles":["bold"]}]
[doc/p4] [{"text":"Han Zhang, M.Sc","styles":[]},{"text":"1,2,3 †","styles":["superscript"]},{"text":". Chanlin Han, M.Sc","styles":[]},{"text":"1,2,3 †","styles":["superscript"]},{"text":". Rui Hu, M.Sc","styles":[]},{"text":"12,3","styles":["superscript"]},{"text":". Xiao Zhou, M.B","styles":[]},{"text":"12,3","styles":["superscript"]},{"text":". Xuemei Li, M.Sc","styles":[]},{"text":"1,2,3 ","styles":["superscript"]},{"text":". Jifan Tan, M.D","styles":[]},{"text":"1,2,3*","styles":["superscript"]},{"text":". ","styles":[]}]
[doc/p5] [{"text":"1 Reproductive Medicine Center, Shenzhen Maternity and Child Healthcare Hospital, Women and Children's Medical Center, Southern Medical University, Shenzhen, Guangdong Province, China","styles":[]}]
[doc/p6] [{"text":"2 Shenzhen Key Laboratory of Maternal and Child Health and Diseases，Shenzhen 518000，Guangdong，China","styles":[]}]
[doc/p7] [{"text":"3 Shenzhen Clinical Research Center for Obstetrics & Gynecology and Reproductive System Diseases，Shenzhen 518000，Guangdong，China","styles":[]}]
[doc/p8] []
[doc/p9] [{"text":"† The authors consider that the first two authors should be regarded as joint First Authors.","styles":[]}]
[doc/p10] [{"text":"* Corresponding author. ","styles":[]}]
[doc/p11] [{"text":"Jifan Tan, Tel: +86 15017554785; Email：","styles":[]},{"text":"tanjifan@alumni.sysu.edu.cn","styles":["underline"]}]
[doc/p12] [{"text":"Academic editor: Andrea Tinelli, Michael H. Dahan","styles":[]}]
[doc/p13] []
[doc/p14] [{"text":"Submitted: 23 / 1/2026","styles":[]}]
[doc/p15] [{"text":"Revised: 27 / 2/ 2026","styles":[]}]
[doc/p16] [{"text":"Accepted:7/ 4/ 2026","styles":[]}]
[doc/p17] [{"text":"Author contributions:","styles":["bold"]}]
[doc/p18] [{"text":"Han Zhang (","styles":["bold"]},{"text":"ORCID: 0000-0002-6454-4301","styles":[]},{"text":"):","styles":["bold"]},{"text":" Conceptualization, Data curation, Writing original draft, Writing review editing. ","styles":[]}]
[doc/p19] [{"text":"Chanlin Han (","styles":["bold"]},{"text":"ORCID: 0009-0004-5525-5964","styles":[]},{"text":"):","styles":["bold"]},{"text":" Data curation, Formal analysis, Writing original draft, Writing review editing. ","styles":[]}]
[doc/p20] [{"text":"Rui Hu:","styles":["bold"]},{"text":" Data curation, Investigation, Writing original draft, Writing review editing. ","styles":[]}]
[doc/p21] [{"text":"Xiao Zhou:","styles":["bold"]},{"text":" Data curation, Investigation, Writing original draft, Writing review editing. ","styles":[]}]
[doc/p22] [{"text":"Xuemei Li (","styles":["bold"]},{"text":"ORCID: 0000-0002-2457-6165","styles":[]},{"text":"):","styles":["bold"]},{"text":" Conceptualization, Methodology, Funding acquisition, Supervision.  ","styles":[]}]
[doc/p23] [{"text":"Jifan Tan (","styles":["bold"]},{"text":"ORCID: ","styles":[]},{"text":"0000-0002-5014-5393):","styles":["bold"]},{"text":" Conceptualization, Funding acquisition, Investigation, Methodology, project administration, review editing. ","styles":[]}]
[doc/p24] []
[doc/p25] [{"text":"Article type:","styles":["bold"]},{"text":" Observational Study","styles":[]}]

**输出**

<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Original Research</subject></subj-group><subj-group subj-group-type="article-type"><subject>Observational Study</subject></subj-group></article-categories><title-group><article-title>Evaluating the Performance of Large Language Models GPT-4, Claude 3 Sonnet, and Gemini Pro in Recurrent Pregnancy Loss.</article-title></title-group><contrib-group><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-6454-4301</contrib-id><name><surname>Zhang</surname><given-names>Han</given-names></name><degrees>M.Sc</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><xref ref-type="fn" rid="fn-1"><sup>†</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0004-5525-5964</contrib-id><name><surname>Han</surname><given-names>Chanlin</given-names></name><degrees>M.Sc</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><xref ref-type="fn" rid="fn-1"><sup>†</sup></xref></contrib><contrib contrib-type="author"><name><surname>Hu</surname><given-names>Rui</given-names></name><degrees>M.Sc</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><name><surname>Zhou</surname><given-names>Xiao</given-names></name><degrees>M.B</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-2457-6165</contrib-id><name><surname>Li</surname><given-names>Xuemei</given-names></name><degrees>M.Sc</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-5014-5393</contrib-id><name><surname>Tan</surname><given-names>Jifan</given-names></name><degrees>M.D</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>tanjifan@alumni.sysu.edu.cn</email></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>Tinelli</surname><given-names>Andrea</given-names></name><role>Academic Editor</role></contrib><contrib contrib-type="editor"><name><surname>Dahan</surname><given-names>Michael H.</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Reproductive Medicine Center, Shenzhen Maternity and Child Healthcare Hospital, Women and Children's Medical Center, Southern Medical University, Shenzhen, Guangdong Province, China</aff><aff id="aff2"><sup>2</sup>Shenzhen Key Laboratory of Maternal and Child Health and Diseases，Shenzhen 518000，Guangdong，China</aff><aff id="aff3"><sup>3</sup>Shenzhen Clinical Research Center for Obstetrics &amp; Gynecology and Reproductive System Diseases，Shenzhen 518000，Guangdong，China</aff><author-notes><corresp id="cor1"><sup>*</sup>Corresponding author. Jifan Tan, Tel: +86 15017554785; Email：<email>tanjifan@alumni.sysu.edu.cn</email></corresp><fn id="fn-1"><p><sup>†</sup>The authors consider that the first two authors should be regarded as joint First Authors.</p></fn></author-notes><history><date date-type="received"><day>23</day><month>1</month><year>2026</year></date><date date-type="rev-recd"><day>27</day><month>2</month><year>2026</year></date><date date-type="accepted"><day>7</day><month>4</month><year>2026</year></date></history></article-meta></front></article>

### 示例九

**Word 记录**

[doc/p1] [{"text":"Original Research","styles":["italic"]}]
[doc/p2] [{"text":"Elevated Circulating HMGB1 Levels as a Potential Biomarker for the Diagnosis and Therapy of Heart Failure: A Cross-Sectional Study","styles":[]}]
[doc/p3] [{"text":"Xiaoting Jiang ","styles":[]},{"text":"1,2,3, #","styles":["superscript"]},{"text":", Xia Feng ","styles":[]},{"text":"1, ","styles":["superscript"]},{"text":", Wen Liu","styles":[]},{"text":"1","styles":["superscript"]},{"text":", Shaolin Gong ","styles":[]},{"text":"1,2,3,","styles":["superscript"]},{"text":", Xiaoping Peng ","styles":[]},{"text":"1,2,3, *","styles":["superscript"]},{"text":", Xiang Wang ","styles":[]},{"text":"1,2,3, *","styles":["superscript"]}]
[doc/p4] [{"text":"1","styles":["superscript"]},{"text":"Department of Cardiology, The First Affiliated Hospital, Jiangxi Medical College, Nanchang University, Nanchang, Jiangxi, China","styles":[]}]
[doc/p5] [{"text":"2","styles":["superscript"]},{"text":"Academician Workstation of Cardiovascular Innovative Materials, Nanchang, Jiangxi, China","styles":[]}]
[doc/p6] [{"text":"3","styles":["superscript"]},{"text":"Jiangxi Hypertension Research Institute, Nanchang, Jiangxi, China","styles":[]}]
[doc/p7] []
[doc/p8] [{"text":"Academic editor: Giuseppe Boriani, Boyoung Joung","styles":[]}]
[doc/p9] []
[doc/p10] [{"text":"Submitted:5/1/2026","styles":[]}]
[doc/p11] [{"text":"Revised:9/2/2026","styles":[]}]
[doc/p12] [{"text":"Accepted: 26/2/2026","styles":[]}]

**输出**

<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Original Research</subject></subj-group></article-categories><title-group><article-title>Elevated Circulating HMGB1 Levels as a Potential Biomarker for the Diagnosis and Therapy of Heart Failure: A Cross-Sectional Study</article-title></title-group><contrib-group><contrib contrib-type="author"><name><surname>Jiang</surname><given-names>Xiaoting</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><author-comment><p>#</p></author-comment></contrib><contrib contrib-type="author"><name><surname>Feng</surname><given-names>Xia</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Liu</surname><given-names>Wen</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref></contrib><contrib contrib-type="author"><name><surname>Gong</surname><given-names>Shaolin</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><name><surname>Peng</surname><given-names>Xiaoping</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref></contrib><contrib contrib-type="author"><name><surname>Wang</surname><given-names>Xiang</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>Boriani</surname><given-names>Giuseppe</given-names></name><role>Academic Editor</role></contrib><contrib contrib-type="editor"><name><surname>Joung</surname><given-names>Boyoung</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Department of Cardiology, The First Affiliated Hospital, Jiangxi Medical College, Nanchang University, Nanchang, Jiangxi, China</aff><aff id="aff2"><sup>2</sup>Academician Workstation of Cardiovascular Innovative Materials, Nanchang, Jiangxi, China</aff><aff id="aff3"><sup>3</sup>Jiangxi Hypertension Research Institute, Nanchang, Jiangxi, China</aff><author-notes><corresp id="cor1"><sup>*</sup>Correspondence: Xiaoping Peng; Xiang Wang</corresp></author-notes><history><date date-type="received"><day>5</day><month>1</month><year>2026</year></date><date date-type="rev-recd"><day>9</day><month>2</month><year>2026</year></date><date date-type="accepted"><day>26</day><month>2</month><year>2026</year></date></history></article-meta></front></article>

### 示例十

**Word 记录**

[doc/p1] [{"text":"Original Research","styles":["italic"]}]
[doc/p2] [{"text":"Early Cardiac Workload and Long-Term Prognosis After Intracerebral Hemorrhage: Insights from a Large Multicenter Cohort","styles":["bold"]}]
[doc/p3] [{"text":"Chuanying Wang","styles":[]},{"text":"1,2","styles":["superscript"]},{"text":"  Yunyi Hao","styles":[]},{"text":"1,2,3","styles":["superscript"]},{"text":"  Zeqiang Ji","styles":[]},{"text":"1,2","styles":["superscript"]},{"text":"  Anxin Wang","styles":[]},{"text":"1,2,3,4","styles":["superscript"]},{"text":"  Xiaoli Zhang","styles":[]},{"text":"1,2,3,4","styles":["superscript"]},{"text":"  Yujie Zhou","styles":[]},{"text":"5","styles":["superscript"]},{"text":"  Kaijiang Kang","styles":[]},{"text":"1,2","styles":["superscript"]},{"text":"*  Xingquan Zhao","styles":[]},{"text":"1,2,6","styles":["superscript"]},{"text":"*  Wenjuan Wang","styles":[]},{"text":"1,2","styles":["superscript"]},{"text":"  ","styles":[]}]
[doc/p4] []
[doc/p5] [{"text":"Affiliations:","styles":[]}]
[doc/p6] [{"text":"1","styles":["superscript"]},{"text":"Department of Neurology, Beijing Tiantan Hospital, Capital Medical University, Beijing, China. ","styles":[]}]
[doc/p7] [{"text":"2","styles":["superscript"]},{"text":"China National Clinical Research Center for Neurological Diseases, Beijing Tiantan Hospital, Capital Medical University, Beijing, China.","styles":[]}]
[doc/p8] [{"text":"3","styles":["superscript"]},{"text":"Department of Clinical Epidemiology and Clinical Trial, Capital Medical University, Beijing, China.","styles":[]}]
[doc/p9] [{"text":"4","styles":["superscript"]},{"text":"Department of Epidemiology, Beijing Neurosurgical Institute, Beijing Tiantan Hospital, Capital Medical University, Beijing, China.","styles":[]}]
[doc/p10] [{"text":"5","styles":["superscript"]},{"text":"Department of Cardiology, Beijing Anzhen Hospital, Capital Medical University, Beijing, China.","styles":[]}]
[doc/p11] [{"text":"6","styles":["superscript"]},{"text":"Research Unit of Artificial Intelligence in Cerebrovascular Disease, Chinese Academy of Medical Sciences, Beijing, China.","styles":[]}]
[doc/p12] [{"text":"*These authors contributed equally to this work and share corresponding authorship.","styles":[]}]
[doc/p13] [{"text":" ","styles":["bold"]}]
[doc/p14] [{"text":"Corresponding authors:","styles":["bold"]}]
[doc/p15] [{"text":"Kaijiang Kang, MD,","styles":[]}]
[doc/p16] [{"text":"Department of Neurology, Beijing Tiantan Hospital, Capital Medical University","styles":[]}]
[doc/p17] [{"text":"No. 119 South 4th Ring West Road, Fengtai District, Beijing 100070, China.","styles":[]}]
[doc/p18] [{"text":"phone: +861059975701","styles":[]}]
[doc/p19] [{"text":"e-mail: ","styles":[]},{"text":"kangkaijiang678@126.com","styles":["underline"]}]
[doc/p20] []
[doc/p21] [{"text":"Xingquan Zhao, MD,","styles":[]}]
[doc/p22] [{"text":"Department of Neurology, Beijing Tiantan Hospital, Capital Medical University","styles":[]}]
[doc/p23] [{"text":"No. 119 South 4th Ring West Road, Fengtai District, Beijing 100070, China.","styles":[]}]
[doc/p24] [{"text":"phone: +861059975701","styles":[]}]
[doc/p25] [{"text":"e-mail: ","styles":[]},{"text":"zxq@vip.163.com","styles":["underline"]}]
[doc/p26] []
[doc/p27] [{"text":"1)Chuanying Wang: 0000-0003-1721-5553","styles":[]}]
[doc/p28] [{"text":"2)Yunyi Hao: NA","styles":[]}]
[doc/p29] [{"text":"3)Zeqiang Ji: 0009-0001-5746-1671","styles":[]}]
[doc/p30] [{"text":"4)Anxin Wang: 0000-0003-4351-2877","styles":[]}]
[doc/p31] [{"text":"5)Xiaoli Zhang: NA","styles":[]}]
[doc/p32] [{"text":"6)Yujie Zhou: NA","styles":[]}]
[doc/p33] [{"text":"7)Kaijiang Kang: 0000-0002-1110-0627","styles":[]}]
[doc/p34] [{"text":"8)Xingquan Zhao: 0000-0001-8345-5147","styles":[]}]
[doc/p35] [{"text":"9)Wenjuan Wang: NA","styles":[]}]
[doc/p36] []
[doc/p37] []
[doc/p38] []
[doc/p39] [{"text":"2026/1/2","styles":[]}]
[doc/p40] [{"text":"2026/2/27","styles":[]}]
[doc/p41] [{"text":"2026/2/28","styles":[]}]
[doc/p42] []
[doc/p43] [{"text":"学编：Davide Bolignano","styles":[]}]

**输出**

<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Original Research</subject></subj-group></article-categories><title-group><article-title>Early Cardiac Workload and Long-Term Prognosis After Intracerebral Hemorrhage: Insights from a Large Multicenter Cohort</article-title></title-group><contrib-group><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-1721-5553</contrib-id><name><surname>Wang</surname><given-names>Chuanying</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib><contrib contrib-type="author"><name><surname>Hao</surname><given-names>Yunyi</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0009-0001-5746-1671</contrib-id><name><surname>Ji</surname><given-names>Zeqiang</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0003-4351-2877</contrib-id><name><surname>Wang</surname><given-names>Anxin</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><xref ref-type="aff" rid="aff4"><sup>4</sup></xref></contrib><contrib contrib-type="author"><name><surname>Zhang</surname><given-names>Xiaoli</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff3"><sup>3</sup></xref><xref ref-type="aff" rid="aff4"><sup>4</sup></xref></contrib><contrib contrib-type="author"><name><surname>Zhou</surname><given-names>Yujie</given-names></name><xref ref-type="aff" rid="aff5"><sup>5</sup></xref></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0002-1110-0627</contrib-id><name><surname>Kang</surname><given-names>Kaijiang</given-names></name><degrees>MD</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>kangkaijiang678@126.com</email><address><addr-line>Department of Neurology, Beijing Tiantan Hospital, Capital Medical University</addr-line><addr-line>No. 119 South 4th Ring West Road, Fengtai District, Beijing 100070, China.</addr-line><phone>+861059975701</phone></address></contrib><contrib contrib-type="author"><contrib-id contrib-id-type="orcid" authenticated="true">https://orcid.org/0000-0001-8345-5147</contrib-id><name><surname>Zhao</surname><given-names>Xingquan</given-names></name><degrees>MD</degrees><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref><xref ref-type="aff" rid="aff6"><sup>6</sup></xref><xref ref-type="corresp" rid="cor1"><sup>*</sup></xref><email>zxq@vip.163.com</email><address><addr-line>Department of Neurology, Beijing Tiantan Hospital, Capital Medical University</addr-line><addr-line>No. 119 South 4th Ring West Road, Fengtai District, Beijing 100070, China.</addr-line><phone>+861059975701</phone></address></contrib><contrib contrib-type="author"><name><surname>Wang</surname><given-names>Wenjuan</given-names></name><xref ref-type="aff" rid="aff1"><sup>1</sup></xref><xref ref-type="aff" rid="aff2"><sup>2</sup></xref></contrib></contrib-group><contrib-group><contrib contrib-type="editor"><name><surname>Bolignano</surname><given-names>Davide</given-names></name><role>Academic Editor</role></contrib></contrib-group><aff id="aff1"><sup>1</sup>Department of Neurology, Beijing Tiantan Hospital, Capital Medical University, Beijing, China.</aff><aff id="aff2"><sup>2</sup>China National Clinical Research Center for Neurological Diseases, Beijing Tiantan Hospital, Capital Medical University, Beijing, China.</aff><aff id="aff3"><sup>3</sup>Department of Clinical Epidemiology and Clinical Trial, Capital Medical University, Beijing, China.</aff><aff id="aff4"><sup>4</sup>Department of Epidemiology, Beijing Neurosurgical Institute, Beijing Tiantan Hospital, Capital Medical University, Beijing, China.</aff><aff id="aff5"><sup>5</sup>Department of Cardiology, Beijing Anzhen Hospital, Capital Medical University, Beijing, China.</aff><aff id="aff6"><sup>6</sup>Research Unit of Artificial Intelligence in Cerebrovascular Disease, Chinese Academy of Medical Sciences, Beijing, China.</aff><author-notes><corresp id="cor1"><sup>*</sup>These authors contributed equally to this work and share corresponding authorship. Correspondence: <email>kangkaijiang678@126.com</email> (Kaijiang Kang); <email>zxq@vip.163.com</email> (Xingquan Zhao)</corresp></author-notes><history><date date-type="received"><day>2</day><month>1</month><year>2026</year></date><date date-type="rev-recd"><day>27</day><month>2</month><year>2026</year></date><date date-type="accepted"><day>28</day><month>2</month><year>2026</year></date></history></article-meta></front></article>
