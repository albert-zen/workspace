const test=require('node:test');
const assert=require('node:assert/strict');
const {disclosure}=require('../assets/inbox.js');
function setup(){
  let time=0,serial=0;const jobs=new Map(),events=[];
  const controller=disclosure({show:key=>events.push(['show',key]),hide:key=>events.push(['hide',key]),beforeSwitch:key=>{events.push(['capture',key]);return()=>events.push(['restore',key]);},now:()=>time,schedule:(fn,delay)=>{jobs.set(++serial,{fn,at:time+delay});return serial;},cancel:id=>jobs.delete(id)});
  return {controller,events,advance(ms){time+=ms;for(const [id,job] of [...jobs])if(job.at<=time){jobs.delete(id);job.fn();}}};
}
test('stationary hover opens after 450ms and leaving the whole row closes after grace',()=>{
 const h=setup(),d=h.controller;d.move('a',10,10);h.advance(449);assert.equal(d.current(),null);h.advance(1);assert.equal(d.current(),'a');
 d.leave('a');h.advance(179);assert.equal(d.current(),'a');h.advance(1);assert.equal(d.current(),null);
 d.move('a',10,11);h.advance(450);
 d.move('b',10,11);h.advance(1000);assert.equal(d.current(),'a','layout-only movement must not switch');
 d.move('b',10,12);h.advance(450);assert.equal(d.current(),'b');
 assert.deepEqual(h.events.slice(-4),[['capture','b'],['hide','a'],['show','b'],['restore','b']]);
});
test('scroll cancels pending work, retains open content, and needs a fresh movement after quiet period',()=>{
 const h=setup(),d=h.controller;d.focus('a');d.move('b',2,2);h.advance(200);d.scroll();h.advance(250);assert.equal(d.current(),'a');
 d.move('b',2,2);h.advance(500);assert.equal(d.current(),'a');
 d.scroll();d.move('b',2,3);h.advance(500);assert.equal(d.current(),'a');
 d.move('b',2,4);h.advance(450);assert.equal(d.current(),'b');
});
test('leave cancels pending reveal and keyboard focus/Escape are explicit',()=>{
 const h=setup(),d=h.controller;d.move('a',1,1);d.leave('a');h.advance(500);assert.equal(d.current(),null);
 d.focus('a');assert.equal(d.current(),'a');d.escape();assert.equal(d.current(),null);
 d.move('b',2,2);d.reset();h.advance(500);assert.equal(d.current(),null);
});

test('entering description or copy keeps reading alive; keyboard focus holds until blur',()=>{
 const h=setup(),d=h.controller;d.move('a',1,1);h.advance(450);d.leave('a');h.advance(100);d.enter('a');h.advance(300);assert.equal(d.current(),'a');
 d.focus('a');d.leave('a');h.advance(300);assert.equal(d.current(),'a');d.blur('a');h.advance(180);assert.equal(d.current(),null);
});
test('scroll defers an outside close until quiet and does not restart hover automatically',()=>{
 const h=setup(),d=h.controller;d.move('a',1,1);h.advance(450);d.scroll();d.leave('a');h.advance(180);assert.equal(d.current(),'a');h.advance(40);assert.equal(d.current(),null);h.advance(1000);assert.equal(d.current(),null);
});
