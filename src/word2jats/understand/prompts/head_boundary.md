为两个相互独立的文首处理任务确定输入范围。只判断范围，不提取字段，不改写原文，也不生成 XML。

## 一、输入

user 消息中是从 Word 主文档开头连续提取的记录。每行开头的 `[doc/p1]` 一类字符串是记录地址；后面的数组是 Word 文本片段。按顺序连接一条记录中的所有 `text`，就能得到该记录在 Word 中的完整可见文字。`⟦图#o1⟧`、`⟦公式#o2⟧` 和 `⟦对象#o3⟧` 是 Word 原始对象的可见占位，不是稿件文字。

## 二、两个任务

任务一处理文首元信息，包括文章类别、标题、作者和编辑、单位、地址、联系方式、作者注释以及收稿、修回、接受等稿件日期。

任务二处理文首中具有正文式文字结构的部分，包括各种摘要及关键词。结构化摘要、非结构化摘要、翻译摘要、短摘要和多个摘要都属于同一个任务，不要在定位阶段把它们拆成不同范围。摘要内部的段落、分节和公式不改变范围的数量。本任务只划文字范围，图片和其他显示对象不参与端点判断，不论它们出现在摘要内部还是独立成段。

## 三、输出

只返回一个符合给定 schema 的 JSON 对象，不要返回其他文字：

- `metadata_range`：任务一的一个连续输入范围；`first_node` 和 `last_node` 分别是范围内第一条和最后一条非空记录的位置。稿件没有文首元信息时返回 null。
- `front_content_range`：任务二的一个连续输入范围；`first_node` 和 `last_node` 含义相同。稿件没有摘要和关键词时返回 null。

## 四、范围怎么定

记录地址必须从 user 消息中原样复制。空白记录不能作为范围端点。

每个任务只有一个范围，从它的第一条相关记录延伸到最后一条相关记录。范围中间夹有与该任务无关的记录时照样包含在内，不要为了避开这些记录缩短范围或把范围拆成多段；范围内的全部记录都会交给这个任务，由它自行识别其中的结构。两个范围可以相邻；两类内容在 Word 中交错排列时，两个范围也可以重叠。

如果给出的记录在范围结束前已经截断，`last_node` 取其中最后一条相关记录。

## 五、示例
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
[doc/p31] []
[doc/p32] []
[doc/p33] [{"text":"Abstract:","styles":["bold"]}]
[doc/p34] [{"text":"Background:","styles":["bold"]},{"text":" Dependence of acquisition of coronary artery calcification score (CACS) on computed tomography (CT) has drawbacks, including ethical concerns of radiation exposure in the care of patients with non-cardiovascular diseases, where CACS has been shown to correlate with its prognosis. Significant heterogeneities exist between patients with and with coronary artery calcification (CAC). Mathematical formulae using medical history and common, non-invasive test results enable cheap, ready assessment of CAC and subsequent research into how it can be used for clinical decision making.","styles":[]}]
[doc/p35] [{"text":"Methods:","styles":["bold"]},{"text":" Retrospectively collected 1035 patient records of visits to Fuwai Hospital, Chinese Academy of Medical Sciences and Peking Union Medical College from 2009 to 2023 were collected and were partitioned into training (visited before 2023) and independent validation set (visited in 2023). With age, gender, current smoking, diabetes, low-density lipoprotein cholesterol (LDL-C), declined renal function, usage of statins and aspirin as candidate predictors, five logistic regression models were built under two paradigms. Bootstrap resampling was employed for internal validation, followed by external validation and calibration on the validation set. Models built under each paradigm were compared, followed by head-to-head comparison of the “best” models built under each paradigm with a comprehensive criterion involving both model performance and predictor parsimony.","styles":[]}]
[doc/p36] [{"text":"Results:","styles":["bold"]},{"text":" 694 records were used for modeling, with 536 and 158 records in the training and validation set respectively. Model 1 (c statistic upon external validation: 0.77) outperformed other models built under Paradigm 1 while Models 4 (c statistic upon external validation: 0.79) and 5 (c statistic upon external validation: 0.79) built under Paradigm 2 outperformed Model 1. Model 5 was more parsimonious in predictors. All models were well calibrated.","styles":[]}]
[doc/p37] [{"text":"Conclusion:","styles":["bold"]},{"text":" With gender, current smoking, LDL-C, age, diabetes and declined renal function as predictors, Model 5 outperformed other models and was hence recommended for further use. By assessing the presence of CAC with medical history and blood test results instead of CT, our model offers an approach to ready, radiation-free assessment of CAC, which may further unleash the clinical utility of CAC in clinical practice that may have remained unraveled.","styles":[]}]
[doc/p38] [{"text":"Keywords:","styles":["bold"]}]
[doc/p39] [{"text":"coronary artery calcification; radiation-free evaluation of arterial calcification; machine learning; precision medicine; prediction model","styles":[]}]
[doc/p40] [{"text":"Introduction","styles":["bold"]}]
[doc/p41] [{"text":"Coronary artery calcification (CAC) is associated with elevated cardiovascular risk. Research has revealed the crucial roles coronary artery calcification score (CACS) play in diagnosis of early, subclinical coronary artery disease[1]; risk stratification of diabetic[2], hypertensive[3], elderly populations[4] and smokers[5]. CAC is not only associated with coronary in-stent restenosis and in-stent thrombosis, conditions that are both associated with stent under-expansion[6], but also associated with prognosis of certain non-cardiovascular diseases (CVDs) (e.g., carcinomas, hip fracture, chronic obstructive pulmonary disease)[7].","styles":[]}]
[doc/p42] [{"text":"Significant heterogeneities exist between those with CACS = 0 and those with CACS>0. Patients afflicted by CAC suffer from an increased risk of adverse events, regardless of its severity.[8] CACS=0 is also an indicator of very low 10-year mortality in both middle-aged and elderly patients[9] and younger ones[10], a marker of good prognosis in those with a huge risk factor burden[11], lipid profile impairment[12] and metabolic syndrome[13] and the strongest protective factor among several protective factors[14]. Statins use was associated with a reduction in risk of major adverse cardiovascular events (MACEs) in patients with CACS>0, while those with CACS=0 did not enjoy such a benefit[15]. These results showcase the disparities between the two populations and the need for distinguishing them with methods including, but, as this study presents, not confined to computed tomography (CT) exams.","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p30"}, "front_content_range": {"first_node": "doc/p33", "last_node": "doc/p39"}}

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
[doc/p16] []
[doc/p17] []
[doc/p18] [{"text":"Abstract","styles":["bold"]}]
[doc/p19] [{"text":"Mitral regurgitation (MR) is a common valvular heart disease, and its prevalence is continuously rising with the aging population, posing a serious threat to human health in the advanced stages of the disease. Sole reliance on medication and traditional surgical treatments can no longer meet the therapeutic needs of all patients. Transcatheter interventional therapy is gradually emerging as a new and ideal alternative treatment option. In recent years, the technology for transcatheter interventional treatment of mitral regurgitation has flourished, with expanding indications and a continuous stream of new devices. It has entered the fast lane in the treatment of structural heart disease, showcasing prospects for broad clinical application. This article reviews the key technologies and developmental trends in the current transcatheter interventional treatment of mitral regurgitation, aiming to provide the theoretical basis and rationale for the safe and standardized implementation and promotion of this series of technologies.","styles":[]}]
[doc/p20] []
[doc/p21] [{"text":"Keywords","styles":["bold"]}]
[doc/p22] [{"text":"Mitral regurgitation; Transcatheter intervention; Structural heart disease; Medical devices","styles":[]}]
[doc/p23] []
[doc/p24] []
[doc/p25] [{"text":"Abbreviations and Acronyms","styles":["bold"]}]
[doc/p26] [{"text":"MR, mitral regurgitation; TMVI, Transcatheter mitral valve interventions; TMVr, transcatheter mitral valve repair; TMVR, transcatheter mitral valve replacement; TEER, transcatheter edge-to-edge repair; FDA, Food and Drug Administration; NMPA, National Medical Products Administration; CE,European Conformity; MV, mitral valve; COPD, chronic obstructive pulmonary disease; COAPT, cardiovascular outcomes assessment of the MitraClip percutaneous therapy; EVEREST, Endovascular Valve Edge-to-Edge Repair Study; EXPAND G4, A Post-Market Study Assessment of the Safety and Performance of the MitraClip G4 System; DMR, degenerative mitral regurgitation; FMR, functional mitral regurgitation; AFMR,atrial functional mitral regurgitation; GDMT, guideline-directed medical therapy; LVOT, left ventricular outflow tract; MAC, mitral annular calcification; mPG, mean pressure gradient; NYHA, New York Heart Association; PISA, proximal isovelocity surface area; RF, regurgitant fraction; RVol: regurgitant volume; SLDA,single leaflet device attachment; TTE, transthoracic echocardiography; TEE, transesophageal echocardiography; M-TEER, mitral valve transcatheter edge-to-edge repair; VCW, vena contracta width; 3D: three-dimensional; VCA, vena contracta area; HFH, heart failure hospitalization; KCCQ,Kansas City Cardiomyopathy Questionnaire; STS,Society of Thoracic Surgeons; ViV, valve-in-valve; ViR, valve-in-ring; ViMAC,valve-in-calcified annulus; ESC/EACTS, European Society of Cardiology/European Association for Cardio-Thoracic Surgery; HR, Hazard Ratio; CI: Confidence Interval","styles":[]}]
[doc/p27] []
[doc/p28] [{"text":"1. Introduction ","styles":["bold"]}]
[doc/p29] [{"text":"Mitral regurgitation (MR) is a clinically common valvular heart disease that poses a serious threat to human health in its advanced stages. According to statistics [1], the prevalence of MR reaches 9.3% among individuals aged 75 and older in the United States, while hospital-based data in Europe report an MR prevalence of 24.4%, with moderate to severe MR accounting for 5.2%. The prevalence of MR increases significantly with age, and it is estimated that approximately 7.5 million patients in China require interventional treatment for MR [2]. Over two-thirds of patients are ineligible for surgical treatment due to high-risk factors such as advanced age and comorbidities, with a five-year mortality rate as high as 50% [3].","styles":[]}]
[doc/p30] [{"text":"With the rising number of MR cases, pharmacological therapies have proven ineffective in altering disease progression, while traditional surgical repair or replacement cannot be performed on all patients because of underlying comorbidities [4,5]. Transcatheter mitral valve interventions (TMVI) have seen rapidly growing demand due to their advantages, including lower risk, minimal invasiveness, and fewer postoperative complications[6-8]. A comparison of various techniques is presented in Table 1. With technological advancements, the indications for TMVI continue to expand, and innovative devices are constantly emerging [9-13]. TMVI has now become a highly promising procedure in the field of structural heart disease [14-18]. This review provides a comprehensive review of the key technologies and recent advances in transcatheter mitral valve interventions for MR, aiming to promote the safe and standardized application of these techniques and to offer a theoretical foundation and evidence for their role in clinical practice.","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p15"}, "front_content_range": {"first_node": "doc/p18", "last_node": "doc/p22"}}

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
[doc/p21] []
[doc/p22] [{"text":"Abstract","styles":["bold"]}]
[doc/p23] [{"text":"Background:","styles":["bold"]},{"text":" Cumulative mental fatigue poses a significant threat to safety, productivity, and health in the workplace. This study aimed to establish a robust machine learning framework using optimized resting-state electroencephalography (rs-EEG) features to detect such fatigue and to validate a four-day high-stress cognitive competition paradigm for its induction. ","styles":[]},{"text":"Methods:","styles":["bold"]},{"text":" EEG signals were recorded from participants under eyes-closed (EC) and eyes-open (EO) conditions during fatigue and recovery phases. We extracted 544 features spanning power spectral density, entropy, and nonlinear complexity. Support Vector Machine Recursive Feature Elimination (SVM-RFE) was used for feature selection. The derived model index (Mean Model Result, MMR) was correlated with a subjective sleepiness index (the Stanford Sleepiness Scale, SSS) and sleep duration. ","styles":[]},{"text":"Results:","styles":["bold"]},{"text":" Analysis of participant data identified a discriminative subset of 65 features from the EC EEG. The model achieved an accuracy of 90.37% in classifying deeply fatigued versus fully recovered states, significantly outperforming the EO-based model (86.54%). The MMR demonstrated a significant negative correlation with SSS scores (","styles":[]},{"text":"r","styles":["italic"]},{"text":"s","styles":["italic","subscript"]},{"text":" = –0.358, ","styles":[]},{"text":"p","styles":["italic"]},{"text":" = 0.020) and a positive correlation with sleep duration (","styles":[]},{"text":"r","styles":["italic"]},{"text":"s","styles":["italic","subscript"]},{"text":" = 0.494, ","styles":[]},{"text":"p","styles":["italic"]},{"text":" < 0.001). ","styles":[]},{"text":"Conclusions:","styles":["bold"]},{"text":" This study demonstrates the superior efficacy of EC rs-EEG for monitoring cumulative fatigue and establishes a quantifiable EEG-sleep relationship, supporting the practical feasibility of this framework for occupational fatigue risk assessment.","styles":[]}]
[doc/p24] [{"text":"Keywords","styles":["bold"]}]
[doc/p25] [{"text":"mental fatigue; electroencephalography (EEG); machine learning; Support Vector Machine; sleep duration","styles":[]}]
[doc/p26] [{"text":"1. Introduction","styles":["bold"]}]
[doc/p27] [{"text":"Fatigue represents a critical threat to operational safety in professions that require sustained attention, significantly elevating error rates and compromising human performance [1,2]. This multidimensional phenomenon, which spans physical exhaustion and cognitive depletion, directly impairs workforce performance across safety-critical domains [3]. Subjective instruments, such as the Fatigue Assessment Scale, suffer from recall bias and low temporal resolution [4], whereas performance-based metrics, such as the Psychomotor Vigilance Task, impede natural workflow and lack ecological validity [5]. Fatigue detection based on physiological signals represents a promising approach for preemptive risk mitigation [6].","styles":[]}]
[doc/p28] [{"text":"Electroencephalography (EEG) serves as an objective method for fatigue assessment, as it directly measures central nervous system activity [7]. Capitalizing on wearability and convenience [8], portable EEG systems have been increasingly employed to detect fatigue induced by repetitive tasks [9] and sustained physical exertion [10]. The expanding adoption of EEG technology, coupled with a heightened focus on fatigue assessment, has spurred the development of multiple standardized, open-access fatigue research datasets [11–13]. Significant progress has also been achieved in EEG-based fatigue detection algorithms, driven by advances in deep learning and machine learning techniques [14,15]. For instance, the classification accuracy rate for driving-induced fatigue can reach up to 92.43% [15]. Nevertheless, despite substantial progress in hardware instrumentation, algorithmic sophistication, and the accumulation of experimental data, unresolved challenges continue to impede the translation of EEG-based fatigue assessment into routine clinical applications. Dominant experimental paradigms rely on acute fatigue induction through brief simulations [11] and fail to model the cumulative fatigue dynamics arising from the interaction between prolonged cognitive load and chronic sleep debt [16]. It remains uncertain whether cognitive fatigue induced by short-term tasks exhibits the same neurophysiological signatures as fatigue resulting from chronic sleep debt. Thus, whether fatigue models constructed under such short-term conditions apply to cumulative fatigue has yet to be validated. Previous research on cumulative fatigue has predominantly relied on sleep deprivation paradigms, which represent a passive form of fatigue induction. Whether these paradigms elicit the same patterns of neural activity as fatigue induced by prolonged active engagement under high mental workload and sustained cognitive investment remains unclear. These methodological limitations severely constrain generalization to real-world occupational settings, such as aviation and critical care, where fatigue manifests cumulatively over extended periods [17]. ","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p20.3"}, "front_content_range": {"first_node": "doc/p22", "last_node": "doc/p25"}}

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
[doc/p31] []
[doc/p32] []
[doc/p33] [{"text":"Abstract","styles":["bold"]}]
[doc/p34] [{"text":"Antiseizure medications (ASMs) have traditionally been characterized by their modulation of neuronal ion channels and synaptic processes; however, accumulating evidence indicates that numerous ASMs also directly modulate mitochondrial function. Specifically, several ASMs interact with ion channels located in both the inner and outer mitochondrial membranes, including the voltage-dependent anion channel (VDAC), the mitochondrial calcium uniporter (MCU), the mitochondrial Na⁺/Ca²⁺ exchanger (NCLX), the mitochondrial permeability transition pore (mPTP), and mitochondrial ATP-sensitive potassium channels (mitoKATP). Modulation of these channels regulates critical processes in epilepsy, including Ca²⁺ homeostasis, ATP synthesis, redox equilibrium, and susceptibility to neuronal apoptosis. Phenytoin and carbamazepine reduce VDAC1-associated mitochondrial permeability by modulating the Bax/Bcl-2 ratio; ethosuximide limits mitochondrial Ca²⁺ overload through modulation of the MCU complex; valproic acid stabilizes NCLX function and prevents mPTP opening via antioxidant mechanisms; levetiracetam contributes to preserving intracellular Ca²⁺ handling; and mitoKATP activators, including diazoxide and retigabine, promote mitochondrial membrane potential stability and reduce seizure-induced reactive oxygen species (ROS) generation. The mitochondrial effects vary according to epilepsy subtype, contributing to the attenuation of hippocampal apoptosis in temporal lobe epilepsy and thalamocortical network modulation in generalized epilepsies. This narrative review examines the experimental and molecular evidence demonstrating how ASMs modulate mitochondrial ion channels and how these interactions contribute to their anticonvulsant mechanisms, thereby broadening the understanding of mitochondria as key functional components in antiseizure pharmacology.","styles":[]}]
[doc/p35] []
[doc/p36] []
[doc/p37] [{"text":"Keywords:","styles":["bold"]},{"text":" Mitochondria; Anticonvulsants; Voltage-Dependent Anion Channels; Ion Channels; Mitochondrial Membrane Transport Proteins; Epilepsy; Reactive Oxygen Species; Calcium Signaling","styles":[]}]
[doc/p38] []
[doc/p39] [{"text":"1. Introduction","styles":["bold"]}]
[doc/p40] []
[doc/p41] [{"text":"Epilepsy encompasses a diverse array of neurological conditions characterized by a chronic propensity to generate epileptic seizures arising from aberrant, excessive, and hypersynchronous neural activity [1,2]. Seizures arise from a neurobiological imbalance between excitatory and inhibitory mechanisms, including disruptions in glutamatergic and GABAergic transmission, variations in intrinsic neuronal excitability, and reconfigurations of neuronal networks [3,4,5].","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p30"}, "front_content_range": {"first_node": "doc/p33", "last_node": "doc/p37"}}

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
[doc/p33] []
[doc/p34] [{"text":"Abstract ","styles":["bold"]}]
[doc/p35] [{"text":"Age and the development of need for urgent surgical aortic valve replacement (SAVR) could affect 30-day mortality and long-term survival. These factors are assessed in a stratified way for their effect on these outcomes. A retrospective file study was performed in patients undergoing SAVR. Urgent SAVR was defined as a need for surgery during the admission in which the diagnosis of aortic valve disease was made. Preoperative predictors for urgent SAVR were identified by entering significant factors in a logistic regression analysis. Predictors for 30-day mortality were identified in parallel stratified analyses according to age (younger than 80 years, 80 to 85 years and older than 85 years), and according to elective status (elective vs. urgent). The effect of age classes on long-term mortality, stratified for elective status was performed by a Kaplan-Meier analysis. Predictors for this outcome were identified by a Cox’ proportional hazard analysis. The need for urgent SAVR was mainly predicted by cardiac factors. Age had only an effect in univariate analysis. Thirty-day mortality rose significantly with age over 80 years, which was pivotal as level for further analysis by stratification for age classes. Need for urgent SAVR was the dominant predictor for mortality in all age classes. Conversely, age over 80 was the dominant predictor for 30-day mortality in patients undergoing elective and urgent SAVR. High age and need for urgent SAVR reduced long-term survival in a comparable degree, but only age was identified as an independent predictor. Need for urgent surgery in patients older than 85 years shows a very poor outcome with respect to short-term mortality and long-term survival. The need for urgent SAVR represents an exhaustion of cardiac compensatory mechanisms to maintain adequate circulation. With increasing age, patients become more vulnerable to the effects of need for urgent SAVR. This condition should be avoided by timely valve replacement. ","styles":[]}]
[doc/p36] []
[doc/p37] [{"text":"Key words: ","styles":["bold"]}]
[doc/p38] [{"text":"aortic valve replacement; urgent surgery; age; survival ","styles":[]}]
[doc/p39] []
[doc/p40] [{"text":"Introduction ","styles":["bold"]}]
[doc/p41] [{"text":"The incidence of aortic valve stenosis (AS) increases with age. This condition affects up to 10% of the population older than 70 years and is highly fatal once it becomes symptomatic [1]. With the decrease of rheumatic origin as main cause, the patient age at onset of severe obstruction has changed from the sixth decade to the eighth decade. Although the onset of symptoms is an important determinant of outcome, high age could make it more difficult to make a distinction between symptoms of valvular origin and those from other age-related comorbidities [2]. The development of left ventricular hypertrophy and decrease in aortic valve area were identified as predictors for symptoms. Although asymptomatic patients could be considered as having a low risk, the course of the condition at this stage is not benign because of myocardial fibrosis [1]. Once symptoms of AS have developed, life expectancy without adequate treatment is reduced drastically. Only aortic valve replacement improves prognosis [2]. Furthermore, the burden of clinically relevant AS on the left ventricle might be underestimated [3]. A metanalysis indicated that the baseline severity of AS was predictive for the disease progression with respect to mean transvalvular gradient, peak velocity and aortic valve calcium score. Patients with rapid progress of the disease might benefit from early valve replacement, even if they are still asymptomatic [4]. SAVR has been the gold standard treatment for decades and is the oldest mode of prognosis altering treatment. However, over the last 10 to 15 years transcatheter aortic valve implantation (TAVI) replacement has emerged as an attractive, less-invasive option for appropriately selected patients [5]. A marked and rapid growth in the annual volume of combined SAVR and TAVI volume was observed over an 18-year study period which started in 2000. This increase was parallel to a rapid growth in the number of patients with an indication for valve replacement. This observation might point to a significant underutilization of valve replacement for severe AS, especially in patients with low-gradient severe AS. Given the mortality rate of untreated symptomatic severe AS, the recognition of the risk posed by undertreatment of severe valve disease should prompt timely intervention [6]. Although TAVI has been introduced as a less invasive alternative, SAVR is still employed as treatment, even in elderly patients. We observed an increase in elderly and very elderly patients referred for surgery until recently [7], which could be related to the reimbursement policy for TAVI in Belgium. Postoperative results with respect to 30-day mortality and long-term survival are generally favorable. However, the outcome decreases with age and with a need for urgent valve replacement [8]. Initial observations indicate that the need for urgent SAVR was higher in elderly patients, making disentanglement of the effect of age and of elective status more difficult. The importance of this problem is illustrated by a recently observed need for urgent SAVR in 16% of the patients [9]. Reports dealing with the effect of stratified age and selective status are scanty. For this reason, following research questions need to be solved: What is the distribution of need for urgent SAVR distributed across age categories. Which factors predict the need for urgent SAVR. What is the dominant predictor for 30-day mortality and long-term survival for separate age categories. How does the need for urgent SAVR influence the effect of age on outcome. ","styles":[]}]
[doc/p42] []

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p32"}, "front_content_range": {"first_node": "doc/p34", "last_node": "doc/p38"}}

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
[doc/p17] []
[doc/p18] [{"text":"Abstract","styles":["bold"]}]
[doc/p19] [{"text":"Background: Spinal muscular atrophy (SMA) is a fatal autosomal recessive hereditary neuromuscular disorder. Definitive treatment remains limited, and the carrier frequency in the general population is relatively high. This study aims to determine the carrier frequency of SMA and to characterized mutation types in the survival motor neuron 1 (SMN1) gene among pregnant women in the Changzhi area. It also evaluates the clinical value of large-scale carrier screening combined with prenatal diagnosis. Methods: Real-time fluorescence quantitative polymerase chain reaction (PCR) was used to detect the copy numbers of exon 7 (E7) and exon 8 (E8) of the SMN1 gene in pregnant women for carrier screening, with simultaneous testing of their spouses. For couples in which both partners were carriers, invasive prenatal diagnosis of high-risk fetuses was performed using multiplex ligation-dependent probe amplification (MLPA) on amniotic fluid or on chorionic villous sampling. PCR–melting curve analysis was used to recheck some initially positive pregnant women to exclude false positive results and to compare the accuracy of the detection reagents. Results: Among 13,500 pregnant women, a total of 338 SMA carriers were identified, corresponding to a carrier frequency of 1/40 (2.5%). The distribution of mutation types was as follows: 217 cases (64.20%) of E7+E8 heterozygous deletion, 43 cases (12.72%) of E7 heterozygous deletion, 77 cases (22.78%) of E8 heterozygous deletion, and 1 case (0.30%) of E8 homozygous deletion. A total of four couples in which both partners were SMA carriers were identified. This study employed Tianlong and Wushishi reagents for clinical screening. The Tianlong reagent showed higher accuracy and concordance in the detection of SMN1 E7 and E8 copy numbers. The SMA carrier frequency in the Changzhi area was higher than that in the Gansu and Jiangsu regions (χ² = 10.868 and 17.015, respectively; P < 0.05). However, no statistically significant difference was observed compared with the Shenzhen (P > 0.05). Conclusions: The SMA carrier frequency in the Changzhi region was 2.50%, with E7+E8 heterozygous deletion as the predominant mutation type. Large-scale SMA gene screening in pregnant women, together with early diagnosis in high-risk groups, may support prevention strategies. This study did not include a formal cost-benefit analysis. The discussion of economic significance is merely descriptive, and the conclusions should be interpreted with caution.","styles":[]}]
[doc/p20] [{"text":"Keywords","styles":["bold"]}]
[doc/p21] [{"text":"spinal muscular atrophy; SMN1 gene; carrier screening; prenatal diagnosis; mutation type","styles":[]}]
[doc/p22] []
[doc/p23] [{"text":"1. Introduction","styles":["bold"]}]
[doc/p24] [{"text":"Spinal muscular atrophy (SMA) is an autosomal recessive neuromuscular disease characterized by degeneration of anterior horn cells in the spinal cord. Clinically, it presents with symmetric proximal limb muscle weakness and atrophy that gradually progress, and severe cases may involve the respiratory muscles, leading to death [1]. The global incidence of SMA is approximately 1/6000–1/10,000, and the carrier frequency of the pathogenic gene in the general population ranges from 1/38 to 1/70, making it the second most lethal autosomal recessive disease in children, with an overall carrier frequency of about 1/50 [2]. The pathogenic gene for SMA is the survival motor neuron (SMN) gene, which is located in the 5q13 chromosomal region and includes two highly homologous inverted repeat sequences, ","styles":[]},{"text":"SMN1","styles":["italic"]},{"text":" and ","styles":[]},{"text":"SMN2","styles":["italic"]},{"text":" [3]. ","styles":[]},{"text":"SMN1","styles":["italic"]},{"text":" is the primary pathogenic gene and encodes the full-length functional SMN protein, whereas ","styles":[]},{"text":"SMN2","styles":["italic"]},{"text":" is a modifier gene, with only 10%–20% of its transcripts producing functional protein. The copy number of ","styles":[]},{"text":"SMN2","styles":["italic"]},{"text":" is closely associated with disease severity [4]. Clinical data show that more than 95% of SMA cases are caused by homozygous deletion of ","styles":[]},{"text":"SMN1","styles":["italic"]},{"text":" exon 7 (E7) or combined homozygous deletion of E7+E8, while the remaining fewer than 5% are compound heterozygous mutations consisting of E7 heterozygous deletion and point mutations [5].","styles":[]}]
[doc/p25] [{"text":"Given the severe clinical manifestations, lack of curative treatment, high carrier frequency, and clearly defined pathogenic gene of SMA, both the American College of Medical Genetics and Genomics (ACMG) and the American College of Obstetricians and Gynecologists (ACOG) recommend routine SMA carrier screening for all pregnant women and prenatal diagnosis for high-risk fetuses [6,7]. In China, with the promotion of prenatal screening and diagnostic technologies, regional SMA carrier screening has gradually been implemented; however, data on carrier frequency and prenatal diagnosis outcomes in southern Shanxi, particularly in the Changzhi region, remain limited. As a typical industrial and agricultural city in southern Shanxi, Changzhi has a stable population structure and a large population of women of reproductive age, making regional genetic disease screening in this area representative. Previous studies have demonstrated ethnic and regional differences in SMA carrier frequency, with a carrier frequency of approximately 1/94 in southern China and 1/88 in northern China [8]. However, specific data for the Changzhi region are lacking, which limits the development of targeted prevention and control strategies.","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p16"}, "front_content_range": {"first_node": "doc/p18", "last_node": "doc/p21"}}

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
[doc/p32] []
[doc/p33] [{"text":"Abstract","styles":["bold"]}]
[doc/p34] [{"text":"Objective","styles":["bold"]},{"text":": To provide an updated overview of how molecular pathology is reshaping diagnostic, prognostic, and therapeutic paradigms in gynecologic oncology and redefining the role of the gynecologic pathologist in the era of precision medicine. ","styles":[]},{"text":"Mechanism","styles":["bold"]},{"text":": Advances in next-generation sequencing (NGS) and large-scale genomic initiatives have enabled comprehensive molecular characterization of endometrial, ovarian, and cervical carcinomas. The integration of genomic, immunophenotypic, and clinicopathologic data supports a multidimensional diagnostic framework that refines risk stratification and guides targeted and immune-based therapies. Emerging tools, including liquid biopsy, digital pathology, and artificial intelligence (AI), are further advancing the clinical integration of molecular data. ","styles":[]},{"text":"Findings in Brief","styles":["bold"]},{"text":": In endometrial carcinoma (EC), molecular classification complements and often surpasses morphology-based systems by providing more accurate prognostic assessment and informing therapeutic decision-making. In ovarian carcinoma, assessment of breast cancer susceptibility gene (BRCA) alterations and homologous recombination deficiency (HRD) has become central to personalized treatment strategies. In cervical carcinoma, although persistent high-risk human papillomavirus (HPV) infection remains the principal oncogenic driver, additional genomic alterations are increasingly being incorporated into the management of advanced disease. Furthermore, NGS enables the identification of germline alterations associated with hereditary cancer syndromes, thereby reinforcing the pathologist’s role in identifying familial cancer predisposition and supporting appropriate referral for genetic counseling. ","styles":[]},{"text":"Conclusions","styles":["bold"]},{"text":": Molecular pathology is driving the transition toward an integrated, biology-driven model of gynecologic oncology, positioning the gynecologic pathologist as a key clinical integrator in precision medicine.","styles":[]}]
[doc/p35] [{"text":"Keywords","styles":["bold"]}]
[doc/p36] [{"text":"molecular pathology; gynecologic oncology; next-generation sequencing; precision medicine; integrated diagnostics","styles":[]}]
[doc/p37] []
[doc/p38] [{"text":"1. Introduction—","styles":["bold"]},{"text":"Signs o’ the Times","styles":["bold","italic"]}]
[doc/p39] [{"text":"Gynecological carcinomas represent a major global public health challenge, with approximately 1.2 million new cases diagnosed worldwide each year. Gynecologic tumors comprise a heterogeneous group of neoplasms arising from the female reproductive tract, including malignancies of the endometrium, ovary, cervix, vulva, vagina, and placenta.","styles":[]}]
[doc/p40] [{"text":"Their clinical management requires effective strategies for prevention, early diagnosis, and treatment. Although conventional histopathological evaluation remains a cornerstone of diagnosis, it is often insufficient to resolve diagnostically ambiguous cases or to identify actionable therapeutic targets.","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p31"}, "front_content_range": {"first_node": "doc/p33", "last_node": "doc/p36"}}

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
[doc/p26] []
[doc/p27] [{"text":"Funding Statement: ","styles":["bold"]},{"text":"The study was supported by Supported by Sanming Project of Medicine in Shenzhen (No.SZSM202411034), Shenzhen Key Laboratory of Maternal and Child Health and Diseases (ZDSYS20230626091559006) and Shenzhen Clinical Research Center for Obstetrics & Gynecologyand Reproductive System Diseases (No.LCYSSQ20220823091401002).","styles":[]}]
[doc/p28] []
[doc/p29] [{"text":"Disclosure Statement:","styles":["bold"]},{"text":"The authors report no conflict of interest. ","styles":[]}]
[doc/p30] []
[doc/p31] [{"text":"Attestation Statement:","styles":["bold"]},{"text":" (The following attestation statements are required for all manuscripts) ","styles":[]}]
[doc/p32] [{"text":"• The subjects in this trial have not concomitantly been involved in other randomized ","styles":[]}]
[doc/p33] [{"text":"trials (If applicable). ","styles":[]}]
[doc/p34] [{"text":"• Data regarding any of the subjects in the study has not been previously published unless specified. ","styles":[]}]
[doc/p35] [{"text":"• Data will be made available to the editors of the journal for review or query upon ","styles":[]}]
[doc/p36] [{"text":"request. Data Sharing Statement: (For reports of clinical trials, authors are required to provide a Data Sharing Statement to indicate if data will be shared or not with other researchers for further analysis. This information will be published in a Data Sharing Statement. Please see Instructions for Authors for data sharing details) ","styles":[]}]
[doc/p37] []
[doc/p38] [{"text":"Trial registration:","styles":["bold"]},{"text":" not applicable","styles":[]}]
[doc/p39] []
[doc/p40] [{"text":"Capsule:","styles":["bold"]},{"text":" We found that within the field of RPL consultation, Claude-3 outperformed ChatGPT-4 and Gemini Pro in terms of accuracy and comprehensiveness of short essay questions.","styles":[]}]
[doc/p41] []
[doc/p42] [{"text":"Author information:","styles":["bold"]}]
[doc/p43] []
[doc/p44] [{"text":"Author information:","styles":["bold"]}]
[doc/p45] [{"text":"Abstract","styles":["bold"]}]
[doc/p46] [{"text":"Background:","styles":["bold"]},{"text":" The utility of large language models (LLMs) in recurrent pregnancy loss (RPL) consultation and patient education has not yet been systematically investigated. This study evaluated the performance of 3 LLMs (GPT-4, Claude 3 Sonnet, and Gemini Pro) in the field of RPL by assessing accuracy, comprehensiveness, and readability.","styles":[]},{"text":" Methods:","styles":["bold"]},{"text":" Two experienced obstetricians and gynecologists developed medical questions based on the 2022 guidelines of the European Society of Human Reproduction and Embryology (ESHRE). The questionnaire included multiple formats, including choice questions (single-answer and multiple-answer) and short-answer questions. Short-answer questions were further categorized as common questions or clinical cases based on the question type, and as prevention, diagnosis, or treatment based on the content. Subsequently, the LLMs-generated answers were graded for accuracy, comprehensiveness, and readability. Choice questions were evaluated for accuracy only, whereas short-answer questions were evaluated for accuracy, comprehensiveness, and readability. Accuracy and comprehensiveness were evaluated using a 5-point Likert scale. Readability was evaluated using the Flesch Reading Ease (FRE) score and the Flesch–Kincaid Grade Level (FKGL). ","styles":[]},{"text":"Results:","styles":["bold"]},{"text":" Responses to 47 questions generated by LLMs showed that the best-performing model, Claude 3 Sonnet, achieved higher scores in short-answer questions for both accuracy (median score 5.00 [interquartile range (IQR), 4.00-5.00]) and comprehensiveness (median score 5.00 [IQR, 4.13-5.00]). No differences were observed between LLMs in accuracy scores for all choice questions, including single-choice and multiple-choice questions ","styles":[]},{"text":"(P > ","styles":["italic"]},{"text":"0.05). Regarding readability, the FRE and FKGL scores indicated difficult readability, ranging from college-level to professional-levels reading skill. For single LLM, the median accuracy scores did not differ significantly across question types. After targeted, grounded prompting based on specific categories (prevention, diagnosis, and treatment), the accuracy scores of all 3 LLMs were improved (GPT-4, median score 4.00 [IQR, 3.00-5.00] vs 5.00 [IQR, 5.00-5.00], ","styles":[]},{"text":"P","styles":["italic"]},{"text":" < 0.001; Claude 3 Sonnet, median score 5.00 [IQR, 4.00-5.00] vs 5.00 [IQR, 5.00-5.00], ","styles":[]},{"text":"P ","styles":["italic"]},{"text":"= 0.001; Gemini Pro, median score 3.50 [IQR, 2.50-5.00] vs 5.00 [IQR, 5.00-5.00], ","styles":[]},{"text":"P","styles":["italic"]},{"text":" < 0.001). The comprehensiveness scores for GPT-4 improved significantly after grounded prompting, whereas Claude 3 Sonnet and Gemini Pro performed worse than baseline, although these differences were not statistically significant. ","styles":[]},{"text":"Conclusions:","styles":["bold"]},{"text":" Within the field of RPL consultation, Claude 3 Sonnet outperformed GPT-4 and Gemini Pro in terms of accuracy and comprehensiveness of short-answer questions. After targeted, grounded prompting across specific categories (prevention, diagnosis, and treatment), the accuracy scores of all 3 LLMs improved. These findings suggest the potential of LLMs as an important supplementary tool for the current medical system in the field of RPL, supporting improvements in patient management. ","styles":[]}]
[doc/p47] [{"text":"Keywords ","styles":["bold"]}]
[doc/p48] [{"text":"large language model; artificial intelligence; recurrent miscarriage; recurrent pregnancy loss.","styles":[]}]
[doc/p49] []
[doc/p50] [{"text":"Introduction","styles":["bold"]}]
[doc/p51] [{"text":"Recurrent pregnancy loss (RPL) is a serious pregnancy disorder affecting approximately 1-5% of women attempting to conceive [1] and is defined as the loss of two or more clinically recognized pregnancies [2]. Previous studies have found that RPL profoundly affects the quality of life of women and their partners and is associated with an increased risk of adverse obstetric outcomes, metabolic syndrome, and psychological disorders such as anxiety and depression in women [3,4]. Therefore, improving the diagnosis, treatment strategies, and preventive management of RPL in couples is essential and holds important clinical and social significance.","styles":[]}]
[doc/p52] [{"text":"Large language models (LLMs) are artificial intelligence (AI) model trained on large-scale datasets to generate natural language text applicable to a wide range of fields [5]. Among them, GPT is the most widely used and has demonstrated strong performance in the medical field, with broad application prospects in clinical decision-making (i.e., risk assessment, diagnosis, treatment selection), patient engagement (i.e., medical reminders, lifestyle advice), and public health[ 6,7], contributing a reduced workload for medical professionals, increased efficiency, and lower costs [8]. While these findings highlight the potential of LLMs as valuable supplementary tools in clinical settings, their accuracy varies considerably across medical domains. This variability underscores the critical importance of model selection, prompt design, and domain-specific evaluation in optimizing the quality of clinical consultation [9-10].","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p25"}, "front_content_range": {"first_node": "doc/p40", "last_node": "doc/p48"}}

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
[doc/p13] []
[doc/p14] [{"text":"Abstract","styles":["bold"]}]
[doc/p15] [{"text":"Background","styles":["bold"]},{"text":": High mobility group box 1 (HMGB1), a damage-associated molecular pattern (DAMP), has been increasingly implicated in the pathogenesis of various cardiovascular diseases. Our study aimed to elucidate the relationship between circulating HMGB1 levels and the development of heart failure (HF).","styles":[]}]
[doc/p16] [{"text":"Methods","styles":["bold"]},{"text":": The single-center cross-sectional study enrolled 412 patients with chest tightness or pain from the Department of Cardiovascular Medicine, First Affiliated Hospital of Nanchang University, between January and July 2025. The relationship between HMGB1 and the occurrence of HF was evaluated using Spearman's correlation analysis, logistic regression models, restricted cubic spline (RCS) plots, and receiver operating characteristic (ROC) curves. Subgroup analyses were further performed to verify the robustness of the results.","styles":[]}]
[doc/p17] [{"text":"Results","styles":["bold"]},{"text":": Among 412 enrolled subjects, 343 were included in the analysis after exclusion criteria were applied, 151 were diagnosed with HF, and 192 served as controls. Serum HMGB1 levels were significantly elevated in the HF group compared to controls (1.53 ng/mL vs 0.93 ng/mL, ","styles":[]},{"text":"P","styles":["italic"]},{"text":" < 0.001). Spearman's correlation analysis revealed significant positive correlations between HMGB1 levels and LVEDD, LAD, and systemic inflammatory response index (SIRI), along with a significant negative correlation with LVEF (all ","styles":[]},{"text":"P","styles":["italic"]},{"text":" < 0.05). In univariate logistic regression, elevated HMGB1 was strongly associated with HF risk (OR = 2.942, 95% CI: 2.113-4.095, ","styles":[]},{"text":"P","styles":["italic"]},{"text":" < 0.001). This association remained significant in multivariate models after sequential adjustment for demographic, clinical, and laboratory covariates, with a fully adjusted OR of 2.273 (95% CI: 1.410-3.663, ","styles":[]},{"text":"P","styles":["italic"]},{"text":" < 0.001). RCS analysis verified a linear dose-response association between HMGB1 levels and HF risk (","styles":[]},{"text":"P","styles":["italic"]},{"text":" for nonlinearity = 0.174). The ROC analysis revealed that HMGB1 alone provided good predictive value for HF (AUC = 0.736), outperforming individual traditional markers. Furthermore, a combined model incorporating HMGB1 with LVEF, LVEDD, LAD, and SIRI achieved superior predictive accuracy (AUC = 0.807). Finally, subgroup analyses demonstrated that the association between HMGB1 and HF risk remained consistent across all prespecified subgroups without significant interaction effects (all ","styles":[]},{"text":"P","styles":["italic"]},{"text":" for interaction > 0.05), indicating the robustness of this relationship independent of major clinical variables.","styles":[]}]
[doc/p18] [{"text":"Conclusion:","styles":["bold"]},{"text":" The","styles":[]},{"text":" ","styles":["bold"]},{"text":"levels of HMGB1 elevation were significantly related to the occurrence of HF, which may serve as a potential biomarker for the development of innovative therapeutic strategies in patients with heart failure.","styles":[]}]
[doc/p19] [{"text":"Key words: ","styles":["bold"]},{"text":"Heart failure; High-mobility group box 1; Biomarker; Damage-associated molecular patterns; Inflammation.","styles":[]}]
[doc/p20] []
[doc/p21] []
[doc/p22] []
[doc/p23] []
[doc/p24] [{"text":"Introduction","styles":["bold"]}]
[doc/p25] [{"text":"Heart failure (HF) is a multifaceted clinical syndrome defined by structural or functional cardiac dysfunction stemming from diverse etiologies, a condition that may progress to serious adverse cardiovascular events [1]. Despite a stabilization in incidence and mortality rates in developed nations, heart failure persists as a major global public health challenge with a high overall prevalence[2, 3]. The results of the latest epidemiological survey in China indicate that the total number of prevalent cases of heart failure increased from 7.03 million in 1990 to 18.51 million in 2019, showing an increasing trend year by year, with a prevalence rate of 1.3% among residents aged 35 and older [4, 5]. Characterized by high prevalence, mortality, and readmission rates, heart failure imposes a significant burden on public health, and the early detection and timely intervention of heart failure are crucial for improving patient outcomes and altering the disease trajectory[6].","styles":[]}]
[doc/p26] [{"text":"The pathogenesis of heart failure is characterized by intricate crosstalk among multiple signaling pathways. Cardiac fibrosis, a key pathological feature of heart failure resulting from excessive collagen deposition, plays a crucial role in disease progression[7]. Accumulating evidence has further underscored that a spectrum of acute and chronic inflammatory responses serves as a central mediator in the pathogenesis of cardiac fibrosis[8]. A range of inflammatory mediators, including C-reactive protein and interleukins, have been consistently identified to exhibit a marked upregulation in patients with HF, and these factors are closely correlated with the long-term prognostic outcomes of HF patients[9, 10].","styles":[]}]

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p12"}, "front_content_range": {"first_node": "doc/p14", "last_node": "doc/p19"}}

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
[doc/p44] []
[doc/p45] []
[doc/p46] [{"text":"Abstract","styles":["bold"]}]
[doc/p47] [{"text":"Background: ","styles":["bold"]},{"text":"Acute intracerebral hemorrhage (ICH) often induces a hyperadrenergic response, resulting in a significantly increased cardiac workload. This study aimed to assess the relationship between early cardiac workload and long-term outcomes following ICH, utilizing the rate-pressure product (RPP) as a simple surrogate indicator. ","styles":[]},{"text":"Methods: ","styles":["bold"]},{"text":"We conducted an analysis of data from a large multicenter, prospective cohort comprising 1,364 ICH patients. Heart rate (HR) and systolic blood pressure (SBP) were recorded to calculate the RPP. Multivariable logistic regression and Cox proportional hazards models were used to evaluate the associations of RPP with unfavorable functional outcome (modified Rankin Scale score >3) and all-cause mortality at 90 days and 1 year, respectively. ","styles":[]},{"text":"Results: ","styles":["bold"]},{"text":"Elevated RPP was independently associated with unfavorable functional outcomes and all-cause mortality at 90 days and 1 year (all ","styles":[]},{"text":"p","styles":["italic"]},{"text":" < 0.05). RPP exhibited superior predictive performance, as indicated by higher C-statistics for all outcomes when compared to HR or SBP alone (all ","styles":[]},{"text":"p","styles":["italic"]},{"text":" < 0.05). A significant interaction was noted with in-hospital β-blocker treatment (","styles":[]},{"text":"p","styles":["italic"]},{"text":" for interaction < 0.05), indicating that the association between high RPP and primary outcomes was attenuated in patients receiving β-blockers. ","styles":[]},{"text":"Conclusions: ","styles":["bold"]},{"text":"Early cardiac workload, quantified by the RPP, is a potent independent predictor of long-term unfavorable functional outcome and all-cause mortality in patients with ICH.","styles":[]}]
[doc/p48] []
[doc/p49] [{"text":"Keywords: ","styles":["bold"]}]
[doc/p50] [{"text":"brain-heart interaction; cardiac workload; intracerebral hemorrhage; prognosis; rate-pressure product","styles":[]}]
[doc/p51] []
[doc/p51.2] [{"text":"⟦图#o1⟧","styles":[]}]
[doc/p52] [{"text":"1. Introduction ","styles":["bold"]}]
[doc/p53] [{"text":"Intracerebral hemorrhage (ICH) is a devastating subtype of stroke accounting for 15–20% of all stroke cases, and is associated with significant mortality and long-term disability [1–3]. Recent evidence has increasingly underscored the critical role of the brain-heart interaction in the pathophysiology of acute brain injury [4–6]. Following the onset of ICH, neurohormonal and autonomic dysregulation frequently precipitate a hyperadrenergic state, which imposes substantial stress on the cardiovascular system [5,6].","styles":[]}]
[doc/p54] []

**输出**

{"metadata_range": {"first_node": "doc/p1", "last_node": "doc/p43"}, "front_content_range": {"first_node": "doc/p46", "last_node": "doc/p50"}}
