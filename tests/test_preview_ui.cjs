const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../plugins/hp-local-editor/assets/preview');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'control.js'), 'utf8');
function element() {
 let text = '', value = '', src = 'http://127.0.0.1/site/';
 const attributes = new Map();
 return {
  style:{},hidden:false, disabled:false, children:[], listeners:{}, parentNode:null,
  textWrites:0, valueWrites:0, srcWrites:0, selectionStart:0, selectionEnd:0,
  scrollHeight:300, scrollTop:200, clientHeight:100,
  get textContent(){return text;},
  set textContent(next){this.textWrites++;text=next;},
  get value(){return value;},
  set value(next){this.valueWrites++;value=next;this.selectionStart=this.selectionEnd=next.length;},
  get src(){return src;},
  set src(next){this.srcWrites++;src=next;},
  replaceChildren(){for(const child of this.children)child.parentNode=null;this.children=[];},
  append(...items){for(const item of items)this.insertBefore(item,null);},
  insertBefore(item,before){item.remove();const index=before?this.children.indexOf(before):this.children.length;this.children.splice(index,0,item);item.parentNode=this;},
  remove(){if(this.parentNode){const list=this.parentNode.children;list.splice(list.indexOf(this),1);this.parentNode=null;}},
  contains(node){return this===node||this.children.some(child=>child.contains(node));},
  addEventListener(name,cb){this.listeners[name]=cb;},setAttribute(name,value){attributes.set(name,String(value));},getAttribute(name){return attributes.get(name)??null;},focus(){this.focused=true;},
  showModal(){this.open=true;},close(){this.open=false;}
 };
}
function harness(initial, fail = () => false) {
 const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(([,id]) => {
  const node=element();node.hidden=new RegExp('id="'+id+'"[^>]*\\bhidden\\b').test(html);return [id,node];
 }));
 let selection = null;
  let current=structuredClone(initial), now=100000, timers=[];
  const calls=[];
  const sandbox={document:{getElementById:id=>elements[id],querySelector:()=>({content:'csrf'}),createElement:element,getSelection:()=>selection},crypto:require('node:crypto').webcrypto,URL,Date:{now:()=>now},setTimeout:cb=>timers.push(cb),fetch:async(url,opts)=>{
    const body=opts?.body?JSON.parse(opts.body):null;const action=body?.action;calls.push({url,action,body});
    const failure=fail(url,action,body);if(failure)return {ok:false,json:async()=>({error:typeof failure==='string'?failure:'control_response_unavailable'})};
    return {ok:true,json:async()=>structuredClone(url==='/api/revision'?{revision:current.local.revision}:current)};
  }};
  vm.runInNewContext(source,sandbox);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  return {elements,calls,flush,select(node){selection={isCollapsed:false,anchorNode:node,focusNode:node};},set(value){current=structuredClone(value);},async tick(ms){now+=ms;const cb=timers.shift();assert.ok(cb,'poll must remain scheduled');await cb();await flush();}};
}
const clean={site_id:'clinic',local:{ready:true,dirty:false,pending:false,revision:'v1',tree_sha256:'tree1',saved_commit:'saved1'},identity:{site_name:'テスト医院',email:'hidden@example.invalid'},operation:null,confirmation:null};
test('initial connection retries automatically and no manual refresh control exists',async()=>{
 let failures=2;
 const h=harness({...clean,identity:null},(url,action)=>action==='refresh'&&failures-->0);
 await h.flush();assert.equal(h.elements.refresh,undefined);
 assert.match(h.elements['connection-status'].textContent,/自動/);
 h.set(clean);await h.tick(20000);await h.tick(30000);
 assert.equal(h.elements.account.textContent,'テスト医院');assert.equal(h.elements.prepare.disabled,false);
 assert.deepEqual(h.calls.filter(x=>x.action).map(x=>x.action),['refresh','refresh','refresh']);
});
test('unknown operation keeps reconciling beyond six polls without resubmitting',async()=>{
 const current={...clean,operation:{state:'unknown',arguments:{operation_id:'same-op'}}};
 const h=harness(current);await h.flush();
 for(let i=0;i<9;i++)await h.tick(60000);
 assert.ok(h.calls.filter(x=>x.action==='refresh').length>=10);
 assert.ok(h.calls.every(x=>!x.action||x.action==='refresh'));
 assert.equal(h.elements.prepare.disabled,true);assert.equal(h.elements.open.hidden,true);
});
test('initial local-state failure recovers even when revision does not change',async()=>{
 let failures=2;const h=harness(clean,(url)=>url==='/api/state'&&failures-->0);
 await h.flush();await h.tick(1000);await h.tick(1000);
 assert.equal(h.elements.account.textContent,'テスト医院');
 assert.ok(h.calls.some(x=>x.action==='refresh'));assert.equal(h.elements.error.hidden,true);
});
test('dirty, ready preview, and published states expose only the relevant next step',async()=>{
 const h=harness({...clean,local:{...clean.local,dirty:true}});await h.flush();
 assert.equal(h.elements['next-step'].hidden,true);assert.equal(h.elements.open.hidden,true);
 const preview={...clean,local:{...clean.local,revision:'v2'},operation:{state:'observed',local_tree_sha256:'tree1',arguments:{operation_id:'op',saved_commit:'saved1'},receipt:{status:'completed',result:{preview_id:'pv',preview_url:'https://example.invalid/dev'}}}};
 h.set(preview);await h.tick(1000);assert.equal(h.elements.prepare.hidden,true);assert.equal(h.elements.open.hidden,false);assert.equal(h.elements['dev-link'].href,'https://example.invalid/dev');
 h.set({...preview,local:{...preview.local,revision:'v3'},confirmation:{state:'observed',binding:{mode:'publish',preview_id:'pv'},operation:{status:'completed',operation_id:'publish'}}});await h.tick(1000);
 assert.equal(h.elements.open.hidden,true);assert.equal(h.elements['next-step'].hidden,true);
 assert.ok(h.calls.every(x=>!x.action||x.action==='refresh'));
});
test('publication still requires explicit trusted click; opening dialog only prepares confirmation',async()=>{
 const h=harness(clean);await h.flush();
 h.elements.open.listeners.click({isTrusted:true});await h.flush();assert.equal(h.calls.at(-1).action,'open_confirmation');
 h.elements.confirm.listeners.click({isTrusted:false});await h.flush();assert.equal(h.calls.at(-1).action,'open_confirmation');
 h.elements.confirm.listeners.click({isTrusted:true});await h.flush();assert.equal(h.calls.at(-1).action,'confirm');
});

test('saving a new local revision offers a new preview instead of stale publication',async()=>{
 const h=harness({...clean,operation:{state:'observed',local_tree_sha256:'old-tree',arguments:{operation_id:'old',saved_commit:'old-save'},receipt:{status:'completed',result:{preview_id:'old-preview',preview_url:'https://example.invalid/old'}}}});await h.flush();
 assert.equal(h.elements.prepare.hidden,false);assert.equal(h.elements.prepare.disabled,false);assert.equal(h.elements.open.hidden,true);assert.equal(h.elements['dev-link'].hidden,true);
});

test('account mismatch stays visible and blocks actions until a successful identity read',async()=>{
 let mismatch=true;const h=harness(clean,(url,action)=>action==='refresh'&&mismatch?'account_changed':false);await h.flush();
 assert.match(h.elements.error.textContent,/アカウントが変わりました/);assert.equal(h.elements.prepare.disabled,true);
 mismatch=false;await h.tick(20000);assert.equal(h.elements.error.hidden,true);assert.equal(h.elements.prepare.disabled,false);
});
test('editing during refresh rereads state without displaying a connection failure',async()=>{
 let race=true;const h=harness(clean,(url,action)=>action==='refresh'&&race?'local_files_changed':false);await h.flush();
 race=false;await h.tick(1000);assert.equal(h.elements['connection-status'].textContent,'');assert.equal(h.elements.error.hidden,true);
});
test('partial remote read failures back off but keep automatic reconciliation alive',async()=>{
 const h=harness({...clean,readback_errors:['confirmation_readback_unavailable']});await h.flush();
 assert.match(h.elements['connection-status'].textContent,/自動/);assert.equal(h.elements.prepare.disabled,true);
 const before=h.calls.filter(c=>c.action).length;await h.tick(5000);assert.equal(h.calls.filter(c=>c.action).length,before);
 await h.tick(60000);assert.equal(h.calls.filter(c=>c.action).length,before+1);
 h.set(clean);await h.tick(60000);assert.equal(h.elements['connection-status'].textContent,'');
});
test('in-progress publication offers its existing result and never another publish action',async()=>{
 const h=harness({...clean,operation:{state:'observed',local_tree_sha256:'tree1',arguments:{operation_id:'op',saved_commit:'saved1'},receipt:{status:'completed',result:{preview_id:'pv',preview_url:'https://example.invalid/dev'}}},confirmation:{state:'observed',binding:{mode:'publish',preview_id:'pv'},operation:{status:'running',operation_id:'pub'}}});await h.flush();
 assert.equal(h.elements.open.hidden,true);assert.equal(h.elements.previous.hidden,false);assert.match(h.elements['next-step'].textContent,/反映結果/);
});
test('preview links require HTTPS and do not permit credential URLs',async()=>{
 for(const preview_url of ['javascript:alert(1)','https://user:password@example.invalid/','http://example.invalid/']){
  const h=harness({...clean,operation:{state:'observed',local_tree_sha256:'tree1',arguments:{operation_id:'op',saved_commit:'saved1'},receipt:{status:'completed',result:{preview_id:'pv',preview_url}}}});await h.flush();
  assert.equal(h.elements['dev-link'].hidden,true);assert.equal(h.elements.open.hidden,true);
 }
});

test('a transient local poll error clears automatically without a file change',async()=>{
 let down=false;const h=harness(clean,url=>url==='/api/state'&&down);await h.flush();
 down=true;await h.tick(1000);assert.equal(h.elements.error.hidden,false);
 down=false;await h.tick(1000);assert.equal(h.elements.error.hidden,true);
});
test('chat launcher opens and closes the panel, with no fake request submission',async()=>{
 const h=harness(clean);await h.flush();assert.equal(h.elements.menu.hidden,true);
 h.elements.launcher.listeners.click();assert.equal(h.elements.menu.hidden,false);
 h.elements.toggle.listeners.click();assert.equal(h.elements.menu.hidden,true);
 let prevented=false;h.elements.composer.listeners.submit({preventDefault(){prevented=true;}});assert.ok(prevented);
 assert.ok(h.calls.every(x=>!x.action||x.action==='refresh'));
});

test('chat submit is explicit, clears accepted text, and renders answers as text',async()=>{
 let h;h=harness({...clean,chat:{available:true,busy:false,requests:[]}},(url,action,body)=>{
  if(url==='/api/chat')h.set({...clean,chat:{available:true,busy:false,requests:[{id:body.request_id,text:body.text,reply:'<script>not executable</script>',status:'completed'}]}});
  return false;
 });await h.flush();h.elements['chat-input'].value='文章を直して';
 h.elements.composer.listeners.submit({isTrusted:true,preventDefault(){}});await h.flush();
 assert.equal(h.calls.filter(c=>c.url==='/api/chat').length,1);assert.equal(h.elements['chat-input'].value,'');
 assert.equal(h.elements['chat-messages'].children[1].children[1].textContent,'<script>not executable</script>');
});
test('lost chat acknowledgement reconciles automatically and manual retry keeps request identity',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[]}},url=>url==='/api/chat');await h.flush();h.elements['chat-input'].value='変更して';
 h.elements.composer.listeners.submit({isTrusted:true,preventDefault(){}});await h.flush();
 h.elements.composer.listeners.submit({isTrusted:true,preventDefault(){}});await h.flush();
 const sends=h.calls.filter(c=>c.url==='/api/chat');assert.equal(sends.length,2);assert.equal(sends[0].body.request_id,sends[1].body.request_id);
 h.set({...clean,chat:{available:true,busy:false,requests:[{id:sends[0].body.request_id,text:'変更して',reply:'完了しました',status:'completed'}]}});await h.tick(1000);assert.equal(h.elements['chat-input'].value,'');
});

test('partially applied edits block dev and public confirmation',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[{id:'r',text:'edit',status:'apply_pending'}]}});await h.flush();
 assert.equal(h.elements.prepare.disabled,true);assert.equal(h.elements['chat-retry'].hidden,false);
});

test('welcome suggestions draft text without sending and hide once conversation begins',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[]}});await h.flush();
 assert.equal(h.elements.welcome.hidden,false);assert.equal(h.elements.workflow.hidden,false);
 h.elements['suggest-hours'].listeners.click();
 assert.equal(h.elements['chat-input'].value,'診療時間を変更したいです。');
 assert.ok(h.calls.every(c=>c.url!=='/api/chat'));
 h.set({...clean,chat:{available:true,busy:false,requests:[{id:'hello',text:'変更して',reply:'確認します',status:'completed'}]}});await h.tick(1000);
 assert.equal(h.elements.welcome.hidden,true);assert.equal(h.elements.workflow.hidden,false);
});

function enterEvent(overrides = {}) {
 const event = {key:'Enter',isTrusted:true,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...overrides};
 return event;
}
test('Enter sends once through the same chat path and preserves request text',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[]}});await h.flush();
 h.elements['chat-input'].value='この文章を修正して';
 const event=enterEvent();h.elements['chat-input'].listeners.keydown(event);
 h.elements['chat-input'].listeners.keydown(enterEvent());await h.flush();
 assert.equal(event.defaultPrevented,true);
 const sends=h.calls.filter(c=>c.url==='/api/chat');assert.equal(sends.length,1);
 assert.equal(sends[0].body.text,'この文章を修正して');
});
test('Shift+Enter and IME confirmation do not submit or prevent native editing',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[]}});await h.flush();
 h.elements['chat-input'].value='日本語を変換中';
 for(const overrides of [{shiftKey:true},{isComposing:true},{keyCode:229},{ctrlKey:true},{altKey:true},{metaKey:true}]){
  const event=enterEvent(overrides);h.elements['chat-input'].listeners.keydown(event);assert.equal(event.defaultPrevented,false);
 }
 h.elements['chat-input'].listeners.compositionstart();
 const composing=enterEvent();h.elements['chat-input'].listeners.keydown(composing);assert.equal(composing.defaultPrevented,false);
 h.elements['chat-input'].listeners.compositionend();
 const safari=enterEvent({keyCode:229});h.elements['chat-input'].listeners.keydown(safari);assert.equal(safari.defaultPrevented,false);
 await h.flush();assert.equal(h.calls.filter(c=>c.url==='/api/chat').length,0);
 h.elements['chat-input'].listeners.keydown(enterEvent());await h.flush();assert.equal(h.calls.filter(c=>c.url==='/api/chat').length,1);
});
test('held Enter, blank text, disabled input, and synthetic events cannot submit',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[]}});await h.flush();
 h.elements['chat-input'].value='   ';h.elements['chat-input'].listeners.keydown(enterEvent());await h.flush();
 h.elements['chat-input'].value='修正して';
 h.elements['chat-input'].listeners.keydown(enterEvent({repeat:true}));
 h.elements['chat-input'].listeners.keydown(enterEvent({isTrusted:false}));
 h.elements['chat-input'].listeners.keydown(enterEvent({defaultPrevented:true}));
 h.elements['chat-input'].disabled=true;h.elements['chat-input'].listeners.keydown(enterEvent());
 await h.flush();assert.equal(h.calls.filter(c=>c.url==='/api/chat').length,0);
});

test('unchanged polling preserves text nodes and textarea selection',async()=>{
 const row={id:'r1',text:'診療時間について',reply:'診療時間を確認しました。',status:'completed'};
 const h=harness({...clean,chat:{available:true,busy:false,requests:[row]}});await h.flush();
 const account=h.elements.account,body=h.elements['chat-messages'].children[1].children[1],input=h.elements['chat-input'];
 input.value='この文章を選択しています';input.selectionStart=2;input.selectionEnd=7;
 const writes=[account.textWrites,body.textWrites,input.valueWrites];
 for(let i=0;i<4;i++)await h.tick(1000);
 assert.deepEqual([account.textWrites,body.textWrites,input.valueWrites],writes);
 assert.equal(h.elements['chat-messages'].children[1].children[1],body);
 assert.deepEqual([input.selectionStart,input.selectionEnd],[2,7]);
});
test('new replies retain earlier message nodes and do not scroll selected text',async()=>{
 const rows=[{id:'r1',text:'先の依頼',reply:'選択中の回答です',status:'completed'},{id:'r2',text:'次の依頼',reply:'',status:'running'}];
 const h=harness({...clean,chat:{available:true,busy:true,requests:rows}});await h.flush();
 const log=h.elements['chat-messages'],prior=log.children[1],body=prior.children[1];h.select(body);
 h.elements['menu-body'].scrollTop=200;
 h.set({...clean,chat:{available:true,busy:false,requests:[rows[0],{...rows[1],reply:'新しい回答です',status:'completed'}]}});
 await h.tick(1000);
 assert.equal(log.children[1],prior);assert.equal(log.children[1].children[1],body);
 assert.equal(log.children[3].children[1].textContent,'新しい回答です');
 assert.equal(h.elements['menu-body'].scrollTop,200);
});
test('late acknowledgement preserves a newer draft and its selection',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[]}},url=>url==='/api/chat');await h.flush();
 const input=h.elements['chat-input'];input.value='送信した依頼';
 h.elements.composer.listeners.submit({isTrusted:true,preventDefault(){}});await h.flush();
 const id=h.calls.find(c=>c.url==='/api/chat').body.request_id;
 input.value='次に送る新しい依頼';input.selectionStart=1;input.selectionEnd=4;
 h.set({...clean,chat:{available:true,busy:true,requests:[{id,text:'送信した依頼',status:'running'}]}});await h.tick(1000);
 assert.equal(input.value,'次に送る新しい依頼');assert.deepEqual([input.selectionStart,input.selectionEnd],[1,4]);
 assert.equal(input.disabled,false);assert.equal(h.elements.send.disabled,true);
});
test('metadata-only revisions preserve the site document while changed files reload it',async()=>{
 const h=harness(clean);await h.flush();const frame=h.elements['local-preview'];
 h.set({...clean,local:{...clean.local,revision:'new-metadata',saved_commit:'saved2'}});await h.tick(1000);
 assert.equal(frame.srcWrites,0);
 h.set({...clean,local:{...clean.local,revision:'new-files',tree_sha256:'tree2'}});await h.tick(1000);
 assert.equal(frame.srcWrites,1);assert.match(frame.src,/v=tree2/);
});
test('final status and delivery error text are not rewritten on every poll',async()=>{
 const data={...clean,chat:{available:true,busy:false,requests:[],delivery:{status:'failed',error:'account_changed'}},operation:{state:'observed',local_tree_sha256:'tree1',arguments:{operation_id:'op',saved_commit:'saved1'},receipt:{status:'completed',result:{preview_id:'pv',preview_url:'https://example.invalid/dev'}}},confirmation:{state:'observed',binding:{mode:'publish',preview_id:'pv'},operation:{status:'completed',operation_id:'pub'}}};
 const h=harness(data);await h.flush();
 const hint=h.elements['next-step'],error=h.elements['chat-availability'];
 const writes=[hint.textWrites,error.textWrites];
 for(let i=0;i<3;i++)await h.tick(1000);
 assert.deepEqual([hint.textWrites,error.textWrites],writes);
});

test('resume sends the original request while preserving the new draft and selection',async()=>{
 const row={id:'resume-id',text:'以前の修正依頼',status:'apply_pending',reply:'再開できます'};
 let h;h=harness({...clean,chat:{available:true,busy:false,requests:[row]}},url=>{
  if(url==='/api/chat')h.set({...clean,chat:{available:true,busy:false,requests:[{...row,status:'completed',reply:'修正しました'}]}});
  return false;
 });await h.flush();const input=h.elements['chat-input'];
 input.value='次の修正の下書き';input.selectionStart=1;input.selectionEnd=4;
 h.elements['chat-retry'].listeners.click({isTrusted:true});await h.flush();
 const sends=h.calls.filter(c=>c.url==='/api/chat');assert.equal(sends.length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(sends[0].body)),{request_id:'resume-id',text:'以前の修正依頼'});
 assert.equal(input.value,'次の修正の下書き');assert.deepEqual([input.selectionStart,input.selectionEnd],[1,4]);
 assert.equal(h.elements['chat-retry'].hidden,true);
});

test('legacy pending undo recovery preserves draft input and message nodes',async()=>{
 const row={id:'undo-id',text:'元の依頼',reply:'修正しました',status:'completed',can_undo:true,undo_status:'pending'};
 let h;h=harness({...clean,chat:{available:true,busy:false,requests:[row]}},url=>{
  if(url==='/api/undo')h.set({...clean,chat:{available:true,busy:false,requests:[{...row,can_undo:false,undo_status:'completed'}]}});
  return false;
 });await h.flush();const message=h.elements['chat-messages'].children[1];const body=message.children[1];
 const input=h.elements['chat-input'];input.value='次の依頼';input.selectionStart=1;input.selectionEnd=3;
 const undo=message.children[2];assert.equal(undo.textContent,'中断した変更を再開');
 undo.listeners.click({isTrusted:false});await h.flush();assert.equal(h.calls.filter(c=>c.url==='/api/undo').length,0);
 undo.listeners.click({isTrusted:true});await h.flush();
 const sends=h.calls.filter(c=>c.url==='/api/undo');assert.equal(sends.length,1);assert.equal(sends[0].body.request_id,'undo-id');
 assert.equal(input.value,'次の依頼');assert.deepEqual([input.selectionStart,input.selectionEnd],[1,3]);
 assert.equal(h.elements['chat-messages'].children[1].children[1],body);
 assert.equal(undo.hidden,true);assert.equal(message.children[3].textContent,'');assert.equal(message.children[3].hidden,true);
});
test('pending undo blocks sending and publishing while offering the same undo action',async()=>{
 const row={id:'undo-pending',text:'修正',reply:'修正しました',status:'completed',can_undo:true,undo_status:'pending'};
 const h=harness({...clean,chat:{available:true,busy:false,requests:[row]}});await h.flush();
 assert.equal(h.elements.prepare.disabled,true);assert.equal(h.elements.send.disabled,true);
 assert.equal(h.elements['chat-messages'].children[1].children[2].textContent,'中断した変更を再開');
});
test('opening focuses the composer and input grows without rewriting the draft',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[]}});await h.flush();
 h.elements.launcher.listeners.click();assert.equal(h.elements['chat-input'].focused,true);
 const input=h.elements['chat-input'];input.value='複数行の依頼';input.selectionStart=1;input.selectionEnd=3;input.scrollHeight=180;
 input.listeners.input();assert.equal(input.style.height,'136px');assert.equal(input.value,'複数行の依頼');
 assert.deepEqual([input.selectionStart,input.selectionEnd],[1,3]);
});

test('lost undo response clears its notice when the same undo is read back',async()=>{
 const row={id:'undo-lost',text:'修正',reply:'変更しました',status:'completed',can_undo:true,undo_status:'pending'};
 let h;h=harness({...clean,chat:{available:true,busy:false,requests:[row]}},url=>{
  if(url==='/api/undo'){h.set({...clean,chat:{available:true,busy:false,requests:[{...row,undo_status:'applying'}]}});return true;}
  return false;
 });await h.flush();
 h.elements['chat-messages'].children[1].children[2].listeners.click({isTrusted:true});await h.flush();
 assert.match(h.elements['chat-availability'].textContent,/結果を確認/);
 h.set({...clean,chat:{available:true,busy:false,requests:[{...row,undo_status:'completed',can_undo:false}]}});await h.tick(1000);
 assert.doesNotMatch(h.elements['chat-availability'].textContent,/結果を確認/);
 assert.equal(h.calls.filter(c=>c.url==='/api/undo').length,1);
});

test('completed edits use conversation without an undo button',async()=>{
 const h=harness({...clean,chat:{available:true,busy:false,requests:[{id:'edit-done',text:'修正して',reply:'変更しました',status:'completed',can_undo:true}]}});await h.flush();
 assert.equal(h.elements['chat-messages'].children[1].children[2].hidden,true);
 assert.equal(h.elements['chat-input'].disabled,false);
 assert.ok(h.calls.every(call=>call.url!=='/api/undo'));
});

test('environment versions show content identity while dates stay in tooltips',async()=>{
 const h=harness({...clean,environments:{dev:{version:12,updated_at:1794035798},production:{version:12,updated_at:1794036870}}});await h.flush();
 assert.equal(h.elements['dev-version'].textContent,'v12');
 assert.equal(h.elements['production-version'].textContent,'v12');
 assert.match(h.elements['dev-environment'].getAttribute('title'),/確認用に反映 .*16:16:38/);
 assert.match(h.elements['production-environment'].getAttribute('title'),/公開 .*16:34:30/);
 assert.equal(h.elements['next-step'].hidden,true);
 assert.equal(h.elements['local-status'],undefined);assert.equal(h.elements['last-confirmation'],undefined);
 const writes=h.elements['dev-version'].textWrites;await h.tick(1000);assert.equal(h.elements['dev-version'].textWrites,writes);
 h.set({...clean,environments:{dev:{version:13,updated_at:1794035798},production:{version:12,updated_at:1794036870}}});await h.tick(1000);
 assert.equal(h.elements['dev-version'].textContent,'v13');assert.equal(h.elements['production-version'].textContent,'v12');
});
test('missing version never falls back to dates or locally invented numbers',async()=>{
 const h=harness({...clean,environments:{dev:{version:'12',updated_at:'2026-11-07'},production:{version:-1,updated_at:-1}}});await h.flush();
 assert.equal(h.elements['dev-version'].textContent,'—');assert.equal(h.elements['production-version'].textContent,'—');
 assert.match(h.elements['dev-environment'].getAttribute('title'),/日時未取得/);
});

const publicationCard={state:'prepared',site_name:'サンプルクリニック',expires_at:999999,
 binding:{mode:'publish',preview_id:'pv',source_commit:'private-sha',artifact_sha256:'private-hash'},
 preview_url:'https://preview.example.invalid',production_url:'https://example.invalid',
 publication_details:{from_version:2,to_version:3,already_current:false,changes:{items:[{title:'診療時間',kind:'changed',before:'18時まで',after:'19時まで'}],additional_count:0}}};
test('publication dialog shows exact version transition and changed content safely',async()=>{
 const h=harness({...clean,can_confirm:true,confirmation:publicationCard});await h.flush();
 assert.equal(h.elements['dialog-title'].textContent,'公開内容を確認');
 assert.equal(h.elements['version-change'].textContent,'公開中 v2 → 公開後 v3');
 assert.equal(h.elements['change-list'].children[0].children[1].textContent,'18時まで');
 assert.equal(h.elements['change-list'].children[0].children[2].textContent,'19時まで');
 assert.equal(h.elements.confirm.hidden,false);assert.equal(h.elements.confirm.disabled,false);
 assert.equal(h.elements['confirm-status'].hidden,true);assert.equal(h.elements.artifact,undefined);
 const node=h.elements['change-list'].children[0];h.select(node);await h.tick(1000);
 assert.equal(h.elements['change-list'].children[0],node);
});
test('published result has its changes and public link without publication controls',async()=>{
 const h=harness({...clean,confirmation:{...publicationCard,state:'observed',operation:{status:'completed',operation_id:'internal-operation-id'}}});await h.flush();
 assert.equal(h.elements['dialog-title'].textContent,'公開済みの内容');
 assert.equal(h.elements['version-change'].textContent,'公開した版 v3');
 assert.equal(h.elements.confirm.hidden,true);assert.equal(h.elements.withdraw.hidden,true);
 assert.equal(h.elements['preview-link'].hidden,true);assert.equal(h.elements['production-link'].textContent,'公開サイトを開く');
 assert.equal(h.elements['confirm-status'].textContent,'');
});
test('in-flight, unavailable summaries, and connection tests remain distinct',async()=>{
 const h=harness({...clean,confirmation:{...publicationCard,state:'unknown',publication_details:null}});await h.flush();
 assert.equal(h.elements['dialog-title'].textContent,'公開結果を確認しています');assert.equal(h.elements.confirm.hidden,true);
 assert.match(h.elements['changes-note'].textContent,/変更内容を取得できません/);
 h.set({...clean,can_confirm:true,confirmation:{...publicationCard,binding:{...publicationCard.binding,mode:'connection_test'},publication_details:{...publicationCard.publication_details,already_current:true}}});await h.tick(1000);
 assert.equal(h.elements['dialog-title'].textContent,'接続を確認');assert.equal(h.elements.confirm.hidden,false);
 assert.equal(h.elements['change-list'].hidden,true);
});
