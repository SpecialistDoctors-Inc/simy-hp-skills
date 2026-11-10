"""Local preview journal and HP actions. Site files and save metadata are read-only."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from urllib.parse import urlsplit
import uuid

import hp_local_editor as editor

ACTIVE = {'queued', 'running', 'retry_wait', 'reconciling', 'reporting'}


class Blocked(ValueError):
    pass


class DefiniteRejection(Blocked):
    pass


SAFE_REFUSALS = {'confirmation_invalid', 'confirmation_expired', 'confirmation_stale',
    'publication_not_enabled', 'site_access_denied', 'insufficient_scope', 'access_revoked',
    'operation_access_denied', 'workspace_access_denied', 'workspace_changed', 'invalid_input'}


def need(condition, code):
    if not condition:
        raise Blocked(code)


def https(value):
    need(isinstance(value, str), 'invalid_remote_url')
    parsed = urlsplit(value)
    need(parsed.scheme == 'https' and parsed.hostname and '@' not in parsed.netloc and
        parsed.port in (None, 443) and not any(ord(c) < 33 for c in value), 'invalid_remote_url')
    host = parsed.hostname
    need(host != 'localhost' and not host.endswith(('.localhost', '.local')), 'invalid_remote_url')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    need(address is None or address.is_global, 'invalid_remote_url')
    return value


def binding(value):
    need(isinstance(value, dict), 'invalid_confirmation')
    for name in ('approval_id', 'preview_id'):
        need(isinstance(value.get(name), str) and str(uuid.UUID(value[name])) == value[name], 'invalid_confirmation')
    need(value.get('mode') in ('publish', 'connection_test'), 'invalid_confirmation')
    need(isinstance(value.get('site_id'), str), 'invalid_confirmation')
    for name, size in (('source_commit', 40), ('artifact_sha256', 64)):
        need(isinstance(value.get(name), str) and re.fullmatch('[0-9a-f]{'+str(size)+'}', value[name]), 'invalid_confirmation')
    return {name: value[name] for name in ('approval_id', 'preview_id', 'site_id', 'source_commit', 'artifact_sha256', 'mode')}


def publication_details(value):
    if not isinstance(value, dict):
        return None
    result = {name: value.get(name) if type(value.get(name)) is int and value[name] > 0 else None
              for name in ('from_version', 'to_version')}
    result['already_current'] = value.get('already_current') is True
    result['changes'] = None
    changes = value.get('changes')
    if isinstance(changes, dict) and isinstance(changes.get('items'), list):
        items = changes['items']
        count = changes.get('additional_count')
        if (len(items) <= 5 and type(count) is int and count >= 0 and
                all(isinstance(item, dict) and item.get('kind') in ('added', 'removed', 'changed', 'description') and all(isinstance(item.get(key), str) and len(item[key]) <= 120
                    for key in ('title', 'before', 'after')) for item in items)):
            result['changes'] = {'items': [{key: item[key] for key in ('title', 'kind', 'before', 'after')}
                                         for item in items], 'additional_count': count}
    return result


class Session:
    def __init__(self, workspace, client, clock=time.time):
        self.root = Path(workspace).resolve()
        self.client, self.clock = client, clock
        self.action_lock, self.data_lock = threading.Lock(), threading.RLock()
        self.confirmation = None
        self.last_poll = 0
        self.local_revision, self.local = None, None
        with editor.workspace_lock(self.root):
            _, hp, state, _ = editor.load(self.root)
            self.site_id, self.workspace_id = state['snapshot']['site_id'], state['snapshot']['workspace_id']
            self.directory = hp / 'control'
            need(not self.directory.is_symlink(), 'unsafe_control_metadata')
            self.directory.mkdir(mode=0o700, exist_ok=True)
            path = self.directory / 'journal.json'
            need(not path.is_symlink() and (not path.exists() or stat.S_ISREG(path.lstat().st_mode)), 'unsafe_control_metadata')
            self.journal = editor.parse_json(path.read_bytes()) if path.exists() else {
                'schema_version': 1, 'site_id': self.site_id, 'workspace_id': self.workspace_id}
            need(self.journal.get('schema_version') == 1 and self.journal.get('site_id') == self.site_id and
                self.journal.get('workspace_id') == self.workspace_id, 'journal_workspace_mismatch')
        self.identity = None
        self.environments = {}

    def record(self, **changes):
        with self.data_lock:
            self.journal = {**self.journal, **changes}
            path = self.directory / 'journal.json'
            temporary = self.directory / ('journal-' + secrets.token_hex(8) + '.tmp')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(self.journal, stream, ensure_ascii=False, separators=(',', ':'))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)

    def revision(self):
        rows = []
        for directory, dirs, files in os.walk(self.root / 'site', followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                info = path.lstat()
                need(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'unsafe_site_file')
                relative = path.relative_to(self.root / 'site').as_posix()
                editor.safe_path(relative, directory=path.is_dir())
                rows.append((relative, info.st_mtime_ns, info.st_size, info.st_ino))
        need(len(rows) <= 3000, 'site_file_limit')
        metadata = self.root / '.hp/state.json'
        info = metadata.lstat()
        rows.append(('.hp/state.json', info.st_mtime_ns, info.st_size, info.st_ino))
        rows.append(('.hp/pending', (self.root / '.hp/pending').exists()))
        return hashlib.sha256(editor.canonical(sorted(rows, key=lambda row: row[0]))).hexdigest()

    def snapshot(self, force=False):
        revision = self.revision()
        if force or revision != self.local_revision:
            try:
                with editor.workspace_lock(self.root):
                    _, _, state, base = editor.load(self.root)
                    need(state['snapshot']['site_id'] == self.site_id and state['snapshot']['workspace_id'] == self.workspace_id, 'workspace_changed')
                    files = editor.read_tree(self.root / 'site')
                    pending = (self.root / '.hp/pending').exists()
                    self.local = {'revision': revision, 'saved_commit': state['snapshot']['base_commit'],
                        'dirty': bool(editor.changes(base, files)), 'pending': pending,
                        'tree_sha256': editor.digest(editor.canonical(editor.entries(files))), 'ready': True}
                    self.local_revision = revision
            except ValueError as error:
                if 'Workspace is busy' not in str(error):
                    raise
                return {'revision': revision, 'ready': False, 'busy': True, 'dirty': True, 'pending': True}
        return dict(self.local)

    def clean(self):
        local = self.snapshot(force=True)
        need(local['ready'] and not local['dirty'] and not local['pending'], 'save_with_codex_first')
        return local

    def tool(self, name, arguments):
        result = self.client.call(name, arguments)
        need(isinstance(result, dict), 'hp_response_invalid')
        if result.get('isError'):
            code = (result.get('structuredContent') or {}).get('error')
            if code in SAFE_REFUSALS:
                raise DefiniteRejection(code)
            raise Blocked('hp_request_rejected')
        need(isinstance(result.get('structuredContent'), dict), 'hp_response_invalid')
        return result

    def authenticate(self):
        try:
            profile = self.tool('hp_profile', {})['structuredContent']
            sites = self.tool('hp_list_sites', {})['structuredContent'].get('sites')
            need(isinstance(profile.get('subject'), str) and self.site_id in profile.get('sites', []), 'account_site_mismatch')
            need(not self.journal.get('subject') or self.journal['subject'] == profile['subject'], 'account_changed')
            need(isinstance(sites, list), 'account_site_mismatch')
            site = next((site for site in sites if site.get('site_id') == self.site_id), None)
            need(site is not None, 'account_site_mismatch')
        except Exception:
            with self.data_lock:
                self.identity = None
                self.environments = {}
                self.confirmation = None
            raise
        identity = {'email': profile.get('email', ''), 'site_name': site.get('name', self.site_id)}
        deployments = site.get('deployments') or {}
        environments = {}
        for environment in ('dev', 'production'):
            record = deployments.get(environment) if isinstance(deployments, dict) else None
            timestamp = record.get('updated_at') if isinstance(record, dict) else None
            version = record.get('version') if isinstance(record, dict) else None
            environments[environment] = {
                'updated_at': timestamp if type(timestamp) is int and timestamp > 0 else None,
                'version': version if type(version) is int and version > 0 else None,
            }
        with self.data_lock:
            self.identity = identity
            self.environments = environments
        self.record(subject=profile['subject'])
        return profile

    def preview(self):
        operation = self.journal.get('preview_operation')
        need(isinstance(operation, dict) and operation['arguments']['workspace_id'] == self.workspace_id and
            operation['arguments']['site_id'] == self.site_id, 'preview_not_ready')
        receipt = operation.get('receipt') or {}
        result = receipt.get('result') or {}
        need(receipt.get('status') in ('completed', 'reporting') and result.get('kind') in ('preview', 'no_changes'), 'preview_not_ready')
        need(re.fullmatch('[0-9a-f]{40}', result.get('source_commit', '')) and
            re.fullmatch('[0-9a-f]{64}', result.get('artifact_sha256', '')), 'preview_not_ready')
        need(str(uuid.UUID(result.get('preview_id', ''))) == result['preview_id'], 'preview_not_ready')
        https(result['preview_url'])
        return operation, result

    def read_operation(self, operation):
        receipt = self.tool('hp_get_operation', {'operation_id': operation['arguments']['operation_id']})['structuredContent']
        need(receipt.get('operation_id') == operation['arguments']['operation_id'] and receipt.get('site_id') == self.site_id and receipt.get('kind') == 'submit_preview', 'operation_mismatch')
        updated = {**operation, 'receipt': receipt, 'state': 'observed'}
        self.record(preview_operation=updated)
        return updated

    def recover_confirmation(self):
        saved = self.journal.get('confirmation')
        if not saved or saved.get('state') not in ('prepared', 'sending', 'unknown', 'observed', 'withdrawing', 'withdraw_unknown', 'rejected'):
            return
        result = self.tool('hp_get_confirmation', {'approval_id': saved['binding']['approval_id']})['structuredContent']
        need(binding(result) == saved['binding'], 'confirmation_changed')
        details = publication_details(result.get('publication_details'))
        if details is not None and details != saved.get('publication_details'):
            saved = {**saved, 'publication_details': details}
            self.record(confirmation=saved)
        if result['state'] in ('accepted', 'already_requested'):
            operation = result.get('operation')
            need(isinstance(operation, dict) and operation.get('site_id') == self.site_id and operation.get('kind') == 'publish', 'operation_mismatch')
            if saved['state'] == 'prepared' and self.confirmation and operation.get('status') in ('failed', 'cancelled'):
                # A newly opened card plus a fresh explicit click may reach the
                # authoritative service guard; this observation alone grants nothing.
                self.record(confirmation={**saved, 'existing_operation': operation})
            else:
                self.confirmation = None
                self.record(confirmation={**saved, 'state': 'observed', 'operation': operation})
        elif result['state'] == 'cancelled':
            self.confirmation = None
            self.record(confirmation={**saved, 'state': 'cancelled'})
        elif result['state'] == 'connection_tested':
            tested = result.get('result')
            need(isinstance(tested, dict) and tested.get('kind') == 'connection_test' and tested.get('status') == 'completed' and tested.get('approval_id') == saved['binding']['approval_id'] and tested.get('preview_id') == saved['binding']['preview_id'], 'confirmation_changed')
            self.confirmation = None
            self.record(confirmation={**saved, 'state': 'tested', 'result': tested})
        # Prepared/unknown (even expiry) never authorizes resending confirmation.

    def refresh(self):
        self.authenticate()
        errors = []
        operation = self.journal.get('preview_operation')
        if operation and operation.get('state') != 'rejected':
            try:
                self.read_operation(operation)
            except Exception:
                self.record(preview_operation={**operation, 'state': 'unreceived'})
                errors.append('operation_readback_unavailable')
        try:
            self.recover_confirmation()
        except Exception:
            errors.append('confirmation_readback_unavailable')
        self.last_poll = self.clock()
        self.record(readback_errors=errors)

    def prepare_preview(self):
        local = self.clean(); self.authenticate()
        old = self.journal.get('preview_operation')
        if old and old.get('state') != 'rejected':
            self.read_operation(old)
            need((self.journal['preview_operation'].get('receipt') or {}).get('status') in ('failed', 'cancelled', 'completed'), 'inspect_original_operation')
        confirmation = self.journal.get('confirmation') or {}
        need(confirmation.get('state') not in ('sending', 'unknown', 'withdrawing', 'withdraw_unknown'), 'confirmation_uncertain')
        final = self.clean()
        need(final['tree_sha256'] == local['tree_sha256'] and final['saved_commit'] == local['saved_commit'], 'local_files_changed')
        arguments = {'operation_id': str(uuid.uuid4()), 'site_id': self.site_id,
            'workspace_id': self.workspace_id, 'saved_commit': local['saved_commit']}
        operation = {'arguments': arguments, 'local_tree_sha256': local['tree_sha256'], 'state': 'sending'}
        self.confirmation = None
        if old and old.get('state') == 'rejected':
            self.record(rejected_previews=[*self.journal.get('rejected_previews', []), old])
        self.record(preview_operation=operation, confirmation=None)
        try:
            receipt = self.tool('hp_submit_preview', arguments)['structuredContent']
            need(receipt.get('operation_id') == arguments['operation_id'] and receipt.get('site_id') == self.site_id, 'operation_mismatch')
            self.record(preview_operation={**operation, 'receipt': receipt, 'state': 'observed'})
        except Exception:
            self.record(preview_operation={**operation, 'state': 'unknown'})
            raise Blocked('operation_response_uncertain') from None

    def open_confirmation(self):
        local = self.clean(); self.refresh()
        operation, preview = self.preview()
        need(operation['arguments']['saved_commit'] == local['saved_commit'] and operation['local_tree_sha256'] == local['tree_sha256'], 'local_preview_mismatch')
        prior = self.journal.get('confirmation') or {}
        need(prior.get('state') not in ('sending', 'unknown', 'withdrawing', 'withdraw_unknown'), 'confirmation_uncertain')
        response = self.tool('hp_prepare_publish', {'site_id': self.site_id, 'preview_id': preview['preview_id']})
        data = response['structuredContent']; expected = binding(data)
        need(all(expected[name] == value for name, value in {'site_id': self.site_id, 'preview_id': preview['preview_id'], 'source_commit': preview['source_commit'], 'artifact_sha256': preview['artifact_sha256']}.items()), 'confirmation_changed')
        nonce = response.get('_meta', {}).get('confirmation_nonce')
        need(isinstance(nonce, str) and len(nonce) >= 32 and type(data.get('expires_at')) is int and data['expires_at'] > self.clock(), 'confirmation_invalid')
        https(data['preview_url']); https(data['production_url'])
        need(data['preview_url'] == preview['preview_url'], 'confirmation_changed')
        public = {name: data[name] for name in ('site_name', 'summary', 'preview_url', 'production_url', 'expires_at')}
        public['publication_details'] = publication_details(data.get('publication_details'))
        self.confirmation = {'nonce': nonce, 'binding': expected, 'local': local, **public}
        self.record(confirmation={'state': 'prepared', 'binding': expected, **public})

    def confirm(self):
        current = self.confirmation
        need(current is not None, 'reopen_confirmation')
        local = self.clean()
        need(local['tree_sha256'] == current['local']['tree_sha256'] and local['saved_commit'] == current['local']['saved_commit'] and current['expires_at'] > self.clock(), 'confirmation_changed')
        self.refresh()
        _, preview = self.preview()
        need(preview['preview_id'] == current['binding']['preview_id'] and preview['artifact_sha256'] == current['binding']['artifact_sha256'] and preview['source_commit'] == current['binding']['source_commit'], 'confirmation_changed')
        status = self.tool('hp_get_confirmation', {'approval_id': current['binding']['approval_id']})['structuredContent']
        need(binding(status) == current['binding'] and status['expires_at'] > self.clock(), 'confirmation_changed')
        need(status.get('state') == 'prepared' or (status.get('state') == 'already_requested' and (status.get('operation') or {}).get('status') in ('failed', 'cancelled')), 'confirmation_changed')
        need((self.journal.get('confirmation') or {}).get('state') == 'prepared', 'confirmation_uncertain')
        final = self.clean()  # Recheck after network reads, immediately before admission.
        need(final['tree_sha256'] == current['local']['tree_sha256'] and final['saved_commit'] == current['local']['saved_commit'], 'confirmation_changed')

        saved = self.journal['confirmation']
        self.record(confirmation={**saved, 'state': 'sending'})
        self.confirmation = None  # Consume RAM nonce once, including uncertain outcomes.
        try:
            self.tool('hp_confirm_publish', {'approval_id': current['binding']['approval_id'], 'confirmation_nonce': current['nonce']})
        except DefiniteRejection as error:
            self.record(confirmation={**saved, 'state': 'rejected', 'error_code': str(error)})
            raise Blocked('confirmation_rejected') from None
        except Exception:
            self.record(confirmation={**saved, 'state': 'unknown'})
            raise Blocked('confirmation_response_uncertain') from None
        try:
            self.recover_confirmation()
        except Exception:
            # Confirmation already returned success: a later read refusal is
            # never proof that the earlier mutation was rejected.
            self.record(confirmation={**saved, 'state': 'unknown'})
            raise Blocked('confirmation_response_uncertain') from None

    def retry_preview(self):
        self.authenticate()
        operation = self.journal.get('preview_operation')
        need(operation and operation.get('state') in ('sending', 'unknown', 'unreceived'), 'inspect_original_operation')
        # Reconcile the immutable earlier request even if local editing/saving
        # continued. Publication still requires the current clean tree to match.
        self.record(preview_operation={**operation, 'state': 'sending'})
        try:
            self.tool('hp_submit_preview', operation['arguments'])
        except DefiniteRejection as error:
            if str(error) == 'workspace_changed':
                # Service first returns an existing same-ID receipt. This code
                # means no such operation was admitted, and its workspace CAS
                # prevents an older in-flight admission after the newer save.
                self.record(preview_operation={**operation, 'state': 'rejected',
                    'receipt': None, 'error_code': 'workspace_changed'})
                return
            self.record(preview_operation={**operation, 'state': 'unknown'})
            raise Blocked('operation_response_uncertain') from None
        except Exception:
            self.record(preview_operation={**operation, 'state': 'unknown'})
            raise Blocked('operation_response_uncertain') from None
        try:
            self.read_operation(operation)
        except Exception:
            self.record(preview_operation={**operation, 'state': 'unknown'})
            raise Blocked('operation_response_uncertain') from None

    def withdraw(self):
        self.authenticate()
        saved = self.journal.get('confirmation')
        need(saved and saved.get('state') in ('prepared', 'unknown', 'sending', 'rejected', 'withdrawing', 'withdraw_unknown'), 'confirmation_not_withdrawable')
        self.confirmation = None
        self.record(confirmation={**saved, 'state': 'withdrawing'})
        try:
            self.tool('hp_cancel_confirmation', {'approval_id': saved['binding']['approval_id']})
            # The readback may reveal another surface's existing operation;
            # cancellation never means rollback or absence of a publication.
            self.recover_confirmation()
        except Exception:
            self.record(confirmation={**saved, 'state': 'withdraw_unknown'})
            raise Blocked('confirmation_withdrawal_uncertain') from None

    def resume(self):
        self.clean(); self.refresh()
        operation = self.journal.get('preview_operation') or {}
        need((operation.get('receipt') or {}).get('status') == 'needs_attention', 'operation_not_resumable')
        recovery = operation.get('recovery_id') or str(uuid.uuid4())
        self.record(preview_operation={**operation, 'recovery_id': recovery})
        try:
            self.tool('hp_resume_operation', {'operation_id': operation['arguments']['operation_id'], 'recovery_id': recovery})
            self.read_operation(self.journal['preview_operation'])
        except Exception:
            raise Blocked('operation_response_uncertain') from None

    def action(self, action, revision):
        need(self.action_lock.acquire(blocking=False), 'action_in_progress')
        try:
            need(revision == self.snapshot(force=True)['revision'], 'local_files_changed')
            actions = {'refresh': self.refresh, 'prepare_preview': self.prepare_preview,
                'open_confirmation': self.open_confirmation, 'confirm': self.confirm, 'resume': self.resume,
                'retry_preview': self.retry_preview, 'withdraw': self.withdraw}
            need(action in actions, 'unsupported_action')
            actions[action]()
        finally:
            self.action_lock.release()

    def public(self):
        local = self.snapshot()
        with self.data_lock:
            saved = self.journal.get('confirmation')
            public = {'site_id': self.site_id, 'workspace_id': self.workspace_id, 'local': local,
                'identity': self.identity, 'environments': self.environments, 'operation': self.journal.get('preview_operation'),
                'readback_errors': [code for code in self.journal.get('readback_errors', [])
                    if code in ('operation_readback_unavailable', 'confirmation_readback_unavailable')],
                'confirmation': saved, 'can_confirm': bool(self.confirmation and saved and saved['state'] == 'prepared' and local.get('ready') and not local.get('dirty') and not local.get('pending') and self.confirmation['expires_at'] > self.clock() and local.get('tree_sha256') == self.confirmation['local']['tree_sha256'] and local.get('saved_commit') == self.confirmation['local']['saved_commit'])}
        # Journal never contains nonce or full prepare response.
        return public
