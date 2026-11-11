import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
    'plugins/hp-local-editor/skills/hp-local-editor/scripts'))
from hp_codex_client import CodexClient, desktop_codex, desktop_environment, select_hp_server, TransportError


class DesktopRuntimeTests(unittest.TestCase):
    def test_initial_call_waits_for_mcp_startup_then_calls_tool_once(self):
        client = CodexClient.__new__(CodexClient)
        client.thread_id, client.hp_server = 'fixture-thread', None
        server = {'name': 'plugin_hp', 'httpOrigin': 'https://hp-control.simy.one',
                  'runtimeStatus': 'starting', 'tools': {}}
        statuses = iter(['absent', 'starting', 'connected'])
        calls = []
        def rpc(method, params, timeout=90):
            calls.append((method, params))
            if method == 'mcpServerStatus/list':
                status = next(statuses)
                if status == 'absent': return {'data': []}
                return {'data': [{**server, 'runtimeStatus': status,
                    'tools': {'hp_profile': {}} if status == 'connected' else {}}]}
            return {'structuredContent': {'email': 'review@example.invalid'}}
        client.rpc = rpc
        with patch('hp_codex_client.time.sleep') as wait:
            result = client.call('hp_profile', {})
        self.assertEqual(result['structuredContent']['email'], 'review@example.invalid')
        self.assertEqual(wait.call_count, 2)
        self.assertEqual(sum(method == 'mcpServer/tool/call' for method, _ in calls), 1)
        self.assertEqual(calls[-1][1]['server'], 'plugin_hp')

    def test_startup_timeout_or_auth_requirement_never_calls_a_tool(self):
        for status in ('starting', 'authenticationRequired'):
            with self.subTest(status=status):
                client = CodexClient.__new__(CodexClient)
                client.thread_id, client.hp_server = 'fixture-thread', None
                calls = []
                now = [0]
                def rpc(method, params, timeout=90):
                    calls.append(method)
                    if status == 'starting': now[0] = 31
                    return {'data': [{'name': 'hp-control', 'httpOrigin': 'https://hp-control.simy.one',
                        'runtimeStatus': status, 'tools': {}}]}
                client.rpc = rpc
                expected = 'hp_connection_start_timeout' if status == 'starting' else 'hp_connection_unavailable'
                with patch('hp_codex_client.time.monotonic', side_effect=lambda: now[0]), \
                     patch('hp_codex_client.time.sleep') as wait, self.assertRaisesRegex(TransportError, expected):
                    client.call('hp_profile', {})
                self.assertNotIn('mcpServer/tool/call', calls)
                wait.assert_not_called()

    def test_inventory_pages_share_one_startup_time_budget(self):
        client = CodexClient.__new__(CodexClient); client.thread_id = 'fixture'
        now, timeouts = [0], []
        def rpc(method, params, timeout=90):
            self.assertEqual(method, 'mcpServerStatus/list')
            timeouts.append(timeout); now[0] += 17
            return {'data': [], 'nextCursor': 'next'}
        client.rpc = rpc
        with patch('hp_codex_client.time.monotonic', side_effect=lambda: now[0]), \
             self.assertRaisesRegex(TransportError, 'hp_connection_start_timeout'):
            client.discover()
        self.assertEqual(timeouts, [30, 13])

    def test_late_rpc_replies_do_not_reset_the_response_deadline(self):
        client = CodexClient.__new__(CodexClient)
        client.lock, client.sequence = threading.Lock(), 0
        client._send = lambda value: None
        now, timeouts = [0], []
        class Replies:
            def get(self, timeout):
                timeouts.append(timeout); now[0] += 20
                return {'id': 999, 'result': {}}
        client.responses = Replies()
        with patch('hp_codex_client.time.monotonic', side_effect=lambda: now[0]), \
             self.assertRaisesRegex(TransportError, 'codex_response_uncertain'):
            client.rpc('fixture', {}, timeout=30)
        self.assertEqual(timeouts, [30, 10])
        self.assertFalse(client.lock.locked())

    def test_plugin_namespaced_connection_is_selected_by_verified_origin(self):
        valid = {'name': 'plugin_example_hp-control', 'httpOrigin': 'https://hp-control.simy.one',
                 'runtimeStatus': 'connected', 'tools': {'hp_profile': {}}}
        wrong = {**valid, 'name': 'hp-control', 'httpOrigin': 'https://unrelated.invalid'}
        self.assertEqual(select_hp_server([wrong, valid]), valid['name'])
        with self.assertRaisesRegex(TransportError, 'hp_connection_ambiguous'):
            select_hp_server([valid, {**valid, 'name': 'other_hp'}])
        with self.assertRaisesRegex(TransportError, 'hp_connection_ambiguous'):
            select_hp_server([valid, {**valid, 'name': 'starting_hp', 'runtimeStatus': 'starting'}])
        with self.assertRaisesRegex(TransportError, 'hp_connection_unavailable'):
            select_hp_server([{**valid, 'runtimeStatus': 'authenticationRequired'}])

    def test_empty_path_uses_os_utilities_without_adding_separate_cli_install(self):
        self.assertEqual(desktop_environment({'PATH': '', 'KEEP': 'value'}),
            {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'KEEP': 'value'})
    def test_finds_bundled_desktop_without_path_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'ChatGPT.app/Contents/Resources/codex-cli/bin/codex'
            binary.parent.mkdir(parents=True)
            binary.write_text('# fixture, never executed\n')
            binary.chmod(0o700)
            self.assertEqual(desktop_codex(app_roots=[Path(directory)]), str(binary.resolve()))

    def test_missing_desktop_does_not_fall_back_to_unrelated_path_command(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TransportError, 'codex_desktop_runtime_unavailable'):
                desktop_codex(app_roots=[Path(directory)])

    def test_explicit_missing_override_is_not_silently_ignored(self):
        with self.assertRaisesRegex(TransportError, 'codex_executable_unavailable'):
            desktop_codex('/nonexistent/hp-runtime-test/codex')


if __name__ == '__main__':
    unittest.main()
