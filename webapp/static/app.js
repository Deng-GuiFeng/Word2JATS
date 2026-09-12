"use strict";

const $ = (id) => document.getElementById(id);
const body = document.body;

const form = $("convert-form");
const drop = $("drop");
const docxInput = $("docx-input");
const submitBtn = $("submit-btn");
const dropTitle = $("drop-title");
const dropHint = $("drop-hint");

let pollTimer = null;
let tickTimer = null;
let startedAt = 0;

// ---- 三态切换（upload / progress / result / error） ----
function setState(s) {
  body.setAttribute("data-state", s);
  window.scrollTo({ top: 0, behavior: "auto" });
}

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
    /* 拉不到不影响主流程：留空即自动识别 */
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
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); })
);
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f && f.name.toLowerCase().endsWith(".docx")) {
    docxInput.files = e.dataTransfer.files;
    onDocxChosen();
  }
});

// ---- 分片上传参数 ----
// 依据：本服务经 cloudflared 隧道对外，隧道无法灰云绕过 Cloudflare，整文件单请求在弱网/高
// 时延下会撞 CF ~100s 超时(524)或中途断连(Failed to fetch/502)。故切成有界数据块，断了只重传
// 一块；限并发在丢包链路上叠带宽；块大小 2MiB（256KB 整数倍，弱网也远小于 100s）。
// 数据块默认 2MiB，可用 window.__W2J_CHUNK_SIZE__ 覆盖（运维调优 / 测试触发多块）。
// 2MiB 而非 4MiB：弱网下单块传输时间减半→中途断连的浪费与概率减半、超时余量翻倍、
// 进度更细腻；代价（请求数增多）在限并发下被 RTT 重叠掩盖，实测总耗时无明显差异。
const CHUNK_SIZE = Number(window.__W2J_CHUNK_SIZE__) || 2 * 1024 * 1024;
const UP_CONCURRENCY = 3;
const UP_MAX_RETRY = 6;

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// 整包 SHA-256（服务端据此校验完整性）；过大或不支持则跳过，服务端仍校验字节数
async function sha256Hex(file) {
  if (file.size > 64 * 1024 * 1024 || !(crypto.subtle)) return "";
  try {
    const d = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch (e) { return ""; }
}

function chunkBlob(file, i) {
  return file.slice(i * CHUNK_SIZE, Math.min((i + 1) * CHUNK_SIZE, file.size));
}

// 单片上传：失败按指数退避 + 抖动重试，治链路中途断连
async function putChunk(uploadId, i, blob, onOne, onRetry) {
  let attempt = 0;
  for (;;) {
    try {
      const r = await fetch("/api/upload/" + uploadId + "/" + i, { method: "PUT", body: blob });
      if (!r.ok) throw new Error("HTTP " + r.status);
      if (onOne) onOne();
      return;
    } catch (e) {
      if (++attempt > UP_MAX_RETRY) throw new Error("第 " + (i + 1) + " 个数据块多次重试仍失败（" + e.message + "）");
      if (onRetry) onRetry(i, attempt);   // 弱网重试期间给用户反馈，别让进度条看着像卡死
      await sleep(Math.min(8000, 500 * 2 ** (attempt - 1)) + Math.floor(Math.random() * 400));
    }
  }
}

async function uploadChunked(file, doi, journal, onProgress) {
  const total = Math.ceil(file.size / CHUNK_SIZE);

  const initFd = new FormData();
  initFd.append("filename", file.name);
  initFd.append("size", String(file.size));
  initFd.append("total_chunks", String(total));
  let ir;
  try { ir = await fetch("/api/upload/init", { method: "POST", body: initFd }); }
  catch (e) { throw new Error("网络没连上：" + e.message); }
  if (!ir.ok) throw new Error(await safeErr(ir));
  const uploadId = (await ir.json()).upload_id;

  // 限并发 + 逐片重试
  let done = 0;
  const bump = () => onProgress(++done, total);
  const onRetry = (i, k) => {
    const s = $("upload-sub");
    if (s) s.textContent = "网络不稳，正在重传第 " + (i + 1) + " 个数据块（第 " + k + " 次重试）…";
  };
  let cursor = 0;
  async function worker() {
    for (;;) {
      const i = cursor++;
      if (i >= total) return;
      await putChunk(uploadId, i, chunkBlob(file, i), bump, onRetry);
    }
  }
  await Promise.all(Array.from({ length: Math.min(UP_CONCURRENCY, total) }, worker));

  const sha = await sha256Hex(file);

  // 完成；若服务端报缺片则补传后重试（断点续传）
  for (let round = 0; round < 4; round++) {
    const cf = new FormData();
    cf.append("doi", doi);
    cf.append("journal", journal);
    if (sha) cf.append("sha256", sha);
    let cr;
    try { cr = await fetch("/api/upload/" + uploadId + "/complete", { method: "POST", body: cf }); }
    catch (e) { throw new Error("网络没连上：" + e.message); }
    if (cr.ok) return (await cr.json()).task_id;
    if (cr.status === 409) {
      let missing = [];
      try { const j = await cr.json(); missing = (j.detail && j.detail.missing) || []; } catch (e) { /* */ }
      if (missing.length) {
        for (const i of missing) await putChunk(uploadId, i, chunkBlob(file, i), null, onRetry);
        continue;
      }
    }
    throw new Error(await safeErr(cr));
  }
  throw new Error("上传未能完成，请重试");
}

// ---- 提交转换 ----
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = docxInput.files[0];
  if (!file) return;

  setState("progress");
  $("progress-file").textContent = file.name;
  resetStepper();
  startedAt = Date.now();
  startTick();
  showUpload(0, Math.ceil(file.size / CHUNK_SIZE));

  let taskId;
  try {
    taskId = await uploadChunked(
      file, $("doi-input").value.trim(), $("journal-input").value,
      (d, t) => showUpload(d, t));
  } catch (err) {
    return fail("上传失败：" + err.message);
  }
  hideUpload();
  poll(taskId);
});

// ---- 轮询状态 ----
function poll(taskId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    let r;
    try { r = await fetch("/api/status/" + taskId); }
    catch (err) { return; }
    if (!r.ok) return;
    const s = await r.json();
    updateStepper(s.stage_key);
    if (s.status === "done") {
      clearInterval(pollTimer); stopTick(); loadResult(taskId);
    } else if (s.status === "error") {
      clearInterval(pollTimer); stopTick(); fail(s.error || "转换失败");
    }
  }, 1000);
}

// ---- 展示结果 ----
async function loadResult(taskId) {
  let r;
  try { r = await fetch("/api/result/" + taskId); }
  catch (err) { return fail("取结果失败：" + err.message); }
  if (!r.ok) return fail(await safeErr(r));
  const data = await r.json();

  const v = data.validation || {};
  const st = data.stats || {};
  const fid = data.fidelity;
  const checks = data.checks || [];
  const articleId = data.article_id || "article";
  const doi = (st && st.doi) || ""; // 一般无；从 xml/结果里推不到时留空

  buildVerdict(v, checks, fid, st, taskId, articleId);
  buildProofSlug(articleId, data.xml);
  buildNotice(data.notice);

  $("preview-label").textContent = articleId + " · 排版预览";
  $("render-frame").src = "/api/render/" + taskId;

  buildFidelity(fid);
  buildInventory(st);
  buildCompliance(v, checks);

  $("xml-fname").textContent = articleId + ".xml";
  $("xml-view").innerHTML = highlightXml(data.xml || "");

  selectTab($("t1"));
  setState("result");
}

// ---- 结论条：能不能放行 ----
function buildVerdict(v, checks, fid, st, taskId, articleId) {
  const dtdOk = v.dtd_valid === true;
  const highN = checks.filter((c) => c.severity === "high").length;
  const blocking = (dtdOk ? 0 : 1) + highN;
  const problem = blocking > 0;

  body.setAttribute("data-view", problem ? "problem" : "pass");
  $("stamp-main").textContent = problem ? "待确认" : "可交付";
  $("verdict-line").textContent = problem
    ? "转换完成，有 " + blocking + " 处需要确认后再交付。"
    : "转换完成，可以直接交付。";

  const badges = $("verdict-badges");
  badges.innerHTML = "";
  if (fid && typeof fid.from_source_pct === "number") badges.appendChild(badge(false, "原文一致"));
  if (!problem) badges.appendChild(badge(false, "符合出版标准"));
  else badges.appendChild(badge(true, "合规：" + blocking + " 处待处理"));

  $("verdict-elapsed").textContent = st.elapsed_sec != null ? "耗时 " + st.elapsed_sec + "s" : "";
  $("download-btn").href = "/api/download/" + taskId;
  $("download-meta").textContent = articleId + ".zip";
}

function badge(warn, text) {
  const b = document.createElement("span");
  b.className = "badge" + (warn ? " warn" : "");
  b.textContent = text;
  return b;
}

function buildProofSlug(articleId, xml) {
  const slug = $("proof-slug");
  const doi = xmlDoi(xml);
  const parts = [];
  if (articleId && articleId !== "article") parts.push('文章号 <b>' + esc(articleId) + "</b>");
  if (doi) parts.push("DOI <b>" + esc(doi) + "</b>");
  if (!parts.length) { slug.hidden = true; return; }
  slug.innerHTML = parts.map((p) => "<span>" + p + "</span>").join("");
  slug.hidden = false;
}

function xmlDoi(xml) {
  if (!xml) return "";
  const m = xml.match(/pub-id-type="doi"[^>]*>([^<]+)</);
  return m ? m[1].trim() : "";
}

function buildNotice(notice) {
  const el = $("result-notice");
  if (notice) { el.textContent = notice; el.hidden = false; }
  else el.hidden = true;
}

// ---- §2 原文核对 ----
function buildFidelity(fid) {
  const host = $("fidelity-view");
  if (!fid) { host.innerHTML = '<p class="empty-hint">原文核对数据暂不可用。</p>'; return; }

  const nExtra = fid.n_extra != null ? fid.n_extra : (fid.extra_words || []).length;
  const nMiss = fid.n_missing != null ? fid.n_missing : (fid.missing_words || []).length;

  const lead = (nExtra === 0 && nMiss === 0)
    ? "正文整段照搬、不经过模型，逐词比对完全一致，没有多出或少掉的词。"
    : "正文整段照搬、不经过模型，<b>没有成句改写</b>。下面列出逐词比对里两边对不上的词，供你逐一核对。";
  let html = '<p class="panel-lead">' + lead + "</p>";

  html += '<div class="bars">' +
    barBlock("生成的 XML 里，来自原稿的词", fid.from_source_pct, "越接近 100%，说明正文越是原样搬过来的，没有改写") +
    barBlock("原稿里的词，保留进了 XML", fid.kept_pct, "没到 100% 的部分见下方「少掉的词」，可逐词核对") +
    "</div>";

  const dExtra = (fid.extra_words && fid.extra_words.length)
    ? detailBlock("多出的词（" + nExtra + " 个）", nExtra, fid.extra_words, "",
        "XML 里有、原稿里没有：多为系统按出版规范补的刊名、ISSN、版权与开放获取声明，也可能夹带个别切词边界差异。") : "";
  const dMiss = (fid.missing_words && fid.missing_words.length)
    ? detailBlock("少掉的词（" + nMiss + " 个）", nMiss, fid.missing_words, "drop",
        "原稿里有、XML 里没有：可能是做成图片随图片转走的表格/图注文字，或作者单位、通讯地址、ORCID 等改成了结构化字段著录，也可能有个别切词差异。逐词核对，确认不是正文被漏掉。") : "";
  if (dExtra || dMiss) html += '<div class="details-grid">' + dExtra + dMiss + "</div>";

  host.innerHTML = html;
  requestAnimationFrame(() => {
    host.querySelectorAll(".fill").forEach((f) => { f.style.width = f.dataset.w; });
  });
}

function barBlock(label, pct, sub) {
  const p = (typeof pct === "number") ? pct : 0;
  return '<div class="bar"><div class="bar-label"><span class="txt">' + esc(label) +
    '</span><span class="pct">' + p + '%</span></div>' +
    '<div class="track"><div class="fill" data-w="' + Math.max(0, Math.min(100, p)) + '%"></div></div>' +
    '<div class="bar-sub">' + esc(sub) + "</div></div>";
}

function detailBlock(summary, count, words, cls, note) {
  const chips = words.map((w) => '<span class="chip ' + cls + '">' + esc(w) + "</span>").join("");
  const noteHtml = note ? '<p class="fid-note">' + esc(note) + "</p>" : "";
  return '<details class="detail"><summary>' + esc(summary) + '<span class="tag">' + count +
    ' 词</span></summary>' + noteHtml + '<div class="chips">' + chips + "</div></details>";
}

// ---- §3 内容清单 ----
function buildInventory(st) {
  // stats.formulas 现为整数；兼容旧的 {inline, disp} 形态。
  const f = st.formulas;
  const nFormula = (typeof f === "number")
    ? f : (((f && f.inline) || 0) + ((f && f.disp) || 0));
  const nRef = st.references || 0;
  const nRefStruct = st.refs_structured || 0;

  const groups = [
    { no: "A", name: "文章信息",
      prompt: "对照原稿封面，核对作者顺序、单位对应、关键词是否齐全。",
      items: [["作者", st.authors], ["单位", st.affiliations], ["关键词", st.keywords], ["摘要小节", st.abstract_sections]] },
    { no: "B", name: "正文",
      prompt: "对照原稿核对分节、图表公式的数量和位置；正文引用都已指到对应的图表和文献。",
      items: [["正文分节", st.body_sections], ["图", st.figures_exported], ["表", st.tables],
              ["公式", nFormula], ["图表 / 文献的正文引用", st.xrefs]] },
    { no: "C", name: "参考文献",
      prompt: "共 " + nRef + " 条，其中 " + nRefStruct + " 条已拆成可检索字段（作者 / 年份 / 期刊…），其余保留原样著录。",
      wide: [nRef, "条参考文献 · " + nRefStruct + " 条已拆成可检索字段"] },
  ];

  let html = "";
  for (const g of groups) {
    html += '<div class="clist-group"><div class="clist-head"><span class="clist-name">' +
      '<span class="gno">' + g.no + "</span>" + esc(g.name) + "</span>" +
      '<span class="clist-prompt">' + esc(g.prompt) + "</span></div><div class=\"stat-grid\">";
    if (g.wide) {
      html += '<div class="stat wide"><div class="num">' + g.wide[0] + '</div><div class="lab">' + esc(g.wide[1]) + "</div></div>";
    } else {
      for (const [lab, num] of g.items) {
        if (num == null) continue;
        html += '<div class="stat"><div class="num">' + num + '</div><div class="lab">' + esc(lab) + "</div></div>";
      }
    }
    html += "</div></div>";
  }
  $("inventory-view").innerHTML = html;
}

// ---- §4 合规检查 ----
function buildCompliance(v, checks) {
  const host = $("compliance-view");
  const dtdOk = v.dtd_valid === true;
  const highN = checks.filter((c) => c.severity === "high").length;
  const blocking = (dtdOk ? 0 : 1) + highN;

  if (dtdOk && checks.length === 0) {
    host.innerHTML =
      '<div class="pass-callout"><div class="pass-mark">✓</div>' +
      '<div class="pass-text">符合通用出版标准（JATS），可直接进入出版流程，PubMed、知网、CrossRef 等平台可接收。</div></div>' +
      '<div class="compliance-note">JATS 1.3 · 结构 / 引用 / 元数据 全部校验通过</div>';
    return;
  }

  const sev = { high: ["need", "需处理"], medium: ["suggest", "建议处理"], low: ["note", "提示"] };
  let issues = "";
  if (!dtdOk) {
    const errs = (v.errors || []).slice(0, 20);
    if (!errs.length) issues += issueRow("need", "需处理", "未通过出版标准检查。", "");
    errs.forEach((e) => issues += issueRow("need", "需处理", "结构不符合出版标准", e));
  }
  checks.forEach((c) => {
    const s = sev[c.severity] || ["note", "提示"];
    issues += issueRow(s[0], s[1], c.detail || "", c.code ? "检查项 " + c.code : "");
  });

  const note = blocking > 0
    ? "JATS 1.3 · " + blocking + " 处待确认 · 通过后可交付"
    : "JATS 1.3 · 均为建议项，可直接交付";
  host.innerHTML = '<div class="issues">' + issues + '</div><div class="compliance-note">' + esc(note) + "</div>";
}

function issueRow(cls, lvl, title, bodyRaw) {
  let bodyHtml = "";
  if (bodyRaw) {
    // 把裸露的检查码/标签用 <code> 包一层，其余转义
    bodyHtml = '<div class="issue-body">' + esc(bodyRaw) + "</div>";
  }
  return '<div class="issue ' + cls + '"><span class="lvl">' + esc(lvl) + "</span>" +
    '<div><div class="issue-title">' + esc(title) + "</div>" + bodyHtml + "</div></div>";
}

// ---- §5 XML 行号高亮（转义后着色，安全） ----
function highlightXml(xml) {
  if (!xml) return '<span class="ln">（无内容）</span>';
  const esc0 = xml.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc0.split("\n").map((line) => {
    let h = line
      .replace(/&lt;!--[\s\S]*?--&gt;/g, (m) => '<span class="t-com">' + m + "</span>")
      .replace(/(&lt;[?!/]?)([A-Za-z][\w:.-]*)([\s\S]*?)(\/?&gt;)/g, (m, open, name, attrs, close) => {
        const a = attrs.replace(/([\w:.-]+)=("[^"]*")/g,
          (mm, k, val) => '<span class="t-attr">' + k + '</span><span class="t-punct">=</span><span class="t-val">' + val + "</span>");
        return '<span class="t-punct">' + open + '</span><span class="t-tag">' + name + "</span>" + a +
          '<span class="t-punct">' + close + "</span>";
      });
    return '<span class="ln">' + (h.length ? h : " ") + "</span>";
  }).join("");
}

// ---- 页签切换 + roving tabindex + 方向键 ----
const tabs = Array.from(document.querySelectorAll(".tab"));
function selectTab(tab) {
  tabs.forEach((t) => {
    const on = t === tab;
    t.setAttribute("aria-selected", on ? "true" : "false");
    t.tabIndex = on ? 0 : -1;
    const panel = $(t.getAttribute("aria-controls"));
    panel.classList.toggle("show", on);
    panel.hidden = !on;
  });
}
tabs.forEach((tab, i) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (e) => {
    let n = null;
    if (e.key === "ArrowRight") n = tabs[(i + 1) % tabs.length];
    else if (e.key === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
    else if (e.key === "Home") n = tabs[0];
    else if (e.key === "End") n = tabs[tabs.length - 1];
    if (n) { e.preventDefault(); selectTab(n); n.focus(); }
  });
});

// ---- 上传进度条（分片阶段，服务端还没开始转换）----
function showUpload(done, total) {
  const box = $("upload-progress");
  if (box) box.hidden = false;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const p = $("upload-pct"); if (p) p.textContent = pct + "%";
  const f = $("upload-fill"); if (f) f.style.width = pct + "%";
  const s = $("upload-sub"); if (s) s.textContent = "已上传 " + done + " / " + total + " 个数据块";
  updateStepper("parse");   // 上传期间点亮“读取 Word 稿件”
}
function hideUpload() {
  const box = $("upload-progress"); if (box) box.hidden = true;
}

// ---- 阶段步骤条 ----
const STEPS = ["parse", "understand", "render", "validate"];
function resetStepper() {
  document.querySelectorAll("#stepper li").forEach((li) => li.classList.remove("done", "active"));
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
    $("progress-timer").textContent = ((Date.now() - startedAt) / 1000).toFixed(1) + "s";
  }, 100);
}
function stopTick() { clearInterval(tickTimer); tickTimer = null; }

// ---- 失败 ----
function fail(msg) {
  clearInterval(pollTimer); stopTick();
  $("error-msg").textContent = msg;
  setState("error");
}
$("retry-btn").addEventListener("click", () => setState("upload"));

async function safeErr(res) {
  try { const j = await res.json(); return j.detail || ("服务返回 " + res.status); }
  catch (e) { return "服务返回 " + res.status; }
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

loadJournals();
