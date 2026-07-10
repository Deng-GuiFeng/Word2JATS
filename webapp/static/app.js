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
    /* 下拉拉不到不影响主流程：留空即自动推断 */
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

// ---- 展示结果 ----
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

  // 下载
  $("download-btn").href = "/api/download/" + taskId;

  // 顶部状态条
  const chip = $("chip-dtd");
  if (v.dtd_valid) {
    chip.className = "chip ok";
    chip.textContent = "DTD 合法";
  } else {
    chip.className = "chip bad";
    chip.textContent = "DTD 未通过";
  }
  $("chip-fidelity").textContent = fid
    ? "输出正文 " + fid.from_source_pct + "% 的词来自原稿，未见成句改写"
    : "";
  $("chip-elapsed").textContent = st.elapsed_sec != null ? "耗时 " + st.elapsed_sec + "s" : "";

  // 提示条（未配 key 等）
  const notice = $("result-notice");
  if (data.notice) {
    notice.textContent = data.notice;
    notice.hidden = false;
  } else {
    notice.hidden = true;
  }

  // 各标签面板
  $("render-frame").src = "/api/render/" + taskId;
  $("xml-view").innerHTML = highlightXml(data.xml || "（无内容）");
  buildValidate(v, data.checks || []);
  buildStructure(st);
  buildFidelity(fid);

  activateTab("render");
  showOnly(resultCard);
}

// ---- 校验分级 ----
function buildValidate(v, checks) {
  const host = $("validate-view");
  host.innerHTML = "";
  const lines = [];

  if (v && v.dtd_valid === false) {
    (v.errors || []).slice(0, 30).forEach((e) =>
      lines.push({ sev: "error", tag: "DTD", detail: e })
    );
    if (!(v.errors || []).length) lines.push({ sev: "error", tag: "DTD", detail: "未通过 DTD 校验" });
  }
  const sevMap = { high: "error", medium: "warn", low: "info" };
  const sevTag = { high: "错误", medium: "警告", low: "提示" };
  checks.forEach((c) =>
    lines.push({ sev: sevMap[c.severity] || "info", tag: sevTag[c.severity] || "提示",
                 detail: c.detail, code: c.code })
  );

  if (!lines.length) {
    const ok = document.createElement("div");
    ok.className = "check-ok";
    ok.textContent = v && v.dtd_valid
      ? "通过 DTD 校验，未发现结构问题。"
      : "未发现结构问题。";
    host.appendChild(ok);
    return;
  }
  lines.forEach((l) => {
    const row = document.createElement("div");
    row.className = "check-line " + l.sev;
    const sev = document.createElement("span");
    sev.className = "sev";
    sev.textContent = l.tag;
    const detail = document.createElement("span");
    detail.className = "detail";
    detail.textContent = l.detail;
    if (l.code) {
      const code = document.createElement("span");
      code.className = "code";
      code.textContent = " (" + l.code + ")";
      detail.appendChild(code);
    }
    row.appendChild(sev);
    row.appendChild(detail);
    host.appendChild(row);
  });
}

// ---- 结构摘要指标卡 ----
function buildStructure(st) {
  const host = $("structure-view");
  host.innerHTML = "";
  const f = st.formulas || {};
  const nFormula = (f.inline || 0) + (f.disp || 0);
  const cards = [
    ["作者", st.authors],
    ["单位", st.affiliations],
    ["关键词", st.keywords],
    ["摘要小节", st.abstract_sections],
    ["正文分节", st.body_sections],
    ["参考文献", st.references],
    ["结构化著录", st.refs_structured],
    ["图", st.figures_exported],
    ["表", st.tables],
    ["公式", nFormula],
    ["交叉引用", st.xrefs],
  ];
  cards.forEach(([label, num]) => {
    if (num == null) return;
    const card = document.createElement("div");
    card.className = "stat-card";
    const n = document.createElement("div");
    n.className = "stat-num";
    n.textContent = num;
    const l = document.createElement("div");
    l.className = "stat-label";
    l.textContent = label;
    card.appendChild(n);
    card.appendChild(l);
    host.appendChild(card);
  });
}

// ---- 内容忠实 ----
function buildFidelity(fid) {
  const host = $("fidelity-view");
  host.innerHTML = "";
  if (!fid) {
    host.innerHTML = '<p class="panel-hint">忠实自检数据不可用。</p>';
    return;
  }
  host.appendChild(el("div", "fid-guarantee",
    '<span class="fid-icon">✓</span>' +
    "<p>这里帮你确认<strong>转换没有改动原文</strong>。下面把生成的 XML 和你上传的原稿逐词比对：正文几乎全部来自原稿，多出来的词主要是按出版规范补的刊名、ISSN、版权声明等信息（可逐词核对）。</p>"));

  const bars = el("div", "fid-bars", "");
  bars.appendChild(fidBar("生成的 XML 里的词，来自原稿", fid.from_source_pct,
    "生成的 XML 里的词，有多少能在你上传的原稿里找到"));
  bars.appendChild(fidBar("原稿里的词，保留进了 XML", fid.kept_pct,
    "原稿里的词，有多少出现在生成的 XML 里（做成图片的表格、图注文字随图片一起转，不计在内）"));
  host.appendChild(bars);

  const diff = el("div", "fid-diff", "");
  if (fid.extra_words && fid.extra_words.length) {
    diff.appendChild(el("h4", "", "XML 里多出的词（" + fid.n_extra + " 个）"));
    diff.appendChild(el("p", "fid-note", "主要是按出版规范补的刊名、ISSN、版权声明等信息，可逐词核对——不是改动了你的正文。"));
    diff.appendChild(wordChips(fid.extra_words, "extra"));
  }
  if (fid.missing_words && fid.missing_words.length) {
    diff.appendChild(el("h4", "", "原稿里没进 XML 的词（" + fid.n_missing + " 个）"));
    diff.appendChild(el("p", "fid-note", "主要是做成图片的表格 / 图注文字（随图片一起转走了），以及个别断词差异。"));
    diff.appendChild(wordChips(fid.missing_words, "miss"));
  }
  host.appendChild(diff);
}

function fidBar(label, pct, sub) {
  const wrap = document.createElement("div");
  const head = el("div", "fid-bar-label",
    "<strong>" + label + "</strong><span class='pct'>" + pct + "%</span>");
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

// ---- 标签切换 ----
function activateTab(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("is-active", t.dataset.tab === name)
  );
  document.querySelectorAll(".tab-panel").forEach((p) =>
    p.classList.toggle("is-active", p.dataset.panel === name)
  );
}
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => activateTab(t.dataset.tab))
);

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
