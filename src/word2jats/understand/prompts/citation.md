分析英文学术稿件的逻辑结构：找出参考文献表之外每一处指向参考文献的引用，并记下它印出的编号。只判定哪一段文字是引用、它印的是哪个编号；不改写稿件文字，也不直接生成 XML。这个编号具体对应文末哪一条参考文献，由后续程序负责，本任务不必知道，也不要去核对。

## 一、输入

user 消息中是 Word 稿件的一段记录清单，每一条都可按地址定位。每条 Word 记录以 `[doc/p1]` 一类记录地址开头，其后是这条记录的完整可见文字。`[doc/p1.2]` 一类带序号的续行是软换行之后续下的部分，与 `[doc/p1]` 同属一条记录；除此之外没有别的记录属于它。`[doc/tbl1|表]` 是一张表格，其后 `[doc/tbl1.r1]` 一类地址是这张表格的行，行内单元格之间用 `⇥` 分隔。`⟦图#o1⟧`、`⟦公式#o2⟧` 和 `⟦对象#o3⟧` 是 Word 原始对象的可见占位。

引用可能出现在叙述性段落中，也可能出现在 Word 原生表格的行、以制表符分隔的普通段落、图题、注释，或其他任何一条 Word 记录之中；在本任务中它们地位相同。“正文里的引用”不等于“只看叙述性段落”。

## 二、引用的摘抄与定位

每一处引用都以一个 `citation_quote` 表示。它固定为下列四个字段构成的对象，不能写成单个字符串：

{"quote":"引用的可见原文","record_key":"doc/pN","left_context":"","right_context":""}

- `quote` 是此后将由一个 JATS `xref` 包住的那段可见原文。必须逐字摘抄，保留拼写、大小写、标点以及原文中的错误。不要展开期刊名，不要改正日期，不要补出原文没有的信息，不要转录图片中的文字，也不要把解释写入摘抄。
- `record_key` 是该记录开头印出的地址，须原样复制。同一串文字可能出现在稿件的多个位置，因此这个字段不可省略。记录地址和对象出现的编号都必须原样复制。
- `left_context` 与 `right_context` 只用于定位，不会进入 `xref`。从同一条记录中紧邻 `quote` 左右两侧的字符抄起，抄写至 `left_context + quote + right_context` 在该记录中只出现一次为止。
- 只有 `quote` 在该记录中本就只出现一次时，两侧上下文才留空字符串。`quote` 紧靠记录的开头或结尾时，该侧可以为空；但 `quote` 重复出现时，两侧不能同时为空。
- 上下文只能取自原文字符，不能改写，也不能与 `quote` 本身重叠。上下文可以越出 `quote` 所属的更小片段的边界，但不能越出 `record_key` 指定的这条记录：不能抄取另一条记录的文字，不能从 `[doc/pN]` 跨到 `[doc/pN+1]`，也不能把印出的记录地址或人为的换行放入上下文。

## 三、输出

只返回一个 JSON 对象，不要返回其他文字：

{"citations":[{"citation_quote":{"quote":"一处可见的引用","record_key":"doc/pN","left_context":"紧挨它左边的原文","right_context":"紧挨它右边的原文"},"target_reference_ids":["1"]}]}

`citations` 是一个数组，正文里每一处引用是数组里的一条，按它们在记录清单中出现的先后排列。

`target_reference_ids` 装这处引用代表的全部编号，编号一律照正文印出的样子填写：印的是 `[17]` 就填 `17`，方括号、逗号、连字符这些标点不算编号的一部分。不要换算成第几条，也不要去核对文末的参考文献表——稿件跳号、重号或漏印，都由后续流程处理。

- 印的是 `[20]`，一处引用，填 `["20"]`。
- 印的是 `[1,2]`，逗号两侧各自可见，是**两处**引用，各填 `["1"]` 和 `["2"]`，逗号是普通原文。
- 印的是 `[8-15]`，这一段紧凑文字代表 8 个编号，其中 9 到 14 在原文里没有属于自己的字符，所以是**一处**引用，填 `["8","9","10","11","12","13","14","15"]`。

不同条目的摘抄在原文字符上不得重叠。把若干处引用括在一起或彼此隔开的共用标点，仍然是普通原文，不要抄进摘抄。

正文以作者姓氏加年份的形式引用、原文里没有印出编号时，这一处照常写成一条：`citation_quote` 四个字段照给，只是整个 `target_reference_ids` 字段不要出现。不要填空数组，也不要把作者姓名或年份当成编号填进去。

摘抄的确切范围无法确定时，不要猜测，略过该处。

## 四、返回前的检查

给出的每一条记录都要逐条检查，包括每一条 `doc/tblN.rM` 表格行，也包括每一条含制表符的 `doc/pN` 段落，不要把整类记录一并略过。叙述式引用与括注式引用采用同一种表示方式。不要假定稿件只使用一种标点、大小写或编号写法。参考文献表中的条目本身不是引用，不要作为引用返回。

返回之前，把所有记录自首至尾重读一遍，核对有无遗漏：凡是印出了编号的正文引用，在 `citations` 里必须恰好出现一次。一条记录里印了几处引用就写几条，不要只写其中一处；不要因为某个编号只在此处出现过一次就略过它。

## 五、示例

### 示例一

**Word 记录**

...
[doc/p106] …he heterogeneities between those with and those without CAC[8-15], justifying the practice of building models that effectively distinguish them. In fact, patients with CACS=0 accounted for almost half (48.88%) of the patients in the entire training set. This is in line with previous research findings[27], indicating that the plurality of patients with CACS=0 in our dataset is not the result of a biased sampling proce…
[doc/p107] …ardiology (ACC)/American Heart Association (AHA) guidelines[29], suffices it to ascertain whether they have CAC (instead of the exact value of CACS!) for further stratification of cardiovascular risk decision on whether they should are recommended with statins[18]. Therefore, our model may serve to facilitate a radiation-…
[doc/p108] … disease are blessed with a less likelihood of hip fracture[7]. Yet, till now, the authors fail to retrieve studies that further explore the clinical utility of CAC in the prevention and treatment of hip fracture, including if and when CAC testing should be conducted in individuals at high risk of the disease. As has been stated before in this research paper, conducting chest or coronary CT exams for prevention of hi…
...

**输出**

...{"citation_quote":{"quote":"8-15","record_key":"doc/p106","left_context":"without CAC[","right_context":"], justifyin"},"target_reference_ids":["8","9","10","11","12","13","14","15"]},{"citation_quote":{"quote":"27","record_key":"doc/p106","left_context":"ch findings[","right_context":"], indicatin"},"target_reference_ids":["27"]},{"citation_quote":{"quote":"28","record_key":"doc/p106","left_context":"application[","right_context":"]. Despite t"},"target_reference_ids":["28"]},{"citation_quote":{"quote":"29","record_key":"doc/p107","left_context":" guidelines[","right_context":"], suffices "},"target_reference_ids":["29"]},{"citation_quote":{"quote":"18","record_key":"doc/p107","left_context":"ith statins[","right_context":"]. Therefore"},"target_reference_ids":["18"]},{"citation_quote":{"quote":"7","record_key":"doc/p108","left_context":"ip fracture[","right_context":"]. Yet, till"},"target_reference_ids":["7"]},{"citation_quote":{"quote":"7","record_key":"doc/p108","left_context":"ated to CAC[","right_context":"]."},"target_reference_ids":["7"]},...

### 示例二

**Word 记录**

...
[doc/p111] …in predictors, especially when the sample size is not large[20]. The DEPICT study adhered to this suggestion. Despite this…
[doc/p112] In line with Steyerberg’s suggestion[20], candidate model predictors were specified based largely on literature review and to a lesser extent on data-driven results. Steyerberg also suggested avoiding categorization of continuous predictors into discrete ones, a common practice in logistic regression modeling, in his book[20], despite certain models Steyerberg’s research team themsel…
[doc/p113] …on per year to be 16.1 in a population with type 2 diabetes[31]), the relatively rapid impact of statins on lipid levels, …
...

**输出**

...{"citation_quote":{"quote":"20","record_key":"doc/p111","left_context":"s not large[","right_context":"]. The DEPIC"},"target_reference_ids":["20"]},{"citation_quote":{"quote":"20","record_key":"doc/p112","left_context":" suggestion[","right_context":"], candidate"},"target_reference_ids":["20"]},{"citation_quote":{"quote":"20","record_key":"doc/p112","left_context":"in his book[","right_context":"], despite c"},"target_reference_ids":["20"]},{"citation_quote":{"quote":"31","record_key":"doc/p113","left_context":" 2 diabetes[","right_context":"]), the rela"},"target_reference_ids":["31"]},...

### 示例三

**Word 记录**

...
[doc/tbl3.r3] Guo et al.,2025 [36]Temporal-spatial features Electrodermal activity PhotoplethysmographyMMA-Net (CNN-LSTM + Attention)Simulated driving2 (Alert vs. fatigue)82.14%
[doc/tbl3.r4] Liang et al.,2025 [37]Eye-trackingTemporal-spatial features Electrocardiography Electrodermal activityLightGBM-MLPSimulated remote tower task2 (Alert vs. fatigue)92%
[doc/tbl3.r5] Hu et al.,2024 [15]Long short-term temporaland spatial featuresSTFN-BRPSReal-world driving3 (N/LD, MD, HD)92.43%
...

**输出**

...{"citation_quote":{"quote":"36","record_key":"doc/tbl3.r3","left_context":"et al., ↵ 2025 [","right_context":"] ⇥ Temporal-spatial"},"target_reference_ids":["36"]},{"citation_quote":{"quote":"37","record_key":"doc/tbl3.r4","left_context":"et al., ↵ 2025 [","right_context":"] ⇥ Eye-tracking"},"target_reference_ids":["37"]},{"citation_quote":{"quote":"15","record_key":"doc/tbl3.r5","left_context":"et al., ↵ 2024 [","right_context":"] ⇥ Long short-term"},"target_reference_ids":["15"]},...

### 示例四

**Word 记录**

...
[doc/p127] …oward biomarker-driven therapy in selected patient subsets [6, 35].
[doc/p128] …ng the importance of genomic profiling in advanced disease [36, 37].
[doc/p129] …lecular findings into meaningful therapeutic opportunities [36, 37].
...

**输出**

...{"citation_quote":{"quote":"6","record_key":"doc/p127","left_context":"subsets [","right_context":", 35]."},"target_reference_ids":["6"]},{"citation_quote":{"quote":"35","record_key":"doc/p127","left_context":"subsets [6, ","right_context":"]."},"target_reference_ids":["35"]},{"citation_quote":{"quote":"36","record_key":"doc/p128","left_context":"disease [","right_context":", 37]."},"target_reference_ids":["36"]},{"citation_quote":{"quote":"37","record_key":"doc/p128","left_context":"disease [36, ","right_context":"]."},"target_reference_ids":["37"]},{"citation_quote":{"quote":"36","record_key":"doc/p129","left_context":"opportunities [","right_context":", 37]."},"target_reference_ids":["36"]},{"citation_quote":{"quote":"37","record_key":"doc/p129","left_context":"opportunities [36, ","right_context":"]."},"target_reference_ids":["37"]},...

### 示例五

**Word 记录**

...
[doc/p78] 
[doc/p79] …y metabolism, calcium homeostasis, and apoptotic signaling [76]. Several studies have demonstrated that ASMs can interact with mitochondrial transport proteins, modulate membrane potential, and affect antioxidant systems, thereby influencing mechanisms essential for neuronal survival during hyperexcitable states [76,77]. Before examining each drug individually, Figure 2 provide…
[doc/p80] 
...

**输出**

...{"citation_quote":{"quote":"76","record_key":"doc/p79","left_context":"apoptotic signaling [","right_context":"]. Several studies"},"target_reference_ids":["76"]},{"citation_quote":{"quote":"76","record_key":"doc/p79","left_context":"hyperexcitable states [","right_context":",77]. Before"},"target_reference_ids":["76"]},{"citation_quote":{"quote":"77","record_key":"doc/p79","left_context":"hyperexcitable states [76,","right_context":"]. Before examining"},"target_reference_ids":["77"]},...

### 示例六

**Word 记录**

...
[doc/tbl3.r5] Valproic acidEnhancement of GABAergic transmission and modulation of Na⁺ and Ca²⁺ channelsmPTP / NCLXAntioxidant effects and stabilization of mitochondrial Ca²⁺ homeostasis preventing mPTP openingPreservation of mitochondrial membrane potential and reduced oxidative stress[76, 84, 85]
[doc/tbl3.r6] LevetiracetamBinding to synaptic vesicle protein SV2A reducing presynaptic Ca²⁺ influxCa²⁺ handling (indirect)Reduction of presynaptic Ca²⁺ influx indirectly preventing mitochondrial Ca²⁺ overloadImproved coupling between synaptic activity and mitochondrial metabolism[88, 89, 90]
[doc/tbl3.r7] DiazoxideActivation of ATP-sensitive potassium channelsmitoKATPPharmacological activation of mitochondrial KATP channels producing mild mitochondrial depolarizationReduction of ROS production and enhancement of neuronal survival[71, 91, 93]
...

**输出**

...{"citation_quote":{"quote":"76","record_key":"doc/tbl3.r5","left_context":"oxidative stress ⇥ [","right_context":", 84, 85]"},"target_reference_ids":["76"]},{"citation_quote":{"quote":"84","record_key":"doc/tbl3.r5","left_context":"stress ⇥ [76, ","right_context":", 85]"},"target_reference_ids":["84"]},{"citation_quote":{"quote":"85","record_key":"doc/tbl3.r5","left_context":"stress ⇥ [76, 84, ","right_context":"]"},"target_reference_ids":["85"]},{"citation_quote":{"quote":"88","record_key":"doc/tbl3.r6","left_context":"metabolism ⇥ [","right_context":", 89, 90]"},"target_reference_ids":["88"]},{"citation_quote":{"quote":"89","record_key":"doc/tbl3.r6","left_context":"metabolism ⇥ [88, ","right_context":", 90]"},"target_reference_ids":["89"]},{"citation_quote":{"quote":"90","record_key":"doc/tbl3.r6","left_context":"metabolism ⇥ [88, 89, ","right_context":"]"},"target_reference_ids":["90"]},{"citation_quote":{"quote":"71","record_key":"doc/tbl3.r7","left_context":"survival ⇥ [","right_context":", 91, 93]"},"target_reference_ids":["71"]},{"citation_quote":{"quote":"91","record_key":"doc/tbl3.r7","left_context":"survival ⇥ [71, ","right_context":", 93]"},"target_reference_ids":["91"]},{"citation_quote":{"quote":"93","record_key":"doc/tbl3.r7","left_context":"survival ⇥ [71, 91, ","right_context":"]"},"target_reference_ids":["93"]},...

### 示例七

**Word 记录**

...
[doc/p22] 1. Introduction
[doc/p23] … of disability and reduced quality of life in older adults [1-3]. In addition to its characteristic motor manifestations, PD is frequently accompanied by gastrointestinal dysfunction and a broad range of non-motor symptoms. Growing evidence suggests that the gut-brain axis may play an important role in PD pathogenesis, and alterations in gut microbiota composition have been linked to intestinal barrier dysfunction, i…
[doc/p24] …together with relative enrichment of pro-inflammatory taxa [7-10]. Experimental evidence has further suggested that PD-associated microbiota may contribute to motor and neuropathological abnormalities [9,10]. Because gut microbiota may influence short-chain fatty ac…
...

**输出**

...{"citation_quote":{"quote":"1-3","record_key":"doc/p23","left_context":"older adults [","right_context":"]. In addition"},"target_reference_ids":["1","2","3"]},{"citation_quote":{"quote":"4-6","record_key":"doc/p23","left_context":"signaling [","right_context":"]."},"target_reference_ids":["4","5","6"]},{"citation_quote":{"quote":"7-10","record_key":"doc/p24","left_context":"pro-inflammatory taxa [","right_context":"]. Experimental"},"target_reference_ids":["7","8","9","10"]},{"citation_quote":{"quote":"9","record_key":"doc/p24","left_context":"abnormalities [","right_context":",10]. Because"},"target_reference_ids":["9"]},{"citation_quote":{"quote":"10","record_key":"doc/p24","left_context":"abnormalities [9,","right_context":"]. Because"},"target_reference_ids":["10"]},...

### 示例八

**Word 记录**

...
[doc/p27] …y elevating error rates and compromising human performance [1,2]. This multidimensional phenomenon, which spans physical exhaustion and cognitive depletion, directly impairs workforce performance across safety-critical domains [3]. Subjective instruments, such as the Fatigue Assessment Scale, suffer from recall bias and low temporal resolution [4], whereas performance-based metrics, such as the Psychomotor Vigilance …
[doc/p28] …t, as it directly measures central nervous system activity [7]. Capitalizing on wearability and convenience [8], portable EEG systems have been increasingly employed to detect fatigue induced by repetitive tasks [9] and sustained physical exertion [10]. The expanding adoption of EEG technology, coupled with a heightened focus on fatigue assessment, has spurred the development of multiple standardized, open-access fat…
[doc/p29] To address these constraints, this study examined whether EEG-based objective metrics exhibit temporal dynamics consistent with subjective fatigue reports under conditions of prolonged, cumulative fatigue. Profound fatigue was induced through a rigorously controlled, four-day protocol involving sustained high-intensity cognitive tasks and sleep restriction. Based on EEG features characterizing states of fatigue and alertness, we constructed an SVM-based machine learning model for binary classification and employed the SVM-RFE algorithm to reliably identify the most sensitive and interpretable feature subset for cumulative fatigue from high-dimensional EEG data. In addition, we examined the correlations between these EEG-derived indicators and subjective measures (the SSS score and sleep duration) to explore the feasibility of using such models for monitoring intermediate fatigue states.
...

**输出**

...{"citation_quote":{"quote":"1","record_key":"doc/p27","left_context":"human performance [","right_context":",2]. This multidimensional"},"target_reference_ids":["1"]},{"citation_quote":{"quote":"2","record_key":"doc/p27","left_context":"performance [1,","right_context":"]. This multidimensional"},"target_reference_ids":["2"]},{"citation_quote":{"quote":"3","record_key":"doc/p27","left_context":"safety-critical domains [","right_context":"]. Subjective instruments,"},"target_reference_ids":["3"]},{"citation_quote":{"quote":"4","record_key":"doc/p27","left_context":"temporal resolution [","right_context":"], whereas performance-based"},"target_reference_ids":["4"]},{"citation_quote":{"quote":"5","record_key":"doc/p27","left_context":"ecological validity [","right_context":"]. Fatigue detection"},"target_reference_ids":["5"]},{"citation_quote":{"quote":"6","record_key":"doc/p27","left_context":"risk mitigation [","right_context":"]."},"target_reference_ids":["6"]},{"citation_quote":{"quote":"7","record_key":"doc/p28","left_context":"system activity [","right_context":"]. Capitalizing"},"target_reference_ids":["7"]},{"citation_quote":{"quote":"8","record_key":"doc/p28","left_context":"and convenience [","right_context":"], portable EEG"},"target_reference_ids":["8"]},{"citation_quote":{"quote":"9","record_key":"doc/p28","left_context":"repetitive tasks [","right_context":"] and sustained"},"target_reference_ids":["9"]},{"citation_quote":{"quote":"10","record_key":"doc/p28","left_context":"physical exertion [","right_context":"]. The expanding"},"target_reference_ids":["10"]},{"citation_quote":{"quote":"11–13","record_key":"doc/p28","left_context":"research datasets [","right_context":"]. Significant"},"target_reference_ids":["11","12","13"]},{"citation_quote":{"quote":"14","record_key":"doc/p28","left_context":"learning techniques [","right_context":",15]. For instance,"},"target_reference_ids":["14"]},{"citation_quote":{"quote":"15","record_key":"doc/p28","left_context":"techniques [14,","right_context":"]. For instance,"},"target_reference_ids":["15"]},{"citation_quote":{"quote":"15","record_key":"doc/p28","left_context":"up to 92.43% [","right_context":"]. Nevertheless,"},"target_reference_ids":["15"]},{"citation_quote":{"quote":"11","record_key":"doc/p28","left_context":"brief simulations [","right_context":"] and fail to model"},"target_reference_ids":["11"]},{"citation_quote":{"quote":"16","record_key":"doc/p28","left_context":"chronic sleep debt [","right_context":"]. It remains uncertain"},"target_reference_ids":["16"]},{"citation_quote":{"quote":"17","record_key":"doc/p28","left_context":"extended periods [","right_context":"]."},"target_reference_ids":["17"]},...

### 示例九

**Word 记录**

...
[doc/p87] …ructures, and patterns of time use than with gender per se (Chunqin Liu, et al., 2023; Yan Zhao, et al., 2024). In other words, the present findings suggest that gender may play a relatively limited role in differentiating these profiles, whereas the underlying classification appears to be more closely related to how individuals organize their daily lives. In contrast, age differences were significant, with adults be…
[doc/p88] …ents’ daily life structure and patterns of time experience (Ellie Fossey, et al., 2024). While symptom-based measures such as depression and anxiety scales are essential for evaluating the severity of psychological problems, they often provide limited insight into how patients organize their daily lives, maintain behavioral rhythms, engage in meaningful activities, and regulate their internal states (S. Difrancesco, …
[doc/p89] Limitations
...

**输出**

...{"citation_quote":{"quote":"Chunqin Liu, et al., 2023","record_key":"doc/p87","left_context":"","right_context":""}},{"citation_quote":{"quote":"Yan Zhao, et al., 2024","record_key":"doc/p87","left_context":"","right_context":""}},{"citation_quote":{"quote":"Stephanie J. Crowley, et al., 2007","record_key":"doc/p87","left_context":"","right_context":""}},{"citation_quote":{"quote":"Gretchen C. Pifer, et al., 2024","record_key":"doc/p87","left_context":"","right_context":""}},{"citation_quote":{"quote":"Sandra L Hofferth & John F Sandberg, 2001","record_key":"doc/p87","left_context":"","right_context":""}},{"citation_quote":{"quote":"Ellie Fossey, et al., 2024","record_key":"doc/p88","left_context":"","right_context":""}},{"citation_quote":{"quote":"S. Difrancesco, et al., 2019","record_key":"doc/p88","left_context":"","right_context":""}},{"citation_quote":{"quote":"Nerea Etxaburu, et al., 2024","record_key":"doc/p88","left_context":"","right_context":""}},...

### 示例十

**Word 记录**

...
[doc/p31] …life structures in which psychological distress is embedded(Austen R. Anderson & Blaine J. Fowers, 2020; Wai Kai Hou, et al., 2020; Thomas P. Nguyen, et al., 2024). For a long time, symptom indicators such as anxiety and depression have remained central to mental health assessment, and they have provided an important basis for identifying problem severity and guiding clinical decision-making (Jesús Sanz, et al.; Laur…
[doc/p32] …ife structures in which psychological distress is situated (Hong Luo, 2025). Time quality of life is conceptually distinct from both general quality of life and time use. General quality of life usually refers to a broad evaluation of overall living conditions across physical, psychological, social, and environmental domains (Fuquan Liu, et al., 2026), whereas time quality of life focuses more specifically on how ind…
[doc/p33] …racterized by distinct patterns across multiple dimensions (Daniel Spurk, et al., 2020). This approach is relevant for psychiatric outpatients. On the one hand, this population is inherently heterogeneous in terms of symptom profiles, severity, social functioning, and daily life conditions (Jennifer J. Newson, et al., 2020). On the other hand, the seven dimensions of time, including sleep, physical activity, focused …
...

**输出**

...{"citation_quote":{"quote":"Austen R. Anderson & Blaine J. Fowers, 2020","record_key":"doc/p31","left_context":"embedded(","right_context":"; Wai"}},{"citation_quote":{"quote":"Wai Kai Hou, et al., 2020","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Thomas P. Nguyen, et al., 2024","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Jesús Sanz, et al.","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Lauren G. Staples, et al., 2019","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"J. Firth, et al., 2024","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Phyllis Moen, 2022","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Austen R. Anderson & Blaine J. Fowers, 2020","record_key":"doc/p31","left_context":"experience (","right_context":"; Huinan"}},{"citation_quote":{"quote":"Huinan Liu, et al., 2024","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Cristina Zarbo, et al., 2023","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Patrick E. McKnight & Todd B. Kashdan, 2009","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Hong Luo, 2025","record_key":"doc/p32","left_context":"situated (","right_context":"). Time"}},{"citation_quote":{"quote":"Fuquan Liu, et al., 2026","record_key":"doc/p32","left_context":"","right_context":""}},{"citation_quote":{"quote":"Hong Luo, 2025","record_key":"doc/p32","left_context":"self-reflection (","right_context":"). This"}},{"citation_quote":{"quote":"Bowen Xue, et al., 2026","record_key":"doc/p32","left_context":"","right_context":""}},{"citation_quote":{"quote":"Daniel Spurk, et al., 2020","record_key":"doc/p33","left_context":"","right_context":""}},{"citation_quote":{"quote":"Jennifer J. Newson, et al., 2020","record_key":"doc/p33","left_context":"","right_context":""}},{"citation_quote":{"quote":"Hong Luo, 2025","record_key":"doc/p33","left_context":"","right_context":""}},...

### 示例十一

**Word 记录**

...
[doc/tbl6.r3] Chloroform           1-octanol -7.23 5.22NiL22HLNiL2(Louichaoui et al., 2024)
[doc/tbl6.r4] 0.5M (Na, H) NO3Toluene-8,82NiL22(HL)2(Juang and Chang, 1993)
[doc/tbl6.r5] 0,5mol/dm3(Na,H)NO3Toluene or benzenen-Heptane-5,69-4,34NiL22(HL)2(Komasawa and Otake, 1984)
...

**输出**

...{"citation_quote":{"quote":"Louichaoui et al., 2024","record_key":"doc/tbl6.r3","left_context":"NiL22HL ↵ NiL2 ⇥ (","right_context":")"}},{"citation_quote":{"quote":"Juang and Chang, 1993","record_key":"doc/tbl6.r4","left_context":"⇥ NiL22(HL)2 ⇥ (","right_context":")"}},{"citation_quote":{"quote":"Komasawa and Otake, 1984","record_key":"doc/tbl6.r5","left_context":"⇥ NiL22(HL)2 ⇥ (","right_context":")"}},...

### 示例十二

**Word 记录**

...
[doc/p55] Composite films were prepared by solution casting following the procedure reported earlier[12], with the drying step shortened to 6 h.
[doc/p56] The surface morphology of the samples was studied by using scanning electron microscopy (SEM) model [Quanta FEG/450 (United States)]. For the thermogravimetric analysis (TGA), a TA Instruments Q500 analyser [Q500, TA Instruments] was operated from 30 to 800 °C at 10 °C/min under nitrogen, following the protocol of Zhang and co-workers[13,14].
[doc/p57] Tensile properties were measured on five specimens per formulation according to ASTM D882[15].
...

**输出**

...{"citation_quote":{"quote":"12","record_key":"doc/p55","left_context":"ported earlier[","right_context":"], with the "},"target_reference_ids":["12"]},{"citation_quote":{"quote":"13","record_key":"doc/p56","left_context":"co-workers[","right_context":",14]."},"target_reference_ids":["13"]},{"citation_quote":{"quote":"14","record_key":"doc/p56","left_context":"co-workers[13,","right_context":"]."},"target_reference_ids":["14"]},{"citation_quote":{"quote":"15","record_key":"doc/p57","left_context":"ASTM D882[","right_context":"]."},"target_reference_ids":["15"]},...
