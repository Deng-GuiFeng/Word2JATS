'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {file:null, task:'', result:null, work:null, draft:null, panel:'', dirty:false, saving:false, action:'', uploading:false, downloading:false, generation:0, selected:'', journals:[], trigger:null, outline:true};
  const providerName = value => value === 'dashscope' ? 'Qwen' : 'DeepSeek';
  const duration = W2JSession.duration;
  let storage; try { storage = localStorage; } catch { storage = null; }
  const recent = W2JSession.create(storage);
  const form = values => { const body = new FormData(); Object.entries(values).forEach(([k,v]) => body.append(k,v)); return body; };
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  document.querySelector('.skip-link').onclick = event => { event.preventDefault(); $('main').focus(); };
  async function api(path, options = {}) {
    const response = await fetch(path, {signal:AbortSignal.timeout(20000), ...options});
    let data; try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) { const e = new Error(typeof data.detail === 'string' ? data.detail : '请求未能完成，请重试。'); e.status = response.status; e.detail = data.detail; throw e; }
    return data;
  }
  const post = (path, data) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  let recentTimer;
  function show(stage) {
    clearTimeout(recentTimer);
    ['upload','progress','error','result'].forEach(id => $(id).hidden = id !== stage);
    document.body.dataset.state = stage; $('header-home').hidden = stage === 'upload';
    if (stage === 'upload') { drawRecent(); refreshRecent(); document.title = 'Word2JATS · Word 稿件结构化转换'; }
  }
  async function refreshRecent() {
    const generation = state.generation;
    const pending = recent.read().filter(row => ['pending','running'].includes(row.status));
    await Promise.all(pending.map(async row => {
      try {
        const status = await api('/api/status/' + row.id);
        if (generation !== state.generation || !recent.read().some(item => item.id === row.id)) return;
        recent.remember({...row, status:status.status, filename:status.filename || row.filename, provider:status.provider || row.provider});
      } catch (error) {
        if (error.status === 404 && generation === state.generation && recent.read().some(item => item.id === row.id)) recent.remember({...row,status:'expired'});
      }
    }));
    if (document.body.dataset.state !== 'upload' || generation !== state.generation) return;
    drawRecent();
    if (recent.read().some(row => ['pending','running'].includes(row.status))) recentTimer = setTimeout(refreshRecent, 5000);
  }
  function drawRecent() {
    const rows = recent.read(); $('recent-section').hidden = !rows.length;
    const status = {pending:'等待转换',running:'查看进度',done:'查看结果',error:'未完成',expired:'链接已失效'};
    $('recent-list').innerHTML = rows.map(row => `<div class="recent-item"><a href="#task=${row.id}" class="recent-open"><span class="recent-file-icon" aria-hidden="true">W</span><div><strong>${esc(row.title || row.filename)}</strong><span>${esc(row.title ? row.filename + ' · ' : '')}${row.provider ? providerName(row.provider) + ' · ' : ''}${row.at ? new Date(row.at).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : ''}</span></div><span class="recent-state ${row.status}">${status[row.status]} →</span></a><button class="icon-button recent-remove" data-remove="${row.id}" aria-label="移除 ${esc(row.filename)} 的记录">×</button></div>`).join('');
    document.querySelectorAll('[data-remove]').forEach(button => button.onclick = () => { recent.remove(button.dataset.remove); drawRecent(); $('recent-list').querySelector('a')?.focus(); });
  }
  window.addEventListener('storage', event => { if (event.key === W2JSession.key || event.key === null) drawRecent(); });
  function remember(status) {
    recent.remember({id:state.task, filename:status.filename, provider:status.provider || state.work?.options.provider || '', status:status.status || 'done', ...(status.created_at ? {at:status.created_at * 1000} : {})});
  }
  let toastTimer;
  function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500); }
  function dialog(title, body, confirm = '确定', extra = '') {
    if ($('action-dialog').open) return Promise.resolve(false);
    const trigger = document.activeElement;
    $('dialog-title').textContent = title; $('dialog-body').textContent = body; $('dialog-confirm').textContent = confirm; $('dialog-extra').innerHTML = extra;
    $('action-dialog').showModal(); $('dialog-cancel').focus();
    return new Promise(resolve => {
      const finish = answer => { $('action-dialog').close(); if (trigger?.isConnected) trigger.focus(); resolve(answer); };
      $('dialog-confirm').onclick = () => finish(true); $('dialog-cancel').onclick = () => finish(false);
      $('action-dialog').oncancel = event => { event.preventDefault(); finish(false); };
    });
  }
  async function discard() {
    if (state.action) { toast(state.action + '，请稍候。'); return false; }
    if (state.saving) { toast('正在保存，请稍候。'); return false; }
    if (!state.dirty) return true;
    if (!await dialog('有未保存的修改', '离开后将放弃当前输入，已保存的结果不会改变。', '放弃修改')) return false;
    state.dirty = false; savedState(); return true;
  }
  function setFile(file, multiple = false) {
    let error = '';
    if (multiple) error = '每次转换一篇稿件，请选择一个 Word 文件。';
    else if (file && !/\.docx$/i.test(file.name)) error = '请选择 .docx 格式的 Word 文件。';
    else if (file && !file.size) error = '文件为空，请重新选择。';
    else if (file && file.size > 300 * 1024 * 1024) error = '文件超过 300 MB，请缩小文件后再上传。';
    state.file = error ? null : file;
    $('upload-error').textContent = error; $('upload-error').hidden = !error;
    $('submit-btn').disabled = !state.file; $('file-actions').hidden = !state.file;
    $('drop').classList.toggle('has-file', !!state.file);
    $('drop-title').textContent = state.file ? file.name : '选择或拖入 Word 文件';
    $('drop-hint').textContent = state.file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击可更换文件` : '支持 .docx，最大 300 MB';
  }
  $('drop').onclick = () => $('docx-input').click();
  $('docx-input').onchange = event => setFile(event.target.files[0] || null);
  $('remove-file').onclick = () => { $('docx-input').value = ''; setFile(null); };
  ['dragenter','dragover'].forEach(type => $('drop').addEventListener(type, event => { event.preventDefault(); $('drop').classList.add('drag'); }));
  $('drop').ondragleave = () => $('drop').classList.remove('drag');
  $('drop').ondrop = event => { event.preventDefault(); $('drop').classList.remove('drag'); setFile(event.dataTransfer.files[0] || null, event.dataTransfer.files.length > 1); };
  function progress(stage, elapsed = 0) {
    const stages = ['parse','understand','render','validate'];
    const names = {upload:'正在上传',queued:'等待开始',parse:'正在读取文档',understand:'正在识别稿件结构',render:'正在生成 JATS 文件',validate:'正在检查结果'};
    $('progress-title').textContent = names[stage] || '正在转换稿件';
    $('progress-detail').textContent = {upload:'文件正在上传，请保持页面打开',queued:'文件已收到，轮到这篇稿件后会自动开始',parse:'读取文字、图片、表格和公式',understand:'识别文章信息、章节和引用关系',render:'整理结构并生成 XML 与图片资源',validate:'检查文件结构与内容完整性'}[stage] || '转换正在进行，请稍候';
    $('progress-timer').textContent = duration(elapsed); $('upload-progress').hidden = stage !== 'upload';
    $('progress-actions').hidden = stage === 'upload' || !state.task;
    document.querySelector('#progress .progress-card > p.help').textContent = stage === 'upload' ? '上传期间请保持页面打开。文件上传完成后，刷新页面可继续查看转换进度。' : '耗时随稿件长度和内容复杂度变化。刷新页面后可继续查看进度。';
    document.querySelectorAll('#stepper li').forEach(el => { el.classList.toggle('active', el.dataset.step === stage); el.classList.toggle('done', stages.indexOf(el.dataset.step) < stages.indexOf(stage)); });
  }
  function fail(message, expired = false) {
    show('error'); $('error-title').textContent = expired ? '未找到这次转换' : '转换未能完成';
    $('error-msg').textContent = message; $('retry-task-btn').hidden = !state.task || expired;
  }
  async function upload(event) {
    event.preventDefault(); if (!state.file || state.uploading) return;
    const values = {doi:$('doi-input').value.trim(), journal:$('journal-input').value, provider:$('provider-input').value};
    const file = state.file; state.task = ''; state.uploading = true; ++state.generation; show('progress'); progress('upload'); $('upload-progress').value = 0; $('connection-notice').hidden = true; $('progress-file').textContent = file.name;
    try {
      const chunkSize = 2 * 1024 * 1024, total = Math.ceil(file.size / chunkSize);
      const init = await api('/api/upload/init', {method:'POST', body:form({filename:file.name,size:file.size,total_chunks:total})});
      let received = 0, next = 0;
      const send = async index => {
        for (let attempt = 0; ; attempt++) {
          try { await api(`/api/upload/${init.upload_id}/${index}`, {method:'PUT', body:file.slice(index * chunkSize, (index + 1) * chunkSize)}); return; }
          catch (error) { if (attempt >= 5 || (error.status && error.status < 500 && error.status !== 429)) throw error; $('connection-notice').hidden = false; await pause(Math.min(6000, 500 * 2 ** attempt)); }
        }
      };
      await Promise.all(Array.from({length:Math.min(3,total)}, async () => { while (next < total) { const index = next++; await send(index); received++; $('upload-progress').value = received / total * 100; } }));
      if (file.size <= 64 * 1024 * 1024 && globalThis.crypto?.subtle) values.sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())), v => v.toString(16).padStart(2,'0')).join('');
      let result;
      try { result = await api(`/api/upload/${init.upload_id}/complete`, {method:'POST', body:form(values)}); }
      catch (error) { if (error.status !== 409 || !Array.isArray(error.detail?.missing)) throw error; await Promise.all(error.detail.missing.map(send)); result = await api(`/api/upload/${init.upload_id}/complete`, {method:'POST', body:form(values)}); }
      recent.remember({id:result.task_id, filename:file.name, provider:values.provider, status:'pending'});
      location.hash = `task=${result.task_id}`;
    } catch (error) { fail(error.status ? error.message : '上传中断，文件尚未完成提交。请重新上传。'); }
    finally { state.uploading = false; }
  }
  $('convert-form').onsubmit = upload;
  async function route() {
    const match = location.hash.match(/^#task=([a-f0-9]{16})$/); const generation = ++state.generation;
    if (!match) { state.task = ''; show('upload'); return; }
    state.task = match[1]; state.selected = ''; state.dirty = false; state.result = null; state.work = null; closePanelNow(); show('progress'); progress('queued'); $('connection-notice').hidden = true; $('progress-file').textContent = ''; $('download-error').hidden = true; $('outline-query').value = '';
    document.querySelectorAll('.previous-result').forEach(el => el.remove());
    let previous; try { previous = sessionStorage.getItem(`w2j-previous-${state.task}`); } catch { /* 浏览器禁用存储时仍可正常转换。 */ }
    if (/^[a-f0-9]{16}$/.test(previous || '')) {
      for (const parent of [document.querySelector('#progress .progress-card'),document.querySelector('#more-menu .menu')]) {
        const link = document.createElement('a'); link.className = 'previous-result help'; link.href = `#task=${previous}`; link.textContent = '查看重新转换前的结果'; parent.append(link);
      }
    }
    async function poll() {
      if (generation !== state.generation) return;
      try {
        const status = await api(`/api/status/${state.task}`); if (generation !== state.generation) return;
        remember(status);
        $('connection-notice').hidden = true; $('progress-file').textContent = status.filename;
        if (status.status === 'done') { await loadResult(generation); return; }
        if (status.status === 'error') { fail(status.error || '这次转换未能完成，请重试。'); return; }
        progress(status.stage_key, status.elapsed);
      } catch (error) { if (generation !== state.generation) return; if (error.status === 404) { const old = recent.read().find(row => row.id === state.task); if (old) recent.remember({...old,status:'expired'}); fail('这次转换不存在或已失效，请重新上传文件。', true); return; } $('connection-notice').hidden = false; }
      if (generation === state.generation) setTimeout(poll, 1000);
    }
    poll();
  }
  async function loadResult(generation = state.generation) {
    const task = state.task;
    let [result, work] = await Promise.all([api(`/api/result/${task}`), api(`/api/workbench/${task}`).catch(() => null)]);
    if (work && result.version && work.version !== result.version) {
      result = await api(`/api/result/${task}`);
      if (work.version !== result.version) work = null;
    }
    if (generation !== state.generation || task !== state.task) return false;
    state.result = result; state.work = work; renderResult();
    remember(result); recent.remember({id:task, title:work?.fields.title || '', status:'done'});
    return true;
  }
  function renderResult() {
    const r = state.result, w = state.work; show('result');
    $('result-filename').textContent = r.filename;
    document.title = `${r.filename} · Word2JATS`;
    const provider = r.provider || w?.options.provider || (JSON.stringify(r.stats.llm?.by_model || []).includes('deepseek') ? 'deepseek' : 'dashscope');
    $('result-meta').textContent = `${providerName(provider)} · 转换用时 ${duration(r.stats.elapsed_sec)}${w?.edited ? ' · 已保存修改' : ''}`;
    $('download-btn').href = `/api/download/${state.task}`; $('original-download').href = `/api/original/${state.task}`;
    $('restore-btn').disabled = !w?.edited;
    savedState();
    document.querySelectorAll('[data-panel="article"],[data-panel="publication"]').forEach(el => el.disabled = !w);
    const issues = [...(w?.issues || [])];
    if (r.notice && !issues.some(item => item.title === r.notice)) issues.unshift({title:r.notice,detail:'您可以从“更多”中选择重新转换，或先下载当前结果。',action:'checks'});
    $('issue-count').textContent = issues.length ? `(${issues.length})` : '';
    $('result-alert').hidden = !!w && !issues.length;
    $('result-alert').innerHTML = w ? `<p>${esc(issues[0]?.title || '')}${issues.length > 1 ? `，另有 ${issues.length - 1} 项提示` : ''}</p><button class="text-button" id="alert-action">${issues[0]?.action === 'publication' ? '补充出版信息' : '查看问题'}</button>` : '<p>编辑和目录暂时未能载入，仍可预览或下载结果。</p><button class="text-button" id="alert-action">重新载入</button>';
    $('alert-action').onclick = () => w ? openPanel(issues[0]?.action === 'publication' ? 'publication' : 'checks') : loadResult().catch(() => toast('仍未能载入，请稍后重试。'));
    drawOutline();
    $('xml-view').textContent = r.xml; $('preview-loading').hidden = false;
    $('render-frame').src = `/api/render/${state.task}?v=${w?.version || Date.now()}`;
    $('render-frame').onload = () => { $('preview-loading').hidden = true; if (state.selected) locate(state.selected); };
    setView('preview');
  }
  function savedState() {
    $('version-label').textContent = state.action || (state.saving ? '正在保存' : state.dirty ? '有未保存的修改' : state.result?.edited ? '已保存修改' : '自动转换结果');
    $('version-label').classList.toggle('unsaved', state.dirty || state.saving);
    $('reader-save-status').textContent = state.dirty ? '预览与下载显示已保存版本' : state.result?.edited ? '已更新至保存版本' : '';
  }
  function drawOutline() {
    const query = $('outline-query').value.trim().toLocaleLowerCase();
    const blocks = (state.work?.blocks || []).filter(b => b.navigation && (!query || b.label.toLocaleLowerCase().includes(query)));
    $('outline-list').innerHTML = blocks.map(b => `<button class="kind-${esc(b.kind)}" style="--depth:${Math.min(4,b.depth || 0)}" data-location="${esc(b.id)}" title="${esc(b.label)}">${esc(b.label)}</button>`).join('');
    $('outline-empty').hidden = !!blocks.length;
    $('outline-empty').textContent = query ? '没有匹配的目录项' : '此结果暂无可用目录';
    $('outline-list').querySelectorAll('button').forEach(el => el.onclick = () => { locate(el.dataset.location); if (matchMedia('(max-width:700px)').matches) toggleOutline(false); });
  }
  function toggleOutline(open = !state.outline) {
    state.outline = open; $('work-area').classList.toggle('outline-closed', !open);
    $('outline-toggle').setAttribute('aria-expanded', String(open)); $('outline-toggle').setAttribute('aria-label', open ? '收起文章目录' : '展开文章目录');
  }
  $('outline-toggle').onclick = () => toggleOutline(); $('outline-query').oninput = drawOutline;
  function setView(view) { const xml = view === 'xml'; $('xml-view').hidden = !xml; $('render-frame').hidden = xml; $('reader-caption').textContent = xml ? 'JATS XML · 已保存版本' : '转换结果'; $('preview-loading').hidden = xml || !!$('render-frame').contentDocument?.body?.innerText; ['preview','xml'].forEach(name => { $(`${name}-tab`).classList.toggle('active',name === view); $(`${name}-tab`).setAttribute('aria-pressed',String(name === view)); }); }
  $('preview-tab').onclick = () => setView('preview'); $('xml-tab').onclick = () => setView('xml');
  function locate(id) { state.selected = id; setView('preview'); $('render-frame').contentWindow?.postMessage({type:'w2j-locate',id}, location.origin); document.querySelectorAll('[data-location]').forEach(el => el.classList.toggle('active',el.dataset.location === id)); if (state.panel === 'source') drawSource(); }
  window.addEventListener('message', event => { if (event.origin === location.origin && event.source === $('render-frame').contentWindow && event.data?.type === 'w2j-select') { state.selected = event.data.id; if (state.panel === 'source') drawSource(); } });
  function closePanelNow() { if (state.panel === 'source') toggleOutline(state.beforeCompare); state.panel = ''; $('side-panel').hidden = true; $('work-area').classList.remove('has-panel','comparing'); document.querySelectorAll('[data-panel]').forEach(el => { el.classList.remove('active'); el.setAttribute('aria-pressed','false'); }); syncPanelAccess(); if (state.trigger?.isConnected) state.trigger.focus(); }
  async function closePanel() { if (await discard()) closePanelNow(); }
  $('panel-close').onclick = closePanel;
  async function openPanel(mode) {
    if (mode === state.panel) { await closePanel(); return; }
    if (!await discard()) return;
    if (state.panel === 'source') toggleOutline(state.beforeCompare);
    if (mode === 'source') { state.beforeCompare = state.outline; toggleOutline(false); }
    state.trigger = document.activeElement; state.panel = mode; state.draft = state.work ? structuredClone(state.work.fields) : null;
    $('side-panel').hidden = false; $('work-area').classList.add('has-panel'); $('panel-content').className = 'panel-content'; $('panel-footer').hidden = true;
    $('work-area').classList.toggle('comparing', mode === 'source'); $('more-menu').open = false;
    $('panel-title').textContent = {article:'文章信息',publication:'出版信息',source:'Word 原稿',checks:'检查详情',usage:'转换用量',delivery:'下载文件说明'}[mode];
    $('panel-eyebrow').textContent = ['article','publication'].includes(mode) ? '修改后保存到 XML' : mode === 'source' ? '与转换结果对照' : '当前保存版本';
    document.querySelectorAll('[data-panel]').forEach(el => { el.classList.toggle('active', el.dataset.panel === mode); el.setAttribute('aria-pressed',String(el.dataset.panel === mode)); });
    if (mode === 'article' || mode === 'publication') drawEditor();
    else if (mode === 'source') { setView('preview'); drawSource(); } else if (mode === 'checks') drawChecks(); else if (mode === 'delivery') drawDelivery(); else drawUsage();
    syncPanelAccess();
    $('panel-close').focus();
  }
  function syncPanelAccess() {
    const modal = !!state.panel && matchMedia('(max-width:850px)').matches;
    $('side-panel').setAttribute('role', modal ? 'dialog' : 'complementary');
    if (modal) $('side-panel').setAttribute('aria-modal','true'); else $('side-panel').removeAttribute('aria-modal');
    document.querySelectorAll('.site-header,.site-footer,.result-header,.result-alert,.work-toolbar,.preview-region').forEach(el => el.inert = modal);
  }
  window.addEventListener('resize',syncPanelAccess);
  document.querySelectorAll('[data-panel]').forEach(el => el.onclick = () => openPanel(el.dataset.panel));
  function drawSource(anchorOverride) {
    const block = state.work?.blocks.find(b => b.id === state.selected); const anchor = anchorOverride || block?.source_anchor || '';
    $('panel-content').className = 'panel-content source-panel';
    let frame = $('panel-content').querySelector('#source-frame');
    if (!frame || frame.dataset.task !== state.task) {
      $('panel-content').innerHTML = `<p class="source-note"><span role="status"></span> <a href="/api/original/${state.task}" download>下载 Word</a></p><iframe id="source-frame" data-task="${state.task}" title="Word 原稿内容" src="/api/source/${state.task}"></iframe>`;
      frame = $('source-frame'); frame.onload = () => { frame.dataset.loaded = 'true'; locateSource(frame); };
    }
    frame.dataset.anchor = anchor; frame.dataset.selected = state.selected;
    locateSource(frame);
  }
  function locateSource(frame) {
    if (state.panel !== 'source' || frame.dataset.task !== state.task) return;
    const note = $('panel-content').querySelector('.source-note span');
    const doc = frame.contentDocument, anchor = frame.dataset.anchor;
    if (!doc?.querySelector('.source-document')) { note.textContent = frame.dataset.loaded ? 'Word 原稿暂时无法预览，可下载 Word 查看。' : '正在载入 Word 原稿…'; return; }
    doc.querySelectorAll('.source-selected').forEach(el => el.classList.remove('source-selected'));
    const target = anchor ? doc.getElementById(anchor) : null;
    if (target) {
      target.classList.add('source-selected'); target.style.scrollMarginTop = '20px';
      frame.contentWindow.scrollTo({top:Math.max(0,target.getBoundingClientRect().top + frame.contentWindow.scrollY - 20),behavior:'instant'});
      note.textContent = '已定位到相关原稿段落，可上下滚动查看上下文。';
    } else note.textContent = frame.dataset.selected || anchor ? '此处没有精确对应位置，原稿保留在当前位置供对照。' : '在内容预览中选择段落或目录，可定位相关原稿。';
  }
  function drawChecks() {
    const r = state.result, issues = state.work?.issues || []; r.validation ||= {};
    $('panel-content').innerHTML = `${r.validation.dtd_valid ? '<p class="check-pass">当前 XML 已通过 JATS 格式检查。</p>' : '<p class="message warning">当前 XML 尚未通过 JATS 格式检查。</p>'}${issues.map((item,i) => `<div class="check-card warning"><span class="check-category">${{publication:'可在网页补充',content:'内容与原稿',format:'XML 标签结构'}[item.category] || '转换提示'}</span><h3>${esc(item.title)}</h3><p>${esc(item.detail)}</p>${item.excerpt ? `<blockquote>${esc(item.excerpt)}</blockquote>` : ''}${item.action !== 'checks' ? `<button class="text-button" data-issue="${i}">${item.action === 'publication' ? '补充出版信息' : '对照相关原稿'}</button>` : ''}</div>`).join('')}${issues.some(item => item.action === 'source') ? '<p class="help">题名、作者和期刊信息可在网页修改。正文、图表或引用结构需要调整时，可下载 XML 继续编辑，或整理 Word 后重新转换。</p>' : ''}<p class="help">格式检查针对 XML 标签与引用关系，内容仍需结合原稿判断。</p><details class="technical"><summary>查看技术详情</summary><pre>${esc(JSON.stringify({xml_format:r.validation,structure_checks:r.checks,conversion_findings:r.review_items},null,2))}</pre></details><button id="checks-xml" class="button panel-download">下载当前 XML</button>`;
    $('checks-xml').onclick = () => download('xml');
    document.querySelectorAll('[data-issue]').forEach(el => el.onclick = async () => { const issue = issues[Number(el.dataset.issue)]; if (issue.anchor) locate(issue.anchor); await openPanel(issue.action); if (issue.action === 'source') drawSource(issue.source_anchor); });
  }
  async function drawDelivery() {
    const task = state.task, generation = state.generation;
    $('panel-content').innerHTML = '<p class="help" role="status">正在读取文件信息…</p>';
    try {
      const info = await api(`/api/delivery/${task}?version=${state.result.version || ''}`);
      if (state.task !== task || state.generation !== generation || state.panel !== 'delivery') return;
      $('panel-content').innerHTML = `<p class="delivery-name">${esc(info.download_filename)}</p><p class="help">${info.edited ? '包含已保存的修改。' : '包含本次自动转换结果。'}${state.dirty ? '尚未保存的输入不在下载文件中。' : ''}</p><ul class="delivery-files">${info.files.map(file => `<li><strong>${esc(file.name)}</strong><span>${esc(file.description)}</span></li>`).join('')}</ul><p class="delivery-instruction">解压成果包后，将 figures.zip 解压到 XML 所在目录，并保留图片包内的目录结构。</p><div class="button-row"><button id="panel-download-xml" class="button">单独下载 XML</button><button id="panel-download-all" class="button primary">下载转换成果</button></div>`;
      $('panel-download-xml').onclick = () => download('xml'); $('panel-download-all').onclick = () => download('all');
    } catch (error) {
      if (state.task !== task || state.generation !== generation || state.panel !== 'delivery') return;
      $('panel-content').innerHTML = `<p class="message error">${esc(error.status ? error.message : '文件信息暂时未能载入，请重试。')}</p><button class="button panel-download" id="retry-delivery">重新载入文件信息</button>`;
      $('retry-delivery').onclick = drawDelivery;
    }
  }
  function drawUsage() {
    const llm = state.result.stats.llm || {}, usage = llm.result_usage || llm.usage || {}, added = llm.incremental_usage || llm.usage || {};
    const labels = [['input_tokens','输入'],['output_tokens','输出'],['cache_hit_tokens','输入：缓存命中'],['cache_miss_tokens','输入：未命中缓存'],['total_tokens','合计']];
    const reused = llm.reused_responses || llm.cache_hits || 0;
    const reuseNote = !reused ? '' : added.requests === 0 ? `<p class="check-pass">本次复用已有分析，没有新增消耗。${usage.available === false ? '' : '以下保留原始用量。'}</p>` : `<p class="help">本次部分复用已有分析，新增 ${Number(added.total_tokens || 0).toLocaleString('zh-CN')} Token；以下同时包含复用分析的原始用量。</p>`;
    const missingNote = usage.available === false ? '<p class="message warning">这份结果未保存原始模型用量，暂无法提供完整数值。</p>' : usage.complete === false ? '<p class="message warning">部分原始用量记录缺失，以下仅列已记录的用量。</p>' : '';
    $('panel-content').innerHTML = `<p class="usage-model">${providerName(state.result.provider || state.work?.options.provider || (JSON.stringify(llm.by_model || []).includes('deepseek') ? 'deepseek' : 'dashscope'))}</p><p class="help">Token 是模型处理文字的计量单位。输入包含缓存命中和未命中两部分，合计为输入加输出。</p>${reuseNote}${missingNote}<div class="usage-grid">${labels.map(([key,label]) => `<div class="usage-stat"><span>${label}</span><strong>${usage.available === false || usage[key] == null ? '—' : Number(usage[key]).toLocaleString('zh-CN')}</strong></div>`).join('')}</div><p class="help">用量记录随转换成果下载。手动修改文章或出版信息不增加模型用量。</p><details class="technical"><summary>模型型号</summary><p>${esc((llm.by_model || []).map(row => row.model).filter(Boolean).join(' / ') || (usage.returned_models || []).join(' / ') || '未记录具体型号')}</p></details>`;
  }
  const field = (label,path,value,area=false) => `<label>${esc(label)}${area ? `<textarea data-field="${path}" rows="3">${esc(value)}</textarea>` : `<input data-field="${path}" value="${esc(value)}">`}</label>`;
  function drawEditor() {
    const d = state.draft; if (!d) return;
    let html;
    if (state.panel === 'publication') {
      html = '<p class="help">填写后会更新当前 XML。刊名和至少一个 ISSN 用于补全期刊信息。</p><label>选择已配置期刊<select id="editor-journal"><option value="">自定义期刊信息</option>' + state.journals.map(j => `<option value="${esc(j.id)}">${esc(j.title)}</option>`).join('') + '</select></label>';
      html += field('DOI','publication.doi',d.publication.doi) + field('期刊名称','publication.title',d.publication.title) + '<div class="form-grid">' + field('印刷版 ISSN','publication.issn_print',d.publication.issn_print) + field('电子版 ISSN','publication.issn_electronic',d.publication.issn_electronic) + '</div>' + field('出版方','publication.publisher',d.publication.publisher);
    } else {
      html = '<nav class="editor-nav" aria-label="文章信息定位"><button type="button" data-edit-jump="title">题名</button><button type="button" data-edit-jump="authors">作者</button>' + (d.affiliations.length ? '<button type="button" data-edit-jump="affiliations">单位</button>' : '') + (d.contacts.length ? '<button type="button" data-edit-jump="contacts">通讯信息</button>' : '') + '</nav>' + field('文章题名','title',d.title,true) + '<section class="edit-section" id="edit-authors"><h3>作者</h3>';
      html += d.authors.map((a,i) => `<div class="author-card"><div class="author-title"><span>作者 ${i+1}</span><div class="move-buttons"><button type="button" data-move="${i}" data-direction="-1" aria-label="作者 ${i+1} 上移" ${!i || d.authors[i-1].group !== a.group ? 'disabled' : ''}>↑</button><button type="button" data-move="${i}" data-direction="1" aria-label="作者 ${i+1} 下移" ${i === d.authors.length-1 || d.authors[i+1].group !== a.group ? 'disabled' : ''}>↓</button></div></div>${a.kind === 'name' ? '<div class="form-grid">' + field('姓',`authors.${i}.surname`,a.surname) + field('名',`authors.${i}.given_names`,a.given_names) + '</div>' : field('姓名 / 团体名称',`authors.${i}.name`,a.name)}${field('ORCID',`authors.${i}.orcid`,a.orcid)}<label class="check-label"><input type="checkbox" data-field="authors.${i}.corresponding" ${a.corresponding ? 'checked' : ''}>通讯作者</label>${d.affiliations.length ? '<div><p class="field-title">所属单位</p><div class="aff-options">' + d.affiliations.map(aff => `<label class="check-label"><input type="checkbox" data-aff-author="${i}" value="${esc(aff.id)}" ${a.affiliations.includes(aff.id) ? 'checked' : ''}>${esc(aff.label ? aff.label + ' · ' : '')}${esc(aff.text)}</label>`).join('') + '</div></div>' : ''}</div>`).join('') + '</section>';
      if (d.affiliations.length) html += '<section class="edit-section" id="edit-affiliations"><h3>单位</h3>' + d.affiliations.map((aff,i) => field(`单位 ${aff.label || i+1}`,`affiliations.${i}.text`,aff.text,true)).join('') + '</section>';
      if (d.contacts.length) html += '<section class="edit-section" id="edit-contacts"><h3>通讯信息</h3>' + d.contacts.map((contact,i) => field(`通讯说明 ${d.contacts.length > 1 ? i+1 : ''}`,`contacts.${i}.text`,contact.text,true) + contact.emails.map((email,j) => field('邮箱',`contacts.${i}.emails.${j}.value`,email.value)).join('')).join('') + '</section>';
    }
    $('panel-content').innerHTML = `<form id="edit-form" class="edit-form">${html}<p id="edit-error" class="message error" role="alert" hidden></p></form>`;
    $('panel-footer').hidden = false; $('panel-footer').innerHTML = `<span class="save-state" id="save-state" role="status">${state.dirty ? '尚未保存' : '未作修改'}</span><button id="cancel-edit" class="button">取消</button><button id="save-edit" type="submit" form="edit-form" class="button primary" ${state.dirty ? '' : 'disabled'}>保存修改</button>`;
    document.querySelectorAll('[data-edit-jump]').forEach(button => button.onclick = () => {
      const target = button.dataset.editJump === 'title' ? document.querySelector('[data-field="title"]') : $('edit-' + button.dataset.editJump)?.querySelector('input,textarea');
      target?.focus(); target?.scrollIntoView({block:'center'});
    });
    $('cancel-edit').onclick = closePanel; $('edit-form').onsubmit = saveEdit;
    $('edit-form').oninput = event => {
      const el = event.target;
      if (el.dataset.field) { const parts = el.dataset.field.split('.'); let target = state.draft; parts.slice(0,-1).forEach(key => target = target[key]); target[parts.at(-1)] = el.type === 'checkbox' ? el.checked : el.value; }
      else if (el.dataset.affAuthor != null) { const author = state.draft.authors[Number(el.dataset.affAuthor)]; author.affiliations = el.checked ? [...author.affiliations,el.value] : author.affiliations.filter(id => id !== el.value); }
      else return;
      changed();
    };
    document.querySelectorAll('[data-move]').forEach(el => el.onclick = () => { const i = Number(el.dataset.move), j = i + Number(el.dataset.direction); [d.authors[i],d.authors[j]] = [d.authors[j],d.authors[i]]; changed(); const scroll = $('panel-content').scrollTop; drawEditor(); $('panel-content').scrollTop = scroll; document.querySelector(`[data-move="${j}"][data-direction="${el.dataset.direction}"]`)?.focus(); });
    if ($('editor-journal')) { $('editor-journal').value = d.publication.journal_id; $('editor-journal').onchange = event => { const journal = state.journals.find(j => j.id === event.target.value); if (journal) Object.assign(d.publication,{journal_id:journal.id,title:journal.title,issn_print:journal.issn_print || '',issn_electronic:journal.issn_electronic || '',publisher:journal.publisher || ''}); else d.publication.journal_id = ''; changed(); drawEditor(); }; }
  }
  function changed() { state.dirty = JSON.stringify(state.draft) !== JSON.stringify(state.work.fields); $('save-state').textContent = state.dirty ? '尚未保存' : '未作修改'; $('save-edit').disabled = !state.dirty; savedState(); }
  async function saveEdit(event) {
    event.preventDefault(); if (!state.dirty || state.saving) return;
    state.saving = true; $('save-edit').disabled = true; $('save-state').textContent = '正在保存…'; $('edit-error').hidden = true;
    savedState();
    $('edit-form').querySelectorAll('input,textarea,select,button').forEach(el => el.disabled = true);
    try {
      await post(`/api/edit/${state.task}`, {version:state.work.version,fields:state.draft});
      await loadResult();
      if (!state.work) throw new Error('无法载入保存结果');
      state.dirty = false; state.draft = structuredClone(state.work.fields); drawEditor(); toast('修改已保存，预览和下载文件已更新。');
    } catch (error) {
      $('edit-error').textContent = error.status ? error.message : '未能确认保存结果。输入已保留，请重试；若提示版本已更新，请重新载入核实。'; $('edit-error').hidden = false; $('edit-error').scrollIntoView({block:'nearest'});
      if (error.status === 409) { const button = document.createElement('button'); button.type = 'button'; button.className = 'text-button'; button.textContent = '重新载入已保存结果'; button.onclick = async () => { if (await discard()) { try { await loadResult(); if (!state.work) throw new Error(); state.draft = structuredClone(state.work.fields); drawEditor(); } catch { toast('仍未能载入，请稍后重试。'); } } }; $('edit-error').append(document.createElement('br'),button); }
      $('edit-error').scrollIntoView({block:'end',behavior:'instant'});
      $('edit-form').querySelectorAll('input,textarea,select,button').forEach(el => el.disabled = false);
      document.querySelectorAll('[data-move]').forEach(el => { const i=Number(el.dataset.move), j=i+Number(el.dataset.direction); el.disabled = !state.draft.authors[j] || state.draft.authors[j].group !== state.draft.authors[i].group; });
      $('save-edit').disabled = false; $('save-state').textContent = '尚未保存';
    } finally { state.saving = false; savedState(); }
  }
  async function home() {
    if (state.uploading) { toast('文件仍在上传，请在上传完成后返回首页。'); return; }
    if (!await discard()) return;
    ++state.generation; closePanelNow(); location.hash = ''; state.task = ''; show('upload'); setFile(null); $('docx-input').value = '';
  }
  ['home-btn','header-home','progress-home','next-btn','retry-btn'].forEach(id => $(id).onclick = home);
  async function copyLink() {
    if (!state.task) return;
    const url = location.origin + '/#task=' + state.task;
    try { await navigator.clipboard.writeText(url); toast('任务链接已复制。请仅分享给可以查看该稿件的人。'); }
    catch { const closed = dialog('任务链接', '复制下方地址即可再次打开。获得链接的人可以查看和修改这篇稿件，请谨慎分享。', '完成', `<input id="task-link" aria-label="任务链接" readonly value="${esc(url)}">`); $('task-link')?.focus(); $('task-link')?.select(); await closed; }
  }
  $('copy-link').onclick = copyLink; $('progress-link').onclick = copyLink;
  async function download(kind = 'all') {
    if (state.downloading) return;
    if (state.action) { toast(state.action + '，请稍候。'); return; }
    if (state.saving) { toast('正在保存，请稍候。'); return; }
    if (state.dirty && !await dialog('修改尚未保存', '下载的是上一次保存的结果，不包含当前输入。可以取消并先保存修改。', '下载已保存结果')) return;
    const task = state.task, generation = state.generation, result = state.result;
    if (!result) return;
    state.downloading = true; $('download-error').hidden = true; $('download-btn').setAttribute('aria-busy','true'); $('download-btn').setAttribute('aria-disabled','true'); $('download-btn').textContent = '正在准备下载…'; $('more-menu').open = false;
    $('panel-download-error')?.remove();
    try {
      const response = await fetch(`/api/${kind === 'xml' ? 'xml' : 'download'}/${task}?version=${result.version || state.work?.version || ''}`,{signal:AbortSignal.timeout(120000)});
      if (!response.ok) { let data; try { data = await response.json(); } catch { data = {}; } const error = new Error(typeof data.detail === 'string' ? data.detail : '下载暂时未能完成，请重试。'); error.status = response.status; throw error; }
      const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = kind === 'xml' ? result.xml_filename : result.download_filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),30000);
      toast(kind === 'xml' ? 'XML 已交给浏览器下载。' : '转换成果已交给浏览器下载。');
    } catch (error) {
      if (state.task !== task || state.generation !== generation) return;
      $('download-error').textContent = error.status ? error.message : '下载连接中断，请重试。已保存的转换结果不受影响。'; $('download-error').hidden = false;
      if (error.status === 409) { const button = document.createElement('button'); button.className = 'text-button'; button.textContent = '重新载入结果'; button.onclick = async () => { if (!await discard()) return; closePanelNow(); try { await loadResult(); $('download-error').hidden = true; } catch { toast('结果仍未能载入，请稍后重试。'); } }; $('download-error').append(document.createTextNode(' '),button); }
      if (state.panel) {
        const notice = $('download-error').cloneNode(true); notice.id = 'panel-download-error';
        const button = notice.querySelector('button'); if (button) button.onclick = $('download-error').querySelector('button').onclick;
        $('panel-content').append(notice); notice.scrollIntoView({block:'nearest'});
      }
    } finally {
      state.downloading = false; $('download-btn').removeAttribute('aria-busy'); $('download-btn').removeAttribute('aria-disabled'); $('download-btn').innerHTML = '下载转换成果 <span aria-hidden="true">↓</span>';
    }
  }
  $('download-btn').onclick = event => { event.preventDefault(); download(); }; $('download-xml').onclick = () => download('xml');
  async function reconvert() {
    if (!await discard()) return; $('more-menu').open = false;
    if (!await dialog('重新转换这篇稿件', '将沿用首次转换时的出版设置，重新识别原始 Word。当前结果仍会保留，人工修改不会带入新结果。', '开始重新转换', `<label>转换模型<select id="reconvert-provider"><option value="deepseek">DeepSeek</option><option value="dashscope">Qwen</option></select></label>`)) return;
    const task = state.task, provider = $('reconvert-provider').value;
    state.action = '正在创建转换任务'; closePanelNow(); savedState();
    try { const result = await post(`/api/reconvert/${task}`,{provider}); try { sessionStorage.setItem(`w2j-previous-${result.task_id}`,task); } catch { /* 可通过浏览器后退返回原任务。 */ } state.action = ''; location.hash = `task=${result.task_id}`; } catch (error) { toast(error.status ? error.message : '暂时无法重新转换，请重试。'); } finally { state.action = ''; savedState(); }
  }
  $('reconvert-btn').onclick = reconvert; $('retry-task-btn').onclick = reconvert;
  $('restore-btn').onclick = async () => {
    if (!await discard()) return; $('more-menu').open = false;
    if (!await dialog('恢复原始版本', '恢复为本次自动转换生成的 XML，已保存的人工修改将从当前结果中移除。Word 原稿不受影响。', '恢复原始版本')) return;
    const task = state.task, version = state.work.version;
    state.action = '正在恢复原始版本'; closePanelNow(); savedState();
    try { await post(`/api/restore/${task}`,{version}); await loadResult(); toast('已恢复自动生成的原始版本。'); }
    catch (error) { toast(error.status ? error.message : '恢复未能完成，请重试。'); }
    finally { state.action = ''; savedState(); }
  };
  window.addEventListener('beforeunload',event => { if (state.dirty || state.saving || state.action || state.uploading) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('keydown',event => { if ($('action-dialog').open) return; if (event.key === 'Escape' && state.panel) { event.preventDefault(); closePanel(); } if (event.key === 'Tab' && state.panel && matchMedia('(max-width:850px)').matches) { const focusable = [...$('side-panel').querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a,summary,iframe')].filter(el => !el.closest('[hidden]')); const first = focusable[0], last = focusable.at(-1); if (event.shiftKey && (document.activeElement === first || !$('side-panel').contains(document.activeElement))) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } });
  window.addEventListener('hashchange', async () => {
    if (state.uploading) { history.replaceState(null,'',state.task ? `#task=${state.task}` : location.pathname); toast('文件仍在上传，请稍候。'); return; }
    if ((state.dirty || state.saving || state.action) && !await discard()) { history.replaceState(null,'',`#task=${state.task}`); return; }
    route();
  });
  api('/api/journals').then(data => { state.journals = Array.isArray(data) ? data : data.journals || []; $('journal-input').innerHTML += state.journals.map(j => `<option value="${esc(j.id)}">${esc(j.title)}</option>`).join(''); }).catch(() => { $('journal-input').disabled = true; });
  toggleOutline(!matchMedia('(max-width:700px)').matches); route();
})();
