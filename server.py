"""RepoLens server. Standard library only. Binds to localhost; snapshots live in memory only."""
import json, os, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from repolens import engine
from repolens.scanner import ScanError
from repolens.github import clone_github_repo, cleanup_clone, GitHubCloneError, GITHUB_REGEX

ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLE_PATH = os.path.join(ROOT, 'repolens', 'sample_repo')
MAX_BODY = 64 * 1024
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"


class Handler(BaseHTTPRequestHandler):
    server_version = 'RepoLens/2.0'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json', extra=None):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith(('text', 'application/json')) else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code, msg):
        self._send(code, {'error': msg})

    def _origin_ok(self):
        o, h = self.headers.get('Origin'), self.headers.get('Host', '')
        return not o or urllib.parse.urlparse(o).netloc == h

    def _snap(self):
        s = self.server.snapshot
        if s is None:
            self._err(409, 'No repository analyzed yet. POST /api/analyze first.')
        return s

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path in ('/', '/index.html'):
                with open(os.path.join(ROOT, 'frontend', 'index.html'), 'rb') as f:
                    return self._send(200, f.read(), 'text/html')
            if u.path == '/api/health':
                return self._send(200, {'status': 'ok', 'version': '2.0'})
            if u.path == '/api/sample':
                return self._send(200, {
                    'path': SAMPLE_PATH,
                    'name': 'sample_repo',
                    'description': 'Bundled Multi-Language Fixture (Python + TypeScript)'
                })
            if not u.path.startswith('/api/'):
                return self._err(404, 'Not found.')
            s = self._snap()
            if s is None:
                return
            if u.path == '/api/snapshot':
                pub = engine.public(s)
                if getattr(self.server, 'snapshot_meta', None):
                    pub.update(self.server.snapshot_meta)
                return self._send(200, pub)
            if u.path == '/api/search':
                return self._send(200, {'query': q.get('q', ''), 'results': engine.search(s, q.get('q', ''))})
            if u.path == '/api/impact':
                return self._send(200, engine.impact(s, q.get('path', '')))
            if u.path == '/api/source':
                try:
                    line = int(q.get('line', '1'))
                except ValueError:
                    return self._err(400, 'line must be an integer.')
                return self._send(200, engine.source_excerpt(s, q.get('path', ''), line))
            if u.path == '/api/report':
                return self._send(200, engine.report_markdown(s), 'text/markdown',
                                  {'Content-Disposition': 'attachment; filename="repolens-report.md"'})
            return self._err(404, 'Unknown endpoint.')
        except KeyError:
            return self._err(404, 'File is not part of the analyzed repository.')
        except Exception:
            return self._err(500, 'Internal error.')

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != '/api/analyze':
            return self._err(404, 'Not found.')
        if not self._origin_ok():
            return self._err(403, 'Cross-origin requests are not allowed.')
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self._err(400, 'Invalid Content-Length.')
        if n > MAX_BODY:
            return self._err(413, 'Request body too large.')
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return self._err(400, 'Body must be valid JSON.')
        if not isinstance(body, dict):
            return self._err(400, 'Body must be a JSON object.')

        raw_path = body.get('path')
        raw_url = body.get('url')
        source_mode = body.get('mode') or body.get('source')

        # Determine target: check if URL or path matches GitHub repository format
        target = raw_url if raw_url else raw_path
        if not isinstance(target, str) or not target.strip():
            return self._err(400, "Field 'path' or 'url' (string) is required.")

        target = target.strip()
        is_github = False

        if source_mode == 'github' or raw_url or GITHUB_REGEX.match(target):
            is_github = True

        cloned_dir = None
        repo_name = None
        clone_url = None

        if is_github:
            try:
                cloned_dir, repo_name, clone_url = clone_github_repo(target)
                target_path = cloned_dir
            except GitHubCloneError as e:
                return self._err(400, str(e))
            except Exception as e:
                return self._err(500, f'Clone failed: {str(e)}')
        else:
            target_path = target
            allowed = os.environ.get('REPOLENS_ALLOWED_ROOT')
            if allowed:
                real = os.path.realpath(os.path.expanduser(target_path))
                a = os.path.realpath(allowed)
                if real != a and not real.startswith(a + os.sep):
                    return self._err(403, 'Path is outside the permitted root.')

        try:
            with self.server.lock:
                snap = engine.analyze_repo(target_path)
                
                # Cleanup previous clone if one was active
                if self.server.active_clone and self.server.active_clone != cloned_dir:
                    cleanup_clone(self.server.active_clone)
                self.server.active_clone = cloned_dir

                meta = {
                    'is_github': is_github,
                    'repo_name': repo_name or os.path.basename(os.path.normpath(target_path)),
                    'source_path': target if is_github else os.path.realpath(target_path)
                }
                if clone_url:
                    meta['clone_url'] = clone_url
                self.server.snapshot = snap
                self.server.snapshot_meta = meta

            pub = engine.public(snap)
            pub.update(meta)
            return self._send(200, pub)
        except FileNotFoundError:
            if cloned_dir:
                cleanup_clone(cloned_dir)
            return self._err(404, 'Path does not exist.')
        except PermissionError:
            if cloned_dir:
                cleanup_clone(cloned_dir)
            return self._err(403, 'Directory is not readable.')
        except ScanError as e:
            if cloned_dir:
                cleanup_clone(cloned_dir)
            return self._err(400, str(e))
        except Exception as e:
            if cloned_dir:
                cleanup_clone(cloned_dir)
            return self._err(500, f'Analysis failed unexpectedly: {str(e)}')


def make_server(host='127.0.0.1', port=8765):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.snapshot = None
    srv.snapshot_meta = None
    srv.active_clone = None
    srv.lock = threading.Lock()
    return srv


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = make_server(port=port)
    print(f'RepoLens V2 running at http://127.0.0.1:{port}  (Ctrl+C to stop)')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        if srv.active_clone:
            cleanup_clone(srv.active_clone)
