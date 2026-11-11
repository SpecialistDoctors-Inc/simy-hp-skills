'use strict';
const byId = id => document.getElementById(id);
const csrf = document.querySelector('meta[name=hp-csrf]').content;
let state, busy = false, remotePoll = 0, refreshFailures = 0, connectionIssue = false, connectionError = null, displayError = null, chatSending = false, pendingChat = null, chatSignature = null, chatError = null, undoSending = null, pendingUndo = null;
const messageNodes = new Map();
let siteRevision;
const labels = { queued:'受付済み',running:'処理中',retry_wait:'再開待ち',reconciling:'結果を照合中',reporting:'記録・通知を確認中',completed:'完了',needs_attention:'確認が必要',failed:'失敗',cancelled:'キャンセル済み',published_after_cancel_or_revocation:'取消し後に公開済みを確認' };
const errors = {preview_request_rejected:'確認用ページの依頼が受け付けられませんでした。もう一度「確認用に反映」を押してください。',confirmation_rejected:'サービスが確認を拒否しました。古い確認は再送せず、新しい確認内容を開き直してください。',confirmation_withdrawal_uncertain:'取り消しの結果を確認中です。自動で同じ確認の結果を調べます。',save_with_codex_first:'未保存または保存待ちです。Codexで保存を完了してください。',local_files_changed:'ローカルの内容が変わりました。表示を確認し直してください。',local_preview_mismatch:'保存した内容と確認ページが一致しません。Codexで保存後、新しい確認ページを作ってください。',confirmation_response_uncertain:'送信結果が不明です。同じ確認IDの結果を照合します。再送しません。',operation_response_uncertain:'受付結果が不明です。自動で同じ依頼の結果を確認します。',confirmation_uncertain:'前回の確認結果が不明です。自動で同じ依頼の結果を確認します。',reopen_confirmation:'確認内容を開き直してください。',confirmation_changed:'確認対象または期限が変わりました。公開は依頼せず確認し直してください。',preview_not_ready:'検証済みの確認ページがまだありません。',hp_request_rejected:'サービスが操作を受け付けませんでした。Codexで本人・権限・操作状況を確認してください。',account_changed:'Codexの接続アカウントが変わりました。操作を止めて確認してください。',account_site_mismatch:'この作業領域と接続先を確認できません。Codexで本人・対象サイトを確認してください。',action_in_progress:'別の操作を確認しています。しばらく待ってください。',inspect_original_operation:'前回の処理を元の操作IDで確認してください。',control_response_unavailable:'接続結果を確認できません。再送せず、Codexで状況を確認してください。',local_workspace_unavailable:'作業フォルダーを確認できません。保存処理中なら完了を待ってください。'};
function setText(element, value) {
 const text = String(value ?? '');
 if (element.textContent !== text) element.textContent = text;
}
function syncPreview(local) {
 if (!local.ready || !local.tree_sha256) return;
 if (siteRevision !== undefined && siteRevision !== local.tree_sha256) {
  const frame = byId('local-preview');
  const url = new URL(frame.src);
  url.searchParams.set('v', local.tree_sha256);
  frame.src = url.href;
 }
 siteRevision = local.tree_sha256;
}
function safeLink(value) {
 try {const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:null;} catch {return null;}
}
const updateDateFullFormat = new Intl.DateTimeFormat('ja-JP', {
 year:'numeric', month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false, timeZone:'Asia/Tokyo'
});
function renderEnvironmentVersions(environments) {
 for(const [environment, prefix, label] of [['dev','dev','確認用に反映'],['production','production','公開']]){
  const version = environments?.[environment]?.version;
  const timestamp = environments?.[environment]?.updated_at;
  const validVersion = Number.isSafeInteger(version)&&version>0;
  const validTime = Number.isInteger(timestamp)&&timestamp>0&&timestamp<8640000000000;
  const text = validVersion?'v'+version:'—';
  setText(byId(prefix+'-version'), text);
  const details = label+' '+(validTime?updateDateFullFormat.format(timestamp*1000)+'（日本時間）':'日時未取得');
  byId(prefix+'-environment').setAttribute('title', details);
  byId(prefix+'-environment').setAttribute('aria-label', label+' '+text+' '+details);
 }
}
function display(next) {
 state=next;renderChat(state.chat);const local=state.local;syncPreview(local);const editing=!!state.chat?.busy;const applyingPending=state.chat?.requests?.some(row=>row.status==='apply_pending'||['applying','pending'].includes(row.undo_status));const clean=local.ready&&!local.dirty&&!local.pending;
 setText(byId('account'), state.identity?.site_name||'');
 const op=state.operation, receipt=op?.receipt;
 const hasConversation=!!state.chat?.requests?.length;
 byId('workflow').hidden=!(state.identity||hasConversation||local.dirty||local.pending||op||state.confirmation);
 const previewUrl=safeLink(receipt?.result?.preview_url);
 const previewReady=!editing&&clean&&op?.state==='observed'&&!!previewUrl&&op?.local_tree_sha256===local.tree_sha256&&op?.arguments?.saved_commit===local.saved_commit&&!!receipt?.result?.preview_id&&['completed','reporting'].includes(receipt.status);
 renderEnvironmentVersions(state.environments);
 let nextStep = '';
 if(state.chat?.delivery?.status==='running')nextStep='確認用サイトへの反映を準備しています…';
 else if(!local.ready||local.pending)nextStep='保存を確認しています…';
 else if(op&&['unknown','unreceived','sending'].includes(op.state))nextStep='確認用サイトへの反映結果を確認しています…';
 else if(receipt&&['queued','running','retry_wait','reconciling','reporting'].includes(receipt.status))nextStep='devに反映しています…';
 else if(receipt&&['failed','needs_attention'].includes(receipt.status))nextStep='確認用サイトへの反映に確認が必要です。';
 setText(byId('connection-status'), connectionIssue&&!connectionError?'接続を確認できません。自動で再確認しています。':!state.identity&&!connectionError?'接続を確認しています…':'');
 setText(byId('error'), errors[connectionError||displayError]||((connectionError||displayError)?'処理を確認できません。Codexに確認を依頼してください。':''));
 byId('error').hidden=!(connectionError||displayError);
 byId('dev-link').hidden=!previewReady;
 if(previewReady)byId('dev-link').href=previewUrl;
 byId('prepare').disabled=busy||editing||applyingPending||!state.identity||connectionIssue||!local.ready||(!state.chat?.available&&!clean)||!!(op&&op.state!=='rejected'&&(!receipt||!['completed','failed','cancelled'].includes(receipt.status)));
 byId('open').hidden=!previewReady;
 byId('open').disabled=busy||editing||applyingPending||!state.identity||connectionIssue||!previewReady;
 byId('retry-preview').hidden=!op||!['unknown','unreceived','sending'].includes(op.state);byId('retry-preview').disabled=busy||!local.ready;
 byId('resume').hidden=receipt?.status!=='needs_attention';byId('resume').disabled=busy||!clean;
 const card=state.confirmation;
 const publishing=!!card?.operation&&['queued','running','retry_wait','reconciling','reporting','needs_attention'].includes(card.operation.status);
 const alreadyPublished=card?.operation?.status==='completed'&&card.binding.preview_id===receipt?.result?.preview_id;
 byId('open').hidden=!previewReady||alreadyPublished||publishing;
 byId('prepare').hidden=previewReady&&!['failed','interrupted'].includes(state.chat?.delivery?.status);
 if(publishing)nextStep=card.operation.status==='needs_attention'?'本番への反映に確認が必要です。':'本番への反映結果を確認しています…';
 setText(byId('next-step'), nextStep);
 byId('previous').hidden=!card||card.state==='cancelled';
 byId('next-step').hidden=!nextStep;
 if(card)renderConfirmation(card, {clean, editing, applyingPending});
}
let changeListSignature = null;
function renderConfirmation(card, local) {
 const details = card.publication_details;
 const status = card.operation?.status;
 const tested = card.state==='tested';
 const testMode = card.binding.mode==='connection_test';
 const completed = ['completed','published_after_cancel_or_revocation'].includes(status)
  || (status==='reporting' && card.operation?.result?.receipt?.verified===true);
 const pending = ['queued','running','retry_wait','reconciling','reporting'].includes(status)
  || ['sending','unknown','withdrawing','withdraw_unknown'].includes(card.state);
 const failed = ['failed','cancelled','needs_attention'].includes(status)||['rejected','cancelled'].includes(card.state);
 const same = !testMode&&details?.already_current===true;
 let title = '公開内容を確認';
 if(testMode)title=tested?'接続を確認しました':'接続を確認';
 else if(completed)title='公開済みの内容';
 else if(pending)title='公開結果を確認しています';
 else if(failed)title='公開状況を確認してください';
 else if(same)title='この内容は公開済みです';
 setText(byId('dialog-title'), title);
 setText(byId('site-name'), card.site_name||state.identity?.site_name||'');
 const from = Number.isSafeInteger(details?.from_version)&&details.from_version>0?'v'+details.from_version:'以前の内容';
 const to = Number.isSafeInteger(details?.to_version)&&details.to_version>0?'v'+details.to_version:'確認した内容';
 setText(byId('version-change'), testMode?'':completed||same?'公開した版 '+to:'公開中 '+from+' → 公開後 '+to);
 byId('version-change').hidden=testMode;
 const changes = details?.changes;
 const signature = JSON.stringify(testMode?null:changes);
 if(signature!==changeListSignature){
  byId('change-list').replaceChildren();
  for(const item of testMode?[]:(changes?.items||[])){
   const row=document.createElement('li');
   const heading=document.createElement('strong');heading.className='change-title';heading.textContent=item.title+(item.kind==='removed'?'（削除）':'');
   if(item.kind!=='description')row.append(heading);
   if(item.before){const before=document.createElement('span');before.className='change-before';before.textContent=item.before;row.append(before);}
   if(item.after){const after=document.createElement('span');after.className='change-after';after.textContent=(item.kind==='added'?'追加　':'')+item.after;row.append(after);}
   byId('change-list').append(row);
  }
  changeListSignature=signature;
 }
 byId('change-list').hidden=testMode||!changes?.items?.length;
 let note='';
 if(!testMode&&!same&&!changes)note='変更内容を取得できません。確認用サイトで内容を確認してください。';
 else if(changes?.additional_count)note='ほか'+changes.additional_count+'件の変更';
 setText(byId('changes-note'),note);byId('changes-note').hidden=!note;
 let message='';
 if(testMode&&!tested)message='サイトを公開せず、接続だけを確認します。';
 else if(status==='needs_attention')message='公開処理が止まっています。チャットで確認を依頼してください。';
 else if(['failed','cancelled'].includes(status)||card.state==='cancelled')message='この公開処理は終了しました。再度公開する場合は確認画面を開き直してください。';
 else if(card.state==='rejected')message='確認期限や内容が変わりました。確認画面を開き直してください。';
 else if(!completed&&!pending&&!same&&!tested&&!state.can_confirm)message='確認画面を開き直してください。';
 setText(byId('confirm-status'),message);byId('confirm-status').hidden=!message;
 const preview=safeLink(card.preview_url),production=safeLink(card.production_url);
 byId('preview-link').hidden=!preview||completed||same||testMode;
 if(preview)byId('preview-link').href=preview;
 byId('production-link').hidden=!production||testMode;
 if(production)byId('production-link').href=production;
 setText(byId('production-link'),completed||same?'公開サイトを開く':'公開中を見る');
 setText(byId('confirm'),testMode?'接続を確認する':'公開する');
 byId('confirm').hidden=completed||pending||failed||same||tested;
 byId('confirm').disabled=busy||local.editing||local.applyingPending||connectionIssue||!local.clean||!state.can_confirm||card.expires_at*1000<=Date.now();
 byId('withdraw').hidden=completed||tested||same||!['sending','unknown','withdrawing','withdraw_unknown'].includes(card.state);
 byId('withdraw').disabled=busy;
}

async function readState(){
 const response=await fetch('/api/state',{cache:'no-store'});
 const data=await response.json();if(!response.ok)throw data;
 if(displayError==='local_workspace_unavailable')displayError=null;
 display(data);
}
async function action(name){
 if(busy||chatSending||undoSending)return;
 busy=true;if(name!=='refresh')displayError=null;if(state)display(state);
 try {
  const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-HP-CSRF':csrf},body:JSON.stringify({action:name,revision:state.local.revision})});
  const data=await response.json();if(!response.ok)throw data;
  if(name==='refresh'){
   connectionError=null;
   connectionIssue=!!data.readback_errors?.length;
   refreshFailures=connectionIssue?Math.min(refreshFailures+1,4):0;
   if(!connectionIssue&&['operation_response_uncertain','confirmation_response_uncertain','confirmation_uncertain','confirmation_withdrawal_uncertain'].includes(displayError))displayError=null;
  }
  display(data);if(name==='open_confirmation')byId('confirmation').showModal();
 } catch(error) {
  if(name==='refresh'){
   if(error.error==='local_files_changed'){
    // An edit racing a read is not a connection failure; re-read its revision.
    remotePoll=0;
   } else {
    connectionIssue=true;refreshFailures=Math.min(refreshFailures+1,4);
    connectionError=error.error&&error.error!=='control_response_unavailable'?error.error:null;
   }
  } else displayError=error.error||'control_response_unavailable';
  try{await readState();}catch{displayError='local_workspace_unavailable';}
 } finally {busy=false;if(state)display(state);}
}
byId('prepare').addEventListener('click',event=>{if(event.isTrusted)void action('prepare_preview');});
byId('open').addEventListener('click',event=>{if(event.isTrusted)void action('open_confirmation');});
byId('confirm').addEventListener('click',event=>{if(event.isTrusted)void action('confirm');});
byId('retry-preview').addEventListener('click',event=>{if(event.isTrusted)void action('retry_preview');});
byId('withdraw').addEventListener('click',event=>{if(event.isTrusted)void action('withdraw');});
byId('resume').addEventListener('click',event=>{if(event.isTrusted)void action('resume');});
function setChatOpen(open){
 byId('menu').hidden=!open;
 byId('launcher').setAttribute('aria-expanded',String(open));
 byId('launcher').setAttribute('aria-label',open?'HP編集チャットを閉じる':'HP編集チャットを開く');
 if(open){
  (byId('chat-input').disabled?byId('toggle'):byId('chat-input')).focus({preventScroll:true});
  resizeComposer();
 }else byId('launcher').focus();
}
byId('launcher').addEventListener('click',()=>setChatOpen(byId('menu').hidden));
byId('toggle').addEventListener('click',()=>setChatOpen(false));
byId('menu').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();setChatOpen(false);}});
function renderChat(chat){
 const available=!!chat?.available;
 // Keep the draft editable and selectable while an earlier request runs.
 byId('chat-input').disabled=!available;
 byId('send').disabled=!available||chatSending||!!chat?.busy||!!undoSending;
 let availability = !available ? 'この画面ではチャットに接続できません。' : pendingChat ? '送信結果を確認しています…' : chat?.busy ? '処理中です…' : 'Enterで送信 · Shift＋Enterで改行';
 const requests=chat?.requests||[];
 if(pendingUndo&&!undoSending&&requests.some(row=>row.id===pendingUndo&&['completed','pending','conflict'].includes(row.undo_status))){
  pendingUndo=null;chatError=null;
 }
 byId('welcome').hidden=requests.length>0;
 byId('suggest-copy').disabled=!available||!!chat?.busy;
 byId('suggest-hours').disabled=!available||!!chat?.busy;
 const applyPending=requests.find(r=>r.status==='apply_pending');
 byId('chat-retry').hidden=!applyPending;byId('chat-retry').disabled=chatSending||!!chat?.busy;
 if(applyPending||requests.some(row=>['applying','pending'].includes(row.undo_status)))byId('send').disabled=true;
 if(pendingChat&&requests.some(r=>r.id===pendingChat.id&&(!pendingChat.resume||r.status!=='apply_pending'))){
  const input = byId('chat-input');
  if (!pendingChat.resume && !chatComposing && input.value.trim() === pendingChat.text){input.value='';resizeComposer();}
  pendingChat=null;chatError=null;availability=chat?.busy?'処理中です…':'Enterで送信 · Shift＋Enterで改行';
 }
 const signature=JSON.stringify(requests);
 if(signature!==chatSignature){
  const firstRender=chatSignature===null;
  chatSignature=signature;
  const scroller=byId('menu-body');
  const nearBottom=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<100;
  const log = byId('chat-messages');
  const desired = [];
  const activeKeys = new Set();
  for (const row of requests) {
   const reply = row.reply || (['running', 'applying'].includes(row.status) ? '修正しています…' : '');
   for (const [role, text] of [['user', row.text], ['assistant', reply]]) {
    if (!text) continue;
    const key = `${row.id}:${role}`;
    activeKeys.add(key);
    let nodes = messageNodes.get(key);
    if (!nodes) {
     const message = document.createElement('div');
     message.className = 'message ' + role;
     const speaker = document.createElement('p');
     speaker.className = 'speaker';
     speaker.textContent = role === 'user' ? 'あなた' : 'Codex';
     const body = document.createElement('p');
     body.className = 'message-body';
     message.append(speaker, body);
     const undo = document.createElement('button');
     undo.type = 'button';
     undo.className = 'undo-edit';
     undo.addEventListener('click', event => {if(event.isTrusted)void undoEdit(row.id);});
     const undoState = document.createElement('p');
     undoState.className = 'undo-state';
     message.append(undo, undoState);
     nodes = {message, body, undo, undoState};
     messageNodes.set(key, nodes);
    }
    setText(nodes.body, text);
    // Keep recovery reachable for an inverse already started by an older version.
    nodes.undo.hidden=role!=='assistant'||row.undo_status!=='pending';
    setText(nodes.undo, '中断した変更を再開');
    const undoNotice=row.undo_status==='conflict'?'別の編集と重なりました。現在の内容を確認して、修正を続けてください。':row.undo_status==='pending'?(row.undo_error==='undo_conflict'?'後の変更と重なっています。内容を確認してください。':'戻す処理が止まりました。再開できます。'):'';
    setText(nodes.undoState, role==='assistant'?undoNotice:'');
    nodes.undoState.hidden=!nodes.undoState.textContent;
    desired.push(nodes.message);
   }
  }
  for (const [key, nodes] of messageNodes) {
   if (!activeKeys.has(key)) {
    nodes.message.remove();
    messageNodes.delete(key);
   }
  }
  // Leave unchanged nodes in place so browser selections keep their anchors.
  desired.forEach((message, index) => {
   if (log.children[index] !== message) log.insertBefore(message, log.children[index] || null);
  });
  const selection = document.getSelection?.();
  const readingSelection = selection && !selection.isCollapsed &&
   (log.contains(selection.anchorNode) || log.contains(selection.focusNode));
  if (!readingSelection && (firstRender || nearBottom || chatSending)){
   scroller.scrollTop=scroller.scrollHeight;
   byId('latest-reply').hidden=true;
  }else if(!firstRender){
   byId('latest-reply').hidden=false;
  }
 }
 for(const nodes of messageNodes.values())nodes.undo.disabled=!!chat?.busy||chatSending||!!undoSending;
 if(undoSending)availability='修正を戻しています…';
 if(chatError)availability=chatError;
 if(chat?.delivery?.status==='failed'||chat?.delivery?.status==='interrupted'){
  availability=errors[chat.delivery.error]||'確認用サイトの準備が止まりました。「確認用に反映」で同じ処理を再開できます。';
 }
 setText(byId('chat-availability'), availability);
}
async function undoEdit(requestId) {
 if (busy || chatSending || undoSending || state?.chat?.busy) return;
 if(pendingUndo&&pendingUndo!==requestId){
  chatError='前の取り消し結果を確認しています。';display(state);return;
 }
 undoSending=requestId;pendingUndo=requestId;
 chatError=null;
 display(state);
 try {
  const response=await fetch('/api/undo', {method:'POST', headers:{'Content-Type':'application/json','X-HP-CSRF':csrf}, body:JSON.stringify({request_id:requestId})});
  const data=await response.json();
  if(!response.ok)throw data;
  pendingUndo=null;
  display(data);
 }catch(error){
  const messages={workspace_busy:'保存処理が進行中です。少し待ってから戻してください。',undo_conflict:'この後の編集と重なっています。Codexに、戻したい箇所を伝えてください。',undo_unavailable:'この修正は戻せません。現在の内容を確認してください。',save_in_progress:'保存中です。完了してから戻してください。',chat_busy:'処理が完了してから戻してください。',local_apply_pending:'途中の修正を先に再開してください。'};
  if(messages[error.error])pendingUndo=null;
  chatError=messages[error.error]||'戻す処理の結果を確認しています。同じボタンから再確認できます。';
  try{await readState();}catch{}
 }finally{
  undoSending=null;
  if(state)display(state);
 }
}
function resizeComposer() {
 const input=byId('chat-input');
 input.style.height='auto';
 input.style.height=Math.min(136,Math.max(42,input.scrollHeight))+'px';
}
byId('chat-input').addEventListener('input', resizeComposer);
byId('latest-reply').addEventListener('click',()=>{
 byId('menu-body').scrollTop=byId('menu-body').scrollHeight;
 byId('latest-reply').hidden=true;
});
byId('menu-body').addEventListener('scroll',()=>{
 const body=byId('menu-body');
 if(body.scrollHeight-body.scrollTop-body.clientHeight<80)byId('latest-reply').hidden=true;
});
async function sendChat(resumeRequest = null){
 if(chatSending||busy||undoSending||state?.chat?.busy||!state?.chat?.available)return;
 const text=resumeRequest?.text??byId('chat-input').value.trim();if(!text)return;
 if(pendingChat&&(pendingChat.text!==text||(resumeRequest&&pendingChat.id!==resumeRequest.id))){setText(byId('chat-availability'), '前の送信結果を確認中です。文章を変えずに再送してください。');return;}
 pendingChat=pendingChat||(resumeRequest?{...resumeRequest,resume:true}:{id:crypto.randomUUID(),text});
 const request = pendingChat;
 chatSending=true;chatError=null;display(state);
 try{
  const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-HP-CSRF':csrf},body:JSON.stringify({request_id:request.id,text:request.text})});
  const data=await response.json();if(!response.ok)throw data;
  if(request.resume&&pendingChat?.id===request.id){pendingChat=null;chatError=null;}
  display(data);
 }catch(error){
  const rejected={invalid_chat_request:'修正内容は4000文字以内で入力してください。',chat_busy:'前の処理が終わるまでお待ちください。',save_in_progress:'保存中です。完了してから依頼してください。',site_text_too_large:'このサイトはチャットで扱えるサイズを超えています。',site_text_encoding:'UTF-8以外のファイルがあります。運用担当に確認してください。',local_apply_pending:'止まっている修正を再開してください。',chat_history_full:'会話の保存上限に達しました。運用担当にご相談ください。',chat_request_conflict:'依頼の内容を確認できません。ページを開き直してください。'};
  if(rejected[error.error]){pendingChat=null;chatError=rejected[error.error];}
  else chatError='送信結果を確認中です。再送する場合も同じ依頼を使います。';
  try{await readState();}catch{}
 }finally{chatSending=false;if(state)display(state);}
}
byId('chat-retry').addEventListener('click',event=>{if(!event.isTrusted)return;const row=state?.chat?.requests.find(r=>r.status==='apply_pending');if(row)void sendChat({id:row.id,text:row.text});});
for(const [id,text] of [['suggest-copy','文章を直したいです。'],['suggest-hours','診療時間を変更したいです。']]){
 byId(id).addEventListener('click',()=>{if(byId('chat-input').disabled)return;byId('chat-input').value=text;resizeComposer();byId('chat-input').focus();});
}
let chatComposing = false;
byId('chat-input').addEventListener('compositionstart', () => { chatComposing = true; });
byId('chat-input').addEventListener('compositionend', () => { chatComposing = false; });
byId('chat-input').addEventListener('keydown', event => {
 if (!event.isTrusted || event.defaultPrevented || event.key !== 'Enter') return;
 // keyCode 229 also covers Safari's IME confirmation event when isComposing
 // has already become false. Shift+Enter keeps the textarea's native newline.
 if (chatComposing || event.isComposing || event.keyCode === 229 || event.shiftKey ||
     event.altKey || event.ctrlKey || event.metaKey) return;
 event.preventDefault();
 if (event.repeat || byId('chat-input').disabled || byId('send').disabled) return;
 void sendChat();
});
byId('composer').addEventListener('submit',event=>{event.preventDefault();if(event.isTrusted)void sendChat();});
byId('previous').addEventListener('click',()=>byId('confirmation').showModal());
byId('close').addEventListener('click',()=>byId('confirmation').close());
async function poll(){try{
 // The local tree hash controls iframe reloads. Metadata/status updates only
 // refresh the widget, preserving selections in an unchanged site document.
 await readState();
 // Read-only reconciliation is automatic, including initial connection failures
 // and ambiguous results. Only explicit user actions can submit or confirm work.
 const active=state?.operation&&(['unknown','unreceived','sending'].includes(state.operation.state)||['queued','running','retry_wait','reconciling','reporting'].includes(state.operation.receipt?.status));
 const uncertain=['prepared','sending','unknown','withdrawing','withdraw_unknown'].includes(state?.confirmation?.state)||(state?.confirmation?.state==='observed'&&['queued','running','retry_wait','reconciling','reporting'].includes(state.confirmation.operation?.status));
 const delay=connectionIssue?Math.min(60000,5000*2**refreshFailures):(active||uncertain||!state?.identity?5000:30000);
 if(state&&!state.chat?.busy&&!busy&&!chatSending&&!undoSending&&Date.now()-remotePoll>=delay){remotePoll=Date.now();await action('refresh');}
 }catch(error){displayError='local_workspace_unavailable';if(state)display(state);else{setText(byId('error'), errors[displayError]);byId('error').hidden=false;}}setTimeout(poll,1000);}
void readState().then(async()=>{remotePoll=Date.now();await action('refresh');void poll();}).catch(()=>void poll());
