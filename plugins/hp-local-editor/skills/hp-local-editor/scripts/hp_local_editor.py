#!/usr/bin/env python3
"""Bounded static-site workspace transport. Python standard library only."""
import argparse
from contextlib import contextmanager
import fcntl
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile

MAX_ZIP = 60 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
MAX_FILE = 10 * 1024 * 1024
MAX_FILES = 1000
SUFFIXES = set('.html .css .js .json .png .jpg .jpeg .webp .gif .svg .ico .txt .xml .woff .woff2 .ttf .pdf .webmanifest'.split())
DENIED = {'node_modules', 'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'vercel.json', 'netlify.toml', 'config.json', 'credentials.json', 'tsconfig.json', 'jsconfig.json', 'service-account.json', 'server.js', 'build.js', 'deploy.js', 'gulpfile.js', 'gruntfile.js'}


def fail(message):
    raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def parse_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def keys(value, expected, optional=()):
    if not isinstance(value, dict) or not set(expected) <= set(value) or set(value) - set(expected) - set(optional):
        fail('Unexpected manifest fields')


def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        fail('Invalid UUID')


def commit(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        fail('Invalid commit')


def safe_path(value, directory=False):
    if not isinstance(value, str) or not value or len(value) > 1024 or unicodedata.normalize('NFC', value) != value:
        fail('Invalid path')
    if any(c in value for c in ('\\', ':', '\x00')) or value.startswith('/') or any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail('Unsafe path')
    parts = value.split('/')
    if any(p in ('', '.', '..') or (p.startswith('.') and p != '.well-known') for p in parts):
        fail('Unsafe path component')
    if any(p.casefold() in DENIED or p.casefold() in {'.hp', '.git', '.github'} for p in parts):
        fail('Forbidden configuration path')
    if any('.config.' in p.casefold() or p.casefold().startswith(('credentials.', 'service-account.', 'service_account.')) for p in parts):
        fail('Forbidden build script')
    if not directory and Path(value).suffix.lower() not in SUFFIXES:
        fail('Unsupported file suffix')
    return value


def paths_valid(paths):
    folded = {}
    for path in paths:
        safe_path(path)
        # Include directory components: A/x and a/y collide on common filesystems.
        components = path.split('/')
        for n in range(1, len(components) + 1):
            prefix = '/'.join(components[:n])
            key = prefix.casefold()
            if key in folded and folded[key] != prefix:
                fail('Case-folding collision')
            folded[key] = prefix
    path_set = set(paths)
    for path in paths:
        if any('/'.join(path.split('/')[:n]) in path_set for n in range(1, len(path.split('/')))):
            fail('File/directory collision')


def validate_files(files):
    paths_valid(files)
    if 'index.html' not in files:
        fail('Root index.html is required')
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL or any(len(v) > MAX_FILE for v in files.values()):
        fail('Editable file limits exceeded')


def entries(files):
    return [{'path': p, 'sha256': digest(b), 'size': len(b)} for p, b in sorted(files.items())]


def snapshot(blob):
    if len(blob) > MAX_ZIP:
        fail('ZIP transport limit exceeded')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES * 3 + 2:
            fail('Too many ZIP entries')
        seen = set()
        seen_fold = {}
        payload = {}
        total = 0
        for item in members:
            name = item.filename
            if name != item.orig_filename or '\x00' in item.orig_filename:
                fail('Unsafe ZIP entry name')
            if name in seen:
                fail('Duplicate ZIP entry')
            seen.add(name)
            mode = item.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (item.is_dir() and kind == stat.S_IFREG) or (not item.is_dir() and kind == stat.S_IFDIR):
                fail('ZIP entry is not a regular file/directory')
            clean = name[:-1] if item.is_dir() else name
            if clean not in ('files', 'manifest.json'):
                if not clean.startswith('files/'):
                    fail('Unexpected ZIP payload')
                safe_path(clean[6:], directory=item.is_dir())
            for n in range(1, len(clean.split('/')) + 1):
                prefix = '/'.join(clean.split('/')[:n])
                folded = prefix.casefold()
                if folded in seen_fold and seen_fold[folded] != prefix:
                    fail('Case-folding ZIP collision')
                seen_fold[folded] = prefix
            if item.is_dir():
                if item.file_size:
                    fail('Directory entry has payload')
                if clean == 'manifest.json':
                    fail('Manifest must be a file')
                continue
            if name == 'files':
                fail('Files root must be a directory')
            limit = 1024 * 1024 if name == 'manifest.json' else MAX_FILE
            if item.file_size > limit:
                fail('ZIP member too large')
            total += item.file_size
            if total > MAX_TOTAL + 1024 * 1024:
                fail('Extracted ZIP limit exceeded')
            payload[name] = archive.read(item)
    if 'manifest.json' not in payload:
        fail('Missing manifest')
    manifest = parse_json(payload.pop('manifest.json'))
    keys(manifest, ('schema_version', 'kind', 'site_id', 'workspace_id', 'base_commit', 'files'))
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1 or manifest['kind'] != 'snapshot':
        fail('Unsupported snapshot')
    if not isinstance(manifest['site_id'], str) or not manifest['site_id'] or len(manifest['site_id']) > 200:
        fail('Invalid site ID')
    identifier(manifest['workspace_id'])
    commit(manifest['base_commit'])
    if not isinstance(manifest['files'], list):
        fail('Invalid files list')
    files = {p[6:]: b for p, b in payload.items()}
    validate_files(files)
    # Exact equality also rejects extra entry fields, unsorted lists, and bad hashes.
    if canonical(manifest['files']) != canonical(entries(files)):
        fail('Snapshot hashes or inventory mismatch')
    return manifest, files


def read_tree(root):
    if root.is_symlink() or not root.is_dir():
        fail('Missing or unsafe file tree')
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                fail('Local symlink/special file rejected')
            safe_path(rel, directory=stat.S_ISDIR(mode))
            if stat.S_ISREG(mode):
                if path.stat().st_size > MAX_FILE:
                    fail('Local file limit exceeded')
                files[rel] = path.read_bytes()
    validate_files(files)
    return files


def write_tree(root, files):
    root.mkdir(parents=True)
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def write_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    if temp.exists() or temp.is_symlink():
        fail('Unexpected metadata temporary file')
    temp.write_bytes(canonical(value))
    temp.replace(path)


def load(workspace):
    root = Path(workspace)
    hp = root / '.hp'
    if root.is_symlink() or hp.is_symlink() or not hp.is_dir():
        fail('Unsafe workspace metadata')
    # Metadata has no links or special files; never follow a substituted base.
    for directory, dirs, names in os.walk(hp, followlinks=False):
        for name in dirs + names:
            mode = (Path(directory) / name).lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                fail('Unsafe workspace metadata entry')
    state = parse_json((hp / 'state.json').read_bytes())
    keys(state, ('snapshot', 'accepted', 'base_directory'))
    if not re.fullmatch(r'base(?:-[0-9a-f-]{36})?', state['base_directory']):
        fail('Invalid baseline directory')
    manifest = state['snapshot']
    base = read_tree(hp / state['base_directory'])
    if canonical(manifest['files']) != canonical(entries(base)):
        fail('Baseline content was tampered with')
    # Validate stored manifest with the same wire validator, including paths.
    snapshot(zip_bytes(manifest, base))
    return root, hp, state, base


def zip_bytes(manifest, files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in [('manifest.json', canonical(manifest))] + [('files/' + p, b) for p, b in sorted(files.items())]:
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    blob = stream.getvalue()
    if len(blob) > MAX_ZIP:
        fail('ZIP transport limit exceeded')
    return blob


def changes(base, current):
    result = []
    for path in sorted(base.keys() | current.keys()):
        before, after = base.get(path), current.get(path)
        if before == after:
            continue
        result.append({'path': path, 'action': 'add' if before is None else 'delete' if after is None else 'modify', 'base_sha256': digest(before) if before is not None else None, 'sha256': digest(after) if after is not None else None, 'size': len(after) if after is not None else 0})
    return result


def workspace_snapshot(archive, receipt_path):
    # Bind bytes to the authenticated control result before creating even a lock
    # file or output directory. A self-consistent ZIP alone has no provenance.
    if Path(archive).stat().st_size > MAX_ZIP:
        fail('ZIP transport limit exceeded')
    if Path(receipt_path).stat().st_size > 16384:
        fail('Workspace receipt too large')
    receipt = parse_json(Path(receipt_path).read_bytes())
    keys(receipt, ('schema_version', 'kind', 'site_id', 'workspace_id', 'base_commit',
        'snapshot_file_id', 'snapshot_sha256', 'download_url', 'upload_folder_id'))
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != 1 or receipt['kind'] != 'workspace':
        fail('Unsupported workspace receipt')
    identifier(receipt['workspace_id'])
    commit(receipt['base_commit'])
    if not isinstance(receipt['snapshot_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', receipt['snapshot_sha256']):
        fail('Invalid snapshot receipt hash')
    for field in ('snapshot_file_id', 'upload_folder_id'):
        if not isinstance(receipt[field], str) or not re.fullmatch('[A-Za-z0-9_-]{10,256}', receipt[field]):
            fail('Invalid workspace receipt file/folder ID')
    if not isinstance(receipt['download_url'], str) or not receipt['download_url'].startswith('https://'):
        fail('Invalid workspace receipt download URL')
    blob = Path(archive).read_bytes()
    if digest(blob) != receipt['snapshot_sha256']:
        fail('Snapshot ZIP hash does not match authenticated workspace receipt')
    manifest, files = snapshot(blob)
    if any(manifest[field] != receipt[field] for field in ('site_id', 'workspace_id', 'base_commit')):
        fail('Snapshot identity/base does not match authenticated workspace receipt')
    return manifest, files, receipt


def _init(workspace, manifest, files, receipt):
    root = Path(workspace)
    if root.exists() or root.is_symlink():
        fail('Workspace must not exist')
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.hp-init-', dir=root.parent))
    try:
        write_tree(staging / 'site', files)
        write_tree(staging / '.hp' / 'base', files)
        write_json(staging / '.hp' / 'state.json', {'snapshot': manifest, 'accepted': [], 'base_directory': 'base'})
        write_json(staging / '.hp' / 'open-receipt.json', receipt)
        staging.rename(root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'workspace': str(root), 'files': len(files)}


def _status(workspace, diff=False):
    root, hp, state, base = load(workspace)
    current = read_tree(root / 'site')
    result = {'changes': changes(base, current), 'pending': (hp / 'pending').exists()}
    if diff:
        hunks = []
        for change in result['changes']:
            path = change['path']
            before, after = base.get(path, b''), current.get(path, b'')
            try:
                if b'\0' in before + after:
                    raise UnicodeError()
                hunks.extend(difflib.unified_diff(before.decode('utf-8').splitlines(keepends=True), after.decode('utf-8').splitlines(keepends=True), fromfile='base/' + path, tofile='site/' + path))
            except UnicodeError:
                hunks.append('Binary file changed: ' + path + '\n')
        result['diff'] = ''.join(hunks)
    return result


def pending(hp, state, base):
    directory = hp / 'pending'
    metadata = parse_json((directory / 'metadata.json').read_bytes())
    candidate = read_tree(directory / 'candidate')
    manifest = metadata['manifest']
    expected = dict(schema_version=1, kind='changes', site_id=state['snapshot']['site_id'], workspace_id=state['snapshot']['workspace_id'], base_commit=state['snapshot']['base_commit'], operation_id=manifest['operation_id'], changes=changes(base, candidate))
    identifier(expected['operation_id'])
    if 'change_summary' in manifest:
        summary = manifest['change_summary']
        if not isinstance(summary, str) or len(summary) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in summary):
            fail('Invalid pending summary')
        expected['change_summary'] = summary
    if canonical(expected) != canonical(manifest) or not expected['changes']:
        fail('Pending manifest was tampered with')
    blob = (directory / 'bundle.zip').read_bytes()
    expected_blob = zip_bytes(manifest, {c['path']: candidate[c['path']] for c in manifest['changes'] if c['action'] != 'delete'})
    if blob != expected_blob or digest(blob) != metadata['bundle_sha256']:
        fail('Pending bundle was tampered with')
    return metadata, candidate, blob


def _pack(workspace, output, summary=None):
    root, hp, state, base = load(workspace)
    if Path(output).is_symlink():
        fail('Bundle output must not be a symlink')
    target = Path(output).resolve()
    if target == root.resolve() or root.resolve() in target.parents:
        fail('Bundle output must be outside the workspace')
    if summary is not None and (len(summary) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in summary)):
        fail('Summary must be a single line up to 200 characters')
    if (hp / 'pending').exists():
        metadata, candidate, blob = pending(hp, state, base)
        if summary is not None and summary != metadata['manifest'].get('change_summary'):
            fail('Pending summary is immutable; retry without a new summary')
    else:
        candidate = read_tree(root / 'site')
        delta = changes(base, candidate)
        if not delta:
            return {'noop': True}
        manifest = {k: state['snapshot'][k] for k in ('schema_version', 'site_id', 'workspace_id', 'base_commit')}
        manifest.update(kind='changes', operation_id=str(uuid.uuid4()), changes=delta)
        if summary is not None:
            manifest['change_summary'] = summary
        blob = zip_bytes(manifest, {c['path']: candidate[c['path']] for c in delta if c['action'] != 'delete'})
        metadata = {'manifest': manifest, 'bundle_sha256': digest(blob)}
        temp = Path(tempfile.mkdtemp(prefix='pending-', dir=hp))
        try:
            write_tree(temp / 'candidate', candidate)
            (temp / 'bundle.zip').write_bytes(blob)
            write_json(temp / 'metadata.json', metadata)
            temp.rename(hp / 'pending')
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    try:
        with target.open('xb') as destination:
            destination.write(blob)
    except FileExistsError:
        if not target.is_file() or target.stat().st_size != len(blob) or target.read_bytes() != blob:
            fail('Bundle output already exists with different bytes; choose a new output path')
    return {'noop': False, 'operation_id': metadata['manifest']['operation_id'], 'bundle_sha256': digest(blob), 'output': str(target)}


def _accept(workspace, receipt_path):
    root, hp, state, base = load(workspace)
    receipt = parse_json(Path(receipt_path).read_bytes())
    keys(receipt, ('schema_version', 'kind', 'site_id', 'workspace_id', 'operation_id', 'base_commit', 'saved_commit', 'bundle_sha256'))
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != 1 or receipt['kind'] != 'saved':
        fail('Unsupported receipt')
    commit(receipt['saved_commit'])
    if receipt in state['accepted']:
        if (hp / 'pending').exists():
            stored = parse_json((hp / 'pending' / 'metadata.json').read_bytes())
            if stored['manifest']['operation_id'] == receipt['operation_id']:
                shutil.rmtree(hp / 'pending')
        return {'accepted': True, 'already_accepted': True, 'base_commit': state['snapshot']['base_commit']}
    if not (hp / 'pending').exists():
        fail('No matching pending operation')
    metadata, candidate, blob = pending(hp, state, base)
    manifest = metadata['manifest']
    for key in ('site_id', 'workspace_id', 'operation_id', 'base_commit'):
        if receipt[key] != manifest[key]:
            fail('Receipt does not match pending operation')
    if receipt['bundle_sha256'] != digest(blob):
        fail('Receipt bundle hash mismatch')
    # Keep editable site/ untouched: the acknowledged candidate is the new base.
    next_manifest = dict(state['snapshot'], base_commit=receipt['saved_commit'], files=entries(candidate))
    next_base = 'base-' + str(uuid.uuid4())
    write_tree(hp / next_base, candidate)
    previous_base = state['base_directory']
    state['base_directory'] = next_base
    state['snapshot'] = next_manifest
    state['accepted'].append(receipt)
    write_json(hp / 'state.json', state)
    shutil.rmtree(hp / previous_base)
    shutil.rmtree(hp / 'pending')
    return {'accepted': True, 'already_accepted': False, 'base_commit': receipt['saved_commit']}


@contextmanager
def workspace_lock(workspace, create_parent=False):
    """Persistent sibling inode prevents unlink/recreate races; flock releases on exit."""
    root = Path(os.path.abspath(workspace))
    if create_parent:
        root.parent.mkdir(parents=True, exist_ok=True)
    lock_path = root.parent.resolve() / ('.' + root.name + '.hp.lock')
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            fail('Unsafe workspace lock')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('Workspace is busy: another helper invocation owns its lock')
        yield
    finally:
        os.close(descriptor)


def init(archive, workspace, receipt):
    manifest, files, open_receipt = workspace_snapshot(archive, receipt)
    if Path(workspace).exists() or Path(workspace).is_symlink():
        fail('Workspace must not exist')
    with workspace_lock(workspace, create_parent=True):
        return _init(workspace, manifest, files, open_receipt)


def status(workspace, diff=False):
    with workspace_lock(workspace):
        return _status(workspace, diff)


def pack(workspace, output, summary=None):
    with workspace_lock(workspace):
        return _pack(workspace, output, summary)


def accept(workspace, receipt):
    with workspace_lock(workspace):
        return _accept(workspace, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('init'); p.add_argument('snapshot'); p.add_argument('workspace'); p.add_argument('--receipt', required=True)
    p = commands.add_parser('status'); p.add_argument('workspace'); p.add_argument('--diff', action='store_true')
    p = commands.add_parser('pack'); p.add_argument('workspace'); p.add_argument('output'); p.add_argument('--summary')
    p = commands.add_parser('accept'); p.add_argument('workspace'); p.add_argument('receipt')
    args = parser.parse_args()
    try:
        if args.command == 'init': result = init(args.snapshot, args.workspace, args.receipt)
        elif args.command == 'status': result = status(args.workspace, args.diff)
        elif args.command == 'pack': result = pack(args.workspace, args.output, args.summary)
        else: result = accept(args.workspace, args.receipt)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        parser.exit(2, 'Error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
