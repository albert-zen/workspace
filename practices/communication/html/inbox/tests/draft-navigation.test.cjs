const test=require('node:test');
const assert=require('node:assert/strict');
const inbox=require('../assets/inbox.js');
test('navigation is blocked until editing is saved or explicitly cancelled',()=>{
 assert.equal(inbox.canLeaveEdit('detail',0),true);
 assert.equal(inbox.canLeaveEdit('list',0),true);
 assert.equal(inbox.canLeaveEdit('edit',0),false);
 assert.equal(inbox.canLeaveEdit('edit',1),false);
 assert.equal(inbox.canLeaveEdit('detail',1),false);
});
