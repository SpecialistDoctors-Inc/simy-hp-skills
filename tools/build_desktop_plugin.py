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
sys.path.insert(0, str(ROOT / 'plugins/hp-local-editor/skills/hp-local-editor/scripts'))
import hp_platform  # noqa: E402  (process identity on macOS, Linux and Windows)

PRODUCTION = 'https://hp-control.simy.one'
ACCEPTANCE = 'https://hp-control-acceptance.simy.one'


def rewrite(path, old, new):
    text = path.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise RuntimeError('endpoint_rewrite_failed: ' + path.name)
    path.write_text(text.replace(old, new), encoding='utf-8')


def target_acceptance(packaged):
    """Point the staged copy at the Stripe-test acceptance service, labelled as such."""
    rewrite(packaged / 'mcp.json', PRODUCTION + '/mcp', ACCEPTANCE + '/mcp')
    rewrite(packaged / 'skills/hp-local-editor/SKILL.md', PRODUCTION + '/mcp', ACCEPTANCE + '/mcp')
    rewrite(packaged / 'skills/hp-local-editor/scripts/hp_codex_client.py',
            f"HP_ORIGIN = '{PRODUCTION}'", f"HP_ORIGIN = '{ACCEPTANCE}'")
    rewrite(packaged / 'plugin.json', '"displayName": "HP ローカル編集"', '"displayName": "HP ローカル編集（検証環境）"')


SUPPORTED = {('darwin', 'arm64'), ('darwin', 'x86_64'), ('linux', 'x86_64'),
             ('linux', 'aarch64'), ('windows', 'x86_64')}


def run(command, cwd, log):
    process = subprocess.Popen(command, cwd=cwd,
        env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(log.parent / 'pyinstaller-cache')})
    identity = hp_platform.process_identity(process.pid)
    with log.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'pid': process.pid, 'ppid': os.getpid(),
                                 'command': command, 'identity': identity}) + '\n')
    # Never terminate build children by a timeout, process group or name.
    if process.wait():
        raise RuntimeError('native_build_failed')


def build(output, acceptance=False):
    target = output.resolve()
    target.mkdir(parents=True, exist_ok=False)
    system, arch = platform.system().lower(), platform.machine().lower()
    arch = {'amd64': 'x86_64'}.get(arch, arch)
    if (system, arch) not in SUPPORTED:
        raise RuntimeError('unsupported_build_platform')
    executable = 'hp.exe' if system == 'windows' else 'hp'
    plugin = ROOT / 'plugins/hp-local-editor'
    stage = target / 'package'
    shutil.copytree(ROOT / '.agents', stage / '.agents')
    shutil.copyfile(ROOT / 'INSTALL.md', stage / 'INSTALL.md')
    shutil.copytree(plugin, stage / 'plugins/hp-local-editor', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    packaged = stage / 'plugins/hp-local-editor'
    if acceptance:
        target_acceptance(packaged)
    scripts = packaged / 'skills/hp-local-editor/scripts'
    destination = stage / 'plugins/hp-local-editor/bin'
    destination.mkdir()
    log = target / 'processes.jsonl'
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
         '--name', 'hp', '--distpath', str(destination), '--workpath', str(target / 'build'),
         '--specpath', str(target), '--paths', str(scripts),
         '--add-data', str(packaged / 'assets') + os.pathsep + 'assets',
         '--collect-data', 'certifi', str(scripts / 'hp_runtime.py')], ROOT, log)
    binary = destination / executable
    run([str(binary), '--help'], stage, log)
    # Record build-only dependencies and a reviewable source-to-binary manifest.
    manifest = {'platform': system + '-' + arch, 'endpoint': ACCEPTANCE if acceptance else PRODUCTION,
        'python': platform.python_version(),
        'entrypoint': 'plugins/hp-local-editor/bin/' + executable,
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'installer_guide_sha256': hashlib.sha256((stage / 'INSTALL.md').read_bytes()).hexdigest(),
        'source_sha256': {p.relative_to(packaged).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(packaged.rglob('*')) if p.is_file() and 'bin' not in p.relative_to(packaged).parts and '__pycache__' not in p.parts and p.suffix != '.pyc'}}
    (stage / 'native-runtime.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    archive = target / ('hp-local-editor-' + manifest['platform'] + ('-acceptance' if acceptance else '') + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(stage.rglob('*')):
            if path.is_file(): bundle.write(path, path.relative_to(stage))
    manifest['archive'] = str(archive)
    manifest['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (target / 'build-receipt.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'source_sha256'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--acceptance', action='store_true',
                        help='connect to the acceptance (Stripe test) service instead of production')
    args = parser.parse_args()
    build(args.output, args.acceptance)
