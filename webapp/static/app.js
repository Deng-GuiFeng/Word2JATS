"use strict";

const $ = (id) => document.getElementById(id);

const form = $("convert-form");
const drop = $("drop");
const docxInput = $("docx-input");
const figuresInput = $("figures-input");
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

// 图片包：更新自定义文件名显示
figuresInput.addEventListener("change", () => {
  const f = figuresInput.files[0];
  const pick = figuresInput.closest(".file-pick");
  const nameEl = $("figures-name");
  if (f) {
    nameEl.textContent = f.name;
    pick.classList.add("has-file");
  } else {
    nameEl.textContent = "未选择";
    pick.classList.remove("has-file");
  }
});

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
  if (figuresInput.files[0]) fd.append("figures", figuresInput.files[0]);
  fd.append("doi", $("doi-input").value.trim());
  fd.append("journal", $("journal-input").value);

  showOnly(progressCard);
  $("progress-file").textContent = docxInput.files[0].name;
  $("progress-stage").textContent = "提交中";
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
    $("progress-stage").textContent = s.stage;
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
  const banner = $("result-banner");
  if (v.dtd_valid) {
    banner.className = "banner ok";
    banner.textContent = "转换完成，产出的 JATS XML 通过 DTD 校验（结构合法）。";
  } else {
    banner.className = "banner bad";
    banner.textContent = "转换完成，但 XML 未通过 DTD 校验，请查看原文核对。";
  }

  const st = data.stats || {};
  const parts = [];
  if (st.body_sections != null) parts.push(st.body_sections + " 节正文");
  if (st.references != null) parts.push(st.references + " 条参考文献");
  if (st.figures_exported != null) parts.push(st.figures_exported + " 图");
  if (st.tables != null) parts.push(st.tables + " 表");
  if (st.elapsed_sec != null) parts.push(st.elapsed_sec + "s");
  $("result-meta").textContent = parts.join(" · ");

  $("download-btn").href = "/api/download/" + taskId;
  $("xml-view").textContent = data.xml || "（无内容）";

  showOnly(resultCard);
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
