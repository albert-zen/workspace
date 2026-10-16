const test = require('node:test');
const assert = require('node:assert/strict');
const projects = require('../assets/inbox.js');
const entry=(project,state='active')=>({workPath:`agent-data/projects/${project}/work/${state}/task.md`});
const sections=[{status:'In progress',items:[entry('demo'),{}]},{status:'Done',items:[entry('demo','archive'),entry('workspace')]}];
test('derives project from active/archive paths without reading documents',()=>{
 assert.equal(projects.key(entry('demo','archive')),'demo');
 assert.equal(projects.key(entry('team/demo')),'team/demo');
 assert.equal(projects.key({}), '');
 assert.equal(projects.key({workPath:'elsewhere/demo.md'}), '');
});
test('registered semantic names keep project filters useful before Work exists',()=>{
 assert.equal(projects.key({semanticName:'示例项目/读取团队文档'}),'示例项目');
 assert.equal(projects.key({semanticName:'demo/模块/兼容检查',workPath:'agent-data/projects/demo/work/active/task.md'}),'demo');
 assert.equal(projects.key({semanticName:'inbox/模块/兼容检查'}),'inbox');
});
test('options count every status including collapsed Done and unclassified',()=>{
 assert.deepEqual(Object.fromEntries(projects.options(sections)),{'':1,workspace:1,demo:2});
});
test('filter preserves full source for whole-document saves',()=>{
 const before=JSON.stringify(sections);
 assert.deepEqual(projects.filter(sections,'demo').map(s=>s.items.length),[1,1]);
 assert.deepEqual(projects.filter(sections,'').map(s=>s.items.length),[1,0]);
 assert.deepEqual(projects.filter(sections,'missing').map(s=>s.items.length),[0,0]);
 assert.equal(JSON.stringify(sections),before);
 assert.deepEqual(projects.filter(sections,null),sections);
});


test('handoff locates only the current matter without duplicating Work', () => {
 const snapshot={absoluteSourcePath:'/workspace/personal/inbox/inbox.md',sections:[{items:[{entryKey:'a',title:'Matter',workPath:'agent-data/work.md'}]}]};
 const expected="请接手我 Inbox 中的事项「Matter」。\n\n先用以下命令定位事项，再沿其中的 Work 链接读取材料：\n\n    rg -n -F -- 'Matter — ' '/workspace/personal/inbox/inbox.md'";
 assert.equal(projects.handoff('a',snapshot),expected);
 delete snapshot.sections[0].items[0].workPath;
 assert.equal(projects.handoff('a',snapshot),expected);
 assert.throws(()=>projects.handoff('old-key',snapshot),/刷新/);
 delete snapshot.absoluteSourcePath;
 assert.throws(()=>projects.handoff('a',snapshot),/路径/);
});

test('registered handoff copies the current server command without rebuilding shell arguments',()=>{
 const handoffCommand="'/workspace/a'\"'\"'b/bin/work' --root '/workspace/a'\"'\"'b' resume '示例项目/Bob'\"'\"'s $(echo nope)'";
 const snapshot={sections:[{items:[{entryKey:'stable',itemId:'stable',title:'Renamed matter',handoffCommand}]}]};
 assert.equal(projects.handoff('stable',snapshot),`请接手我 Inbox 中的事项「Renamed matter」。\n\n    ${handoffCommand}`);
 assert.throws(()=>projects.handoff('missing',snapshot),/刷新/);
});

test('handoff shell quoting preserves literal arguments without substitutions',{skip:process.platform==='win32'},()=>{
 const {execFileSync}=require('node:child_process');
 const title="Bob's $(printf INJECTED) `echo bad` --name";
 const path="/workspace/a'b $(echo bad)/inbox.md";
 const inbox={absoluteSourcePath:path,sections:[{items:[{entryKey:'a',title}]}]};
 const command=projects.handoff('a',inbox).split('\n    ')[1];
 // Shell parses real generated arguments; replace only the rg executable with a recording function.
 const output=execFileSync('/bin/sh',['-c',`rg() { printf '%s\\n' "$@"; }; ${command}`],{encoding:'utf8'});
 assert.deepEqual(output.trimEnd().split('\n'),['-n','-F','--',title+' — ',path]);
});

test('handoff uses PowerShell literal quotes for Windows paths',()=>{
 const inbox={sections:[{items:[{entryKey:'a',title:"Bob's $name"}]}]};
 for(const path of [String.raw`D:\Work\personal\inbox\inbox.md`,String.raw`\\server\share\personal\inbox\inbox.md`]) {
  inbox.absoluteSourcePath=path;
  const result=projects.handoff('a',inbox);
  assert.ok(result.endsWith(`rg -n -F -- 'Bob''s $name — ' '${path}'`));
 }
 inbox.absoluteSourcePath='';assert.throws(()=>projects.handoff('a',inbox),/路径/);
});

test('preview is plain text from the matter section without Markdown or link destinations',()=>{
 const doc={sections:{description:{blocks:[{type:'heading',inlines:[{type:'text',text:'背景'}]},{type:'paragraph',inlines:[{type:'strong',text:'问题 '},{type:'link',text:'材料',target:'secret/path'}]},{type:'list',items:[{inlines:[{type:'text',text:'目标'}]}]}]}}};
 assert.equal(projects.descriptionText(doc),'背景 问题 材料 目标');
 assert.equal(projects.descriptionText({sections:{}}),'');
});
