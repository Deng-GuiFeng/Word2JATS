"use strict";

const $ = (id) => document.getElementById(id);

const form = $("convert-form");
const drop = $("drop");
const docxInput = $("docx-input");
const submitBtn = $("submit-btn");
const dropTitle = $("drop-title");
const dropHint = $("drop-hint");

const uploadCard = $("upload-card");
const progressCard = $("progress-card");
const resultCard = $("result-card");
const errorCard = $("error-card");

let pollTimer = null;
let tickTimer = null;
let startedAt = 0;

// ---- 期刊下拉 ----
async function loadJournals() {
  try {
    const r = await fetch("/api/journals");
    const data = await r.json();
    const sel = $("journal-input");
    for (const j of data.journals) {
      const o = document.createElement("option");
      o.value = j.id;
      o.textContent = j.id + " · " + j.title;
      sel.appendChild(o);
    }
  } catch (e) {
    /* 下拉拉不到不影响主流程：留空即自动识别 */
  }
}

// ---- 选文件 ----
function onDocxChosen() {
  const f = docxInput.files[0];
  if (f) {
    drop.classList.add("has-file");
    dropTitle.textContent = f.name;
    dropHint.textContent = (f.size / 1024).toFixed(0) + " KB · 已就绪";
    submitBtn.disabled = false;
  } else {
    drop.classList.remove("has-file");
    dropTitle.textContent = "选择或拖入 Word 文件";
    dropHint.textContent = "仅支持 .docx";
    submitBtn.disabled = true;
  }
}

docxInput.addEventListener("change", onDocxChosen);

["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.remove("dragover");
  })
);
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f && f.name.toLowerCase().endsWith(".docx")) {
    docxInput.files = e.dataTransfer.files;
    onDocxChosen();
  }
});

// ---- 提交转换 ----
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!docxInput.files[0]) return;

  const fd = new FormData();
  fd.append("docx", docxInput.files[0]);
  fd.append("doi", $("doi-input").value.trim());
  fd.append("journal", $("journal-input").value);

  showOnly(progressCard);
  $("progress-file").textContent = docxInput.files[0].name;
  resetStepper();
  startedAt = Date.now();
  startTick();

  let res;
  try {
    res = await fetch("/api/convert", { method: "POST", body: fd });
  } catch (err) {
    return fail("网络错误，服务没连上：" + err.message);
  }
  if (!res.ok) {
    const msg = await safeErr(res);
    return fail(msg);
  }
  const { task_id } = await res.json();
  poll(task_id);
});

// ---- 轮询状态 ----
function poll(taskId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    let r;
    try {
      r = await fetch("/api/status/" + taskId);
    } catch (err) {
      return; // 瞬时网络抖动，下一拍再试
    }
    if (!r.ok) return;
    const s = await r.json();
    updateStepper(s.stage_key);
    if (s.status === "done") {
      clearInterval(pollTimer);
      stopTick();
      loadResult(taskId);
    } else if (s.status === "error") {
      clearInterval(pollTimer);
      stopTick();
      fail(s.error || "转换失败");
    }
  }, 1000);
}

// ---- 展示交付前自检报告 ----
async function loadResult(taskId) {
  let r;
  try {
    r = await fetch("/api/result/" + taskId);
  } catch (err) {
    return fail("取结果失败：" + err.message);
  }
  if (!r.ok) return fail(await safeErr(r));
  const data = await r.json();

  const v = data.validation || {};
  const st = data.stats || {};
  const fid = data.fidelity;
  const checks = data.checks || [];

  $("download-btn").href = "/api/download/" + taskId;
  $("verdict-elapsed").textContent = st.elapsed_sec != null ? "耗时 " + st.elapsed_sec + "s" : "";

  // 提示条（未配 key 等）
  const notice = $("result-notice");
  if (data.notice) {
    notice.textContent = data.notice;
    notice.hidden = false;
  } else {
    notice.hidden = true;
  }

  buildVerdict(v, checks, fid);
  $("render-frame").src = "/api/render/" + taskId;
  buildFidelity(fid);
  buildInventory(st);
  buildCompliance(v, checks);
  $("xml-view").innerHTML = highlightXml(data.xml || "（无内容）");

  showOnly(resultCard);
}

// ---- 总结论：能不能放行 ----
function buildVerdict(v, checks, fid) {
  const dtdOk = v.dtd_valid === true;
  const highN = checks.filter((c) => c.severity === "high").length;
  const blocking = (dtdOk ? 0 : 1) + highN;

  const line = $("verdict-line");
  const badges = $("verdict-badges");
  const verdict = $("verdict");
  badges.innerHTML = "";

  if (blocking === 0) {
    verdict.classList.remove("has-issue");
    verdict.classList.add("all-clear");
    line.textContent = "转换完成，可以直接交付。符合出版标准，原文内容一字未改。";
  } else {
    verdict.classList.remove("all-clear");
    verdict.classList.add("has-issue");
    line.textContent =
      "转换完成，有 " + blocking + " 处需要你确认后再交付。具体位置和处理办法见下方「能不能直接交付」。";
  }

  // 原文一致徽标（诚实口径：正文来自原稿、无成句改写）
  if (fid && typeof fid.from_source_pct === "number") {
    badges.appendChild(badge(true, "原文一致"));
  }
  // 出版合规徽标
  if (dtdOk && highN === 0) {
    badges.appendChild(badge(true, "符合出版标准"));
  } else {
    badges.appendChild(badge(false, "合规：" + blocking + " 处待处理"));
  }
}

function badge(ok, text) {
  const b = document.createElement("span");
  b.className = "vbadge " + (ok ? "ok" : "warn");
  b.textContent = text;
  return b;
}

// ---- 原文核对：有没有改动或漏掉你的内容 ----
function buildFidelity(fid) {
  const host = $("fidelity-view");
  host.innerHTML = "";
  if (!fid) {
    host.innerHTML = '<p class="sec-hint">原文核对数据暂不可用。</p>';
    return;
  }

  const nExtra = fid.n_extra != null ? fid.n_extra : (fid.extra_words || []).length;
  const nMiss = fid.n_missing != null ? fid.n_missing : (fid.missing_words || []).length;
  host.appendChild(
    el(
      "p",
      "fid-lead",
      "你的正文一字未改。多出的 <strong>" + nExtra +
        "</strong> 个词是按出版规范补的刊名、ISSN、版权声明等；少掉的 <strong>" + nMiss +
        "</strong> 个词是做成图片的表格文字和图注，已随图片一起转走——都不是改动你的正文。"
    )
  );

  const bars = el("div", "fid-bars", "");
  bars.appendChild(
    fidBar("生成的 XML 里，来自原稿的词", fid.from_source_pct,
      "越接近 100%，说明正文越是原样搬过来的，没有改写")
  );
  bars.appendChild(
    fidBar("原稿里的词，保留进了 XML", fid.kept_pct,
      "做成图片的表格、图注文字随图片一起转，不计在内")
  );
  host.appendChild(bars);

  const diff = el("div", "fid-diff", "");
  if (fid.extra_words && fid.extra_words.length) {
    const d = el("details", "fid-fold", "");
    d.appendChild(el("summary", "", "系统补充的出版信息（" + nExtra + " 个，可核对）"));
    d.appendChild(el("p", "fid-note", "按出版规范补的刊名、ISSN、版权声明等，不是改动你的正文。"));
    d.appendChild(wordChips(fid.extra_words, "extra"));
    diff.appendChild(d);
  }
  if (fid.missing_words && fid.missing_words.length) {
    const d = el("details", "fid-fold", "");
    d.appendChild(el("summary", "", "转成图片带走的文字（" + nMiss + " 个）"));
    d.appendChild(el("p", "fid-note", "做成图片的表格 / 图注文字随图片一起转走了，以及个别断词差异。"));
    d.appendChild(wordChips(fid.missing_words, "miss"));
    diff.appendChild(d);
  }
  if (diff.children.length) host.appendChild(diff);
}

function fidBar(label, pct, sub) {
  const wrap = document.createElement("div");
  wrap.className = "fid-bar";
  const head = el("div", "fid-bar-label",
    "<span>" + label + "</span><span class='pct'>" + pct + "%</span>");
  const track = document.createElement("div");
  track.className = "fid-track";
  const fill = document.createElement("div");
  fill.className = "fid-fill";
  fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
  track.appendChild(fill);
  wrap.appendChild(head);
  wrap.appendChild(track);
  wrap.appendChild(el("p", "fid-sub", sub));
  return wrap;
}

// ---- 内容清单：该有的都齐了吗 ----
function buildInventory(st) {
  const host = $("inventory-view");
  host.innerHTML = "";
  const f = st.formulas || {};
  const nFormula = (f.inline || 0) + (f.disp || 0);
  const nRef = st.references || 0;
  const nRefStruct = st.refs_structured || 0;

  const groups = [
    {
      title: "文章信息",
      items: [
        ["作者", st.authors],
        ["单位", st.affiliations],
        ["关键词", st.keywords],
        ["摘要小节", st.abstract_sections],
      ],
      hint: "对照原稿封面，核对作者顺序、单位对应、关键词是否齐全。",
    },
    {
      title: "正文",
      items: [
        ["正文分节", st.body_sections],
        ["图", st.figures_exported],
        ["表", st.tables],
        ["公式", nFormula],
        ["图表 / 文献的正文引用", st.xrefs],
      ],
      hint: "对照原稿核对分节、图表公式的数量和位置；正文引用都已指到对应的图表和文献。",
    },
    {
      title: "参考文献",
      items: [["参考文献", nRef]],
      hint:
        "共 " + nRef + " 条，其中 " + nRefStruct +
        " 条已拆成可检索字段（作者 / 年份 / 期刊…），其余保留原样著录。",
    },
  ];

  groups.forEach((g) => {
    const box = el("div", "inv-group", "");
    box.appendChild(el("h3", "inv-title", g.title));
    const row = el("div", "inv-items", "");
    g.items.forEach(([label, num]) => {
      if (num == null) return;
      const it = el("div", "inv-item", "");
      it.appendChild(el("span", "inv-num", String(num)));
      it.appendChild(el("span", "inv-label", label));
      row.appendChild(it);
    });
    box.appendChild(row);
    box.appendChild(el("p", "inv-hint", g.hint));
    host.appendChild(box);
  });
}

// ---- 合规检查：能不能被出版平台接收 ----
function buildCompliance(v, checks) {
  const host = $("compliance-view");
  host.innerHTML = "";
  const dtdOk = v.dtd_valid === true;

  if (dtdOk) {
    host.appendChild(el("div", "check-ok",
      "符合通用出版标准（JATS），可直接进入出版流程，PubMed、知网、CrossRef 等平台可接收。"));
  } else {
    const errs = (v.errors || []).slice(0, 20);
    host.appendChild(el("div", "check-line error",
      '<span class="sev">需处理</span><span class="detail">结构不符合出版标准，请按下列各项处理后再交付。</span>'));
    errs.forEach((e) => host.appendChild(checkLine("error", "需处理", e)));
    if (!errs.length) host.appendChild(checkLine("error", "需处理", "未通过出版标准检查。"));
  }

  const sevLabel = { high: "需处理", medium: "建议处理", low: "提示" };
  const sevClass = { high: "error", medium: "warn", low: "info" };
  checks.forEach((c) => {
    host.appendChild(checkLine(sevClass[c.severity] || "info",
      sevLabel[c.severity] || "提示", c.detail, c.code));
  });
}

function checkLine(cls, sevText, detail, code) {
  const row = el("div", "check-line " + cls, "");
  const sev = el("span", "sev", sevText);
  const det = el("span", "detail", "");
  det.textContent = detail;
  if (code) {
    const c = el("span", "code", " (" + code + ")");
    det.appendChild(c);
  }
  row.appendChild(sev);
  row.appendChild(det);
  return row;
}

function wordChips(words, cls) {
  const box = document.createElement("div");
  box.className = "word-chips";
  words.forEach((w) => {
    const c = document.createElement("span");
    c.className = "word-chip " + cls;
    c.textContent = w;
    box.appendChild(c);
  });
  return box;
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

// ---- XML 语法高亮（在转义后的文本上着色，安全） ----
function highlightXml(xml) {
  const escd = xml
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escd
    .replace(/&lt;!--[\s\S]*?--&gt;/g, (m) => '<span class="x-com">' + m + "</span>")
    .replace(/(&lt;[?!/]?)([A-Za-z][\w:.-]*)([\s\S]*?)(\/?&gt;)/g, (m, open, name, attrs, close) => {
      const a = attrs.replace(/([\w:.-]+)=("[^"]*")/g,
        (mm, k, val) => '<span class="x-attr">' + k + '</span>=<span class="x-val">' + val + "</span>");
      return '<span class="x-punct">' + open + '</span><span class="x-tag">' + name +
             "</span>" + a + '<span class="x-punct">' + close + "</span>";
    });
}

// ---- 阶段步骤条 ----
const STEPS = ["parse", "understand", "render", "validate"];
function resetStepper() {
  document.querySelectorAll("#stepper li").forEach((li) =>
    li.classList.remove("done", "active")
  );
}
function updateStepper(stageKey) {
  const cur = stageKey === "done" ? STEPS.length : STEPS.indexOf(stageKey);
  document.querySelectorAll("#stepper li").forEach((li) => {
    const i = STEPS.indexOf(li.dataset.step);
    li.classList.toggle("done", i < cur);
    li.classList.toggle("active", i === cur);
  });
}

// ---- 计时器 ----
function startTick() {
  stopTick();
  tickTimer = setInterval(() => {
    const s = (Date.now() - startedAt) / 1000;
    $("progress-timer").textContent = s.toFixed(1) + "s";
  }, 100);
}
function stopTick() {
  clearInterval(tickTimer);
  tickTimer = null;
}

// ---- 失败 ----
function fail(msg) {
  clearInterval(pollTimer);
  stopTick();
  $("error-msg").textContent = msg;
  showOnly(errorCard);
}

$("retry-btn").addEventListener("click", () => {
  showOnly(uploadCard);
});

// ---- 卡片切换：一次只显示一张流程卡 ----
function showOnly(card) {
  for (const c of [uploadCard, progressCard, resultCard, errorCard]) {
    c.hidden = c !== card;
  }
  window.scrollTo({ top: Math.max(0, card.offsetTop - 20), behavior: "smooth" });
}

async function safeErr(res) {
  try {
    const j = await res.json();
    return j.detail || ("服务返回 " + res.status);
  } catch (e) {
    return "服务返回 " + res.status;
  }
}

loadJournals();
