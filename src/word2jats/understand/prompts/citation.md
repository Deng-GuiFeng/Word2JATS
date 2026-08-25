分析英文学术稿件的逻辑结构：找出参考文献表之外每一处指向参考文献的引用；正文印出了编号的，一并记下编号。只判定哪一段文字是引用、它印的是哪个编号；不改写稿件文字，也不直接生成 XML。这个编号具体对应文末哪一条参考文献，由后续程序负责，本任务不必知道，也不要去核对。

## 一、输入

user 消息中是 Word 稿件的一段记录清单，每一条都可按地址定位。每条 Word 记录以 `[doc/p1]` 一类记录地址开头，其后是这条记录的完整可见文字。`[doc/p1.2]` 一类带序号的续行是软换行之后续下的部分，与 `[doc/p1]` 同属一条记录；除此之外没有别的记录属于它。`[doc/tbl1|表]` 是一张表格，其后 `[doc/tbl1.r1]` 一类地址是这张表格的行；行内单元格之间用 ` ⇥ ` 分隔，同一个单元格里有多个段落时、段落之间用 ` ↵ ` 分隔。这两个符号是清单为展示表格而加的，两侧各带一个空格，它们不是稿件里的字符：上下文可以带上它们，`quote` 里不能出现。`⟦图#o1⟧`、`⟦公式#o2⟧` 和 `⟦对象#o3⟧` 是 Word 原始对象的可见占位。

引用可能出现在叙述性段落中，也可能出现在 Word 原生表格的行、以制表符分隔的普通段落、图题、注释，或其他任何一条 Word 记录之中；在本任务中它们地位相同。“正文里的引用”不等于“只看叙述性段落”。

## 二、引用的摘抄与定位

每一处引用都以一个 `citation_quote` 表示。它固定为下列四个字段构成的对象，不能写成单个字符串：

{"quote":"引用的可见原文","record_key":"doc/pN","left_context":"紧挨它左边的原文","right_context":"紧挨它右边的原文"}

- `quote` 是这处引用在正文里印出的那段可见文字。必须逐字摘抄，保留拼写、大小写、标点以及原文中的错误。原文印的年份与文末参考文献对不上时也照抄，不要改；不要补出原文没有的信息，也不要把解释写入摘抄。
- `record_key` 是该记录开头印出的地址，须原样复制。同一串文字可能出现在稿件的多个位置，因此这个字段不可省略。记录地址和对象出现的编号都必须原样复制。
- `left_context` 与 `right_context` 只是定位用的证据，最终被认定为引用的只有 `quote` 那一段。从同一条记录中紧邻 `quote` 左右两侧的字符抄起，抄写至 `left_context + quote + right_context` 在该记录中只出现一次为止。
- 只有 `quote` 在该记录中本就只出现一次时，两侧上下文才留空字符串。`quote` 紧靠记录的开头或结尾时，该侧可以为空；但 `quote` 重复出现时，两侧不能同时为空。
- 上下文只能取自原文字符，不能改写，也不能与 `quote` 本身重叠。上下文可以越出 `quote` 所属的更小片段的边界，但不能越出 `record_key` 指定的这条记录：不能抄取另一条记录的文字，不能从 `[doc/pN]` 跨到 `[doc/pN+1]`，也不能把印出的记录地址或人为的换行放入上下文。

## 三、输出

只返回一个 JSON 对象，不要返回其他文字：

{"citations":[{"citation_quote":{"quote":"一处可见的引用","record_key":"doc/pN","left_context":"紧挨它左边的原文","right_context":"紧挨它右边的原文"},"target_reference_ids":["1"]}]}

`citations` 是一个数组，正文里每一处引用是数组里的一条，按它们在记录清单中出现的先后排列。

`target_reference_ids` 装这处引用代表的全部编号，一律照正文印出的样子填写，里面只写数字、不带标点：印的是 `[17]` 就填 `"17"`。`quote` 是另一回事，它照抄原文——紧凑范围的 `quote` 就写成 `1-3`、`11–13` 这样，中间的连字符照留。不要把编号换算成第几条，也不要去核对文末的参考文献表——稿件跳号、重号或漏印，都由后续流程处理。

- 印的是 `[20]`，一处引用，填 `["20"]`。
- 印的是 `[1,2]`，逗号两侧各自可见，是**两处**引用，各填 `["1"]` 和 `["2"]`，逗号是普通原文。
- 印的是 `[8-15]`，这一段紧凑文字代表 8 个编号，其中 9 到 14 在原文里没有属于自己的字符，所以是**一处**引用，填 `["8","9","10","11","12","13","14","15"]`。

不同条目的摘抄在原文字符上不得重叠。把若干处引用括在一起或彼此隔开的共用标点，仍然是普通原文，不要抄进摘抄。

正文以作者姓氏加年份的形式引用、原文里没有印出编号时，这一处照常写成一条：`citation_quote` 四个字段照给，只是整个 `target_reference_ids` 字段不要出现。不要填空数组，也不要把作者姓名或年份当成编号填进去。

摘抄的确切范围无法确定时，不要猜测，略过该处。

## 四、返回前的检查

给出的每一条记录都要逐条检查，包括每一条 `doc/tblN.rM` 表格行，也包括每一条含制表符的 `doc/pN` 段落，不要把整类记录一并略过。作者名写在句子里、编号紧跟其后（如 `the suggestion of Steyerberg[20]`），与整处引用括在括号里，两种写法都要收，表示方式相同。不要假定稿件只使用一种标点、大小写或编号写法。参考文献表中的条目本身不是引用，不要作为引用返回。

返回之前，把所有记录自首至尾重读一遍，核对有无遗漏：正文里的每一处引用，不论有没有印出编号，在 `citations` 里都必须恰好出现一次。一条记录里印了几处引用就写几条，不要只写其中一处；不要因为某个编号只在此处出现过一次就略过它。

## 五、示例

下面的示例都取自真实稿件。记录清单前后的 `...` 表示略去的其余记录，输出前后的 `...` 表示这些条目只是整个 `citations` 数组中的一段；示例中列出的每一条记录本身都是完整的，未作删节。

### 示例一

**Word 记录**

...
[doc/p50] Statistical analysis & Mathematical modeling
[doc/p51] All of the modeling processes were carried out via Statistical Analysis System (SAS) Version 9.4 TS1M5. The DEPICT study adhered to the TRIPOD guideline[19].
[doc/p52] Univariable analyses
...

**输出**

...{"citation_quote":{"quote":"19","record_key":"doc/p51","left_context":"D guideline[","right_context":"]."},"target_reference_ids":["19"]},...

### 示例二

**Word 记录**

...
[doc/p66] External (temporal) validation
[doc/p67] Temporal validation has been documented as a valid approach in external validation[20]. Following the suggestion of Steyerberg[20], data of visits in 2023 were saved for external validation to test the performance of models on more recently visited patients, which is more clinically relevant. All models built were validated in the independent validation set, generating discriminatory statistics. Calibration of models was only performed in the validation set, as calibration in the training set and during internal validation process provide limited information[20]. Calibration plots were drawn with confidence bands of calibration curves while statistics of calibration in-the-large, weak calibration, calibration intercept, calibration slope and their 95% confidence intervals (CIs) and P-values were computed.
[doc/p68] Comparison of models
...

**输出**

...{"citation_quote":{"quote":"20","record_key":"doc/p67","left_context":" validation[","right_context":"]. Following"},"target_reference_ids":["20"]},{"citation_quote":{"quote":"20","record_key":"doc/p67","left_context":" Steyerberg[","right_context":"], data of v"},"target_reference_ids":["20"]},{"citation_quote":{"quote":"20","record_key":"doc/p67","left_context":"information[","right_context":"]. Calibrati"},"target_reference_ids":["20"]},...

### 示例三

**Word 记录**

...
[doc/tbl5.r4] Gan Su [11] ⇥ 13,302 ⇥ 236 ⇥ 1.80 ⇥ 10.868 ⇥ <0.001
[doc/tbl5.r5] Jiang Su [12] ⇥ 5776 ⇥ 100 ⇥ 1.73 ⇥ 17.015 ⇥ <0.001
[doc/p94] 
...

**输出**

...{"citation_quote":{"quote":"11","record_key":"doc/tbl5.r4","left_context":"Gan Su [","right_context":"] ⇥ 13,302"},"target_reference_ids":["11"]},{"citation_quote":{"quote":"12","record_key":"doc/tbl5.r5","left_context":"Jiang Su [","right_context":"] ⇥ 5776 ⇥"},"target_reference_ids":["12"]},...

### 示例四

**Word 记录**

...
[doc/p49] 2.5.1. Spectral Characteristics
[doc/p50] Power spectral density (PSD) was estimated via the Hamming-windowed Fast Fourier Transform (FFT). Absolute and relative powers were calculated for standard frequency bands: delta (δ: 1–4 Hz), theta (θ: 4–8 Hz), alpha (α: 8–13 Hz), beta (β: 13–30 Hz), and gamma (γ: 30–45 Hz). Additionally, several inter-frequency band energy ratios, such as (α+θ)/(β+γ), (α+θ)/β, α/β, and (α+θ)/(α+β), were computed and employed as sensitive indicators of fatigue [24,25].
[doc/p51] Fatigue has also been established as a modulator of the frequency distribution and variability of EEG signals [26,27]. These properties were also calculated to capture the spectral distribution characteristics.
...

**输出**

...{"citation_quote":{"quote":"24","record_key":"doc/p50","left_context":"indicators of fatigue [","right_context":",25]."},"target_reference_ids":["24"]},{"citation_quote":{"quote":"25","record_key":"doc/p50","left_context":"of fatigue [24,","right_context":"]."},"target_reference_ids":["25"]},{"citation_quote":{"quote":"26","record_key":"doc/p51","left_context":"of EEG signals [","right_context":",27]. These properties"},"target_reference_ids":["26"]},{"citation_quote":{"quote":"27","record_key":"doc/p51","left_context":"EEG signals [26,","right_context":"]. These properties"},"target_reference_ids":["27"]},...

### 示例五

**Word 记录**

...
[doc/p90] 3.5 Comparison of SMA Mutation Carrier Frequency Among Pregnant Women in Some Regions of China
[doc/p91] The Tianlong reagent showed higher accuracy and concordance in detecting SMN1 gene E7 and E8 copy numbers. The SMA carrier frequency in the Changzhi area was higher than that in the Gansu and Jiangsu regions (χ2 = 10.868 and 17.015, respectively; p < 0.05), whereas no statistically significant difference was observed compared with the Shenzhen (p > 0.05) (Table 5, Ref. [10,11,12]).
[doc/p92] 
...

**输出**

...{"citation_quote":{"quote":"10","record_key":"doc/p91","left_context":"(Table 5, Ref. [","right_context":",11,12])."},"target_reference_ids":["10"]},{"citation_quote":{"quote":"11","record_key":"doc/p91","left_context":"5, Ref. [10,","right_context":",12])."},"target_reference_ids":["11"]},{"citation_quote":{"quote":"12","record_key":"doc/p91","left_context":"Ref. [10,11,","right_context":"])."},"target_reference_ids":["12"]},...

### 示例六

**Word 记录**

...
[doc/tbl1.r5] mPTP ⇥ Inner membrane complex ⇥ Non-selective solutes ⇥ Regulates mitochondrial membrane permeability under metabolic stress ⇥ Persistent opening induces mitochondrial depolarization and apoptotic pathways ⇥ [64, 65, 67, 69]
[doc/tbl1.r6] mitoKATP ⇥ Inner membrane ⇥ K⁺ (influx) ⇥ Modulates mitochondrial membrane potential and limits excessive ROS generation ⇥ Activation reduces ROS generation and enhances neuronal survival ⇥ [71, 72, 73, 95]
[doc/p65] 
...

**输出**

...{"citation_quote":{"quote":"64","record_key":"doc/tbl1.r5","left_context":"pathways ⇥ [","right_context":", 65, 67, 69]"},"target_reference_ids":["64"]},{"citation_quote":{"quote":"65","record_key":"doc/tbl1.r5","left_context":"pathways ⇥ [64, ","right_context":", 67, 69]"},"target_reference_ids":["65"]},{"citation_quote":{"quote":"67","record_key":"doc/tbl1.r5","left_context":"pathways ⇥ [64, 65, ","right_context":", 69]"},"target_reference_ids":["67"]},{"citation_quote":{"quote":"69","record_key":"doc/tbl1.r5","left_context":"[64, 65, 67, ","right_context":"]"},"target_reference_ids":["69"]},{"citation_quote":{"quote":"71","record_key":"doc/tbl1.r6","left_context":"survival ⇥ [","right_context":", 72, 73, 95]"},"target_reference_ids":["71"]},{"citation_quote":{"quote":"72","record_key":"doc/tbl1.r6","left_context":"survival ⇥ [71, ","right_context":", 73, 95]"},"target_reference_ids":["72"]},{"citation_quote":{"quote":"73","record_key":"doc/tbl1.r6","left_context":"survival ⇥ [71, 72, ","right_context":", 95]"},"target_reference_ids":["73"]},{"citation_quote":{"quote":"95","record_key":"doc/tbl1.r6","left_context":"[71, 72, 73, ","right_context":"]"},"target_reference_ids":["95"]},...

### 示例七

**Word 记录**

...
[doc/p60] 2.5.2. Entropy Metrics
[doc/p61] Signal complexity and irregularity were quantified using multiscale entropy approaches [28–30]. We calculated the multiscale entropy using the frequency band energy derived from wavelet decomposition. A 5-level wavelet decomposition was employed, and the Daubechies 3 (db3) wavelet was used. Shannon entropy (SE) was calculated using the relative wavelet energies of the standard bands. 
[doc/p62] ⟦公式#o12⟧                                  (4)
...

**输出**

...{"citation_quote":{"quote":"28–30","record_key":"doc/p61","left_context":"entropy approaches [","right_context":"]. We calculated"},"target_reference_ids":["28","29","30"]},...

### 示例八

**Word 记录**

...
[doc/p54] 2.3.5 Generalized Anxiety Disorder-7 (GAD-7)
[doc/p55] The Generalized Anxiety Disorder-7 (GAD-7) was used to assess the severity of anxiety symptoms over the past two weeks (Robert L Spitzer, et al., 2006). The scale consists of seven items, each rated on a 4-point Likert scale ranging from 0 (“not at all”) to 3 (“nearly every day”), reflecting the frequency of symptoms. Total scores range from 0 to 21, with higher scores indicating greater anxiety symptom severity. In the present study, the GAD-7 demonstrated excellent internal consistency (Cronbach’s α = 0.921).
[doc/p56] 2.4 Data Collection
...

**输出**

...{"citation_quote":{"quote":"Robert L Spitzer, et al., 2006","record_key":"doc/p55","left_context":"","right_context":""}},...

### 示例九

**Word 记录**

...
[doc/p30] 1. Introduction 
[doc/p31] Against the backdrop of a growing global mental health burden, an important challenge in mental health research is how to move beyond a purely symptom-based perspective and develop a deeper understanding of the everyday life structures in which psychological distress is embedded(Austen R. Anderson & Blaine J. Fowers, 2020; Wai Kai Hou, et al., 2020; Thomas P. Nguyen, et al., 2024). For a long time, symptom indicators such as anxiety and depression have remained central to mental health assessment, and they have provided an important basis for identifying problem severity and guiding clinical decision-making (Jesús Sanz, et al.; Lauren G. Staples, et al., 2019). However, as the pace of modern life continues to accelerate, digital environments increasingly reshape the distribution of attention, and social ties and life boundaries become more fragmented, mental health problems are increasingly expressed not only in the rise of isolated symptoms but also in the ways individuals spend their daily lives, organize their time, and maintain life order (J. Firth, et al., 2024; Phyllis Moen, 2022). Psychological distress is therefore reflected not only in elevated levels of anxiety and depression, but also in broad changes in daily rhythms, behavioral activation, allocation of attentional resources, social interaction, and regulation of inner experience (Austen R. Anderson & Blaine J. Fowers, 2020; Huinan Liu, et al., 2024). This issue is salient in psychiatric settings, where patients differ not only in the number and severity of symptoms, but also in their life conditions, functional patterns, and ways of experiencing time (Cristina Zarbo, et al., 2023). Accordingly, understanding psychiatric outpatients only in terms of symptom severity may not be sufficient to capture their actual patterns of functional impairment or the heterogeneity underlying those patterns (Patrick E. McKnight & Todd B. Kashdan, 2009). Reconsidering the mental health of psychiatric outpatients from the perspective of daily time experience may therefore offer a new way to identify patient heterogeneity, reveal the structure of everyday life, and inform more ecologically valid assessment and intervention approaches.
[doc/p32] Because mental health problems are embedded in the ways individuals organize and experience daily life, understanding time itself becomes an important entry point for grasping patients’ actual living conditions. In this sense, time quality of life provides a new theoretical perspective for understanding the everyday life structures in which psychological distress is situated (Hong Luo, 2025). Time quality of life is conceptually distinct from both general quality of life and time use. General quality of life usually refers to a broad evaluation of overall living conditions across physical, psychological, social, and environmental domains (Fuquan Liu, et al., 2026), whereas time quality of life focuses more specifically on how individuals experience, organize, and evaluate the quality of their daily time. It also differs from time use, which primarily concerns the objective allocation of time across activities. Rather than focusing only on how much time is spent on specific activities, time quality of life emphasizes whether these time-related experiences are restorative, engaging, socially meaningful, and psychologically integrative. In this sense, it offers a more focused explanatory perspective on the relationship between everyday life structure and mental health. Based on a systematic conceptualization of daily time experience, Luo proposed the “Seven Time” framework, which identifies seven key dimensions of daily life: sleep, physical activity, focused engagement, social connection, interaction with nature, present-moment awareness, and self-reflection (Hong Luo, 2025). This framework suggests that time is not an abstract or neutral background, but a fundamental medium through which individuals maintain biological rhythms, behavioral participation, social interaction, and psychological regulation. Together, these seven dimensions form the basic structure through which individuals live each day, recover, interact with their environment, and integrate inner experience (Bowen Xue, et al., 2026). Accordingly, time quality of life not only reflects individuals’ overall evaluation of their daily time experience, but may also serve as an important intermediary layer linking life structure to mental health status. For psychiatric outpatients, changes in time quality of life may reflect their patterns of life organization and actual functional status more directly than single symptom indicators.
...

**输出**

...{"citation_quote":{"quote":"Austen R. Anderson & Blaine J. Fowers, 2020","record_key":"doc/p31","left_context":"embedded(","right_context":"; Wai"}},{"citation_quote":{"quote":"Wai Kai Hou, et al., 2020","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Thomas P. Nguyen, et al., 2024","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Jesús Sanz, et al.","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Lauren G. Staples, et al., 2019","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"J. Firth, et al., 2024","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Phyllis Moen, 2022","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Austen R. Anderson & Blaine J. Fowers, 2020","record_key":"doc/p31","left_context":"experience (","right_context":"; Huinan"}},{"citation_quote":{"quote":"Huinan Liu, et al., 2024","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Cristina Zarbo, et al., 2023","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Patrick E. McKnight & Todd B. Kashdan, 2009","record_key":"doc/p31","left_context":"","right_context":""}},{"citation_quote":{"quote":"Hong Luo, 2025","record_key":"doc/p32","left_context":"situated (","right_context":"). Time"}},{"citation_quote":{"quote":"Fuquan Liu, et al., 2026","record_key":"doc/p32","left_context":"","right_context":""}},{"citation_quote":{"quote":"Hong Luo, 2025","record_key":"doc/p32","left_context":"self-reflection (","right_context":"). This"}},{"citation_quote":{"quote":"Bowen Xue, et al., 2026","record_key":"doc/p32","left_context":"","right_context":""}},...

### 示例十

**Word 记录**

...
[doc/tbl6.r4] 0.5M (Na, H) NO3 ⇥ Toluene ⇥ -8,82 ⇥ NiL22(HL)2 ⇥ (Juang and Chang, 1993)
[doc/tbl6.r5] 0,5mol/dm3(Na,H)NO3 ⇥ Toluene or benzene ↵ n-Heptane ⇥ -5,69 ↵ -4,34 ⇥ NiL22(HL)2 ⇥ (Komasawa and Otake, 1984)
[doc/tbl6.r6]  ⇥ Toluene ⇥ -8,82 ↵  ⇥ NiL22(HL)2 ⇥ 
...

**输出**

...{"citation_quote":{"quote":"Juang and Chang, 1993","record_key":"doc/tbl6.r4","left_context":"⇥ NiL22(HL)2 ⇥ (","right_context":")"}},{"citation_quote":{"quote":"Komasawa and Otake, 1984","record_key":"doc/tbl6.r5","left_context":"⇥ NiL22(HL)2 ⇥ (","right_context":")"}},...

### 示例十一

**Word 记录**

...
[doc/p66] 2.3 RPP Calculation
[doc/p67] Early cardiac workload was quantified using the RPP measured at admission within 72 hours of symptom onset (the cohort's enrollment window), calculated as: [HR (bpm) × SBP (mmHg)] / 1000 [20]. Both SBP and HR were measured after a 5-minute rest period in a quiet environment, prior to the administration of any in-hospital medications. SBP was assessed using a mercury manometer while the patient was either seated or supine, and HR was derived from a 10-second, 12-lead electrocardiogram.
[doc/p68] 
...

**输出**

...{"citation_quote":{"quote":"20","record_key":"doc/p67","left_context":"[","right_context":"]."},"target_reference_ids":["20"]},...
