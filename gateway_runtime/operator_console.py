"""Loopback-only operator view. Uses saved evidence; credentials never enter the browser."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import secrets
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parent
STATE = ROOT.parent / 'tmp' / 'system-design'
NONCE = secrets.token_urlsafe(32)
LOCK = threading.Lock()
PORT = 8795


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, payload, mime='application/json'):
        raw = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'nonce-" + NONCE + "'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def boundary(self):
        return self.headers.get('Host') == f'127.0.0.1:{PORT}' and self.headers.get('Origin', f'http://127.0.0.1:{PORT}') == f'http://127.0.0.1:{PORT}'

    def do_GET(self):
        if not self.boundary():
            return self.send(403, {'error': 'Loopback origin required'})
        if self.path == '/':
            return self.send(200, (ROOT / 'operator.html').read_text().replace('__NONCE__', NONCE), 'text/html; charset=utf-8')
        if self.path == '/api/evidence':
            report = {}
            for name, path in [('cloud', 'cloud-test-results.json'), ('sdk', 'mcp-sdk-results.json'), ('benchmark', 'token-benchmark.json'), ('deployment', 'deployment.json')]:
                if (STATE / path).exists():
                    report[name] = json.loads((STATE / path).read_text())
            for name, path in [('inventory', 'latest.json'), ('inventoryAttempt', 'latest-attempt.json'), ('inventoryChecks', 'cloud-tests.json'), ('requests', 'requests.json')]:
                source = STATE.parent / 'inventory' / path
                if source.exists():
                    report[name] = json.loads(source.read_text())
            for name, path in [('postgres', 'latest.json'), ('postgresAttempt', 'attempt.json'), ('postgresChecks', 'checks.json'), ('postgresRequests', 'mcp-requests.json')]:
                source = STATE.parent / 'postgres' / path
                if source.exists():
                    report[name] = json.loads(source.read_text())
            return self.send(200, report)
        self.send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.boundary() or not secrets.compare_digest(self.headers.get('X-Operator-Nonce', ''), NONCE):
            return self.send(403, {'error': 'Operator request required'})
        if self.path not in {'/api/verify', '/api/inventory', '/api/denied-demo', '/api/postgres'}:
            return self.send(404, {'error': 'Not found'})
        if not LOCK.acquire(blocking=False):
            return self.send(409, {'error': 'Verification already running'})
        try:
            script = {'/api/inventory': 'inventory_client.py', '/api/verify': 'verify_cloud.py', '/api/denied-demo': 'denied_inventory_demo.py', '/api/postgres': 'postgres_mcp_client.py'}[self.path]
            p = subprocess.run([sys.executable, str(ROOT / 'deploy' / script)], capture_output=True, text=True, timeout=240)
            self.send(200 if p.returncode == 0 else 502, {'passed': p.returncode == 0, 'detail': 'Read the refreshed evidence for individual checks'})
        except subprocess.TimeoutExpired:
            self.send(504, {'error': 'Verification timeout'})
        finally:
            LOCK.release()


if __name__ == '__main__':
    print(f'Kerbline operator console http://127.0.0.1:{PORT}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
