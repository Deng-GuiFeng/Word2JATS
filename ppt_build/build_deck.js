// 决赛答辩 PPT 生成脚本(pptxgenjs)。内容见 docs/01-设计/03-决赛答辩要点.md。
const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE"; // 13.3 x 7.5
p.author = "word2jats 参赛队";
p.title = "Word→JATS XML 智能结构化转换";

const NAVY = "1B2A4A", TEAL = "0E8A8A", MINT = "00A896",
      ICE = "F4F7FA", SLATE = "5B6B7B", WHITE = "FFFFFF", INK = "22303F";
const HF = "Noto Serif CJK SC", BF = "Noto Sans CJK SC";
const W = 13.3, H = 7.5;
const mk = (o) => Object.assign({}, o); // 防止 pptxgenjs 就地改写共享对象
const shadow = () => ({ type: "outer", color: "000000", blur: 7, offset: 3, angle: 135, opacity: 0.12 });

function footer(s, n) {
  s.addText("学术期刊结构化技术创新大赛 · 组别一 word2xml", {
    x: 0.5, y: H - 0.42, w: 9, h: 0.3, fontFace: BF, fontSize: 9, color: SLATE });
  s.addText(String(n), { x: W - 0.9, y: H - 0.42, w: 0.5, h: 0.3,
    fontFace: BF, fontSize: 10, color: SLATE, align: "right" });
}
// 浅色内容页统一标题
function title(s, t, kicker) {
  s.background = { color: ICE };
  s.addShape(p.shapes.OVAL, { x: 0.5, y: 0.55, w: 0.22, h: 0.22, fill: { color: TEAL } });
  s.addText(kicker, { x: 0.8, y: 0.5, w: 11, h: 0.3, fontFace: BF, fontSize: 12, color: TEAL, bold: true, charSpacing: 2 });
  s.addText(t, { x: 0.5, y: 0.78, w: 12.3, h: 0.7, fontFace: HF, fontSize: 30, bold: true, color: NAVY });
}
function card(s, x, y, w, h, fill) {
  s.addShape(p.shapes.RECTANGLE, { x, y, w, h, fill: { color: fill || WHITE }, line: { color: "E2E8F0", width: 1 }, shadow: shadow() });
}

// ---------------- 1. 封面(深色) ----------------
let s = p.addSlide();
s.background = { color: NAVY };
s.addShape(p.shapes.RECTANGLE, { x: 0, y: 0, w: 0.28, h: H, fill: { color: TEAL } });
s.addShape(p.shapes.OVAL, { x: 10.7, y: -1.6, w: 4.4, h: 4.4, fill: { color: "24365C" } });
s.addShape(p.shapes.OVAL, { x: 11.9, y: 4.6, w: 3.2, h: 3.2, fill: { color: "22324f" } });
s.addText("面向学术出版的智能结构化转换", { x: 0.9, y: 2.0, w: 11, h: 0.5, fontFace: BF, fontSize: 16, color: MINT, charSpacing: 1 });
s.addText("Word → JATS XML\n自动语义映射与生成", { x: 0.85, y: 2.5, w: 11.5, h: 1.8, fontFace: HF, fontSize: 44, bold: true, color: WHITE, lineSpacingMultiple: 1.05 });
s.addText("规则打底 · 按需上本地多模态模型 · 自检兜底", { x: 0.9, y: 4.5, w: 11, h: 0.5, fontFace: BF, fontSize: 18, color: "CADCFC" });
s.addText("组别一 word2xml　|　决赛答辩", { x: 0.9, y: 6.4, w: 11, h: 0.4, fontFace: BF, fontSize: 14, color: "9FB3D1" });

// ---------------- 2. 问题与痛点 ----------------
s = p.addSlide(); title(s, "出版社的真实痛点:人工转换又慢又贵", "01　问题");
s.addText("学术论文以 Word 投稿,上线发布却要符合国际通用的 JATS 标准 XML。现在这一步主要靠人工,既慢又贵还容易出错。我们要让它自动完成,而且扛得住真实投稿的千奇百怪。",
  { x: 0.6, y: 1.7, w: 7.2, h: 1.7, fontFace: BF, fontSize: 16, color: INK, lineSpacingMultiple: 1.35, valign: "top" });
const diff = [["Word 只有排版,没有含义", "文件里记的是\"三号黑体居中\",不是\"这是标题\";机器得自己看出每段是什么"],
  ["排版花样百出", "5 个样例里有一篇 325 段全是默认样式,没有任何可用标记——靠固定套路必然栽"],
  ["图表公式都要特殊处理", "图片要外部化、公式是 OMML 要转 MathML、有的表干脆是一张图片"]];
diff.forEach((d, i) => {
  const y = 1.6 + i * 1.55;
  card(s, 8.1, y, 4.7, 1.4);
  s.addText(String(i + 1), { x: 8.25, y: y + 0.2, w: 0.55, h: 0.55, fontFace: HF, fontSize: 22, bold: true, color: WHITE, align: "center", valign: "middle", fill: { color: TEAL }, shape: p.shapes.OVAL });
  s.addText(d[0], { x: 8.95, y: y + 0.16, w: 3.7, h: 0.4, fontFace: BF, fontSize: 14, bold: true, color: NAVY });
  s.addText(d[1], { x: 8.95, y: y + 0.55, w: 3.75, h: 0.8, fontFace: BF, fontSize: 11, color: SLATE, lineSpacingMultiple: 1.15, valign: "top" });
});
footer(s, 2);

// ---------------- 3. 总体思路:智能循环 ----------------
s = p.addSlide(); title(s, "一个会判断的流水线:便宜先上,按需升级", "02　总体思路");
const lanes = [
  ["① 确定性规则打底", "标题、作者、摘要、图、真实表格、公式、正文、参考文献、交叉引用——大多数文档零点几秒就处理对了", TEAL],
  ["② 本地多模态模型按需补", "规则啃不动的硬骨头:看图重建图片表与制表符表、结构化参考文献、抽不出作者时重抽(本地两卡并发)", MINT],
  ["③ 检查 + 修复 + 校验兜底", "查悬空引用、空表、空正文并就地修复,最后过 JATS 1.3 官方 DTD 与出版规范校验", NAVY]];
lanes.forEach((l, i) => {
  const y = 1.75 + i * 1.45;
  card(s, 0.9, y, 9.2, 1.2);
  s.addShape(p.shapes.RECTANGLE, { x: 0.9, y, w: 0.12, h: 1.2, fill: { color: l[2] } });
  s.addText(l[0], { x: 1.2, y: y + 0.14, w: 8.6, h: 0.4, fontFace: BF, fontSize: 16, bold: true, color: l[2] });
  s.addText(l[1], { x: 1.2, y: y + 0.52, w: 8.7, h: 0.6, fontFace: BF, fontSize: 12, color: INK, lineSpacingMultiple: 1.15, valign: "top" });
  if (i < 2) s.addText("▼", { x: 5.3, y: y + 1.18, w: 0.4, h: 0.25, fontFace: BF, fontSize: 12, color: SLATE, align: "center" });
});
card(s, 10.4, 1.75, 2.4, 4.15, NAVY);
s.addText("可一键关掉", { x: 10.5, y: 2.0, w: 2.2, h: 0.4, fontFace: BF, fontSize: 15, bold: true, color: MINT, align: "center" });
s.addText("关掉模型与联网,\n规则也能产出\n完整、合规的 XML。\n\n模型只在规则失败处补、\n绝不覆盖对的结果\n——纯增益、无回退。", { x: 10.5, y: 2.5, w: 2.2, h: 3.2, fontFace: BF, fontSize: 12, color: "CADCFC", align: "center", lineSpacingMultiple: 1.25, valign: "top" });
footer(s, 3);

// ---------------- 4. 确定性管线 ----------------
s = p.addSlide(); title(s, "规则打底:全模块覆盖,零点几秒,零成本", "03　确定性管线");
const mods = ["标题层级", "作者 + 单位 + ORCID", "关键词", "摘要(结构化)", "图片外部化", "数学公式 OMML→MathML", "列表", "真实表格", "参考文献", "交叉引用"];
mods.forEach((m, i) => {
  const x = 0.6 + (i % 5) * 2.46, y = 1.75 + Math.floor(i / 5) * 1.0;
  card(s, x, y, 2.3, 0.82);
  s.addText(m, { x: x + 0.12, y: y, w: 2.06, h: 0.82, fontFace: BF, fontSize: 12.5, bold: true, color: NAVY, align: "center", valign: "middle" });
});
[["0.04–0.2 秒", "单篇转换", TEAL], ["5 / 5", "通过 JATS 1.3 DTD 校验", MINT], ["0 元", "不联网不调模型", NAVY]].forEach((c, i) => {
  const x = 0.9 + i * 4.0;
  s.addText(c[0], { x, y: 4.4, w: 3.4, h: 0.8, fontFace: HF, fontSize: 40, bold: true, color: c[2], align: "center" });
  s.addText(c[1], { x, y: 5.25, w: 3.4, h: 0.4, fontFace: BF, fontSize: 13, color: SLATE, align: "center" });
});
footer(s, 4);

// ---------------- 5. 看图读表(核心创新) ----------------
s = p.addSlide(); title(s, "本地多模态模型:把\"图片表\"看成结构化表格", "04　核心创新");
s.addText("样例2 有 8 张表,在 Word 里整张就是一张图片——规则完全做不了。让模型看图,重建成标准 JATS 表格。",
  { x: 0.6, y: 1.65, w: 12.2, h: 0.6, fontFace: BF, fontSize: 14, color: INK, lineSpacingMultiple: 1.2 });
s.addText("输入:一张图片", { x: 0.9, y: 2.35, w: 5.2, h: 0.35, fontFace: BF, fontSize: 13, bold: true, color: SLATE });
s.addImage({ path: "assets/table_before.jpeg", x: 0.9, y: 2.75, w: 5.2, h: 3.7, sizing: { type: "contain", w: 5.2, h: 3.7 } });
s.addShape(p.shapes.LINE, { x: 6.35, y: 4.4, w: 0.7, h: 0, line: { color: TEAL, width: 2.5, endArrowType: "triangle" } });
s.addText("输出:结构化 <table-wrap>", { x: 7.3, y: 2.35, w: 5.5, h: 0.35, fontFace: BF, fontSize: 13, bold: true, color: TEAL });
s.addTable([
  [{ text: "Device", options: { bold: true, color: WHITE, fill: { color: NAVY } } }, { text: "Company", options: { bold: true, color: WHITE, fill: { color: NAVY } } }, { text: "Stent", options: { bold: true, color: WHITE, fill: { color: NAVY } } }],
  ["AltaValve", "4C Medical (USA)", "Self-expanding nitinol"],
  ["Cardiovalve", "Cardiovalve (Israel)", "Self-expanding"],
  ["Cephea", "Gore (USA)", "Nitinol, double-layer"],
  ["…", "…", "…"]],
  { x: 7.3, y: 2.75, w: 5.5, h: 2.4, fontFace: BF, fontSize: 10.5, color: INK, border: { pt: 0.5, color: "CBD5E1" }, align: "left", valign: "middle", rowH: 0.45 });
s.addText("8 张图片表全部 8/8 重建;两卡并发,冷缓存仅 51 秒(串行需约 7 分钟)。", { x: 7.3, y: 5.4, w: 5.5, h: 0.9, fontFace: BF, fontSize: 12, color: MINT, bold: true, lineSpacingMultiple: 1.2, valign: "top" });
footer(s, 5);

// ---------------- 6. 参考文献 ----------------
s = p.addSlide(); title(s, "参考文献:从一行文字到结构化引用", "05　参考文献");
card(s, 0.9, 1.85, 5.5, 1.5, "FFF6F0");
s.addText("纯文本(docx 里)", { x: 1.1, y: 1.95, w: 5, h: 0.3, fontFace: BF, fontSize: 12, bold: true, color: "B85042" });
s.addText("Qiu T, Yang Y, et al. The association… Journal of Affective Disorders. 2020; 265: 85–90.", { x: 1.1, y: 2.3, w: 5.1, h: 0.95, fontFace: BF, fontSize: 11.5, color: INK, lineSpacingMultiple: 1.2, valign: "top" });
s.addShape(p.shapes.LINE, { x: 6.6, y: 2.6, w: 0.7, h: 0, line: { color: TEAL, width: 2.5, endArrowType: "triangle" } });
card(s, 7.5, 1.85, 5.3, 1.5, "EAF6F4");
s.addText("结构化 element-citation", { x: 7.7, y: 1.95, w: 5, h: 0.3, fontFace: BF, fontSize: 12, bold: true, color: TEAL });
s.addText("surname/given · article-title · source(全称) · year · volume · fpage/lpage · DOI", { x: 7.7, y: 2.3, w: 4.95, h: 0.95, fontFace: BF, fontSize: 11.5, color: INK, lineSpacingMultiple: 1.2, valign: "top" });
s.addText("做法:先用 CrossRef 按标题反查补 DOI、刊名全称、卷页(带防错门槛);CrossRef 没命中的再让本地模型兜底。", { x: 0.9, y: 3.7, w: 11.9, h: 0.6, fontFace: BF, fontSize: 13, color: INK, lineSpacingMultiple: 1.2 });
[["≈ 95%", "参考文献升级为结构化引用", TEAL], ["144 / 145", "样例4 文献几乎全部结构化", MINT]].forEach((c, i) => {
  const x = 1.6 + i * 5.6;
  s.addText(c[0], { x, y: 4.6, w: 4.4, h: 0.8, fontFace: HF, fontSize: 38, bold: true, color: c[2], align: "center" });
  s.addText(c[1], { x, y: 5.45, w: 4.4, h: 0.4, fontFace: BF, fontSize: 13, color: SLATE, align: "center" });
});
footer(s, 6);

// ---------------- 7. 校验 ----------------
s = p.addSlide(); title(s, "双重校验:结构合法 + 出版规范", "06　质量保障");
const checks = [["JATS 1.3 DTD 校验", "本地内置官方 DTD,逐篇校验结构合法性。5 个样例三档位全部通过。"],
  ["出版规范检查(JATS4R 风格)", "ORCID 须完整 URL、图表公式须有 id、公式须含 mml:math、license 须带链接。5 样例 0 问题。"],
  ["一致性检查 + 自动修复", "查悬空引用、空表行、空正文等,就地修掉,保证输出始终稳定合规。"],
  ["异常输入不崩", "空文档/无结构/异常字符,实测都产出合规 XML(未知期刊用占位元数据兜底)。"]];
checks.forEach((c, i) => {
  const x = 0.9 + (i % 2) * 6.1, y = 1.85 + Math.floor(i / 2) * 2.05;
  card(s, x, y, 5.8, 1.8);
  s.addShape(p.shapes.RECTANGLE, { x, y, w: 0.12, h: 1.8, fill: { color: TEAL } });
  s.addText(c[0], { x: x + 0.3, y: y + 0.2, w: 5.3, h: 0.45, fontFace: BF, fontSize: 16, bold: true, color: NAVY });
  s.addText(c[1], { x: x + 0.3, y: y + 0.72, w: 5.3, h: 0.95, fontFace: BF, fontSize: 12.5, color: INK, lineSpacingMultiple: 1.25, valign: "top" });
});
footer(s, 7);

// ---------------- 8. 结果 ----------------
s = p.addSlide(); title(s, "5 个官方样例:全部合规,核心计数贴近金标准", "07　结果");
s.addTable([
  ["样例", "期刊", "DTD", "表格(本地模型)", "参考文献结构化", "交叉引用 / 金标准"].map(t => ({ text: t, options: { bold: true, color: WHITE, fill: { color: NAVY }, align: "center" } })),
  ["1", "RCM", "✓", "4 / 3", "37 / 39", "100 / 100"],
  ["2", "RCM", "✓", "0→8 / 8", "65 / 68", "85 / 86"],
  ["3", "JIN", "✓", "3 / 3", "56 / 56", "102 / 102"],
  ["4", "JIN", "✓", "3 / 3", "145 / 145", "318 / 379"],
  ["5", "HSF", "✓", "0→10 / 14", "27 / 27", "54 / 70"],
].map((r, ri) => ri === 0 ? r : r.map((c, ci) => ({ text: c, options: { align: "center", color: ci === 2 ? TEAL : INK, bold: ci === 2 } }))),
  { x: 0.9, y: 1.9, w: 11.5, h: 2.7, fontFace: BF, fontSize: 13, border: { pt: 0.5, color: "CBD5E1" }, valign: "middle", rowH: 0.42, fill: { color: WHITE } });
s.addText("样例3 各模块与金标准逐项一致;两类最难的表(图片表、制表符表)由本地模型解决。\n剩余差距(样例4/5 交叉引用、样例5 表 10/14)是金标准的人工编辑/拆分所致,刻意不强行追平以免过拟合。",
  { x: 0.9, y: 4.8, w: 11.5, h: 1.2, fontFace: BF, fontSize: 13, color: INK, lineSpacingMultiple: 1.3, valign: "top" });
footer(s, 8);

// ---------------- 9. 创新与评分 ----------------
s = p.addSlide(); title(s, "为什么这套设计能拿分", "08　创新点与评分");
const score = [["落地性 40%", "确定性打底,零点几秒、零成本、稳定;清晰分层 + 测试 + 文档", TEAL],
  ["创新性 30%", "规则 + 本地多模态\"看图\"的混合循环;按需升级、自检兜底", MINT],
  ["业务适用性 20%", "全模块覆盖;过 DTD + 出版规范校验;贴合 IMR 真实模板", NAVY],
  ["展示度 10%", "完整中文文档、清晰命令与日志、可现场演示", "B85042"]];
score.forEach((c, i) => {
  const y = 1.85 + i * 1.18;
  card(s, 0.9, y, 11.9, 1.0);
  s.addText(c[0], { x: 1.15, y: y + 0.12, w: 3.0, h: 0.75, fontFace: HF, fontSize: 19, bold: true, color: c[2], valign: "middle" });
  s.addShape(p.shapes.LINE, { x: 4.3, y: y + 0.18, w: 0, h: 0.64, line: { color: "E2E8F0", width: 1 } });
  s.addText(c[1], { x: 4.55, y: y + 0.12, w: 8.0, h: 0.75, fontFace: BF, fontSize: 13.5, color: INK, valign: "middle", lineSpacingMultiple: 1.15 });
});
footer(s, 9);

// ---------------- 10. 演示 + 致谢(深色) ----------------
s = p.addSlide();
s.background = { color: NAVY };
s.addShape(p.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.18, fill: { color: TEAL } });
s.addText("现场演示", { x: 0.9, y: 0.8, w: 11, h: 0.6, fontFace: HF, fontSize: 30, bold: true, color: WHITE });
const demo = ["确定性档位:一条命令,零点几秒出 XML,当场跑 DTD 校验显示通过",
  "打开样例2 的\"图片表\":加 --llm local 后,模型看图重建出规范表格",
  "拿样式全无的样例5:照样能分章节、认作者、重建制表符表",
  "关掉模型也能产出完整合规 XML——不依赖外网、可复现"];
demo.forEach((d, i) => {
  const y = 2.05 + i * 1.0;
  s.addText(String(i + 1), { x: 0.9, y, w: 0.5, h: 0.5, fontFace: HF, fontSize: 18, bold: true, color: NAVY, align: "center", valign: "middle", fill: { color: MINT }, shape: p.shapes.OVAL });
  s.addText(d, { x: 1.6, y, w: 11, h: 0.5, fontFace: BF, fontSize: 15, color: "E6EDF7", valign: "middle" });
});
s.addText("谢谢　·　欢迎提问", { x: 0.9, y: 6.45, w: 11.5, h: 0.6, fontFace: HF, fontSize: 24, bold: true, color: MINT });

p.writeFile({ fileName: "../决赛答辩.pptx" }).then(f => console.log("已生成:", f));
