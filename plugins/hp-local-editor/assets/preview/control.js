'use strict';
const byId = id => document.getElementById(id);
const csrf = document.querySelector('meta[name=hp-csrf]').content;
let state, busy = false, revision, remotePoll = 0, unknownPolls = 0;
const labels = { queued:'受付済み',running:'処理中',retry_wait:'再開待ち',reconciling:'結果を照合中',reporting:'記録・通知を確認中',completed:'完了',needs_attention:'確認が必要',failed:'失敗',cancelled:'キャンセル済み',published_after_cancel_or_revocation:'取消し後に公開済みを確認' };
const errors = {confirmation_rejected:'サービスが確認を拒否しました。古い確認は再送せず、新しい確認内容を開き直してください。',confirmation_withdrawal_uncertain:'取り消しの結果を確認中です。再送せず、同じ確認の状況を確認してください。',save_with_codex_first:'未保存または保存待ちです。Codexで保存を完了してください。',local_files_changed:'ローカルの内容が変わりました。表示を確認し直してください。',local_preview_mismatch:'保存した内容と確認ページが一致しません。Codexで保存後、新しい確認ページを作ってください。',confirmation_response_uncertain:'送信結果が不明です。同じ確認IDの結果を照合します。再送しません。',operation_response_uncertain:'受付結果が不明です。元の操作IDで状況を確認してください。',confirmation_uncertain:'前回の確認結果が不明です。新しく公開を依頼せず、状況を確認してください。',reopen_confirmation:'確認内容を開き直してください。',confirmation_changed:'確認対象または期限が変わりました。公開は依頼せず確認し直してください。',preview_not_ready:'検証済みの確認ページがまだありません。',hp_request_rejected:'サービスが操作を受け付けませんでした。Codexで本人・権限・操作状況を確認してください。',account_changed:'Codexの接続アカウントが変わりました。操作を止めて確認してください。',account_site_mismatch:'この作業領域と接続先を確認できません。Codexで本人・対象サイトを確認してください。',action_in_progress:'別の操作を確認しています。しばらく待ってください。',inspect_original_operation:'前回の処理を元の操作IDで確認してください。',control_response_unavailable:'接続結果を確認できません。再送せず、Codexで状況を確認してください。',local_workspace_unavailable:'作業フォルダーを確認できません。保存処理中なら完了を待ってください。'};
function display(next) {
 state=next;const local=state.local;const clean=local.ready&&!local.dirty&&!local.pending;
 byId('local-status').textContent=!local.ready?'Codexの保存処理を待っています。':local.pending?'保存パッケージが待機中です。Codexで保存を完了してください。':local.dirty?'未保存の変更があります。表示は手元の内容です。':'保存済みの内容をローカル表示しています。';
 byId('account').textContent=state.identity?`${state.identity.site_name} · ${state.identity.email}`:'Codexの接続を状況確認で照合してください。';
 const op=state.operation, receipt=op?.receipt;
 byId('operation-status').textContent=op?`確認ページ: ${op.state==='rejected'?'前の依頼は未受付。新しい内容で作成できます。':['unknown','unreceived','sending'].includes(op.state)?'結果の照合が必要':labels[receipt?.status]||'結果を確認中'}`:'';
 if(op)byId('operation-id').textContent='操作ID: '+op.arguments.operation_id;
 byId('prepare').disabled=busy||!clean||!!(op&&op.state!=='rejected'&&(!receipt||!['completed','failed','cancelled'].includes(receipt.status)));
 byId('open').disabled=busy||!clean||!receipt?.result?.preview_id||!['completed','reporting'].includes(receipt.status);
 byId('retry-preview').hidden=!op||!['unknown','unreceived','sending'].includes(op.state);byId('retry-preview').disabled=busy||!local.ready;
 byId('resume').hidden=receipt?.status!=='needs_attention';byId('resume').disabled=busy||!clean;
 const card=state.confirmation;
 byId('previous').hidden=!card;
 byId('last-confirmation').textContent=!card?'':card.operation?`前回の公開処理: ${labels[card.operation.status]||card.operation.status}`:card.state==='tested'?'前回: 接続確認済み（公開なし）':card.state==='cancelled'?'前の確認は取り消しました。':card.state==='rejected'?'前の確認は拒否されました。':['unknown','sending','withdraw_unknown','withdrawing'].includes(card.state)?'前の確認結果を照合しています。':'前回の確認は未送信です。';
 if(card){
  byId('dialog-title').textContent=card.binding.mode==='connection_test'?'接続確認（公開しません）':'検証済み内容の公開確認';
  byId('mode').textContent=card.binding.mode==='connection_test'?'これは接続テストです。本番公開は行いません。':'確認したページを本番公開する操作です。';
  byId('site-name').textContent=card.site_name||state.site_id;byId('summary').textContent=card.summary||'';
  if(card.preview_url){byId('preview-link').href=card.preview_url;byId('production-link').href=card.production_url;byId('production-link').textContent=card.production_url;}
  byId('artifact').textContent=`ソース: ${card.binding.source_commit} / 成果物: ${card.binding.artifact_sha256}`;
  byId('confirm').textContent=card.binding.mode==='connection_test'?'接続を確認する（公開なし）':'確認したページを公開';
  byId('withdraw').hidden=!['prepared','sending','unknown','rejected','withdrawing','withdraw_unknown'].includes(card.state);byId('withdraw').disabled=busy;
  byId('confirm').disabled=busy||!clean||!state.can_confirm||card.expires_at*1000<=Date.now();
  byId('confirm-status').textContent=card.state==='tested'?'接続確認が完了しました。本番公開はしていません。':card.state==='cancelled'?'前の確認を取り消しました。これは公開済みの内容を元に戻す操作ではありません。':card.state==='rejected'?'確認は拒否されました。古い確認は再送せず、内容を開き直してください。':card.operation?`既存の公開処理: ${labels[card.operation.status]||card.operation.status} (${card.operation.operation_id})`:['sending','unknown','withdrawing','withdraw_unknown'].includes(card.state)?'確認結果を照合中です。再送や新しい公開依頼は行いません。':!state.can_confirm?'確認を開き直してください。':'上のリモート確認ページをご確認ください。';
 }
}
async function readState(){const response=await fetch('/api/state',{cache:'no-store'});const data=await response.json();if(!response.ok)throw data;display(data);}
async function action(name){if(busy)return;busy=true;byId('error').hidden=true;if(state)display(state);
 try{const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-HP-CSRF':csrf},body:JSON.stringify({action:name,revision:state.local.revision})});const data=await response.json();if(!response.ok)throw data;display(data);if(name==='open_confirmation')byId('confirmation').showModal();}
 catch(error){byId('error').textContent=errors[error.error]||'操作を確認できません。Codexで状況を確認してください。';byId('error').hidden=false;try{await readState();}catch{}}
 finally{busy=false;if(state)display(state);}}
byId('prepare').addEventListener('click',event=>{if(event.isTrusted)void action('prepare_preview');});
byId('open').addEventListener('click',event=>{if(event.isTrusted)void action('open_confirmation');});
byId('confirm').addEventListener('click',event=>{if(event.isTrusted)void action('confirm');});
byId('refresh').addEventListener('click',event=>{if(event.isTrusted){unknownPolls=0;void action('refresh');}});
byId('retry-preview').addEventListener('click',event=>{if(event.isTrusted)void action('retry_preview');});
byId('withdraw').addEventListener('click',event=>{if(event.isTrusted)void action('withdraw');});
byId('resume').addEventListener('click',event=>{if(event.isTrusted)void action('resume');});
byId('toggle').addEventListener('click',()=>{const body=byId('menu-body');body.hidden=!body.hidden;byId('toggle').textContent=body.hidden?'操作を開く':'閉じる';byId('toggle').setAttribute('aria-expanded',String(!body.hidden));});
byId('previous').addEventListener('click',()=>byId('confirmation').showModal());
byId('close').addEventListener('click',()=>byId('confirmation').close());
async function poll(){try{const response=await fetch('/api/revision',{cache:'no-store'});const data=await response.json();if(!response.ok)throw data;if(revision!==data.revision){if(revision!==undefined){const frame=byId('local-preview');const url=new URL(frame.src);url.searchParams.set('v',data.revision);frame.src=url.href;}revision=data.revision;await readState();}
 const active=state?.operation&&(['unknown','unreceived','sending'].includes(state.operation.state)||['queued','running','retry_wait','reconciling','reporting'].includes(state.operation.receipt?.status));
 const uncertain=['sending','unknown','withdrawing','withdraw_unknown'].includes(state?.confirmation?.state)||(state?.confirmation?.state==='observed'&&['queued','running','retry_wait','reconciling','reporting'].includes(state.confirmation.operation?.status));
 const unknown=['unknown','unreceived','sending'].includes(state?.operation?.state)||['unknown','sending','withdrawing','withdraw_unknown'].includes(state?.confirmation?.state);
 const delay=unknown?Math.min(60000,5000*2**unknownPolls):5000;
 if(!busy&&(active||uncertain)&&(!unknown||unknownPolls<6)&&Date.now()-remotePoll>=delay){remotePoll=Date.now();if(unknown)unknownPolls++;void action('refresh');}
 if(unknown&&unknownPolls>=6){byId('error').textContent='自動照合を止めました。「状況を確認」で同じ依頼の結果を確認してください。新しい公開依頼は送りません。';byId('error').hidden=false;}
 }catch(error){byId('error').textContent=errors[error.error]||'ローカルの表示を確認できません。作業フォルダーを確認してください。';byId('error').hidden=false;}setTimeout(poll,1000);}
void readState().then(()=>{void action('refresh');void poll();}).catch(()=>void poll());
