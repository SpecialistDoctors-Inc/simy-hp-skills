import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/hp-local-editor/skills/hp-local-editor/scripts'))
from hp_chat import Chat, ChatError, proposal, apply_candidate, atomic_json, CodexEditor
from test_preview import workspace, FakeHP
from hp_preview_state import Session
import hp_local_editor as editor

class Runner:
    def __init__(self):self.calls=0;self.gate=threading.Event();self.gate.set();self.answer={'reply':'見出しを変更しました。','edits':[{'path':'index.html','old_text':'Initial local page','new_text':'更新した見出し'}]}
    def run(self,prompt,request_id):self.calls+=1;self.gate.wait(5);return self.answer

class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=workspace(Path(self.temp.name));self.runner=Runner();self.chat=Chat(self.root,self.runner);self.addCleanup(self.chat.close)
    def submit(self):
        uid=str(uuid.uuid4());self.chat.submit(uid,'見出しを変更してください');self.chat.worker.join(5);return uid
    def test_real_local_edit_and_same_request_dedup(self):
        uid=self.submit();self.chat.submit(uid,'見出しを変更してください')
        self.assertEqual(self.runner.calls,1);self.assertIn('更新した見出し',(self.root/'site/index.html').read_text())
        state=self.chat.public();self.assertEqual(state['requests'][-1]['status'],'completed');self.assertNotIn('candidate',state['requests'][-1])
        self.assertTrue(editor.status(self.root)['changes']);self.assertFalse((self.root/'.hp/pending').exists())
    def test_same_id_cannot_change_prompt(self):
        uid=self.submit()
        with self.assertRaises(ChatError):self.chat.submit(uid,'違う依頼')
    def test_concurrent_local_edit_is_not_overwritten(self):
        self.runner.gate.clear();self.chat.submit(str(uuid.uuid4()),'変更して')
        (self.root/'site/index.html').write_text('<h1>Customer later edit</h1>')
        self.runner.gate.set();self.chat.worker.join(5)
        self.assertEqual((self.root/'site/index.html').read_text(),'<h1>Customer later edit</h1>')
        self.assertEqual(self.chat.public()['requests'][-1]['status'],'conflict')
    def test_no_parallel_request_or_second_owner(self):
        self.runner.gate.clear();self.chat.submit(str(uuid.uuid4()),'変更して')
        try:
            with self.assertRaises(ChatError):self.chat.submit(str(uuid.uuid4()),'さらに')
            with self.assertRaises(ChatError):Chat(self.root,self.runner)
        finally:self.runner.gate.set();self.chat.worker.join(5)
    def test_model_cannot_choose_metadata_paths_or_ambiguous_edits(self):
        files={'index.html':b'one one'}
        for path,old in [('../outside','one'),('.hp/state.json','one'),('index.html','one')]:
            with self.assertRaises(ValueError):proposal(files,{'reply':'ok','edits':[{'path':path,'old_text':old,'new_text':'two'}]})
    def test_symlink_is_rejected_without_writing_target(self):
        source=(self.root/'site/index.html').read_bytes();outside=Path(self.temp.name)/'outside.txt';outside.write_bytes(b'private')
        candidate={'index.html':{'before':base64.b64encode(source).decode(),'after':base64.b64encode(b'new').decode()}}
        (self.root/'site/index.html').unlink();(self.root/'site/index.html').symlink_to(outside)
        with self.assertRaises(ValueError):apply_candidate(self.root,candidate)
        self.assertEqual(outside.read_bytes(),b'private');(self.root/'site/index.html').unlink();(self.root/'site/index.html').write_bytes(source)
    def test_persisted_candidate_recovers_without_another_model_call(self):
        before=(self.root/'site/index.html').read_bytes()
        with self.chat.lock:
            self.chat.data['requests'].append({'id':str(uuid.uuid4()),'text':'edit','reply':'ok','status':'applying','changed_files':['index.html'],'candidate':{'index.html':{'before':base64.b64encode(before).decode(),'after':base64.b64encode(b'<h1>Recovered</h1>').decode()}}});self.chat.persist()
        self.chat.close();self._cleanups.pop();self.chat=Chat(self.root,self.runner);self.addCleanup(self.chat.close)
        self.assertEqual(self.runner.calls,0);self.assertEqual((self.root/'site/index.html').read_bytes(),b'<h1>Recovered</h1>')
        self.assertEqual(self.chat.public()['requests'][-1]['status'],'completed')
    def test_interrupted_request_is_not_resubmitted(self):
        uid=str(uuid.uuid4());self.chat.data['requests'].append({'id':uid,'text':'edit','status':'running'});self.chat.persist()
        self.chat.close();self._cleanups.pop();self.chat=Chat(self.root,self.runner);self.addCleanup(self.chat.close)
        self.chat.submit(uid,'edit');self.assertEqual(self.runner.calls,0);self.assertEqual(self.chat.public()['requests'][-1]['status'],'interrupted')
    def test_question_without_edits_preserves_files(self):
        self.runner.answer={'reply':'どのお知らせを変更しますか？','edits':[]};before=(self.root/'site/index.html').read_bytes();self.submit()
        self.assertEqual((self.root/'site/index.html').read_bytes(),before)
    def test_dev_quick_reply_clean_workspace_uses_existing_protocol(self):
        hp=FakeHP();session=Session(self.root,hp)
        self.chat.prepare_preview(session,session.snapshot()['revision']);self.chat.worker.join(5)
        self.assertEqual(self.chat.public()['delivery']['status'],'completed')
        self.assertTrue(any(name=='hp_submit_preview' for name,args in hp.calls))
        self.assertFalse(any(name=='hp_confirm_publish' for name,args in hp.calls))
    def test_dirty_quick_reply_saves_then_previews_and_preserves_ids(self):
        self.submit();hp=FakeHP();session=Session(self.root,hp);saves=[]
        original=hp.call
        def call(name,args):
            if name=='hp_save_draft':saves.append(dict(args));return {'structuredContent':{'status':'queued'}}
            if name=='hp_get_operation' and saves and args['operation_id']==saves[0]['operation_id']:
                req=saves[0];return {'structuredContent':{'operation_id':req['operation_id'],'site_id':'sample-site','kind':'save_draft','status':'completed','result':{'schema_version':1,'kind':'saved','site_id':req['site_id'],'workspace_id':req['workspace_id'],'operation_id':req['operation_id'],'base_commit':req['base_commit'],'saved_commit':'e'*40,'bundle_sha256':req['bundle_sha256']}}}
            return original(name,args)
        hp.call=call
        def upload(client,root):
            _,hpdir,state,base=editor.load(root);meta,_,_=editor.pending(hpdir,state,base);uid=meta['manifest']['operation_id']
            return {'operation_id':uid,'bundle_file_id':'hp-upload-'+uid,'bundle_sha256':meta['bundle_sha256']}
        with patch('hp_transfer.upload',side_effect=upload):
            self.chat.prepare_preview(session,session.snapshot()['revision']);self.chat.worker.join(5)
        self.assertEqual(self.chat.public()['delivery']['status'],'completed');self.assertEqual(len(saves),1)
        self.assertEqual(hp.op['saved_commit'],'e'*40);self.assertEqual(editor.status(self.root)['changes'],[])
        self.assertFalse(any(name=='hp_confirm_publish' for name,args in hp.calls))

    def test_apply_failure_retries_saved_candidate_without_model(self):
        with patch('hp_chat.apply_candidate',side_effect=OSError('disk temporarily unavailable')):
            uid=self.submit()
        self.assertEqual(self.chat.public()['requests'][-1]['status'],'apply_pending')
        self.chat.submit(uid,'見出しを変更してください');self.chat.worker.join(5)
        self.assertEqual(self.runner.calls,1);self.assertEqual(self.chat.public()['requests'][-1]['status'],'completed')

    def test_partial_multifile_application_recovers_without_duplicate_model(self):
        (self.root/'site/style.css').write_text('body { color: black; }')
        self.runner.answer['edits'].append({'path':'style.css','old_text':'black','new_text':'navy'})
        import os
        real=os.rename;calls=0
        def rename(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('simulated disk failure')
            return real(*args,**kwargs)
        with patch('hp_chat.os.rename',side_effect=rename):uid=self.submit()
        self.assertEqual(self.chat.public()['requests'][-1]['status'],'apply_pending')
        self.assertIn('更新した見出し',(self.root/'site/index.html').read_text())
        self.chat.submit(uid,'見出しを変更してください');self.chat.worker.join(5)
        self.assertIn('navy',(self.root/'site/style.css').read_text());self.assertEqual(self.runner.calls,1)

    def test_lock_contention_retries_without_model_reexecution(self):
        import hp_chat
        original=hp_chat.apply_candidate;calls=0
        def apply(*args):
            nonlocal calls
            calls+=1
            if calls==1:raise ValueError('Workspace is busy: snapshot')
            return original(*args)
        with patch('hp_chat.apply_candidate',side_effect=apply):self.submit()
        self.assertEqual(calls,2);self.assertEqual(self.runner.calls,1);self.assertEqual(self.chat.public()['requests'][-1]['status'],'completed')

    def test_active_content_and_new_external_links_are_rejected(self):
        before='<p>Old</p>'
        for replacement in ['<script>alert(1)</script>','<img src="https://track.invalid/p">','<a href="java&#x73;cript:alert(1)">click</a>','<form action="/send"></form>']:
            with self.assertRaises(ChatError):proposal({'index.html':before.encode()},{'reply':'ok','edits':[{'path':'index.html','old_text':before,'new_text':replacement}]})

    def test_text_inside_existing_context_elements_can_be_edited(self):
        before='<title>Old clinic</title><noscript><p>Old</p></noscript>'
        for old,new in [('Old clinic','New clinic'),('<p>Old</p>','<p>New</p>')]:
            with self.subTest(old=old):
                self.assertTrue(proposal({'index.html':before.encode()},{'reply':'ok','edits':[{'path':'index.html','old_text':old,'new_text':new}]}))

    def test_existing_data_images_are_preserved_when_editing_text(self):
        before='<img src="data:image/png;base64,AAAA"><p>Old</p>'
        self.assertTrue(proposal({'index.html':before.encode()},{'reply':'ok','edits':[{'path':'index.html','old_text':'Old','new_text':'New'}]}))


    def test_rejected_preview_with_none_receipt_allows_new_preview(self):
        hp = FakeHP()
        session = Session(self.root, hp)
        session.record(preview_operation={'state': 'rejected', 'receipt': None,
                       'arguments': {'operation_id': str(uuid.uuid4())}})
        self.chat.prepare_preview(session, session.snapshot()['revision'])
        self.chat.worker.join(5)
        self.assertEqual(self.chat.public()['delivery']['status'], 'completed')
        self.assertIsNotNone(hp.op)

    def test_failed_delivery_without_journaled_preview_can_save_new_edits(self):
        hp = FakeHP()
        session = Session(self.root, hp)
        old = {'id': str(uuid.uuid4()), 'status': 'failed', 'saved_result': {'kind': 'saved'},
               'preview_started': True, 'prior_preview_id': None}
        self.chat.data['delivery'] = old
        self.submit()
        with patch.object(self.chat, 'deliver') as deliver:
            self.chat.prepare_preview(session, session.snapshot()['revision'])
            self.chat.worker.join(5)
        self.assertNotEqual(self.chat.data['delivery']['id'], old['id'])
        self.assertNotIn('saved_result', self.chat.data['delivery'])
        self.assertEqual(self.chat.data['past_deliveries'], [old])
        self.chat.data['delivery']['status'] = 'completed'

    def test_partial_apply_cannot_be_sent_to_dev(self):
        with patch('hp_chat.apply_candidate', side_effect=OSError('disk error')):
            self.submit()
        hp = FakeHP()
        session = Session(self.root, hp)
        with self.assertRaisesRegex(ChatError, 'local_apply_pending'):
            self.chat.prepare_preview(session, session.snapshot()['revision'])
        self.assertEqual(hp.calls, [])

    def test_encoded_active_content_is_rejected(self):
        attacks = ['<a href="java&#9;script:alert(1)">click</a>',
                   '<img srcset="&#47;&#47;track.invalid/p.gif">',
                   '<p style="background:url(&#47;&#47;track.invalid/p)">x</p>',
                   r'<style>@import "\2f\2f track.invalid/x.css"</style>',
                   # Python's parser sees a quoted title; browsers close
                   # <noscript> and run the onerror handler.
                   '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
                   '<p title="</p><img src=x onerror=alert(1)>">x</p>',
                   '<textarea></textarea><p>x</p>',
                   '<svg><style><img src=x onerror=alert(1)></style></svg>']
        for attack in attacks:
            with self.subTest(attack=attack), self.assertRaises(ChatError):
                proposal({'index.html': b'<p>Old</p>'}, {'reply': 'ok', 'edits': [
                    {'path': 'index.html', 'old_text': '<p>Old</p>', 'new_text': attack}]})


    def test_usage_limit_is_explained_without_resubmitting_or_losing_content(self):
        before = (self.root / 'site/index.html').read_bytes()
        with patch.object(self.runner, 'run', side_effect=ChatError('codex_usage_limit')) as run:
            request_id = self.submit()
            self.chat.submit(request_id, '見出しを変更してください')
            self.assertEqual(run.call_count, 1)
        row = self.chat.public()['requests'][-1]
        self.assertIn('利用上限', row['reply'])
        self.assertEqual((self.root / 'site/index.html').read_bytes(), before)


    def test_failed_delivery_rejected_preview_clean_workspace_starts_new_preview(self):
        hp = FakeHP()
        session = Session(self.root, hp)
        rejected_id = str(uuid.uuid4())
        session.record(preview_operation={'state': 'rejected', 'receipt': None,
                       'arguments': {'operation_id': rejected_id}})
        self.chat.data['delivery'] = {'id': str(uuid.uuid4()), 'status': 'failed',
            'saved_result': {'kind': 'saved'}, 'preview_started': True, 'prior_preview_id': None}
        self.chat.prepare_preview(session, session.snapshot()['revision'])
        self.chat.worker.join(5)
        self.assertEqual(self.chat.public()['delivery']['status'], 'completed')
        self.assertNotEqual(hp.op['operation_id'], rejected_id)


    def test_rejection_during_retry_is_not_reported_as_completed(self):
        hp = FakeHP()
        session = Session(self.root, hp)
        session.record(preview_operation={'state': 'unknown', 'receipt': None,
            'arguments': {'operation_id': str(uuid.uuid4())}})
        self.chat.data['delivery'] = {'id': str(uuid.uuid4()), 'status': 'failed',
            'saved_result': {'kind': 'saved'}, 'preview_started': True, 'prior_preview_id': None}
        def reject():
            session.record(preview_operation={**session.journal['preview_operation'], 'state': 'rejected'})
        with patch.object(session, 'retry_preview', side_effect=reject):
            self.chat.prepare_preview(session, session.snapshot()['revision'])
            self.chat.worker.join(5)
        self.assertEqual(self.chat.public()['delivery']['status'], 'failed')
        self.assertEqual(self.chat.public()['delivery']['error'], 'preview_request_rejected')


    def test_undo_restores_only_the_selected_edit_and_is_idempotent(self):
        before = (self.root / 'site/index.html').read_bytes()
        request_id = self.submit()
        self.assertTrue(self.chat.public()['requests'][-1]['can_undo'])
        (self.root / 'site/style.css').write_text('body { color: navy; }')
        self.chat.undo(request_id)
        self.assertEqual((self.root / 'site/index.html').read_bytes(), before)
        self.assertEqual((self.root / 'site/style.css').read_text(), 'body { color: navy; }')
        self.assertEqual(self.chat.public()['requests'][-1]['undo_status'], 'completed')
        self.assertFalse(self.chat.public()['requests'][-1]['can_undo'])
        (self.root / 'site/index.html').write_text('<h1>Later edit</h1>')
        self.chat.undo(request_id)
        self.assertEqual((self.root / 'site/index.html').read_text(), '<h1>Later edit</h1>')
        self.assertEqual(self.runner.calls, 1)

    def test_undo_rejects_a_later_change_to_the_same_file(self):
        request_id = self.submit()
        (self.root / 'site/index.html').write_text('<h1>Later edit</h1>')
        with self.assertRaisesRegex(ChatError, 'undo_conflict'):
            self.chat.undo(request_id)
        self.assertEqual((self.root / 'site/index.html').read_text(), '<h1>Later edit</h1>')
        self.assertIsNone(self.chat.public()['requests'][-1]['undo_status'])

    def test_undo_blocks_while_save_or_model_work_is_pending(self):
        request_id = self.submit()
        editor.pack(self.root, Path(self.temp.name) / 'pending.zip')
        with self.assertRaisesRegex(ChatError, 'save_in_progress'):
            self.chat.undo(request_id)
        self.assertIsNone(self.chat.public()['requests'][-1]['undo_status'])

    def test_partial_undo_resumes_and_blocks_new_delivery(self):
        original_html = (self.root / 'site/index.html').read_bytes()
        (self.root / 'site/style.css').write_text('body { color: black; }')
        self.runner.answer['edits'].append({'path': 'style.css', 'old_text': 'black', 'new_text': 'navy'})
        request_id = self.submit()
        import os
        rename = os.rename
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('interrupted write')
            return rename(*args, **kwargs)
        with patch('hp_chat.os.rename', side_effect=fail_second):
            self.chat.undo(request_id)
        self.assertEqual(self.chat.public()['requests'][-1]['undo_status'], 'pending')
        self.assertTrue(self.chat.has_pending_application())
        session = Session(self.root, FakeHP())
        with self.assertRaisesRegex(ChatError, 'local_apply_pending'):
            self.chat.prepare_preview(session, session.snapshot()['revision'])
        with self.assertRaisesRegex(ChatError, 'local_apply_pending'):
            self.chat.submit(str(uuid.uuid4()), '次の修正')
        self.chat.undo(request_id)
        self.assertEqual((self.root / 'site/index.html').read_bytes(), original_html)
        self.assertEqual((self.root / 'site/style.css').read_text(), 'body { color: black; }')
        self.assertEqual(self.chat.public()['requests'][-1]['undo_status'], 'completed')
        self.assertEqual(self.runner.calls, 1)

    def test_undo_journal_recovers_an_interrupted_application_on_startup(self):
        before = (self.root / 'site/index.html').read_bytes()
        request_id = self.submit()
        row = self.chat.data['requests'][-1]
        row['undo'] = {'status': 'applying', 'candidate': {
            path: {'before': change['after'], 'after': change['before']}
            for path, change in row['candidate'].items()}}
        self.chat.persist()
        self.chat.close()
        self._cleanups.pop()
        self.chat = Chat(self.root, self.runner)
        self.addCleanup(self.chat.close)
        self.assertEqual((self.root / 'site/index.html').read_bytes(), before)
        self.assertEqual(self.chat.public()['requests'][-1]['undo_status'], 'completed')
        self.assertEqual(self.runner.calls, 1)

    def test_no_undo_action_for_legacy_or_question_only_rows(self):
        self.chat.data['requests'].append({'id': str(uuid.uuid4()), 'text': '以前の質問',
                                         'reply': '回答', 'status': 'completed'})
        public = self.chat.public()['requests'][-1]
        self.assertFalse(public['can_undo'])
        with self.assertRaisesRegex(ChatError, 'undo_unavailable'):
            self.chat.undo(public['id'])


    def test_conflict_during_undo_releases_editing_and_retains_current_files(self):
        request_id = self.submit()
        with patch('hp_chat.apply_candidate', side_effect=ChatError('local_edit_conflict')):
            self.chat.undo(request_id)
        self.assertFalse(self.chat.has_pending_application())
        self.assertEqual(self.chat.public()['requests'][-1]['undo_status'], 'conflict')
        self.assertFalse(self.chat.public()['requests'][-1]['can_undo'])
        self.runner.answer = {'reply': '現在の内容を確認します。', 'edits': []}
        self.chat.submit(str(uuid.uuid4()), 'この状態から続けて')
        self.chat.worker.join(5)
        self.assertEqual(self.chat.public()['requests'][-1]['status'], 'completed')

    def test_busy_undo_reservation_is_a_definite_rejection(self):
        request_id = self.submit()
        with patch('hp_chat.with_local_lock_retry', side_effect=ValueError('Workspace is busy: save')):
            with self.assertRaisesRegex(ChatError, 'workspace_busy'):
                self.chat.undo(request_id)
        self.assertIsNone(self.chat.public()['requests'][-1]['undo_status'])
        self.chat.undo(request_id)
        self.assertEqual(self.chat.public()['requests'][-1]['undo_status'], 'completed')


class ChatHTTPTests(unittest.TestCase):
    def test_real_http_chat_requires_same_origin_and_never_calls_remote_on_submit(self):
        import http.client
        from hp_preview import PreviewHost
        with tempfile.TemporaryDirectory() as directory:
            root=workspace(Path(directory));runner=Runner();chat=Chat(root,runner);hp=FakeHP();session=Session(root,hp);host=PreviewHost(session,chat);host.start()
            def request(path,body,headers):
                conn=http.client.HTTPConnection(host.netloc,timeout=3);conn.request('POST',path,json.dumps(body),headers);r=conn.getresponse();status=r.status;data=json.loads(r.read());conn.close();return status,data
            try:
                body={'request_id':str(uuid.uuid4()),'text':'変更して'}
                for origin in ['null','https://outside.invalid',None]:
                    headers={'Content-Type':'application/json','X-HP-CSRF':host.csrf}
                    if origin:headers['Origin']=origin
                    self.assertEqual(request('/api/chat',body,headers)[0],403)
                    self.assertEqual(request('/api/undo',{'request_id':body['request_id']},headers)[0],403)
                good={'Content-Type':'application/json','X-HP-CSRF':host.csrf,'Origin':host.origin}
                self.assertEqual(request('/api/chat',body,good)[0],202);chat.worker.join(5)
                self.assertEqual(request('/api/chat',body,good)[0],202);self.assertEqual(runner.calls,1);self.assertEqual(hp.calls,[])
                self.assertEqual(request('/api/chat',{'request_id':str(uuid.uuid4()),'text':'a'*4001},good)[0],409)
                undo_status, undo_result = request('/api/undo', {'request_id': body['request_id']}, good)
                self.assertEqual(undo_status, 200)
                self.assertEqual(undo_result['chat']['requests'][-1]['undo_status'], 'completed')
                self.assertEqual(request('/api/undo', {'request_id': body['request_id']}, good)[0], 200)
                self.assertEqual(hp.calls, [])
                self.assertIn('Initial local page', (root/'site/index.html').read_text())
            finally:host.close();chat.close()
