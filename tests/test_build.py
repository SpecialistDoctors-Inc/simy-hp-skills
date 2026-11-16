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
            mcp = json.loads((packaged / 'mcp.json').read_text())
            self.assertEqual(mcp['mcpServers']['hp-control']['url'], build.ACCEPTANCE + '/mcp')
            client = (packaged / 'skills/hp-local-editor/scripts/hp_codex_client.py').read_text()
            self.assertIn(f"HP_ORIGIN = '{build.ACCEPTANCE}'", client)
            plugin = json.loads((packaged / 'plugin.json').read_text())
            self.assertIn('検証環境', plugin['extensions']['com.openai']['interface']['displayName'])
            for path in packaged.rglob('*'):
                if path.is_file() and path.suffix in ('.json', '.md', '.py'):
                    self.assertNotIn(build.PRODUCTION, path.read_text(encoding='utf-8'), path)

    def test_source_tree_stays_production(self):
        mcp = json.loads((ROOT / 'plugins/hp-local-editor/mcp.json').read_text())
        self.assertEqual(mcp['mcpServers']['hp-control']['url'], build.PRODUCTION + '/mcp')


if __name__ == '__main__':
    unittest.main()
