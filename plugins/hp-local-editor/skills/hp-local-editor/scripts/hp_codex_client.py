"""HP-only installed Codex app-server transport. No model turns or credential reads."""
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time

TOOLS = {'hp_save_draft', 'hp_profile', 'hp_list_sites', 'hp_submit_preview', 'hp_get_operation',
         'hp_prepare_publish', 'hp_confirm_publish', 'hp_get_confirmation',
         'hp_resume_operation', 'hp_cancel_operation', 'hp_cancel_confirmation',
         'hp_download_workspace', 'hp_prepare_upload'}


class TransportError(RuntimeError):
    pass


def desktop_codex(executable=None, app_roots=None):
    """Use the already-installed desktop app; never install a second CLI."""
    if executable:
        resolved = shutil.which(str(executable))
        if resolved:
            return resolved
        raise TransportError('codex_executable_unavailable')
    roots = app_roots if app_roots is not None else (
        Path('/Applications'), Path.home() / 'Applications')
    for root in roots:
        for app, relative in (
            ('ChatGPT.app', 'Contents/Resources/codex-cli/bin/codex'),
            ('Codex.app', 'Contents/Resources/codex'),
            ('Codex.app', 'Contents/Resources/codex-cli/bin/codex'),
        ):
            candidate = Path(root) / app / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
    raise TransportError('codex_desktop_runtime_unavailable')


def desktop_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    paths = [part for part in result.get('PATH', '').split(os.pathsep) if part]
    # The desktop runtime itself uses OS utilities. These directories are part
    # of macOS/Linux, not a separately installed customer CLI or Python runtime.
    for directory in ('/usr/bin', '/bin', '/usr/sbin', '/sbin'):
        if directory not in paths:
            paths.append(directory)
    result['PATH'] = os.pathsep.join(paths)
    return result


def select_hp_server(servers):
    matches = hp_servers(servers)
    if len(matches) != 1:
        raise TransportError('hp_connection_ambiguous' if len(matches) > 1 else 'hp_connection_unavailable')
    if matches[0].get('runtimeStatus') != 'connected' or 'hp_profile' not in matches[0].get('tools', {}):
        raise TransportError('hp_connection_unavailable')
    return matches[0]['name']


def hp_servers(servers):
    return [server for server in servers if server.get('httpOrigin') == 'https://hp-control.simy.one'
            and server.get('runtimeStatus') != 'disabled']


class CodexClient:
    def __init__(self, directory, executable=None):
        executable = desktop_codex(executable)
        self.directory = Path(directory)
        self.process = subprocess.Popen([executable, 'app-server', '--listen', 'stdio://'],
            cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=desktop_environment())
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
            self.rpc('initialize', {'clientInfo': {'name': 'hp-local-preview', 'version': '0.3.13'},
                'capabilities': {'experimentalApi': True, 'explicitGatewayOauth': True}})
            self._send({'method': 'initialized', 'params': {}})
            started = self.rpc('thread/start', {'cwd': str(self.directory), 'ephemeral': True,
                'approvalPolicy': 'never', 'sandbox': 'read-only'})
            self.thread_id = started['thread']['id']
            self.hp_server = None
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

    def rpc(self, method, params, timeout=90):
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=max(0, timeout)):
            raise TransportError('codex_response_uncertain')
        try:
            if time.monotonic() >= deadline:
                raise TransportError('codex_response_uncertain')
            self.sequence += 1
            request_id = self.sequence
            try:
                self._send({'id': request_id, 'method': method, 'params': params})
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TransportError('codex_response_uncertain')
                    value = self.responses.get(timeout=remaining)
                    if value is None:
                        raise TransportError('codex_disconnected')
                    if value.get('id') != request_id:
                        continue  # Late result from an earlier timed-out call.
                    if 'error' in value:
                        raise TransportError('codex_request_failed')
                    return value['result']
            except (OSError, queue.Empty):
                raise TransportError('codex_response_uncertain') from None
        finally:
            self.lock.release()

    def call(self, name, arguments):
        if name not in TOOLS:
            raise TransportError('unsupported_hp_tool')
        if self.hp_server is None:
            self.hp_server = self.discover()
        return self.rpc('mcpServer/tool/call', {'threadId': self.thread_id,
            'server': self.hp_server, 'tool': name, 'arguments': arguments})

    def discover(self):
        deadline = time.monotonic() + 30
        while True:
            servers, cursor = [], None
            for _ in range(5):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError('hp_connection_start_timeout')
                try:
                    inventory = self.rpc('mcpServerStatus/list', {'threadId': self.thread_id,
                        'detail': 'toolsAndAuthOnly', 'limit': 100, 'cursor': cursor}, timeout=remaining)
                except TransportError:
                    if time.monotonic() >= deadline:
                        raise TransportError('hp_connection_start_timeout') from None
                    raise
                if time.monotonic() >= deadline:
                    raise TransportError('hp_connection_start_timeout')
                if not isinstance(inventory, dict) or not isinstance(inventory.get('data'), list):
                    raise TransportError('invalid_mcp_inventory')
                servers.extend(inventory['data'])
                cursor = inventory.get('nextCursor')
                if not cursor:
                    break
            if cursor:
                raise TransportError('hp_discovery_incomplete')
            matches = hp_servers(servers)
            if len(matches) > 1 or (matches and matches[0].get('runtimeStatus') not in ('starting', 'notStarted', None)):
                return select_hp_server(servers)
            # thread/start returns before asynchronous MCP startup is ready.
            # Discovery may also be empty before plugin loading has finished.
            # Never wait for an auth grant or accept an ambiguous connection.
            if time.monotonic() >= deadline:
                raise TransportError('hp_connection_start_timeout')
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    def close(self):
        if self.process.poll() is not None:
            return
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if self.process.poll() is not None:
                return
            current = subprocess.run(['/bin/ps', '-p', str(self.process.pid), '-o',
                'pid=,ppid=,lstart=,command='], capture_output=True, text=True)
            if current.returncode == 0 and current.stdout.strip() == self.identity:
                os.kill(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=5)
            else:
                raise TransportError('owned_codex_identity_changed') from None


class LazyCodexClient:
    """Local preview starts independently of cloud/auth availability."""
    def __init__(self, directory, executable=None):
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
