import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_desktop_plugin as build  # noqa: E402


class AcceptanceTargetTest(unittest.TestCase):
    def test_acceptance_build_points_every_endpoint_at_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            packaged = Path(directory) / 'hp-local-editor'
            shutil.copytree(ROOT / 'plugins/hp-local-editor', packaged)
            build.target_acceptance(packaged)
            mcp = json.loads((packaged / 'mcp.json').read_text(encoding='utf-8'))
            self.assertEqual(mcp['mcpServers']['hp-control']['url'], build.ACCEPTANCE + '/mcp')
            client = (packaged / 'skills/hp-local-editor/scripts/hp_codex_client.py').read_text(encoding='utf-8')
            self.assertIn(f"HP_ORIGIN = '{build.ACCEPTANCE}'", client)
            plugin = json.loads((packaged / 'plugin.json').read_text(encoding='utf-8'))
            self.assertIn('検証環境', plugin['extensions']['com.openai']['interface']['displayName'])
            for path in packaged.rglob('*'):
                if path.is_file() and path.suffix in ('.json', '.md', '.py'):
                    self.assertNotIn(build.PRODUCTION, path.read_text(encoding='utf-8'), path)

    def test_source_tree_stays_production(self):
        mcp = json.loads((ROOT / 'plugins/hp-local-editor/mcp.json').read_text(encoding='utf-8'))
        self.assertEqual(mcp['mcpServers']['hp-control']['url'], build.PRODUCTION + '/mcp')



class SigningTest(unittest.TestCase):
    def test_signing_uses_hardened_runtime_timestamp_and_only_library_validation_entitlement(self):
        calls = []
        original = build.run
        build.run = lambda command, cwd, log: calls.append(command)
        try:
            build.sign_macos(Path('/tmp/hp'), 'Developer ID Application: Example (TEAM)', Path('/tmp/log'))
        finally:
            build.run = original
        sign, verify = calls
        self.assertEqual(sign[:5], ['codesign', '--force', '--options', 'runtime', '--timestamp'])
        self.assertEqual(verify[:3], ['codesign', '--verify', '--strict'])
        entitlements = build.ENTITLEMENTS.read_text(encoding='utf-8')
        self.assertEqual(entitlements.count('<key>'), 1)
        self.assertIn('com.apple.security.cs.disable-library-validation', entitlements)

    def test_notarization_must_be_accepted(self):
        import subprocess
        original = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            for status, accepted in (('Accepted', True), ('Invalid', False)):
                subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps({'id': 'n-1', 'status': status}), '')
                try:
                    if accepted:
                        self.assertEqual(build.notarize(Path('x.zip'), 'profile', Path(directory)), {'id': 'n-1', 'status': 'Accepted'})
                    else:
                        with self.assertRaises(RuntimeError):
                            build.notarize(Path('x.zip'), 'profile', Path(directory))
                finally:
                    subprocess.run = original


if __name__ == '__main__':
    unittest.main()
