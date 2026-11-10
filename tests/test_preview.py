"""Local-only control security and recovery. No Codex/cloud credentials or providers."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/hp-local-editor/skills/hp-local-editor/scripts'
sys.path.insert(0, str(SCRIPTS))
import hp_local_editor as editor
from hp_preview import PreviewHost, site_bytes
from hp_preview_state import Session, Blocked


def workspace(parent):
    files = {'index.html': b'<!doctype html><h1>Initial local page</h1>'}
    manifest = dict(schema_version=1, kind='snapshot', site_id='sample-site', workspace_id=str(uuid.uuid4()), base_commit='a'*40, files=editor.entries(files))
    blob = editor.zip_bytes(manifest, files)
    (parent/'snapshot.zip').write_bytes(blob)
    receipt = dict(schema_version=1, kind='workspace', site_id='sample-site', workspace_id=manifest['workspace_id'], base_commit='a'*40, snapshot_file_id='sample-snapshot-file', snapshot_sha256=editor.digest(blob), download_url='https://drive.google.com/file/d/sample-snapshot-file/view', upload_folder_id='sample-upload-folder')
    (parent/'open.json').write_text(json.dumps(receipt))
    editor.init(parent/'snapshot.zip', parent/'workspace', parent/'open.json')
    return parent/'workspace'


class FakeHP:
    def __init__(self):
        self.calls = []; self.op = None; self.approvals = {}; self.mode = 'connection_test'
        self.preview_id = str(uuid.uuid4()); self.status = 'completed'; self.kind = 'no_changes'
        self.lose_submit = False; self.lose_confirm = False; self.prepared_unknown = False
        self.before_confirmation_read = None; self.existing = None
        self.unreceived = False; self.reject_confirm = None; self.lose_withdraw = False; self.withdraw_before = False; self.reject_read_after_confirm = False
        self.nonce = 'PRIVATE-NONCE-NEVER-IN-JOURNAL-OR-PAGE-123456'
    def call(self, name, args):
        self.calls.append((name, dict(args)))
        if name == 'hp_profile': data = {'subject':'sample-subject','email':'sample@example.invalid','sites':['sample-site']}
        elif name == 'hp_list_sites': data = {'sites':[{'site_id':'sample-site','name':'サンプルサイト'}]}
        elif name == 'hp_submit_preview':
            self.op = dict(args)
            if self.lose_submit: raise OSError('simulated lost acknowledgement')
            data = self.operation()
        elif name == 'hp_get_operation':
            if self.unreceived:return {'isError':True,'structuredContent':{'error':'operation_access_denied'}}
            assert args['operation_id'] == self.op['operation_id']; data = self.operation()
        elif name == 'hp_prepare_publish':
            approval = str(uuid.uuid4())
            data = {'approval_id':approval,'site_id':'sample-site','preview_id':self.preview_id,'source_commit':'c'*40,'artifact_sha256':'d'*64,'mode':self.mode,'state':'prepared','expires_at':int(time.time())+300,'site_name':'サンプルサイト','summary':'保存した文章を確認','preview_url':'https://preview.example.invalid/current','production_url':'https://site.example.invalid'}
            self.approvals[approval] = dict(data)
            return {'structuredContent':data,'_meta':{'confirmation_nonce':self.nonce}}
        elif name == 'hp_get_confirmation':
            if self.reject_read_after_confirm and any(value.get('state') in ('accepted','connection_tested') for value in self.approvals.values()):
                return {'isError':True,'structuredContent':{'error':'insufficient_scope'}}
            if self.before_confirmation_read: self.before_confirmation_read(); self.before_confirmation_read = None
            data = dict(self.approvals[args['approval_id']])
            if data['state'] in ('prepared','cancelled') and self.existing: data.update(state='already_requested', operation=dict(self.existing))
        elif name == 'hp_cancel_confirmation':
            if self.withdraw_before:raise OSError('request never reached service')
            current = self.approvals[args['approval_id']]
            if current['state'] == 'prepared':current['state'] = 'cancelled'
            if self.lose_withdraw:raise OSError('unknown withdrawal result')
            data = dict(current)
            if current['state'] == 'cancelled' and self.existing:data.update(state='already_requested',operation=dict(self.existing))
        elif name == 'hp_confirm_publish':
            if self.reject_confirm:return {'isError':True,'structuredContent':{'error':self.reject_confirm}}
            assert args['confirmation_nonce'] == self.nonce
            current = self.approvals[args['approval_id']]
            if not self.prepared_unknown:
                if current['mode'] == 'connection_test': current.update(state='connection_tested',result={'kind':'connection_test','status':'completed','approval_id':args['approval_id'],'preview_id':self.preview_id,'completed_at':int(time.time())})
                else: current.update(state='accepted',operation={'operation_id':str(uuid.uuid4()),'site_id':'sample-site','kind':'publish','status':'queued'})
            if self.lose_confirm: raise OSError('unknown confirmation outcome')
            data = current.get('result') or current.get('operation') or {}
        else: raise AssertionError('unexpected fake tool')
        return {'structuredContent':data}
    def operation(self):
        return {'operation_id':self.op['operation_id'],'site_id':'sample-site','workspace_id':None,'kind':'submit_preview','status':self.status,'result':{'kind':self.kind,'preview_id':self.preview_id,'source_commit':'c'*40,'artifact_sha256':'d'*64,'preview_url':'https://preview.example.invalid/current'}}


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = workspace(Path(self.temp.name)); self.hp = FakeHP(); self.session = Session(self.root, self.hp)
    def test_public_readback_errors_are_bounded_known_codes_only(self):
        self.session.record(readback_errors=['confirmation_readback_unavailable', 'SECRET-UNTRUSTED'])
        self.assertEqual(self.session.public()['readback_errors'], ['confirmation_readback_unavailable'])

    def test_environment_timestamps_are_copied_only_from_site_metadata(self):
        original = self.hp.call
        def call(name, arguments):
            if name == 'hp_list_sites':
                return {'structuredContent': {'sites': [{'site_id': 'sample-site', 'name': 'サンプル',
                    'deployments': {'dev': {'updated_at': 1234, 'version': 12}, 'production': {'updated_at': 'invalid', 'version': True}}}]}}
            return original(name, arguments)
        self.hp.call = call
        self.act('refresh')
        self.assertEqual(self.session.public()['environments'], {
            'dev': {'updated_at': 1234, 'version': 12}, 'production': {'updated_at': None, 'version': None}})

    def test_failed_authentication_clears_previous_identity_dates_and_confirmation(self):
        original = self.hp.call
        for failure in ('account_changed', 'account_site_mismatch', 'access_revoked'):
            with self.subTest(failure=failure):
                self.hp.call = original
                self.session.authenticate()
                self.session.environments = {'dev': {'updated_at': 1234}}
                self.session.confirmation = {'private': 'previous-confirmation'}
                def call(name, arguments):
                    if name == 'hp_profile':
                        if failure == 'access_revoked':
                            return {'isError': True, 'structuredContent': {'error': failure}}
                        return {'structuredContent': {'subject': 'changed' if failure == 'account_changed' else 'sample-subject',
                            'sites': [] if failure == 'account_site_mismatch' else ['sample-site']}}
                    return original(name, arguments)
                self.hp.call = call
                with self.assertRaisesRegex(Blocked, failure):
                    self.session.authenticate()
                state = self.session.public()
                self.assertIsNone(state['identity'])
                self.assertEqual(state['environments'], {})
                self.assertFalse(state['can_confirm'])
                self.assertIsNone(self.session.confirmation)

    def test_launcher_opens_ready_loopback_once_and_keeps_fallback_url(self):
        import io
        import hp_preview
        from contextlib import redirect_stdout
        for mode in ('default', 'disabled', 'failed', 'error'):
            with self.subTest(mode=mode):
                output = io.StringIO()
                argv = ['hp-preview', str(self.root)] + (['--no-open'] if mode == 'disabled' else [])
                def open_browser(url, new):
                    self.assertEqual(new, 2)
                    target = urlsplit(url)
                    self.assertEqual(target.hostname, '127.0.0.1')
                    connection = http.client.HTTPConnection(target.hostname, target.port)
                    try:
                        connection.request('GET', '/')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        response.read()
                    finally:
                        connection.close()
                    if mode == 'error':
                        raise OSError('fixture browser unavailable')
                    return mode != 'failed'
                class Idle:
                    def wait(self): raise KeyboardInterrupt()
                # Patch only main's wait, leaving HTTP server threading intact.
                with patch.object(sys, 'argv', argv), \
                     patch('hp_preview.webbrowser.open', side_effect=open_browser) as opened, \
                     patch('hp_preview.threading', wraps=__import__('threading')) as threads, \
                     redirect_stdout(output):
                    threads.Event.return_value = Idle()
                    hp_preview.main()
                self.assertEqual(opened.call_count, 0 if mode == 'disabled' else 1)
                self.assertIn('Local HP preview: http://127.0.0.1:', output.getvalue())
                self.assertEqual('ブラウザーで確認ページを開いてください' in output.getvalue(), mode in ('failed', 'error'))

    def test_existing_confirmation_receives_bound_change_summary_on_readback(self):
        self.card()
        approval = self.session.public()['confirmation']['binding']['approval_id']
        details = {'from_version': 2, 'to_version': 3, 'already_current': False,
            'changes': {'items': [{'title': '診療時間', 'kind': 'changed', 'before': '18時', 'after': '19時'}], 'additional_count': 0}}
        self.hp.approvals[approval]['publication_details'] = details
        before = sum(name=='hp_prepare_publish' for name,args in self.hp.calls)
        self.act('refresh')
        self.assertEqual(self.session.public()['confirmation']['publication_details'], details)
        self.assertEqual(sum(name=='hp_prepare_publish' for name,args in self.hp.calls), before)
        self.assertFalse(any(name=='hp_confirm_publish' for name,args in self.hp.calls))

    def act(self, name): self.session.action(name, self.session.snapshot()['revision'])
    def card(self): self.act('prepare_preview'); self.act('open_confirmation')
    def save_new_local_baseline(self):
        (self.root/'site/index.html').write_text('<h1>New saved local content</h1>')
        packed = editor.pack(self.root, Path(self.temp.name)/'new-save.zip')
        state = editor.parse_json((self.root/'.hp/state.json').read_bytes())['snapshot']
        receipt = dict(schema_version=1, kind='saved', site_id=state['site_id'], workspace_id=state['workspace_id'],
            base_commit=state['base_commit'], saved_commit='e'*40, operation_id=packed['operation_id'], bundle_sha256=packed['bundle_sha256'])
        path = Path(self.temp.name)/'saved.json'; path.write_text(json.dumps(receipt)); editor.accept(self.root, path)
    def test_local_revision_dirty_and_pending_do_not_call_remote_or_modify_files(self):
        before = (self.root/'site/index.html').read_bytes(); initial = self.session.public()['local']['revision']
        self.assertEqual(self.hp.calls, [])
        (self.root/'site/index.html').write_text('<h1>Changed immediately</h1>')
        self.assertNotEqual(self.session.revision(), initial)
        self.assertTrue(self.session.public()['local']['dirty'])
        with self.assertRaises(Blocked): self.act('prepare_preview')
        self.assertEqual(self.hp.calls, [])
        (self.root/'site/index.html').write_bytes(before)
        editor.pack(self.root, Path(self.temp.name)/'noop.zip')
        self.card()
        self.assertEqual((self.root/'site/index.html').read_bytes(), before)
    def test_verified_no_changes_card_and_private_nonce_never_persist_or_reach_ui(self):
        self.card(); public = self.session.public()
        self.assertTrue(public['can_confirm']); self.assertEqual(public['confirmation']['binding']['source_commit'], 'c'*40)
        self.assertNotIn(self.hp.nonce, json.dumps(public))
        self.assertNotIn(self.hp.nonce, (self.root/'.hp/control/journal.json').read_text())
        self.act('confirm'); self.assertEqual(self.session.public()['confirmation']['state'], 'tested')
        with self.assertRaises(Blocked): self.act('confirm')
        self.assertEqual(sum(name == 'hp_confirm_publish' for name,args in self.hp.calls), 1)
    def test_dirty_or_clean_new_baseline_during_confirmation_blocks_admission(self):
        self.card()
        def saved_again():
            state = editor.parse_json((self.root/'.hp/state.json').read_bytes())
            data = b'<h1>New clean saved content</h1>'
            (self.root/'site/index.html').write_bytes(data)
            (self.root/'.hp'/state['base_directory']/'index.html').write_bytes(data)
            state['snapshot'].update(base_commit='e'*40, files=editor.entries({'index.html':data}))
            (self.root/'.hp/state.json').write_bytes(editor.canonical(state))
        self.hp.before_confirmation_read = saved_again
        with self.assertRaises(Blocked): self.act('confirm')
        self.assertFalse(any(name == 'hp_confirm_publish' for name,args in self.hp.calls))
    def test_lost_submit_restart_reads_same_owned_operation_without_resubmit(self):
        self.hp.lose_submit = True
        with self.assertRaises(Blocked): self.act('prepare_preview')
        original = self.hp.op['operation_id']
        restarted = Session(self.root, self.hp); restarted.action('refresh', restarted.snapshot()['revision'])
        self.assertEqual(restarted.public()['operation']['receipt']['operation_id'], original)
        self.assertEqual(sum(name == 'hp_submit_preview' for name,args in self.hp.calls), 1)
    def test_lost_confirm_restart_readback_test_and_real_modes_never_resend(self):
        for mode in ('connection_test', 'publish'):
            with self.subTest(mode=mode):
                self.session.journal.pop('confirmation', None); self.hp.mode = mode; self.hp.lose_confirm = False
                self.card(); self.hp.lose_confirm = True
                with self.assertRaises(Blocked): self.act('confirm')
                count = sum(name == 'hp_confirm_publish' for name,args in self.hp.calls)
                restarted = Session(self.root, self.hp); restarted.action('refresh', restarted.snapshot()['revision'])
                observed = restarted.public()['confirmation']
                self.assertEqual(observed['state'], 'tested' if mode == 'connection_test' else 'observed')
                self.assertFalse(restarted.public()['can_confirm'])
                self.assertEqual(sum(name == 'hp_confirm_publish' for name,args in self.hp.calls), count)
    def test_uncertain_prepared_never_authorizes_resend_or_new_card(self):
        self.card(); self.hp.lose_confirm = True; self.hp.prepared_unknown = True
        with self.assertRaises(Blocked): self.act('confirm')
        self.act('refresh')
        self.assertEqual(self.session.public()['confirmation']['state'], 'unknown')
        with self.assertRaises(Blocked): self.act('open_confirmation')
        with self.assertRaises(Blocked): self.act('confirm')
        self.assertEqual(sum(name == 'hp_confirm_publish' for name,args in self.hp.calls), 1)
    def test_fresh_explicit_card_for_known_failed_target_delegates_guard_only_on_click(self):
        self.hp.mode = 'publish'; self.hp.existing = {'operation_id':str(uuid.uuid4()),'site_id':'sample-site','kind':'publish','status':'failed'}
        self.card(); self.act('refresh')
        self.assertEqual(self.session.public()['confirmation']['existing_operation']['status'], 'failed')
        self.assertTrue(self.session.public()['can_confirm'])
        self.assertFalse(any(name == 'hp_confirm_publish' for name,args in self.hp.calls))
        self.act('confirm')
        self.assertEqual(sum(name == 'hp_confirm_publish' for name,args in self.hp.calls), 1)
    def test_definite_rejection_allows_new_card_but_never_resends_old_nonce(self):
        self.card(); old = self.session.public()['confirmation']['binding']['approval_id']
        self.hp.reject_confirm = 'confirmation_stale'
        with self.assertRaisesRegex(Blocked,'confirmation_rejected'):self.act('confirm')
        self.assertEqual(self.session.public()['confirmation']['state'],'rejected')
        self.act('open_confirmation')
        self.assertNotEqual(self.session.public()['confirmation']['binding']['approval_id'],old)
        self.assertEqual(sum(name=='hp_confirm_publish' for name,args in self.hp.calls),1)

    def test_explicit_same_request_retry_and_confirmation_readback_independent_of_preview(self):
        self.card(); self.hp.lose_confirm=True
        with self.assertRaises(Blocked):self.act('confirm')
        self.hp.unreceived=True
        self.act('refresh')
        self.assertEqual(self.session.public()['confirmation']['state'],'tested')
        original=dict(self.hp.op)
        self.hp.unreceived=False
        self.act('retry_preview')
        sent=[args for name,args in self.hp.calls if name=='hp_submit_preview']
        self.assertEqual(sent,[original,original])

    def test_atomic_withdraw_uncertainty_recovers_cancelled_and_allows_fresh_card(self):
        self.card(); self.hp.lose_confirm=True;self.hp.prepared_unknown=True
        with self.assertRaises(Blocked):self.act('confirm')
        approval=self.session.public()['confirmation']['binding']['approval_id']
        self.hp.lose_withdraw=True
        with self.assertRaises(Blocked):self.act('withdraw')
        self.assertEqual(self.session.public()['confirmation']['state'],'withdraw_unknown')
        restarted=Session(self.root,self.hp); restarted.action('refresh',restarted.snapshot()['revision'])
        self.assertEqual(restarted.public()['confirmation']['state'],'cancelled')
        restarted.action('open_confirmation',restarted.snapshot()['revision'])
        self.assertNotEqual(restarted.public()['confirmation']['binding']['approval_id'],approval)
        self.assertEqual(sum(name=='hp_confirm_publish' for name,args in self.hp.calls),1)
        self.assertEqual(sum(name=='hp_cancel_confirmation' for name,args in self.hp.calls),1)

    def test_retry_after_new_save_recovers_old_admission_but_cannot_confirm_old_preview(self):
        self.hp.lose_submit = True
        with self.assertRaises(Blocked): self.act('prepare_preview')
        old = dict(self.hp.op)
        self.save_new_local_baseline()
        self.hp.lose_submit = False
        self.act('retry_preview')
        self.assertEqual(self.hp.op, old)
        with self.assertRaisesRegex(Blocked, 'local_preview_mismatch'): self.act('open_confirmation')
        self.act('prepare_preview')
        self.assertNotEqual(self.hp.op['operation_id'], old['operation_id'])
        self.assertEqual(self.hp.op['saved_commit'], 'e'*40)

    def test_retry_unreceived_old_save_records_definite_refusal_and_preserves_old_request(self):
        normal = self.hp.call
        phase = ['lost']
        def provider(name, args):
            if name == 'hp_submit_preview' and args['saved_commit'] == 'a'*40:
                self.hp.calls.append((name, dict(args)))
                if phase[0] == 'lost': raise OSError('before admission')
                return {'isError': True, 'structuredContent': {'error': 'workspace_changed'}}
            return normal(name, args)
        self.hp.call = provider
        with self.assertRaises(Blocked): self.act('prepare_preview')
        old = dict(self.session.journal['preview_operation']['arguments'])
        self.save_new_local_baseline(); phase[0] = 'refused'
        self.act('retry_preview')
        self.assertEqual(self.session.journal['preview_operation']['state'], 'rejected')
        self.act('refresh')
        self.assertEqual(self.session.journal['preview_operation']['state'], 'rejected')
        self.act('prepare_preview')
        self.assertEqual(self.session.journal['rejected_previews'][0]['arguments'], old)
        self.assertNotEqual(self.hp.op['operation_id'], old['operation_id'])
        self.assertEqual(self.hp.op['saved_commit'], 'e'*40)

    def test_confirm_success_then_read_rejection_remains_unknown_not_rejected(self):
        self.card(); self.hp.reject_read_after_confirm = True
        with self.assertRaisesRegex(Blocked,'response_uncertain'):self.act('confirm')
        self.assertEqual(self.session.public()['confirmation']['state'],'unknown')
        with self.assertRaises(Blocked):self.act('open_confirmation')
        self.assertEqual(sum(name=='hp_confirm_publish' for name,args in self.hp.calls),1)

    def test_withdraw_lost_before_acceptance_explicitly_retries_same_id_not_confirm(self):
        self.card(); original=self.session.public()['confirmation']['binding']['approval_id']
        self.hp.withdraw_before=True
        with self.assertRaises(Blocked):self.act('withdraw')
        self.act('refresh')
        self.assertEqual(self.session.public()['confirmation']['state'],'withdraw_unknown')
        self.hp.withdraw_before=False
        self.act('withdraw')
        self.assertEqual(self.session.public()['confirmation']['state'],'cancelled')
        requests=[args['approval_id'] for name,args in self.hp.calls if name=='hp_cancel_confirmation']
        self.assertEqual(requests,[original,original])
        self.assertFalse(any(name=='hp_confirm_publish' for name,args in self.hp.calls))

    def test_withdraw_prioritizes_other_surface_existing_operation_without_cancelling_it(self):
        self.hp.mode='publish';self.card();self.hp.lose_confirm=True;self.hp.prepared_unknown=True
        with self.assertRaises(Blocked):self.act('confirm')
        other={'operation_id':str(uuid.uuid4()),'site_id':'sample-site','kind':'publish','status':'running'}
        self.hp.existing=other
        self.act('withdraw')
        current=self.session.public()['confirmation']
        self.assertEqual(current['state'],'observed');self.assertEqual(current['operation'],other)
        self.assertEqual(sum(name=='hp_confirm_publish' for name,args in self.hp.calls),1)

    def test_lock_busy_and_metadata_symlink_fail_closed(self):
        with editor.workspace_lock(self.root):
            self.assertFalse(self.session.snapshot(force=True)['ready'])
            with self.assertRaises(Blocked): self.act('prepare_preview')
        journal = self.root/'.hp/control/journal.json'
        journal.symlink_to(Path(self.temp.name)/'outside.json')
        with self.assertRaises((ValueError,Blocked)): Session(self.root,self.hp)


class HTTPTests(PreviewTests):
    def setUp(self):
        super().setUp(); self.host = PreviewHost(self.session); self.host.start(); self.addCleanup(self.host.close)
    def request(self, path, method='GET', body=None, headers=None, site=False):
        server = self.host.site_server if site else self.host.control_server
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        connection.request(method,path,body,headers or {}); response = connection.getresponse(); data = response.read(); connection.close()
        return response.status, dict(response.getheaders()), data
    def test_http_origin_csrf_no_rpc_and_sandbox_isolation(self):
        self.assertEqual(self.request('/api/state')[0],200)
        for headers in ({'Origin':'null'}, {'Origin':'https://attacker.invalid'}, {'Host':'attacker.invalid'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/state',headers=headers)[0],403)
        body=json.dumps({'action':'refresh','revision':self.session.snapshot()['revision']})
        for headers in ({}, {'Origin':self.host.origin}, {'Origin':'null','X-HP-CSRF':self.host.csrf}):
            self.assertEqual(self.request('/api/action','POST',body,{'Content-Type':'application/json',**headers})[0],403)
        good={'Origin':self.host.origin,'X-HP-CSRF':self.host.csrf,'Content-Type':'application/json'}
        self.assertEqual(self.request('/api/action','POST',json.dumps({'tool':'hp_confirm_publish'}),good)[0],400)
        status,headers,html=self.request('/')
        self.assertIn(b'sandbox="allow-scripts"',html);self.assertNotIn(b'allow-same-origin',html)
        self.assertEqual(headers['X-Frame-Options'],'DENY');self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(self.hp.calls,[])
    def test_cross_site_top_level_document_can_open_shell_but_not_iframe_or_api(self):
        navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        self.assertEqual(self.request('/',headers=navigation)[0],200)
        self.assertEqual(self.request('/',headers={**navigation,'Sec-Fetch-Dest':'iframe'})[0],403)
        self.assertEqual(self.request('/',headers={**navigation,'Origin':'https://attacker.invalid'})[0],403)
        self.assertEqual(self.request('/api/state',headers=navigation)[0],403)
        self.assertEqual(self.request('/control.js',headers={**navigation,'Sec-Fetch-Dest':'script'})[0],403)
        self.assertEqual(self.hp.calls,[])

    def test_site_fresh_bytes_traversal_and_symlinks(self):
        prefix='/site/'+self.host.site_token+'/'
        self.assertIn(b'Initial local',self.request(prefix,site=True)[2])
        (self.root/'site/index.html').write_text('<h1>Instant local edit</h1>')
        self.assertIn(b'Instant local edit',self.request(prefix,site=True)[2])
        for path in ('../.hp/state.json','%2e%2e/.hp/state.json'):
            self.assertEqual(self.request(prefix+path,site=True)[0],404)
        (self.root/'site/escape.txt').symlink_to('/etc/passwd')
        self.assertEqual(self.request(prefix+'escape.txt',site=True)[0],404)
        self.assertEqual(self.request('/api/state',headers={'Origin':'null'})[0],403)


class TransportTests(unittest.TestCase):
    def test_pipe_writes_are_serialized_as_whole_json_lines(self):
        import threading
        from types import SimpleNamespace
        from hp_codex_client import CodexClient
        characters=[]
        class Pipe:
            def write(self,text):
                for character in text:
                    characters.append(character);time.sleep(0.00001)
            def flush(self):pass
        client=CodexClient.__new__(CodexClient)
        client.write_lock=threading.Lock();client.process=SimpleNamespace(stdin=Pipe())
        threads=[threading.Thread(target=client._send,args=({'id':number,'result':{}},)) for number in range(5)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=2)
        self.assertEqual(sorted(json.loads(line)['id'] for line in ''.join(characters).splitlines()),list(range(5)))

    def test_lazy_client_starts_only_on_remote_call_and_replaces_dead_owned_handle(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from hp_codex_client import LazyCodexClient,TransportError
        closed=[]
        dead=SimpleNamespace(process=SimpleNamespace(poll=lambda:0),reader=SimpleNamespace(is_alive=lambda:False),close=lambda:closed.append(True))
        fresh=SimpleNamespace(call=lambda name,args:{'structuredContent':{'ok':True}},close=lambda:None)
        lazy=LazyCodexClient(Path('/unused-test-directory'))
        with patch('hp_codex_client.CodexClient',return_value=fresh) as start:
            self.assertIsNone(lazy.client);start.assert_not_called()
            with self.assertRaises(TransportError):lazy.call('arbitrary_rpc',{})
            start.assert_not_called();lazy.client=dead
            self.assertTrue(lazy.call('hp_profile',{})['structuredContent']['ok'])
            start.assert_called_once();self.assertEqual(closed,[True])


if __name__ == '__main__': unittest.main()
