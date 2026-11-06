import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/hp-local-editor/skills/hp-local-editor/scripts/hp_local_editor.py'
spec = importlib.util.spec_from_file_location('editor', SCRIPT)
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.files = {'index.html': b'<h1>Original</h1>\n', 'old.txt': b'remove me\n'}
        self.manifest = dict(schema_version=1, kind='snapshot', site_id='sample-site', workspace_id=str(uuid.uuid4()), base_commit='a' * 40, files=e.entries(self.files))
        self.archive = self.root / 'snapshot.zip'
        self.archive.write_bytes(e.zip_bytes(self.manifest, self.files))
        self.bundle = self.root / 'changes.zip'
        self.open_receipt = self.root / 'open-result.json'
        self.open_result = dict(schema_version=1, kind='workspace', site_id=self.manifest['site_id'], workspace_id=self.manifest['workspace_id'], base_commit=self.manifest['base_commit'], snapshot_file_id='example-snapshot-file', snapshot_sha256=e.digest(self.archive.read_bytes()), download_url='https://drive.google.com/file/d/example-snapshot-file/view', upload_folder_id='example-upload-folder')
        self.open_receipt.write_text(json.dumps(self.open_result))

    def init(self):
        e.init(self.archive, self.workspace, self.open_receipt)
        return self.workspace / 'site'

    def receipt(self, packed):
        receipt = dict(schema_version=1, kind='saved', site_id=self.manifest['site_id'], workspace_id=self.manifest['workspace_id'], base_commit='a' * 40, operation_id=packed['operation_id'], saved_commit='b' * 40, bundle_sha256=packed['bundle_sha256'])
        path = self.root / 'receipt.json'
        path.write_text(json.dumps(receipt))
        return path

    def test_init_binds_bytes_and_manifest_identity_to_authenticated_open_result(self):
        changed = dict(self.files, **{'index.html': b'<h1>self-consistent malicious replacement</h1>'})
        rewritten = e.zip_bytes(dict(self.manifest, files=e.entries(changed)), changed)
        original = self.archive.read_bytes()
        scenarios = [('self-consistent ZIP', rewritten, self.open_result),
            ('wrong site', original, dict(self.open_result, site_id='another-site')),
            ('other workspace', original, dict(self.open_result, workspace_id=str(uuid.uuid4()))),
            ('wrong base', original, dict(self.open_result, base_commit='c' * 40))]
        for name, blob, receipt in scenarios:
            with self.subTest(name=name):
                self.archive.write_bytes(blob)
                self.open_receipt.write_text(json.dumps(receipt))
                before = {p.relative_to(self.root).as_posix() for p in self.root.rglob('*')}
                with self.assertRaisesRegex(ValueError, 'workspace receipt'):
                    e.init(self.archive, self.root / 'new-parent' / 'workspace', self.open_receipt)
                self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob('*')}, before)
                self.assertEqual(self.archive.read_bytes(), blob)
                self.assertEqual(json.loads(self.open_receipt.read_text()), receipt)
        self.archive.write_bytes(original)
        self.open_receipt.write_text(json.dumps(self.open_result))
        self.init()
        self.assertEqual(json.loads((self.workspace / '.hp/open-receipt.json').read_text()), self.open_result)

    def test_init_requires_receipt_and_preserves_existing_workspace(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'init', str(self.archive), str(self.workspace)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.workspace.exists())
        self.assertFalse((self.root / '.workspace.hp.lock').exists())
        site = self.init()
        (site / 'index.html').write_text('keep local edits')
        with self.assertRaisesRegex(ValueError, 'must not exist'):
            e.init(self.archive, self.workspace, self.open_receipt)
        self.assertEqual((site / 'index.html').read_text(), 'keep local edits')

    def test_diff_noop_and_explicit_deletion(self):
        site = self.init()
        self.assertEqual(e.status(self.workspace)['changes'], [])
        self.assertEqual(e.pack(self.workspace, self.bundle), {'noop': True})
        self.assertFalse(self.bundle.exists())
        (site / 'index.html').write_text('<h1>Updated</h1>\n')
        (site / 'old.txt').unlink()
        (site / 'new.txt').write_text('new\n')
        status = e.status(self.workspace, True)
        self.assertIn('-<h1>Original</h1>', status['diff'])
        self.assertIn('+<h1>Updated</h1>', status['diff'])
        e.pack(self.workspace, self.bundle)
        with zipfile.ZipFile(self.bundle) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            deleted = next(c for c in manifest['changes'] if c['path'] == 'old.txt')
            self.assertEqual(deleted, dict(path='old.txt', action='delete', base_sha256=e.digest(self.files['old.txt']), sha256=None, size=0))
            self.assertNotIn('files/old.txt', archive.namelist())

    def test_retry_and_accept_preserve_later_edits(self):
        site = self.init()
        (site / 'index.html').write_text('first\n')
        first = e.pack(self.workspace, self.bundle, 'A change')
        original_blob = self.bundle.read_bytes()
        (site / 'index.html').write_text('second\n')
        (site / 'later.txt').write_text('later\n')
        second = e.pack(self.workspace, self.bundle)
        self.assertEqual(first['operation_id'], second['operation_id'])
        self.assertEqual(original_blob, self.bundle.read_bytes())
        with self.assertRaises(ValueError):
            e.pack(self.workspace, self.bundle, 'different')
        receipt = self.receipt(first)
        self.assertFalse(e.accept(self.workspace, receipt)['already_accepted'])
        self.assertTrue(e.accept(self.workspace, receipt)['already_accepted'])
        self.assertEqual((site / 'index.html').read_text(), 'second\n')
        status = e.status(self.workspace, True)
        self.assertIn('-first', status['diff'])
        self.assertIn('+second', status['diff'])
        self.assertIn('later.txt', [c['path'] for c in status['changes']])
        next_bundle = self.root / 'next-changes.zip'
        third = e.pack(self.workspace, next_bundle)
        self.assertNotEqual(first['operation_id'], third['operation_id'])
        with zipfile.ZipFile(next_bundle) as archive:
            self.assertEqual(json.loads(archive.read('manifest.json'))['base_commit'], 'b' * 40)

    def test_lock_contention_all_commands_and_release(self):
        with e.workspace_lock(self.workspace, create_parent=True):
            result = subprocess.run([sys.executable, str(SCRIPT), 'init', str(self.archive), str(self.workspace), '--receipt', str(self.open_receipt)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('Workspace is busy', result.stderr)
        site = self.init()
        (site / 'index.html').write_text('saved')
        packed = e.pack(self.workspace, self.bundle)
        receipt = self.receipt(packed)
        with e.workspace_lock(self.workspace):
            for args in [('status', self.workspace), ('pack', self.workspace, self.bundle), ('accept', self.workspace, receipt)]:
                with self.subTest(command=args[0]):
                    result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('Workspace is busy', result.stderr)
        e.accept(self.workspace, receipt)
        self.assertEqual(e.status(self.workspace)['changes'], [])

    def test_receipt_identity_and_base_must_match_pending(self):
        site = self.init()
        (site / 'index.html').write_text('saved')
        packed = e.pack(self.workspace, self.bundle)
        path = self.receipt(packed)
        original = json.loads(path.read_text())
        for key, value in [('site_id', 'another-site'), ('workspace_id', str(uuid.uuid4())), ('operation_id', str(uuid.uuid4())), ('base_commit', 'c' * 40)]:
            with self.subTest(key=key):
                path.write_text(json.dumps(dict(original, **{key: value})))
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    e.accept(self.workspace, path)
                self.assertTrue((self.workspace / '.hp/pending').is_dir())
                self.assertEqual(e.status(self.workspace)['changes'][0]['base_sha256'], e.digest(self.files['index.html']))
        path.write_text(json.dumps(original))
        e.accept(self.workspace, path)

    def test_pack_never_overwrites_unrelated_existing_output(self):
        site = self.init()
        (site / 'index.html').write_text('changed')
        self.bundle.write_bytes(b'important unrelated existing bytes')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            e.pack(self.workspace, self.bundle)
        self.assertEqual(self.bundle.read_bytes(), b'important unrelated existing bytes')
        new_output = self.root / 'new-package.zip'
        packed = e.pack(self.workspace, new_output)
        original = new_output.read_bytes()
        self.assertEqual(e.pack(self.workspace, new_output)['operation_id'], packed['operation_id'])
        self.assertEqual(new_output.read_bytes(), original)
        link = self.root / 'linked-output.zip'
        link.symlink_to(new_output)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            e.pack(self.workspace, link)
        self.assertEqual(new_output.read_bytes(), original)

    def test_matching_ack_is_noop(self):
        site = self.init()
        (site / 'index.html').write_text('saved')
        packed = e.pack(self.workspace, self.bundle)
        e.accept(self.workspace, self.receipt(packed))
        self.assertEqual(e.status(self.workspace)['changes'], [])
        self.assertEqual(e.pack(self.workspace, self.bundle), {'noop': True})

    def test_wrong_receipt_and_tampered_pending(self):
        site = self.init()
        (site / 'index.html').write_text('saved')
        packed = e.pack(self.workspace, self.bundle)
        receipt = self.receipt(packed)
        value = json.loads(receipt.read_text())
        value['bundle_sha256'] = '0' * 64
        receipt.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            e.accept(self.workspace, receipt)
        (self.workspace / '.hp/pending/candidate/index.html').write_text('tampered')
        with self.assertRaises(ValueError):
            e.pack(self.workspace, self.bundle)

    def test_baseline_tampering_and_local_symlink(self):
        site = self.init()
        baseline = self.workspace / '.hp/base/index.html'
        baseline.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'tampered'):
            e.status(self.workspace)
        baseline.write_bytes(self.files['index.html'])
        (site / 'escape.txt').symlink_to(self.archive)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            e.status(self.workspace)

    def test_cli_without_git_or_path_tools(self):
        env = dict(os.environ, PATH='')
        def run(*args):
            return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], env=env, capture_output=True, text=True, check=True)
        run('init', self.archive, self.workspace, '--receipt', self.open_receipt)
        (self.workspace / 'site/index.html').write_text('updated')
        self.assertTrue(json.loads(run('status', self.workspace, '--diff').stdout)['changes'])
        packed = json.loads(run('pack', self.workspace, self.bundle).stdout)
        run('accept', self.workspace, self.receipt(packed))
        self.assertEqual(json.loads(run('status', self.workspace).stdout)['changes'], [])

    def malformed(self, extras=(), manifest=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(self.manifest if manifest is None else manifest))
            for path, data in self.files.items():
                archive.writestr('files/' + path, data)
            for path, data, mode in extras:
                info = zipfile.ZipInfo(path)
                info.external_attr = mode << 16
                archive.writestr(info, data)
        return stream.getvalue()

    def test_malformed_archives(self):
        for path in ('../escape.txt', '/escape.txt', 'files/../escape.txt', 'files/a\\b.txt', 'files/a:b.txt', 'files/.env', 'files/node_modules/a.js', 'files/package.json', 'files/next.config.js', 'files/e\u0301.txt', 'files/INDEX.html', 'other.txt'):
            with self.subTest(path=path), self.assertRaises((ValueError, zipfile.BadZipFile)):
                e.snapshot(self.malformed([(path, b'x', stat.S_IFREG | 0o644)]))
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o644, stat.S_IFSOCK | 0o644):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                e.snapshot(self.malformed([('files/link.txt', b'target', mode)]))
        with self.assertRaises(ValueError):
            e.snapshot(self.malformed([('files/index.html', b'x', stat.S_IFREG | 0o644)]))
        with self.assertRaises(zipfile.BadZipFile):
            e.snapshot(b'not a zip')
        modified = dict(self.manifest, files=[dict(self.manifest['files'][0], sha256='0' * 64)] + self.manifest['files'][1:])
        with self.assertRaises(ValueError):
            e.snapshot(self.malformed(manifest=modified))

    def test_colliding_directory_and_empty_index(self):
        files = {'index.html': b'', 'A/x.txt': b'x', 'a/y.txt': b'y'}
        with self.assertRaises(ValueError):
            e.snapshot(e.zip_bytes(dict(self.manifest, files=e.entries(files)), files))
        files = {'index.html': b''}
        manifest, loaded = e.snapshot(e.zip_bytes(dict(self.manifest, files=e.entries(files)), files))
        self.assertEqual(loaded, files)

    def test_limits_and_output_inside_workspace(self):
        files = {'index.html': b'x' * (e.MAX_FILE + 1)}
        with self.assertRaises(ValueError):
            e.snapshot(e.zip_bytes(dict(self.manifest, files=e.entries(files)), files))
        site = self.init()
        (site / 'index.html').write_text('changed')
        with self.assertRaises(ValueError):
            e.pack(self.workspace, site / 'bundle.zip')
        with self.assertRaises(ValueError):
            e.pack(self.workspace, self.bundle, 'x' * 201)


if __name__ == '__main__':
    unittest.main()
