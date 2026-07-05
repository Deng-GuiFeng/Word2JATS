// 为补充样例 S02-S05 构建参考 JATS XML(最终文件.xml)+ figures.zip。
// 严格结构化保真:内容逐字忠实 docx,只加结构不改内容;已核实的刊/DOI/ISSN 填入。
export const meta = {
  name: 'refxml-build',
  description: '构建 S02-S05 最终文件.xml(忠实 docx 的 JATS 1.3)+ figures.zip',
  phases: [{ title: 'Build', detail: '每样例 1 个 opus subagent 构建+DTD校验+落盘' }],
}

const ROOT = '/home/denggf/学术期刊结构化技术创新大赛'
const CEOG = { jtitle: 'Clinical and Experimental Obstetrics & Gynecology', abbrev: 'Clin. Exp. Obstet. Gynecol.', issn_p: '0390-6663', issn_e: '2709-0094', journal: 'CEOG' }
const RCM = { jtitle: 'Reviews in Cardiovascular Medicine', abbrev: 'Rev. Cardiovasc. Med.', issn_p: '1530-6550', issn_e: '2153-8174', journal: 'RCM' }
const META = {
  S02: { ...CEOG, doi: '10.31083/CEOG51386', href: 'CEOG51386', atype: 'review-article', subject: 'Review', nfig: 2, pdf: 'https://storage.imrpress.com/IMR/... (从文章页 https://www.imrpress.com/journal/CEOG/53/6/10.31083/CEOG51386 找 PDF 链接)' },
  S03: { ...CEOG, doi: '10.31083/CEOG50327', href: 'CEOG50327', atype: 'research-article', subject: 'Original Research', nfig: 4, pdf: 'https://www.imrpress.com/journal/CEOG/53/6/10.31083/CEOG50327' },
  S04: { ...RCM, doi: '10.31083/RCM49717', href: 'RCM49717', atype: 'research-article', subject: 'Original Research', nfig: 6, pdf: 'https://www.imrpress.com/journal/RCM/27/6/10.31083/RCM49717' },
  S05: { ...RCM, doi: '10.31083/RCM49651', href: 'RCM49651', atype: 'research-article', subject: 'Original Research', nfig: 4, pdf: 'https://www.imrpress.com/journal/RCM/27/6/10.31083/RCM49651' },
}

function prompt(key) {
  const m = META[key]
  return `你的任务:为竞赛样例 ${key} 构建一份 **DTD 合法、JATS Journal Publishing DTD v1.3、逐字忠实于源 docx、精确复刻 IMR Press 约定** 的参考 JATS XML,写到 \`${ROOT}/样例数据/${key}/最终文件.xml\`,并生成 \`${ROOT}/样例数据/${key}/figures.zip\`。这是"Word→JATS XML 结构化技术"任务的参考产物。

## ⚠️ 最高原则:这是"结构化技术"任务,只加结构、绝不改内容
- **内容逐字忠实**:XML 里正文/标题/作者/参考文献/表格/图题的每个字符,必须与 docx 完全一致——包括作者的拼写错误、时态错误、罗马音/译名错误、大小写、疑似 l/I 混淆(无衬线字体里小写 l 与大写 I 同形)、多余空格等一切,**一律原样保留,绝不"修正"**。判断某处"看起来错了",也不能改——那是源内容(作者主观错误或既有技术串字都属源内容),改了就是擅自修改语义,直接判为错误。
- 你只做**结构化**:把 docx 内容映射进 JATS 元素、加出版方 house-style 结构模板(见下)。不做任何内容校对/纠错/润色。
- **发表版 PDF 仅用于核对"是否抽全"**(有没有漏段落、漏参考文献、漏表格);**绝不用它修改任何内容**;docx 与发表版有出入时,**一律以 docx 为准**(把分歧记进报告)。

## 工作目录 / 工具
${ROOT}(下称 ROOT)。Python 用 \`${ROOT}/.venv/bin/python\`(含 lxml)。

## 输入与依据(按权威度)
1. **源 docx(唯一内容权威)**:\`${ROOT}/样例数据/${key}/初始文件.docx\`。解压读 \`word/document.xml\`(ns w=…/wordprocessingml/2006/main)。正文所有段落、参考文献、表格、图题逐字来自这里。注意上标 w:vertAlign=superscript、粗体 w:b、斜体 w:i(基因名/物种名等的斜体语义要用 <italic> 保留)、w:tbl 结构。
2. **已核验事实(front 骨架权威)**:\`${ROOT}/样例数据/${key}/伪标签.json\` —— 标题/作者(given/surname/aff_labels/corresponding/equal_contrib/orcid/email)/单位/编辑/日期/关键词/摘要结构/图数/表(n_cols,n_header_rows,has_footnote)/参考文献数/back 小节,均已多代理回源核验。front 区以此为准,数量必须对上。
3. **JATS 约定模板(格式权威)**:\`${ROOT}/样例数据/01/最终文件.xml\`(RCM 刊,research-article)和 \`${ROOT}/样例数据/03/最终文件.xml\`(JIN 刊)。**逐元素照抄它们的结构约定**:DOCTYPE(标准 public/system id,与金标准一字不差)、根元素属性、journal-meta、article-id×2、article-categories/subj-group[heading]/subject、title-group、contrib-group(作者 contrib-id contrib-id-type=orcid authenticated=true 值=https://orcid.org/…、name/surname/given-names、xref ref-type=aff/corresp 内含 <sup>、email)、editor 独立 contrib-group(role=Academic Editor)、aff id=affN 内含 <sup>、author-notes/corresp、history(date date-type=received/rev-recd/accepted,内含 day/month/year;月份用**数字**如 11、2)、permissions(copyright-statement/copyright-year/license CC BY 4.0,均为 IMR house-style 模板)、abstract(sec/title 如 "Background:"、p)、kwd-group、body(sec/title/p、fig、table-wrap)、back(ack、ref-list/ref/element-citation、声明 sec、fn-group Publisher's Note[IMR house-style 模板,照金标准一字不差])、必要时 app-group。
4. **样板参考(同一处理流程的已定稿样例)**:\`${ROOT}/样例数据/S01/最终文件.xml\` 是本批 S01 的已通过成品(CEOG 刊),可参考其整体组织(尤其 CEOG 的 journal-meta 写法、element-citation、figures 处理)。
5. **发表版 PDF(抽全性旁证,可选)**:文章页 ${m.pdf} —— 可 curl 文章页找到 PDF storage 链接后下载 pdftotext,仅核对是否抽全,不改内容。

## ${key} 的已核实出版元数据(联网逐篇 fetch 官网核实过,直接用,勿改)
- 期刊:${m.jtitle};journal-id(publisher-id)=**${m.journal}**;abbrev-journal-title(publisher)=**${m.abbrev}**
- ISSN:ppub **${m.issn_p}**,epub **${m.issn_e}**;publisher-name=**IMR Press**
- article-id:doi=**${m.doi}**,publisher-id=**${m.doi}**
- article-type=**${m.atype}**;article-categories/subject=**${m.subject}**(以 docx 的 Articletype 标注为准交叉核对;${key === 'S02' ? '本篇是 Narrative Review' : '本篇是原创研究'})
- 图 graphic 的 xlink:href 前缀用 DOI 后缀目录:\`${m.href}/fig-0N.<ext>\`(照金标准 "RCM46777/fig-01.jpg" 约定)

## figures.zip 要求
- 从 docx \`word/media/\` 抽出**真正的编号图**(伪标签.json 记本篇 ${m.nfig} 张图;按图在 document.xml 里的 drawing 锚点/出现顺序甄别,**排除**页眉页脚刊标 logo、无题注的图形摘要/banner 等非编号图)。
- 命名 \`fig-01.<ext>\`…(扩展名随原图真实格式:jpeg→.jpg,png→.png,tiff→.tif;**字节保留不转码**);打包到 \`${ROOT}/样例数据/${key}/figures.zip\`(zip 根目录直放这些文件)。
- XML 里 \`<graphic xlink:href="${m.href}/fig-0N.<ext>" id="F00N.g1"/>\`。抽出的图字节应与 docx media 对应文件 md5 相同。

## 硬约束
- **DTD 合法**:用 lxml 对本地 DTD 校验并确保通过:
  \`\`\`
  from lxml import etree
  dtd=etree.DTD('${ROOT}/src/word2jats/resources/dtd/JATS-Publishing-1-3-MathML3-DTD/JATS-journalpublishing1-3-mathml3.dtd')
  doc=etree.parse('${ROOT}/样例数据/${key}/最终文件.xml', etree.XMLParser(load_dtd=False,no_network=True,resolve_entities=False))
  assert dtd.validate(doc), dtd.error_log
  \`\`\`
  (成品 DOCTYPE 仍写标准 JATS v1.3 public/system id,与金标准一致;本地只有 mathml3 变体 DTD 用于校验,是合法上位集。)
- **忠实**:正文每段、每条参考文献、每个表格、每个图题逐字来自 docx,顺序忠实,不增不减不改。参考文献做成结构化 element-citation(person-group/name surname+given-names、collab 团体作者、etal、article-title、source 内可含 italic、year、volume、fpage/lpage、pub-id doi 或 ext-link、comment 如 "(In Chinese)");docx 是规整 Vancouver 体例,尽量全部结构化,个别实在拆不动的可 mixed-citation 兜底并在报告说明条数。
- **数量对账**:成品 作者/单位/图/表/参考文献/back 小节 数必须与 伪标签.json 一致,不一致要在报告解释。
- 表格:table-wrap/label/caption/table(colgroup+col width%、thead/tbody、th/td 带 align/valign/style/scope,照金标准三线表 house-style;列宽/对齐取自 docx),表脚注入 table-wrap-foot/fn(has_footnote 为 true 的表)。
- 不把 伪标签.json 写进成品;只产出 最终文件.xml 和 figures.zip。

## 交付(返回简明报告,不要贴整份 XML)
DTD 校验结果(dtd.validate 布尔值);front/body/back 各区元素计数并与伪标签对账;figures.zip 文件清单 + 与 docx media 的 md5 对应;参考文献结构化条数/兜底条数;与发表版 PDF 交叉核对"抽全性"结论 + docx-vs-发表版分歧清单(均保留 docx);所有你保留的疑似源错误(如 l/I 混淆、拼写/时态/罗马音错误——列出但**不得修改**);其它残留判断项。`
}

phase('Build')
log('批量构建 S02-S05 最终文件.xml(严格逐字忠实 docx)')
const keys = ['S02', 'S03', 'S04', 'S05']
const results = await parallel(keys.map((k) => () =>
  agent(prompt(k), { label: `build:${k}`, phase: 'Build', model: 'opus', effort: 'high' })
    .then((r) => ({ key: k, report: r }))
))
return { results: results.filter(Boolean) }
