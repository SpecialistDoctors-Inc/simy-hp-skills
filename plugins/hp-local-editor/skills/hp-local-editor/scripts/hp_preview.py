#!/usr/bin/env python3
"""Loopback-only site preview/control shell. No saves, models or credential parsing."""
import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import stat
import threading
from urllib.parse import unquote, urlsplit

from hp_codex_client import LazyCodexClient
from hp_preview_state import Session, Blocked
import hp_local_editor as editor

ASSETS = Path(__file__).resolve().parents[3] / 'assets/preview'


def site_bytes(root, relative):
    relative = relative or 'index.html'
    if relative.endswith('/'):
        relative += 'index.html'
    editor.safe_path(relative)
    descriptors = []
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        parts = relative.split('/')
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(current)
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        descriptors.append(file)
        info = os.fstat(file)
        if not stat.S_ISREG(info.st_mode) or info.st_size > editor.MAX_FILE:
            raise ValueError('unsafe_site_file')
        with os.fdopen(os.dup(file), 'rb') as stream:
            return stream.read(editor.MAX_FILE + 1)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class PreviewHost:
    def __init__(self, session):
        self.session = session
        self.csrf, self.site_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        host = self
        class SiteHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get_all('Host') != [host.site_netloc]:
                    return host.reply(self, 403, {'error': 'invalid_host'})
                parsed = urlsplit(self.path)
                prefix = '/site/' + host.site_token + '/'
                if not parsed.path.startswith(prefix) or self.headers.get('Origin') not in (None, 'null'):
                    return host.reply(self, 403, {'error': 'invalid_site_request'})
                try:
                    relative = unquote(parsed.path[len(prefix):], errors='strict')
                    data = site_bytes(session.root / 'site', relative)
                    mime = mimetypes.guess_type(relative or 'index.html')[0] or 'application/octet-stream'
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Access-Control-Allow-Origin', 'null')
                    self.send_header('Content-Security-Policy', "default-src " + host.site_origin + " data: blob:; script-src 'unsafe-inline' " + host.site_origin + "; style-src 'unsafe-inline' " + host.site_origin + "; connect-src " + host.site_origin + "; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors " + host.origin)
                    self.end_headers(); self.wfile.write(data)
                except (OSError, ValueError):
                    host.reply(self, 404, {'error': 'site_file_unavailable'})
            def log_message(self, *args): pass
        class ControlHandler(BaseHTTPRequestHandler):
            def valid(self, mutation=False, document=False):
                safe_document = (document and self.headers.get('Sec-Fetch-Mode') == 'navigate' and
                    self.headers.get('Sec-Fetch-Dest') == 'document' and self.headers.get('Origin') in (None, host.origin))
                return (self.headers.get_all('Host') == [host.netloc] and
                    (safe_document or self.headers.get('Sec-Fetch-Site') in (None, 'none', 'same-origin')) and
                    self.headers.get('Origin') in ((host.origin,) if mutation else (None, host.origin)) and
                    (not mutation or (self.headers.get_all('X-HP-CSRF') == [host.csrf] and
                        self.headers.get('Content-Type') == 'application/json' and not self.headers.get('Transfer-Encoding'))))
            def do_GET(self):
                parsed = urlsplit(self.path)
                if not self.valid(document=parsed.path == '/' and not parsed.query): return host.reply(self, 403, {'error': 'invalid_origin'})
                if parsed.query: return host.reply(self, 404, {'error': 'not_found'})
                try:
                    if parsed.path == '/':
                        html = (ASSETS / 'index.html').read_text().replace('CSRF_VALUE', host.csrf).replace('SITE_URL', host.site_url)
                        return host.reply(self, 200, html, 'text/html; charset=utf-8')
                    if parsed.path == '/control.js':
                        return host.reply(self, 200, (ASSETS / 'control.js').read_text(), 'text/javascript; charset=utf-8')
                    if parsed.path == '/api/state': return host.reply(self, 200, session.public())
                    if parsed.path == '/api/revision': return host.reply(self, 200, {'revision': session.revision()})
                    host.reply(self, 404, {'error': 'not_found'})
                except (OSError, ValueError):
                    host.reply(self, 409, {'error': 'local_workspace_unavailable'})
            def do_POST(self):
                if not self.valid(True): return host.reply(self, 403, {'error': 'invalid_origin_or_csrf'})
                if self.path != '/api/action': return host.reply(self, 404, {'error': 'not_found'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 1024: return host.reply(self, 413, {'error': 'request_too_large'})
                    body = editor.parse_json(self.rfile.read(length))
                    if not isinstance(body, dict) or set(body) != {'action', 'revision'} or not all(isinstance(value, str) for value in body.values()):
                        return host.reply(self, 400, {'error': 'invalid_request'})
                    session.action(body['action'], body['revision'])
                    host.reply(self, 200, session.public())
                except Blocked as error:
                    host.reply(self, 409, {'error': str(error)})
                except Exception:
                    # Never log/return an RPC exception, private meta, URLs or body.
                    host.reply(self, 502, {'error': 'control_response_unavailable'})
            def log_message(self, *args): pass
        self.site_server = ThreadingHTTPServer(('127.0.0.1', 0), SiteHandler)
        self.control_server = ThreadingHTTPServer(('127.0.0.1', 0), ControlHandler)
        self.site_netloc = '127.0.0.1:' + str(self.site_server.server_port)
        self.netloc = '127.0.0.1:' + str(self.control_server.server_port)
        self.site_origin, self.origin = 'http://' + self.site_netloc, 'http://' + self.netloc
        self.site_url = self.site_origin + '/site/' + self.site_token + '/'
        self.threads = []

    def reply(self, handler, status, value, mime='application/json; charset=utf-8'):
        data = (json.dumps(value, ensure_ascii=False) if mime.startswith('application/json') else value).encode()
        handler.send_response(status)
        for key, item in {'Content-Type': mime, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src " + self.site_origin + "; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():
            handler.send_header(key, item)
        handler.end_headers(); handler.wfile.write(data)

    def start(self):
        for server in (self.site_server, self.control_server):
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start(); self.threads.append(thread)
        return self.origin

    def close(self):
        for server in (self.site_server, self.control_server):
            server.shutdown(); server.server_close()
        for thread in self.threads: thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace'); parser.add_argument('--codex', default='codex')
    args = parser.parse_args()
    session = Session(args.workspace, None)
    descriptor = os.open(session.directory / 'launcher.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.exit(2, 'Local preview already owns this workspace lock; leave it intact.\n')
    client = None; host = None
    try:
        client = LazyCodexClient(session.directory, args.codex); session.client = client
        host = PreviewHost(session)
        print('Local HP preview: ' + host.start(), flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        if host: host.close()
        if client: client.close()
        os.close(descriptor)


if __name__ == '__main__': main()
