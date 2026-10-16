const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Run the real rendering functions with a small DOM and controlled HTTP promises.
// Stop before app startup so these tests never touch a user's Inbox.
function harness() {
  const roots = [];
  class Node {
    constructor(tag, text = '') { this.tagName = tag.toUpperCase(); this.textContent = text; this.children = []; this.dataset = {}; this.hidden = false; this.isConnected = true; this.listeners={}; this.classList={add(){}}; }
    append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    addEventListener(name,listener) { this.listeners[name]=listener; }
    setAttribute(name, value) { this[name] = value; }
    matches() { return false; }
    scrollIntoView() { this.scrolled = true; }
    focus() { this.focused = true; }
  }
  const walk = root => [root, ...root.children.flatMap(walk)];
  const document = {
    listeners:{},
    addEventListener(name,listener) { this.listeners[name]=listener; },
    createElement: tag => new Node(tag),
    createElementNS: (_, tag) => new Node(tag),
    createTextNode: text => new Node('#text', text),
    getElementById: id => roots.flatMap(walk).find(node => node.id === id) || null,
  };
  const pending = [], copied=[];
  const context = vm.createContext({ document, fetch: (url,options) => new Promise((resolve, reject) => pending.push({ resolve, reject, url, options })), window: {addEventListener(){}}, navigator:{clipboard:{writeText:async text=>copied.push(text)}}, setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms);timer.unref();return timer;}, clearTimeout, console });
  const source = fs.readFileSync(process.env.INBOX_JS || require.resolve('../assets/inbox.js'), 'utf8');
  const boundary = source.indexOf('  const ui = WorkspaceUI.create(');
  assert.ok(boundary > 0);
  vm.runInContext(source.slice(0, boundary) + '\n globalThis.api={loadDescription,appendDescription,renderWorkDoc,visitFragment,copyForAI,persistItem,renderLifecycle,state};\n})();', context);
  return { ...context.api, pending, copied, root() { const root = new Node('section'); roots.push(root); return root; }, walk };
}
const paragraph = text => ({type:'paragraph',inlines:[{type:'text',text}]});
const doc = { sections: { description: {id:'事项说明-2',blocks:[paragraph('New description')]}, progress:{id:'当前推进',blocks:[paragraph('Next')]} }, remainingBlocks:[] };

test('late description failure cannot replace a newer success or a collapsed panel', async () => {
  const h = harness(), body = h.root(), item = {workPath:'work.md'};
  const old = h.loadDescription(body,item), latest = h.loadDescription(body,item);
  h.pending[1].resolve({ok:true,json:async()=>doc});
  await latest;
  h.pending[0].reject(new Error('Old failure'));
  await old;
  assert.equal(body.textContent,'New description');
  assert.equal(body.dataset.ready,'true');
  const collapsed = h.loadDescription(body,item);
  body.hidden = true;
  h.pending[2].reject(new Error('Hidden failure'));
  await collapsed;
  assert.equal(body.textContent,'');
  assert.equal(body.dataset.ready,'false');
});

test('detail retains section fragment targets while previews have no heading IDs', () => {
  const h = harness(), detail = h.root();
  h.renderWorkDoc(detail,doc);
  for (const id of ['事项说明-2','当前推进']) {
    h.visitFragment({preventDefault(){}},`#${id}`);
    const heading = h.walk(detail).find(node=>node.id===id);
    assert.ok(heading,`missing ${id}`);
    assert.equal(heading.scrolled,true);
    assert.equal(heading.focused,true);
  }
  const preview = h.root();
  h.appendDescription(preview,{sections:{description:{blocks:[{type:'heading',level:3,id:'nested',inlines:[{type:'text',text:'Scope'}]}]}}});
  assert.equal(h.walk(preview).filter(node=>node.id).length,0);
});


test('copy action keeps a visible SVG and announces success without replacing it with text', async () => {
  const h=harness(),button=h.copyForAI({entryKey:'a',title:'Example',summary:'Ready'},'Waiting');
  assert.equal(button.children[0].tagName,'SVG');
  assert.match(button['aria-label'],/复制给 AI/);
  const clicking=button.listeners.click();
  h.pending[0].resolve({ok:true,json:async()=>({absoluteSourcePath:"/workspace/personal/inbox/inbox.md",sections:[{items:[{entryKey:"a",title:"Example",workPath:"work.md"}]}]})});
  await clicking;
  assert.equal(h.pending.length,1,"copy only reads Inbox, not Work content");
  assert.equal(h.copied.length,1);
  assert.match(h.copied[0],/Example/);
  assert.equal(button.children[0].tagName,'SVG');
  assert.match(button['aria-label'],/已复制/);
  assert.equal(button.children[1].textContent,'已复制');
});

test('copy refuses a stale item without changing the clipboard', async () => {
  const h=harness(),button=h.copyForAI({entryKey:'old',title:'Example'},'Waiting');
  const clicking=button.listeners.click();
  h.pending[0].resolve({ok:true,json:async()=>({absoluteSourcePath:'/workspace/personal/inbox/inbox.md',sections:[{items:[{entryKey:'new',title:'Example'}]}]})});
  await clicking;
  assert.equal(h.copied.length,0);
  assert.match(button.children[1].textContent,/刷新/);
});

test('missing description or failed optional preview leaves the summary in place',async()=>{
 const h=harness(),body=h.root();
 const missing=h.loadDescription(body,{workPath:'work.md'});h.pending[0].resolve({ok:true,json:async()=>({sections:{}})});await missing;
 assert.equal(body.dataset.ready,'false');assert.equal(body.textContent,'');
 const failed=h.loadDescription(body,{workPath:'work.md'});h.pending[1].reject(new Error('unavailable'));await failed;
 assert.equal(body.dataset.ready,'false');assert.equal(body.textContent,'');
});

test('editing a registered item preserves every identity while sending only writable fields',async()=>{
 const h=harness();h.root().id='sync-state';
 h.state.revision='current';
 h.state.sections=[{status:'Waiting',items:[{entryKey:'a',itemId:'a',title:'Before',summary:'Ready',issueUrl:'',workPath:'',handoffCommand:'ignored',lifecycleStatus:'active',pinned:true},{entryKey:'b',itemId:'b',title:'Other',summary:'Kept',issueUrl:'',workPath:''}]},{status:'Done',items:[]}];
 const saving=h.persistItem({originalKey:'a',itemId:'a',title:'Renamed',summary:'Ready',status:'Done',issueUrl:'',workPath:''});
 const request=JSON.parse(h.pending[0].options.body);
 assert.equal(request.sections[0].items[0].itemId,'b');
 assert.deepEqual(request.sections[1].items[0],{title:'Renamed',summary:'Ready',issueUrl:'',workPath:'',itemId:'a',pinned:true});
 assert.equal(request.sections[0].items[0].handoffCommand,undefined);
 h.pending[0].resolve({ok:true,json:async()=>({revision:'next',sections:[request.sections[0],{status:'Done',items:[{...request.sections[1].items[0],entryKey:'a'}]}]})});
 await saving;
 assert.equal(h.state.selected,'a');assert.equal(h.state.revision,'next');
});

test('actual completion and remaining closeout are readable independently of personal categories',()=>{
 const h=harness(),root=h.root();
 h.renderLifecycle(root,{lifecycleStatus:'completed',closeoutPending:['工作树还有未提交改动','分支尚未合并']});
 const text=h.walk(root).map(node=>node.textContent).filter(Boolean).join(' ');
 assert.match(text,/工作进展：已完成/);assert.match(text,/待收尾 · 2 项/);
 assert.match(text,/工作树还有未提交改动/);assert.match(text,/分支尚未合并/);
 const legacy=h.root();h.renderLifecycle(legacy,{lifecycleStatus:'unverified',closeoutPending:[]});
 assert.match(h.walk(legacy).map(node=>node.textContent).join(' '),/待核实/);
 const missing=h.root();h.renderLifecycle(missing,{});assert.equal(missing.children.length,0);
});

test('editing within a personal category keeps the user order and inherits stable identity',async()=>{
 const h=harness();h.root().id='sync-state';h.state.revision='before';
 h.state.sections=[{status:'Waiting',items:[{entryKey:'a',itemId:'a',title:'First',summary:'Before',issueUrl:'',workPath:''},{entryKey:'b',itemId:'b',title:'Second',summary:'Kept',issueUrl:'',workPath:''}]}];
 const saving=h.persistItem({originalKey:'a',title:'First renamed',summary:'After',status:'Waiting',issueUrl:'',workPath:''});
 const payload=JSON.parse(h.pending[0].options.body);
 assert.deepEqual(payload.sections[0].items.map(item=>item.itemId),['a','b']);
 assert.equal(payload.sections[0].items[0].title,'First renamed');
 const sections=payload.sections.map(section=>({...section,items:section.items.map(item=>({...item,entryKey:item.itemId}))}));
 h.pending[0].resolve({ok:true,json:async()=>({revision:'after',sections})});await saving;
 assert.equal(h.state.selected,'a');
});
