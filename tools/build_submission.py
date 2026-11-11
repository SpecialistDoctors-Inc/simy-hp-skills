#!/usr/bin/env python3
"""Build a portal-root ZIP using a verified native build and current listing data.

This creates a local candidate only. It neither uploads nor claims review readiness.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(native_package, output):
    native_package, output = native_package.resolve(), output.resolve()
    proof = json.loads((native_package / 'native-runtime.json').read_text())
    original = native_package / 'plugins/hp-local-editor'
    current = ROOT / 'plugins/hp-local-editor'
    # Listing fields can change without recompiling; all embedded modules and
    # preview assets must still match the proven executable's source snapshot.
    runtime_files = [name for name in proof['source_sha256']
                     if name.startswith(('skills/hp-local-editor/scripts/', 'assets/preview/'))]
    for name in runtime_files:
        if digest(current / name) != proof['source_sha256'][name]:
            raise ValueError('native runtime needs rebuilding: ' + name)
    if digest(original / 'bin/hp') != proof['binary_sha256']:
        raise ValueError('native binary digest mismatch')
    manifest = json.loads((current / 'plugin.json').read_text())
    interface = manifest['extensions']['com.openai']['interface']
    for name in ('displayName', 'shortDescription', 'longDescription', 'developerName',
                 'websiteURL', 'supportURL', 'privacyPolicyURL', 'termsOfServiceURL', 'composerIcon', 'logo'):
        if not interface.get(name):
            raise ValueError('missing listing field: ' + name)
    cases = manifest['extensions']['com.openai']['review']['test_cases']
    if len(cases['positive']) != 5 or len(cases['negative']) != 3:
        raise ValueError('five positive and three negative review cases are required')
    if not manifest['extensions']['com.openai'].get('requires_local_executor'):
        raise ValueError('desktop-only declaration required')
    output.mkdir(parents=True, exist_ok=False)
    stage = output / 'plugin'
    shutil.copytree(current, stage, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (stage / 'bin').mkdir(exist_ok=True)
    shutil.copy2(original / 'bin/hp', stage / 'bin/hp')
    archive = output / ('hp-local-editor-' + manifest['version'] + '-submission-draft.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
        for path in sorted(stage.rglob('*')):
            if path.is_symlink():
                raise ValueError('unexpected package symlink')
            if path.is_file():
                stream.write(path, path.relative_to(stage))
    with zipfile.ZipFile(archive) as stream:
        assert {'plugin.json', 'mcp.json', 'bin/hp'} <= set(stream.namelist())
        assert not any(name.startswith(('.agents/', 'output/')) for name in stream.namelist())
        assert stream.getinfo('bin/hp').external_attr >> 16 & 0o111
    receipt = {'version': manifest['version'], 'platform': proof['platform'],
               'archive': str(archive), 'sha256': digest(archive),
               'native_binary_sha256': proof['binary_sha256'], 'embedded_source_verified': True,
               'positive_cases': 5, 'negative_cases': 3,
               'portal_uploaded': False, 'submitted_for_review': False,
               'requires_separate_evidence': ['publisher_identity', 'service_rollout', 'dedicated_reviewer_account',
                               'reviewer_test_execution', 'accessible_walkthrough_video',
                               'portal_checks_and_attestations']}
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.native_package, args.output)
