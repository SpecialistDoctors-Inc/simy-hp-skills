import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
    'plugins/hp-local-editor/skills/hp-local-editor/scripts'))

import hp_platform
from hp_codex_client import desktop_codex, windows_codex_candidates, TransportError


def symlink_or_skip(test, link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        test.skipTest('symbolic links are unavailable for this account')


class LockTests(unittest.TestCase):
    def test_second_owner_is_refused_until_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owner.lock'
            first = hp_platform.open_private(path, os.O_CREAT | os.O_RDWR)
            hp_platform.lock_nonblocking(first)
            second = hp_platform.open_private(path, os.O_CREAT | os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    hp_platform.lock_nonblocking(second)
                hp_platform.close_lock(first)
                hp_platform.lock_nonblocking(second)
            finally:
                hp_platform.close_lock(second)

    def test_private_open_refuses_a_final_link(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'; target.write_bytes(b'private')
            link = Path(directory) / 'owner.lock'
            symlink_or_skip(self, link, target)
            with self.assertRaises(OSError):
                hp_platform.open_private(link, os.O_CREAT | os.O_RDWR)
            self.assertEqual(target.read_bytes(), b'private')


class SiteFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'site'
        (self.root / 'pages').mkdir(parents=True)
        (self.root / 'pages/index.html').write_bytes(b'<h1>A</h1>\n')
        self.outside = Path(self.temp.name) / 'outside'; self.outside.mkdir()
        (self.outside / 'secret.txt').write_bytes(b'private\n')

    def test_reads_and_replaces_exact_bytes_without_newline_translation(self):
        self.assertEqual(hp_platform.read_regular(self.root, 'pages/index.html', 100), b'<h1>A</h1>\n')
        hp_platform.replace_regular(self.root, 'pages/index.html', b'<h1>B</h1>\n')
        self.assertEqual((self.root / 'pages/index.html').read_bytes(), b'<h1>B</h1>\n')
        self.assertEqual(sorted(p.name for p in (self.root / 'pages').iterdir()), ['index.html'])

    def test_read_limit_returns_one_extra_byte_for_size_checks(self):
        self.assertEqual(len(hp_platform.read_regular(self.root, 'pages/index.html', 3)), 4)

    def test_rejects_directories_traversal_and_linked_files(self):
        with self.assertRaises(ValueError):
            hp_platform.read_regular(self.root, 'pages', 100)
        for relative in ('../outside/secret.txt', 'pages//index.html', ''):
            with self.assertRaises(ValueError):
                hp_platform.read_regular(self.root, relative, 100)
        symlink_or_skip(self, self.root / 'pages/link.txt', self.outside / 'secret.txt')
        with self.assertRaises(OSError):
            hp_platform.read_regular(self.root, 'pages/link.txt', 100)
        with self.assertRaises(OSError):
            hp_platform.replace_regular(self.root, 'pages/link.txt', b'overwrite')
        self.assertEqual((self.outside / 'secret.txt').read_bytes(), b'private\n')

    def test_rejects_linked_directories(self):
        symlink_or_skip(self, self.root / 'linked', self.outside, directory=True)
        with self.assertRaises(OSError):
            hp_platform.read_regular(self.root, 'linked/secret.txt', 100)
        with self.assertRaises(OSError):
            hp_platform.replace_regular(self.root, 'linked/secret.txt', b'overwrite')
        self.assertEqual((self.outside / 'secret.txt').read_bytes(), b'private\n')

    @unittest.skipUnless(hp_platform.WINDOWS, 'junctions exist only on Windows')
    def test_rejects_windows_junctions(self):
        import _winapi
        _winapi.CreateJunction(str(self.outside), str(self.root / 'junction'))
        self.assertTrue(hp_platform.is_link_path(self.root / 'junction'))
        with self.assertRaises(OSError):
            hp_platform.read_regular(self.root, 'junction/secret.txt', 100)
        with self.assertRaises(OSError):
            hp_platform.replace_regular(self.root, 'junction/secret.txt', b'overwrite')
        self.assertEqual((self.outside / 'secret.txt').read_bytes(), b'private\n')

    @unittest.skipUnless(hp_platform.WINDOWS, 'junctions exist only on Windows')
    def test_swapping_a_directory_for_a_junction_mid_update_never_writes_outside(self):
        import _winapi
        def decide(current):
            # Replace the checked parent with a junction to an outside folder.
            (self.root / 'pages').rename(self.root / 'moved')
            _winapi.CreateJunction(str(self.outside), str(self.root / 'pages'))
            return b'<h1>B</h1>\n'
        self.assertTrue(hp_platform.update_regular(self.root, 'pages/index.html', 100, decide))
        self.assertEqual((self.root / 'moved/index.html').read_bytes(), b'<h1>B</h1>\n')
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ['secret.txt'])
        self.assertEqual((self.outside / 'secret.txt').read_bytes(), b'private\n')

    def test_failed_commit_removes_the_temporary_file(self):
        real = hp_platform._commit
        def fail(*args):
            raise OSError('simulated disk failure')
        hp_platform._commit = fail
        try:
            with self.assertRaises(OSError):
                hp_platform.replace_regular(self.root, 'pages/index.html', b'new')
        finally:
            hp_platform._commit = real
        self.assertEqual(sorted(p.name for p in (self.root / 'pages').iterdir()), ['index.html'])
        self.assertEqual((self.root / 'pages/index.html').read_bytes(), b'<h1>A</h1>\n')


class ProcessTests(unittest.TestCase):
    def test_terminates_only_the_owned_live_child(self):
        # Wait until the interpreter finished any launcher re-exec (macOS framework builds).
        child = subprocess.Popen([sys.executable, '-c', 'import time; print("ready", flush=True); time.sleep(60)'],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            record = hp_platform.process_record(child.pid, ['sleeper'])
            self.assertEqual((record['pid'], record['ppid']), (child.pid, os.getpid()))
            self.assertTrue(record['started_at'])
            self.assertEqual(hp_platform.process_identity(child.pid), record['identity'])
            self.assertTrue(hp_platform.terminate_owned(child, record['identity']))
            child.wait(timeout=10)
            self.assertFalse(hp_platform.terminate_owned(child, record['identity']))
        finally:
            if child.poll() is None:
                child.terminate(); child.wait(timeout=10)
            child.stdout.close()

    def test_changed_identity_is_not_signalled(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            with self.assertRaises(ProcessLookupError):
                hp_platform.terminate_owned(child, 'not the recorded identity')
            self.assertIsNone(child.poll())
        finally:
            child.terminate(); child.wait(timeout=10)

    def test_exited_process_has_no_identity(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(timeout=10)
        self.assertIsNone(hp_platform.process_identity(child.pid))


class WindowsDiscoveryTests(unittest.TestCase):
    def test_prefers_local_copy_then_newest_store_package(self):
        with tempfile.TemporaryDirectory() as directory:
            local, programs = Path(directory) / 'local', Path(directory) / 'programs'
            environment = {'LOCALAPPDATA': str(local), 'ProgramFiles': str(programs)}
            for version in ('26.9.1.0', '26.10.2.0'):
                binary = programs / 'WindowsApps' / ('OpenAI.Codex_' + version + '_x64__2p2nqsd0c76g0') / 'app/resources/codex.exe'
                binary.parent.mkdir(parents=True); binary.write_bytes(b'fixture')
            newest = str(programs / 'WindowsApps/OpenAI.Codex_26.10.2.0_x64__2p2nqsd0c76g0/app/resources/codex.exe')
            self.assertEqual(desktop_codex(windows=True, environment=environment), newest)
            copy = local / 'OpenAI/Codex/bin/codex.exe'
            copy.parent.mkdir(parents=True); copy.write_bytes(b'fixture')
            self.assertEqual(desktop_codex(windows=True, environment=environment), str(copy))
            self.assertEqual([str(p) for p in windows_codex_candidates(environment)][1], newest)

    def test_missing_windows_desktop_does_not_fall_back_to_path(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {'LOCALAPPDATA': directory, 'ProgramFiles': directory, 'PATH': directory}
            with self.assertRaisesRegex(TransportError, 'codex_desktop_runtime_unavailable'):
                desktop_codex(windows=True, environment=environment)


if __name__ == '__main__':
    unittest.main()
