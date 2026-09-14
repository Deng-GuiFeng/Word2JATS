const test = require('node:test');
const assert = require('node:assert/strict');
const {create, clean, validID, duration} = require('../webapp/static/session.js');
const id = n => n.toString(16).padStart(16,'0');
function memory() { let value = null; return {getItem:()=>value,setItem:(_,next)=>value=next}; }
test('只保存白名单摘要，最多 12 个唯一合法任务', () => {
  assert.deepEqual(clean(null), []);
  assert.deepEqual(clean([null,{}, {id:'../secret'}]), []);
  assert.equal(validID(1), false);
  const session = create(memory());
  for (let n=0;n<15;n++) session.remember({id:id(n), filename:'Word.docx', xml:'SECRET', status:'done', provider:'deepseek'});
  assert.equal(session.read().length,12);
  session.remember({id:id(14), title:'修改题名'});
  assert.equal(session.read().length,12);
  assert.equal(session.read()[0].title,'修改题名');
  assert.equal(session.read()[0].filename,'Word.docx');
  assert.equal(session.read()[0].xml,undefined);
  session.remove(id(14)); assert.equal(session.read().length,11);
  const rows=session.read(); rows[0].filename='mutated'; assert.notEqual(session.read()[0].filename,'mutated');
  assert.equal(session.remember({id:'invalid'}).length,11);
});
test('损坏/拒绝/满额存储不会阻断当前页', () => {
  const broken = create({getItem:()=>'{',setItem:()=>{throw Error('full');}});
  assert.deepEqual(broken.read(),[]);
  broken.remember({id:id(1)});
  assert.equal(broken.read()[0].id,id(1));
  broken.remove(id(1)); assert.deepEqual(broken.read(),[]);
  const missing=create(null); missing.remember({id:id(2)}); assert.equal(missing.read().length,1);
});
test('跨实例读取更新，异常字段归一化', () => {
  const store=memory(), a=create(store), b=create(store);
  a.remember({id:id(2),provider:'other',status:'other',at:-1});
  assert.equal(b.read()[0].provider,''); assert.equal(b.read()[0].status,'pending');
  b.remove(id(2)); assert.equal(a.read().length,0);
  const list=clean([{id:id(3),filename:'a'.repeat(500),at:Infinity},{id:id(3)}]);
  assert.equal(list.length,1); assert.equal(list[0].filename.length,300); assert.equal(list[0].at,0);
});
test('耗时正确进位，不能显示 1 分 60 秒', () => {
  assert.equal(duration(119.8),'2 分 0 秒'); assert.equal(duration(59.8),'1 分 0 秒');
  assert.equal(duration(-2),'0 秒'); assert.equal(duration(undefined),'0 秒');
});
