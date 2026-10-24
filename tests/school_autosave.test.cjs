/* Node's built-in runner exercises the real queue; only clock/network are controlled. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {AutosaveQueue} = require('../moa/school_draft_ui.js');
const flush = () => new Promise(resolve => setImmediate(resolve));
const ack = {id:'a'.repeat(32),created_at:'2026-10-24T12:00:00+09:00',resume:'/structure-draft?id='+ 'a'.repeat(32)};

function clock() {
  let now=0, n=0; const timers=new Map();
  return {setTimeout(fn,ms) {timers.set(++n,{at:now+ms,fn}); return n;},
    clearTimeout(id) {timers.delete(id);},
    advance(ms) {now+=ms; for (const [id,t] of [...timers]) if (t.at<=now) {timers.delete(id); t.fn();}}};
}
function deferred() {let resolve,reject; const promise=new Promise((a,b)=>{resolve=a; reject=b;}); return {promise,resolve,reject};}
function input(queue, value='initial') {
  const data={value,head:'',events:[],saved:[]};
  data.job=queue.add({read:()=>({body:new URLSearchParams({text:data.value,expected_draft:data.head}).toString(),value:data.value}),
    saved:(snapshot,result)=>{data.head=result.id; data.saved.push(snapshot.value);},
    state:(state,detail)=>data.events.push([state,detail])});
  return data;
}

test('initial and recovered inputs do not write; a burst saves only its final value after idle', async () => {
  const time=clock(), calls=[];
  const queue=new AutosaveQueue({clock:time,send:async body=>{calls.push(body); return ack;}});
  const a=input(queue); time.advance(9000); await flush(); assert.equal(calls.length,0);
  a.value='one'; queue.changed(a.job); time.advance(1400); assert.equal(calls.length,0);
  a.value='two'; queue.changed(a.job); time.advance(1499); assert.equal(calls.length,0);
  time.advance(1); await flush();
  assert.equal(new URLSearchParams(calls[0]).get('text'),'two');
  assert.deepEqual(a.saved,['two']); assert.equal(a.events.at(-1)[0],'saved');
  queue.changed(a.job); time.advance(2000); await flush(); assert.equal(calls.length,1);
});

test('requests serialize across forms and an ACK cannot mark newer typing as saved', async () => {
  const time=clock(), request=deferred(), calls=[];
  const queue=new AutosaveQueue({clock:time,send:body=>{calls.push(body); return calls.length===1?request.promise:Promise.resolve(ack);}});
  const a=input(queue), b=input(queue);
  a.value='first'; queue.changed(a.job); time.advance(1500);
  b.value='second form'; queue.changed(b.job); a.value='newer typing'; queue.changed(a.job);
  time.advance(1500); assert.equal(calls.length,1);
  request.resolve(ack); await flush();
  assert.equal(a.saved[0],'first');
  time.advance(1500); await flush();
  assert.equal(calls.length,3);
  assert.deepEqual(a.saved,['first','newer typing']); assert.deepEqual(b.saved,['second form']);
  assert.equal(new URLSearchParams(calls.find(x=>new URLSearchParams(x).get('text')==='newer typing')).get('expected_draft'),ack.id);
});

test('unknown failure never loops; retry acknowledges the exact failed request before newer input', async () => {
  const time=clock(), calls=[];
  const queue=new AutosaveQueue({clock:time,send:async body=>{calls.push(body); if(calls.length===1) throw new Error('network'); return ack;}});
  const a=input(queue); a.value='possibly committed'; queue.changed(a.job); time.advance(1500); await flush();
  assert.equal(a.events.at(-1)[0],'error');
  a.value='not sent yet'; queue.changed(a.job); time.advance(90000); await flush();
  assert.equal(calls.length,1); assert.equal(a.saved.length,0);
  queue.retry(a.job); await flush(); assert.equal(calls[1],calls[0]);
  assert.deepEqual(a.saved,['possibly committed']);
  time.advance(1500); await flush(); assert.equal(new URLSearchParams(calls[2]).get('text'),'not sent yet');
});

test('conflict blocks typing and retries without changing the saved baseline', async () => {
  const time=clock(), calls=[];
  const queue=new AutosaveQueue({clock:time,send:async body=>{calls.push(body); throw Object.assign(new Error('conflict'),{blocked:true,code:'conflict'});}});
  const a=input(queue); a.value='local remains'; queue.changed(a.job); time.advance(1500); await flush();
  assert.equal(a.events.at(-1)[1].code,'conflict'); assert.equal(a.events.at(-1)[1].blocked,true);
  a.value='more local'; queue.changed(a.job); queue.retry(a.job); time.advance(15000); await flush();
  assert.equal(calls.length,1); assert.deepEqual(a.saved,[]); assert.equal(a.value,'more local');
});

test('page hide or form submission pauses unsent work; cancelled navigation can resume it', async () => {
  const time=clock(), calls=[];
  const queue=new AutosaveQueue({clock:time,send:async body=>{calls.push(body); return ack;}});
  const a=input(queue); a.value='local'; queue.changed(a.job); queue.pause(); time.advance(15000); await flush();
  assert.equal(calls.length,0);
  queue.resume(); time.advance(1500); await flush(); assert.equal(calls.length,1);
});

test('IME composition holds a due job and resumes only on composition end', async () => {
  const time=clock(), calls=[];
  const queue=new AutosaveQueue({clock:time,send:async body=>{calls.push(body); return ack;}});
  const a=input(queue); a.value='incomplete'; queue.changed(a.job); queue.hold(a.job,true);
  time.advance(9000); await flush(); assert.equal(calls.length,0);
  a.value='완성'; queue.hold(a.job,false); time.advance(1500); await flush();
  assert.equal(new URLSearchParams(calls[0]).get('text'),'완성');
});
