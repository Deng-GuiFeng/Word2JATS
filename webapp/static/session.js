/* 最近转换只保存任务入口，不存 Word、XML 或编辑草稿，也不列出他人的任务。 */
'use strict';
((root) => {
  const key = 'word2jats.recent.v1';
  const validID = id => typeof id === 'string' && /^[a-f0-9]{16}$/.test(id);
  function clean(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value.filter(row => row && validID(row.id) && !seen.has(row.id) && seen.add(row.id)).slice(0, 12).map(row => ({
      id: row.id, filename: String(row.filename || 'Word 稿件').slice(0, 300),
      title: String(row.title || '').slice(0, 300),
      provider: ['deepseek', 'dashscope'].includes(row.provider) ? row.provider : '',
      status: ['pending', 'running', 'done', 'error', 'expired'].includes(row.status) ? row.status : 'pending',
      at: Number.isFinite(row.at) && row.at > 0 ? row.at : 0,
    }));
  }
  function create(storage) {
    let memory = [], writable = true;
    function read() {
      if (writable) try { memory = clean(JSON.parse(storage.getItem(key) || '[]')); } catch { /* 损坏时保留本页入口。 */ }
      return memory.map(row => ({...row}));
    }
    function write(rows) {
      memory = clean(rows);
      try { storage.setItem(key, JSON.stringify(memory)); } catch { writable = false; }
      return memory.map(row => ({...row}));
    }
    return {read, remove: id => write(read().filter(row => row.id !== id)),
      remember: row => {
        if (!validID(row?.id)) return read();
        const previous = read(), old = previous.find(item => item.id === row.id);
        return write([{...old, ...row, at: row.at || old?.at || Date.now()}, ...previous.filter(item => item.id !== row.id)]);
      }};
  }
  function duration(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  }
  const api = {key, clean, create, validID, duration};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.W2JSession = api;
})(globalThis);
