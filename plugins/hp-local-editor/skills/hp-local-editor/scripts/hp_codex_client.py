"""HP-only installed Codex app-server transport. No model turns or credential reads."""
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading

TOOLS = {'hp_profile', 'hp_list_sites', 'hp_submit_preview', 'hp_get_operation',
         'hp_prepare_publish', 'hp_confirm_publish', 'hp_get_confirmation',
         'hp_resume_operation', 'hp_cancel_operation', 'hp_cancel_confirmation'}


class TransportError(RuntimeError):
    pass


class CodexClient:
    def __init__(self, directory, executable='codex'):
        executable = shutil.which(executable)
        if not executable:
            raise TransportError('codex_not_installed')
        self.directory = Path(directory)
        self.process = subprocess.Popen([executable, 'app-server', '--listen', 'stdio://'],
            cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.identity = subprocess.check_output(['/bin/ps', '-p', str(self.process.pid),
            '-o', 'pid=,ppid=,lstart=,command='], text=True).strip()
        record = {'pid': self.process.pid, 'ppid': int(subprocess.check_output(['/bin/ps', '-p', str(self.process.pid), '-o', 'ppid='], text=True)),
            'started_at': subprocess.check_output(['/bin/ps', '-p', str(self.process.pid), '-o', 'lstart='], text=True).strip(),
            'command': [executable, 'app-server', '--listen', 'stdio://'], 'identity': self.identity}
        path = self.directory / 'codex-process.json'
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(record, stream)
        self.responses, self.lock, self.sequence = queue.Queue(), threading.Lock(), 0
        self.write_lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.rpc('initialize', {'clientInfo': {'name': 'hp-local-preview', 'version': '0.2.0'},
                'capabilities': {'experimentalApi': True, 'explicitGatewayOauth': True}})
            self._send({'method': 'initialized', 'params': {}})
            started = self.rpc('thread/start', {'cwd': str(self.directory), 'ephemeral': True,
                'approvalPolicy': 'never', 'sandbox': 'read-only'})
            self.thread_id = started['thread']['id']
        except Exception:
            self.close()
            raise

    def _send(self, value):
        with self.write_lock:
            self.process.stdin.write(json.dumps(value) + '\n')
            self.process.stdin.flush()

    def _read(self):
        try:
            for line in self.process.stdout:
                value = json.loads(line)
                if 'id' in value and ('result' in value or 'error' in value):
                    self.responses.put(value)
                elif 'id' in value:
                    # No automatic approvals, browser opening, commands or turns.
                    self._send({'id': value['id'], 'error': {'code': -32601, 'message': 'Unsupported request'}})
        except Exception:
            pass
        self.responses.put(None)

    def rpc(self, method, params):
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            try:
                self._send({'id': request_id, 'method': method, 'params': params})
                while True:
                    value = self.responses.get(timeout=90)
                    if value is None:
                        raise TransportError('codex_disconnected')
                    if value.get('id') != request_id:
                        continue  # Late result from an earlier timed-out call.
                    if 'error' in value:
                        raise TransportError('codex_request_failed')
                    return value['result']
            except (OSError, queue.Empty):
                raise TransportError('codex_response_uncertain') from None

    def call(self, name, arguments):
        if name not in TOOLS:
            raise TransportError('unsupported_hp_tool')
        return self.rpc('mcpServer/tool/call', {'threadId': self.thread_id,
            'server': 'hp-control', 'tool': name, 'arguments': arguments})

    def close(self):
        if self.process.poll() is not None:
            return
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            current = subprocess.run(['/bin/ps', '-p', str(self.process.pid), '-o',
                'pid=,ppid=,lstart=,command='], capture_output=True, text=True)
            if current.returncode == 0 and current.stdout.strip() == self.identity:
                os.kill(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=5)
            else:
                raise TransportError('owned_codex_identity_changed') from None


class LazyCodexClient:
    """Local preview starts independently of cloud/auth availability."""
    def __init__(self, directory, executable='codex'):
        self.directory, self.executable = directory, executable
        self.client, self.lock = None, threading.Lock()

    def call(self, name, arguments):
        if name not in TOOLS:
            raise TransportError('unsupported_hp_tool')
        with self.lock:
            if self.client is not None and (self.client.process.poll() is not None or not self.client.reader.is_alive()):
                self.client.close()
                self.client = None
            if self.client is None:
                self.client = CodexClient(self.directory, self.executable)
            return self.client.call(name, arguments)

    def close(self):
        if self.client:
            self.client.close()
