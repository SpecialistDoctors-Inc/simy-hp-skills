#!/usr/bin/env python3
"""Build a self-contained native plugin for this build host (not a user install)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd, log):
    process = subprocess.Popen(command, cwd=cwd,
        env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(log.parent / 'pyinstaller-cache')})
    identity = subprocess.run(['/bin/ps', '-p', str(process.pid), '-o',
        'pid=,ppid=,lstart=,command='], capture_output=True, text=True).stdout.strip()
    with log.open('a') as stream:
        stream.write(json.dumps({'pid': process.pid, 'ppid': os.getpid(),
                                 'command': command, 'identity': identity}) + '\n')
    # Never terminate build children by a timeout, process group or name.
    if process.wait():
        raise RuntimeError('native_build_failed')


def build(output):
    target = output.resolve()
    target.mkdir(parents=True, exist_ok=False)
    system, arch = platform.system().lower(), platform.machine().lower()
    if (system, arch) not in (('darwin', 'arm64'), ('darwin', 'x86_64'), ('linux', 'x86_64'), ('linux', 'aarch64')):
        raise RuntimeError('unsupported_build_platform')
    plugin = ROOT / 'plugins/hp-local-editor'
    stage = target / 'package'
    shutil.copytree(ROOT / '.agents', stage / '.agents')
    shutil.copyfile(ROOT / 'INSTALL.md', stage / 'INSTALL.md')
    shutil.copytree(plugin, stage / 'plugins/hp-local-editor', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    packaged = stage / 'plugins/hp-local-editor'
    scripts = packaged / 'skills/hp-local-editor/scripts'
    destination = stage / 'plugins/hp-local-editor/bin'
    destination.mkdir()
    log = target / 'processes.jsonl'
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
         '--name', 'hp', '--distpath', str(destination), '--workpath', str(target / 'build'),
         '--specpath', str(target), '--paths', str(scripts),
         '--add-data', str(packaged / 'assets') + os.pathsep + 'assets',
         '--collect-data', 'certifi', str(scripts / 'hp_runtime.py')], ROOT, log)
    binary = destination / 'hp'
    run([str(binary), '--help'], stage, log)
    # Record build-only dependencies and a reviewable source-to-binary manifest.
    manifest = {'platform': system + '-' + arch, 'python': platform.python_version(),
        'entrypoint': 'plugins/hp-local-editor/bin/hp',
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'installer_guide_sha256': hashlib.sha256((stage / 'INSTALL.md').read_bytes()).hexdigest(),
        'source_sha256': {str(p.relative_to(packaged)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(packaged.rglob('*')) if p.is_file() and 'bin' not in p.relative_to(packaged).parts and '__pycache__' not in p.parts and p.suffix != '.pyc'}}
    (stage / 'native-runtime.json').write_text(json.dumps(manifest, indent=2) + '\n')
    archive = target / ('hp-local-editor-' + manifest['platform'] + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(stage.rglob('*')):
            if path.is_file(): bundle.write(path, path.relative_to(stage))
    manifest['archive'] = str(archive)
    manifest['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (target / 'build-receipt.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'source_sha256'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
