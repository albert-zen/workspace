const test=require('node:test');
const assert=require('node:assert/strict');
const {refreshQueue}=require('../assets/inbox.js');
function setup(){
  const h={busy:false,generation:0,requests:[],applied:[],timers:[],statuses:[]};
  h.queue=refreshQueue({
    blocked:()=>h.busy,epoch:()=>h.generation,
    load:version=>new Promise((resolve,reject)=>h.requests.push({version,resolve,reject})),
    apply:data=>h.applied.push(data),status:text=>h.statuses.push(text),
    schedule:fn=>{h.timers.push(fn);return fn;},cancel:fn=>{h.timers=h.timers.filter(x=>x!==fn);}
  });
  h.run=async()=>{const next=h.timers.shift();if(next)await next();};
  return h;
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('draft defers reads and preserves write baseline until editing ends',async()=>{
  const h=setup();h.queue.accepted('old');h.busy=true;
  h.queue.notify('new');assert.equal(h.requests.length,0);
  h.busy=false;const work=h.run();h.requests[0].resolve({version:'new'});await work;
  assert.equal(h.requests[0].version,'old');assert.deepEqual(h.applied,[{version:'new'}]);
  h.queue.notify('new');assert.equal(h.requests.length,1);
});
test('a save or navigation during fetch invalidates the response and fetches again',async()=>{
  const h=setup();h.queue.notify('v1');h.generation++;
  h.requests[0].resolve({version:'v1'});await flush();assert.equal(h.applied.length,0);
  const work=h.run();h.requests[1].resolve({version:'v2'});await work;
  assert.deepEqual(h.applied,[{version:'v2'}]);
});
test('rapid notifications serialize and retain changes arriving during an in-flight request',async()=>{
  const h=setup();h.queue.notify('v1');h.queue.notify('v2');h.queue.notify('v3');
  assert.equal(h.requests.length,1);h.requests[0].resolve({version:'v1'});await flush();
  const work=h.run();assert.equal(h.requests[1].version,'v1');h.requests[1].resolve({version:'v3'});await work;
  assert.deepEqual(h.applied.map(x=>x.version),['v1','v3']);
});
test('transient failure retains rendered data and retries without another backend change',async()=>{
  const h=setup();h.queue.notify('v1');h.requests[0].reject(new Error('offline'));await flush();
  assert.equal(h.applied.length,0);assert.match(h.statuses.at(-1),/重试/);
  const work=h.run();h.requests[1].resolve({version:'v1'});await work;
  assert.equal(h.applied.length,1);
});
