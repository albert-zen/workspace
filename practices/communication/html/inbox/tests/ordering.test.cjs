const test = require('node:test');
const assert = require('node:assert/strict');
const inbox = require('../assets/inbox.js');
const item = (key,project='one') => ({entryKey:key,itemId:key,title:key,semanticName:`${project}/${key}`});
const source = () => [{status:'Needs you',items:[item('z'),item('hidden','two'),item('a'),item('b')]},{status:'In progress',items:[]},{status:'Waiting',items:[item('w')]},{status:'Done',items:[]}];
const keys = sections => sections.map(s=>s.items.map(i=>i.entryKey));
test('top, before and after persist explicit order without alphabetizing or mutating source',()=>{
  const sections=source(),before=JSON.stringify(sections);
  assert.deepEqual(keys(inbox.move(sections,'b',{status:'Needs you',edge:'start'}))[0],['b','z','hidden','a']);
  assert.deepEqual(keys(inbox.move(sections,'b',{status:'Needs you',beforeKey:'a'}))[0],['z','hidden','b','a']);
  assert.deepEqual(keys(inbox.move(sections,'z',{status:'Needs you',afterKey:'a'}))[0],['hidden','a','z','b']);
  assert.equal(JSON.stringify(sections),before);
});
test('move-to-front is one action while pinning remains a persistent state',()=>{
  let sections=inbox.setPin(source(),'hidden',true);
  sections=inbox.move(sections,'b',{status:'Needs you',edge:'start'});
  assert.deepEqual(keys(sections)[0],['hidden','b','z','a']);
  assert.equal(sections[0].items[0].pinned,true);
  assert.equal(!!sections[0].items[1].pinned,false);
  assert.throws(()=>inbox.move(sections,'a',{status:'Needs you',beforeKey:'hidden'}),/置顶/);
  sections=inbox.setPin(sections,'hidden',false);
  assert.deepEqual(keys(sections)[0],['hidden','b','z','a']);
  assert.equal(sections[0].items[0].pinned,false);
});
test('filtered views do not discard hidden projects when moved to front',()=>{
  const sections=source();
  assert.equal(inbox.filter(sections,'one')[0].items.length,3);
  assert.deepEqual(keys(inbox.move(sections,'b',{status:'Needs you',edge:'start'}))[0],['b','z','hidden','a']);
});
test('drag to an empty or different section retains identity and all other items',()=>{
  assert.deepEqual(keys(inbox.move(source(),'a',{status:'In progress',edge:'end'})),[['z','hidden','b'],['a'],['w'],[]]);
  assert.deepEqual(keys(inbox.move(source(),'a',{status:'Waiting',beforeKey:'w'})),[['z','hidden','b'],[],['a','w'],[]]);
});
test('invalid or disappeared targets cannot silently place the item elsewhere',()=>{
  assert.throws(()=>inbox.move(source(),'missing',{status:'Waiting',edge:'end'}));
  assert.throws(()=>inbox.move(source(),'a',{status:'Waiting',beforeKey:'missing'}));
  assert.throws(()=>inbox.move(source(),'a',{status:'Unknown',edge:'start'}));
  assert.deepEqual(keys(inbox.move(source(),'a',{status:'Needs you',beforeKey:'a'})),keys(source()));
});
