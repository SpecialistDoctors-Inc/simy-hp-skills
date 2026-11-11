"""File transfer through HP only; private URLs never enter public receipts."""
import argparse
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import hp_local_editor as editor
from hp_codex_client import CodexClient


class TransferError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(private, data=None):
    """Only use the signed S3 HTTPS target from the authenticated HP result."""
    if not isinstance(private, dict) or set(private) != {'method', 'url', 'headers'}:
        raise TransferError('invalid_private_transfer')
    parsed = urllib.parse.urlsplit(private['url'])
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.fragment or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.s3\.[a-z0-9-]+\.amazonaws\.com', parsed.hostname or '')
            or not parsed.path.startswith('/artifacts/') or not parsed.query):
        raise TransferError('unexpected_transfer_origin')
    method = 'GET' if data is None else 'PUT'
    if private['method'] != method:
        raise TransferError('invalid_transfer_method')
    headers = private['headers']
    allowed = {'Content-Type', 'Content-Length', 'x-amz-checksum-sha256', 'If-None-Match',
               'x-amz-server-side-encryption'}
    if not isinstance(headers, dict) or set(headers) - allowed or any(
            not isinstance(v, str) or '\r' in v or '\n' in v for v in headers.values()):
        raise TransferError('invalid_transfer_headers')
    if data is not None and (len(data) > editor.MAX_ZIP or headers.get('Content-Length') != str(len(data))):
        raise TransferError('invalid_transfer_size')
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(private['url'], headers=headers, data=data, method=method)
    try:
        with opener.open(request, timeout=30) as response:
            result = response.read(editor.MAX_ZIP + 1)
            if len(result) > editor.MAX_ZIP:
                raise TransferError('transfer_size_exceeded')
            return result
    except urllib.error.HTTPError as error:
        # A lost successful PUT is retried with If-None-Match. The worker still
        # verifies bytes, size, ownership and manifest before any draft effect.
        if method == 'PUT' and error.code == 412:
            return b''
        raise TransferError('transfer_response_unavailable') from None
    except (OSError, ValueError, urllib.error.URLError):
        raise TransferError('transfer_response_unavailable') from None


def unpack(result):
    if not isinstance(result, dict) or result.get('isError'):
        raise TransferError('hp_transfer_rejected')
    public = result.get('structuredContent')
    private = result.get('_meta', {}).get('hp_transfer')
    if not isinstance(public, dict) or not isinstance(private, dict):
        raise TransferError('hp_transfer_metadata_missing')
    return public, private


def download(client, receipt_path, output):
    receipt = editor.parse_json(Path(receipt_path).read_bytes())
    if receipt.get('kind') != 'workspace':
        raise TransferError('workspace_receipt_required')
    public, private = unpack(client.call('hp_download_workspace', {
        'site_id': receipt['site_id'], 'workspace_id': receipt['workspace_id']}))
    if any(public.get(key) != receipt[key] for key in ('site_id', 'workspace_id', 'snapshot_sha256')):
        raise TransferError('download_binding_mismatch')
    data = transport(private)
    if editor.digest(data) != receipt['snapshot_sha256']:
        raise TransferError('download_digest_mismatch')
    target = Path(output)
    if target.is_symlink():
        raise TransferError('unsafe_download_output')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.hp-download-', dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Link is create-only: never overwrite a customer file or expose a
        # partially written ZIP at the final path after an interrupted download.
        os.link(temporary, target)
    except FileExistsError:
        if not target.is_file() or target.read_bytes() != data:
            raise TransferError('download_output_exists') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'downloaded': True, 'snapshot_sha256': editor.digest(data), 'output': str(target.resolve())}


def upload(client, workspace):
    with editor.workspace_lock(workspace):
        root, hp, state, base = editor.load(workspace)
        metadata, candidate, data = editor.pending(hp, state, base)
    manifest = metadata['manifest']
    body = {key: manifest[key] for key in ('operation_id', 'site_id', 'workspace_id', 'base_commit')}
    body.update(bundle_sha256=metadata['bundle_sha256'], size=str(len(data)))
    public, private = unpack(client.call('hp_prepare_upload', body))
    if (public.get('operation_id') != body['operation_id'] or
            public.get('bundle_sha256') != body['bundle_sha256'] or
            public.get('size') != len(data) or
            public.get('bundle_file_id') != 'hp-upload-' + body['operation_id']):
        raise TransferError('upload_binding_mismatch')
    transport(private, data)
    return {'operation_id': body['operation_id'], 'bundle_file_id': public['bundle_file_id'],
            'bundle_sha256': body['bundle_sha256'], 'uploaded': True, 'saved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('download'); p.add_argument('receipt'); p.add_argument('output'); p.add_argument('--codex')
    p = sub.add_parser('upload'); p.add_argument('workspace'); p.add_argument('--codex')
    args = parser.parse_args()
    directory = (Path(args.workspace) / '.hp' / 'control' if args.command == 'upload'
                 else Path(args.receipt).resolve().parent / 'transfer-control')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = CodexClient(directory, args.codex)
    try:
        result = (upload(client, args.workspace) if args.command == 'upload'
                  else download(client, args.receipt, args.output))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        client.close()


if __name__ == '__main__':
    main()
