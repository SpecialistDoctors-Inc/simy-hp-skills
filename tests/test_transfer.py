import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
    'plugins/hp-local-editor/skills/hp-local-editor/scripts'))
import hp_transfer
import hp_local_editor as editor
import test_preview as fixtures


class Client:
    def __init__(self): self.calls = []
    def call(self, name, body):
        self.calls.append((name, body))
        return {'structuredContent': {'operation_id': body['operation_id'],
            'bundle_file_id': 'hp-upload-' + body['operation_id'],
            'bundle_sha256': body['bundle_sha256'], 'size': int(body['size'])},
            '_meta': {'hp_transfer': {'method': 'PUT', 'url': 'private', 'headers': {}}}}


class TransferTests(unittest.TestCase):
    def test_lost_upload_uses_same_pending_package_and_preserves_later_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = fixtures.workspace(Path(directory))
            (root / 'site/index.html').write_text('First edit')
            packed = editor.pack(root, Path(directory) / 'changes.zip')
            first = (root / '.hp/pending/bundle.zip').read_bytes()
            client = Client()
            with patch('hp_transfer.transport', side_effect=hp_transfer.TransferError('lost')):
                with self.assertRaises(hp_transfer.TransferError): hp_transfer.upload(client, root)
            (root / 'site/index.html').write_text('Later edit')
            with patch('hp_transfer.transport', return_value=b'') as transport:
                result = hp_transfer.upload(client, root)
                self.assertEqual(transport.call_args.args[1], first)
            self.assertEqual(client.calls[0], client.calls[1])
            self.assertEqual(result['operation_id'], packed['operation_id'])
            self.assertFalse(result['saved'])
            self.assertEqual((root / 'site/index.html').read_text(), 'Later edit')
            self.assertNotIn('private', json.dumps(result))

    def test_transfers_reject_other_origins_redirects_and_extra_headers(self):
        bad_urls = ['http://bucket.s3.us-east-1.amazonaws.com/artifacts/a?x=1',
            'https://evil.example/artifacts/a?x=1',
            'https://bucket.s3.us-east-1.amazonaws.com.evil.example/artifacts/a?x=1',
            'https://user@bucket.s3.us-east-1.amazonaws.com/artifacts/a?x=1',
            'https://bucket.s3.us-east-1.amazonaws.com/other/a?x=1']
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(hp_transfer.TransferError):
                hp_transfer.transport({'method': 'GET', 'url': url, 'headers': {}})
        with self.assertRaises(hp_transfer.TransferError):
            hp_transfer.transport({'method': 'GET',
                'url': 'https://bucket.s3.us-east-1.amazonaws.com/artifacts/a?x=1',
                'headers': {'Authorization': 'not-allowed'}})
        self.assertIsNone(hp_transfer.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))

    def test_hash_mismatch_never_writes_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = {'kind': 'workspace', 'site_id': 'sample-site', 'workspace_id': 'fixture',
                       'snapshot_sha256': editor.digest(b'expected')}
            path = root / 'receipt.json'; path.write_text(json.dumps(receipt))
            client = Client()
            client.call = lambda *args: {'structuredContent': receipt, '_meta': {'hp_transfer': {}}}
            with patch('hp_transfer.transport', return_value=b'wrong'), self.assertRaises(hp_transfer.TransferError):
                hp_transfer.download(client, path, root / 'snapshot.zip')
            self.assertFalse((root / 'snapshot.zip').exists())

    def test_interrupted_download_has_no_partial_target_and_retry_keeps_same_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = {'kind': 'workspace', 'site_id': 'sample-site', 'workspace_id': 'fixture',
                       'snapshot_sha256': editor.digest(b'expected')}
            path = root / 'receipt.json'; path.write_text(json.dumps(receipt))
            client = Client()
            client.call = lambda *args: {'structuredContent': receipt, '_meta': {'hp_transfer': {}}}
            with patch('hp_transfer.transport', return_value=b'expected'):
                with patch('hp_transfer.os.link', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                    hp_transfer.download(client, path, root / 'snapshot.zip')
                self.assertFalse((root / 'snapshot.zip').exists())
                hp_transfer.download(client, path, root / 'snapshot.zip')
                hp_transfer.download(client, path, root / 'snapshot.zip')
            self.assertEqual((root / 'snapshot.zip').read_bytes(), b'expected')


if __name__ == '__main__': unittest.main()
