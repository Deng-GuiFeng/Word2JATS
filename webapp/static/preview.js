"use strict";
// 不依赖操作系统具备所有图片解码器；无法显示时保留原文件入口。
function showOriginalImage(image) {
  if (!(image instanceof HTMLImageElement) || !image.isConnected) return;
  const source = new URL(image.getAttribute('src') || '', location.href);
  if (source.origin !== location.origin || !source.pathname.startsWith('/api/figure/')) return;
  const link = document.createElement('a');
  link.className = 'w2j-media-fallback';
  link.href = source.href;
  link.download = '';
  link.textContent = '此图片暂时无法预览，请下载原文件查看';
  if (image.id) link.id = image.id;
  image.replaceWith(link);
}
document.addEventListener('error', event => showOriginalImage(event.target), true);
document.querySelectorAll('img').forEach(image => {
  if (image.complete && image.naturalWidth === 0) showOriginalImage(image);
});
document.addEventListener('click', (event) => {
  const node = event.target.closest('[id]');
  if (node && window.parent !== window) window.parent.postMessage({type:'w2j-select', id:node.id}, location.origin);
});
window.addEventListener('message', (event) => {
  if (event.origin !== location.origin || event.source !== parent || event.data?.type !== 'w2j-locate') return;
  const node = document.getElementById(event.data.id);
  if (!node) return;
  document.querySelectorAll('.w2j-selected').forEach(el => el.classList.remove('w2j-selected'));
  const target = node.tagName === 'A' && !node.textContent.trim() ? node.parentElement : node;
  target.classList.add('w2j-selected');
  window.scrollTo({top:Math.max(0,node.getBoundingClientRect().top + window.scrollY - 25),behavior:'instant'});
});
