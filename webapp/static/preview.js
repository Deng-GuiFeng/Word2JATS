"use strict";
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
