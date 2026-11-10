"""Exercise the shipped executable without customer Git/Python/CLI on PATH."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_local_editor import EditorTests


def main(binary):
    fixture = EditorTests()
    fixture.setUp()
    records = []
    try:
        empty = fixture.root / 'empty-path'; empty.mkdir()
        environment = {**os.environ, 'PATH': str(empty), 'PYTHONHOME': '/missing', 'PYTHONPATH': '/missing'}
        def call(*args):
            command = [str(Path(binary).resolve()), *map(str, args)]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
            identity = subprocess.run(['/bin/ps', '-p', str(process.pid), '-o', 'pid=,ppid=,lstart=,command='], capture_output=True, text=True).stdout.strip()
            records.append({'pid': process.pid, 'ppid': os.getpid(), 'command': command, 'identity': identity})
            stdout, stderr = process.communicate()
            if process.returncode: raise RuntimeError(stderr)
            return json.loads(stdout)
        call('init', fixture.archive, fixture.workspace, '--receipt', fixture.open_receipt)
        assert call('status', fixture.workspace) == {'changes': [], 'pending': False}
        (fixture.workspace / 'site/index.html').write_text('<h1>Native edit</h1>')
        packed = call('pack', fixture.workspace, fixture.bundle, '--summary', 'Update homepage')
        (fixture.workspace / 'site/index.html').write_text('<h1>Later local edit</h1>')
        retried = call('pack', fixture.workspace, fixture.bundle)
        assert packed == retried
        receipt = fixture.receipt(packed)
        call('accept', fixture.workspace, receipt)
        current = call('status', fixture.workspace)
        assert not current['pending'] and len(current['changes']) == 1
        assert 'Later local edit' in (fixture.workspace / 'site/index.html').read_text()
        print(json.dumps({'native_init_pack_accept': True, 'pending_retry_same_bytes': True,
                          'later_local_edits_preserved': True, 'empty_PATH': True,
                          'processes': records}))
    finally:
        fixture.doCleanups()


if __name__ == '__main__': main(sys.argv[1])
