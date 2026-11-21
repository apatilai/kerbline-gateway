#!/usr/bin/env python3
"""Customer-owned Kerbline inventory and readiness console. No ERP writes."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import socket
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from telemetry import ROLES, SECURITY, FINANCE, validate_trace, summary, project_trace
from telemetry_preview import preview_traces

BASE_DIR = Path(__file__).resolve().parent
MAX_BODY = 1_000_000
LOCK = threading.RLock()
STATIC = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          '/app.js': ('app.js', 'text/javascript'), '/traffic.js': ('traffic.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css')}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def required_text(value, field, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{field}: enter text between 1 and {limit} characters')
    return value.strip()


def text(value, limit=500):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('Invalid or oversized text field')
    return value.strip()


def number(value, field, minimum=0, maximum=1_000_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{field}: enter a number between {minimum} and {maximum}')
    return value


def validate_resource(raw):
    if not isinstance(raw, dict):
        raise ValueError('Resource must be an object')
    provider = required_text(raw.get('provider'), 'provider', 30)
    if provider not in {'gcp', 'azure', 'aws', 'kubernetes', 'on-premises', 'other'}:
        raise ValueError('Unsupported provider')
    resource = {key: text(raw.get(key), 2000 if key == 'resourceId' else 500) for key in
                ('name', 'resourceId', 'account', 'region', 'type', 'component', 'version', 'runtimeStatus', 'observedAt', 'owner')}
    resource['name'] = required_text(raw.get('name'), 'resource name')
    resource['provider'] = provider
    if resource['observedAt']:
        try:
            stamp = datetime.fromisoformat(resource['observedAt'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError()
            if (stamp - datetime.now(timezone.utc)).total_seconds() > 300:
                raise ValueError()
        except ValueError:
            raise ValueError('observedAt must be a timezone-aware ISO timestamp, not in the future')
    configuration = raw.get('configuration', {})
    if not isinstance(configuration, dict):
        raise ValueError('configuration must be an object')
    allowed = ('image', 'cpu', 'memory', 'replicas', 'network', 'runtimeIdentity', 'endpoint', 'ingress')
    resource['configuration'] = {key: text(configuration.get(key), 1000) for key in allowed if configuration.get(key) is not None}
    resource['id'] = hashlib.sha256(f"{provider}:{resource['account']}:{resource['resourceId'] or resource['name']}".encode()).hexdigest()[:24]
    return resource


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS traces (seq INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT UNIQUE NOT NULL, source TEXT NOT NULL, sequence INTEGER NOT NULL, outcome TEXT NOT NULL, invoice_ref TEXT, received_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS trace_outcome_seq ON traces(outcome, seq DESC);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def setting(self, key, default):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else default

    def event(self, db, action, details):
        previous = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        previous_hash = previous['hash'] if previous else '0' * 64
        payload = {'at': now(), 'actor': getattr(self, 'actor', 'console-operator'), 'action': action, 'details': details}
        digest = hashlib.sha256((previous_hash + canonical(payload)).encode()).hexdigest()
        db.execute('INSERT INTO audit(payload,previous_hash,hash) VALUES(?,?,?)', (canonical(payload), previous_hash, digest))

    def save_setting(self, key, value):
        with LOCK, self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, canonical(value)))
            self.event(db, f'{key}.saved', {'note': 'Local console configuration only; execution services are not configured by this action'})

    def resources(self):
        with self.db() as db:
            return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM resources ORDER BY rowid')]

    def import_resources(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('resources'), list) or not 1 <= len(payload['resources']) <= 500:
            raise ValueError('Provide an object containing 1–500 resources')
        source = text(payload.get('source', 'Manual manifest'), 200)
        notes = payload.get('coverageNotes', [])
        if not isinstance(notes, list) or len(notes) > 100:
            raise ValueError('coverageNotes must be a list of up to 100 notes')
        notes = [text(note, 1000) for note in notes]
        resources = [validate_resource(raw) for raw in payload['resources']]
        if len({r['id'] for r in resources}) != len(resources):
            raise ValueError('Duplicate resource identifiers in import')
        with LOCK, self.db() as db:
            for resource in resources:
                resource.update({'source': source or 'Manual manifest', 'importedAt': now(), 'verification': 'reported'})
                db.execute('INSERT OR REPLACE INTO resources VALUES(?,?)', (resource['id'], canonical(resource)))
            report = {'importedAt': now(), 'count': len(resources), 'source': source, 'coverageNotes': notes}
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('lastImport', canonical(report)))
            self.event(db, 'inventory.imported', {'count': len(resources), 'source': source, 'coverageNotes': notes, 'verification': 'Reported, not independently verified'})
        return len(resources)

    def delete_resource(self, resource_id):
        with LOCK, self.db() as db:
            if not db.execute('DELETE FROM resources WHERE id=?', (resource_id,)).rowcount:
                raise ValueError('Resource not found')
            self.event(db, 'inventory.removed', {'resourceId': resource_id})

    def audit(self):
        with self.db() as db:
            rows = db.execute('SELECT * FROM audit ORDER BY seq').fetchall()
        return [dict(json.loads(row['payload']), seq=row['seq'], previousHash=row['previous_hash'], hash=row['hash']) for row in rows]

    def verify_audit(self):
        previous = '0' * 64
        events = self.audit()
        valid = True
        for event in events:
            payload = {key: event[key] for key in ('at', 'actor', 'action', 'details')}
            digest = hashlib.sha256((previous + canonical(payload)).encode()).hexdigest()
            valid = valid and event['previousHash'] == previous and hmac.compare_digest(digest, event['hash'])
            previous = event['hash']
        return {'valid': valid, 'events': len(events), 'checkedAt': now(), 'headHash': previous,
                'scope': 'Local database chain only. No external anchor, WORM storage, deletion detection, or compliance certification.'}

    def reviews(self):
        with self.db() as db:
            return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM reviews ORDER BY rowid DESC')]

    def create_review(self, raw):
        if not isinstance(raw, dict):
            raise ValueError('Review must be an object')
        review = {'id': secrets.token_hex(12), 'invoiceNo': required_text(raw.get('invoiceNo'), 'invoice number', 100),
                  'supplier': required_text(raw.get('supplier'), 'supplier', 200),
                  'totalAud': number(raw.get('totalAud'), 'total'), 'note': text(raw.get('note'), 2000),
                  'createdAt': now(), 'status': 'UNVERIFIED', 'checks': 'Not run', 'source': 'Manual review record',
                  'erpWrite': 'Not attempted', 'policyVersion': None}
        with LOCK, self.db() as db:
            db.execute('INSERT INTO reviews VALUES(?,?)', (review['id'], canonical(review)))
            self.event(db, 'review.created', {'reviewId': review['id'], 'status': 'UNVERIFIED'})
        return review

    def review_action(self, raw):
        if raw.get('action') != 'reject':
            raise ValueError('ERP approval, callback verification and posting are not implemented. No write was attempted.')
        with LOCK, self.db() as db:
            row = db.execute('SELECT payload FROM reviews WHERE id=?', (text(raw.get('id')),)).fetchone()
            if not row:
                raise ValueError('Review not found')
            review = json.loads(row['payload'])
            if review['status'] != 'UNVERIFIED':
                raise ValueError('Review is already closed')
            reason = required_text(raw.get('reason'), 'rejection reason', 1000)
            review.update(status='REJECTED', rejectionReason=reason, updatedAt=now())
            db.execute('UPDATE reviews SET payload=? WHERE id=?', (canonical(review), review['id']))
            self.event(db, 'review.rejected', {'reviewId': review['id'], 'reason': reason, 'erpWrite': 'Not attempted'})
        return review

    def validate(self):
        resources = self.resources()
        environment = self.setting('environment', {})
        checks = [
            {'name': 'Environment registered', 'state': 'configured' if environment.get('name') else 'missing', 'detail': 'Saved customer metadata; cloud ownership has not been verified.', 'owner': 'Infrastructure'},
            {'name': 'Customer inventory', 'state': 'reported' if resources else 'missing', 'detail': f'{len(resources)} resource records imported. Imports do not prove health or ownership.', 'owner': 'Infrastructure'},
            {'name': 'Identity & role enforcement', 'state': 'unavailable', 'detail': 'Server-configured console roles are enforced. Federated login, user lifecycle and MFA are not integrated.', 'owner': 'Identity team'},
            {'name': 'Regional inference & egress', 'state': 'unverified', 'detail': 'No verified model destination or network enforcement evidence. An entered region is not residency proof.', 'owner': 'Security'},
            {'name': 'Document extraction & protection', 'state': 'unverified', 'detail': 'No end-to-end document ingestion, OCR or redaction attestation is connected to this console.', 'owner': 'Platform'},
            {'name': 'ERP connection & reconciliation', 'state': 'unavailable', 'detail': 'No live ERP adapter, durable approval or posting integration. Writes are disabled.', 'owner': 'Finance / Platform'},
            {'name': 'Execution policy', 'state': 'draft' if self.setting('policy', None) else 'missing', 'detail': 'Local draft only. No policy publication to processing services.', 'owner': 'Finance controller'},
            {'name': 'Immutable audit destination', 'state': 'unverified', 'detail': 'Events are stored in local SQLite. External immutable retention is not verified.', 'owner': 'Security'},
        ]
        report = {'checkedAt': now(), 'readyForWrites': False, 'checks': checks}
        self.save_setting('validation', report)
        return report

    def ingest_trace(self, raw, source):
        trace = validate_trace(raw)
        serialized = canonical(trace)
        with LOCK, self.db() as db:
            old = db.execute('SELECT source,sequence,payload,seq FROM traces WHERE trace_id=?', (trace['traceId'],)).fetchone()
            if old:
                previous = json.loads(old['payload'])
                old_contract = {key: value for key, value in previous.items() if key not in {'dataset', 'source', 'receivedAt', 'summary'}}
                if old['source'] != source:
                    raise ValueError('Trace belongs to another telemetry producer')
                if canonical(old_contract) == serialized:
                    return {'accepted': True, 'duplicate': True, 'traceId': trace['traceId']}
                if previous['coverage']['complete'] or trace['sequence'] <= old['sequence']:
                    raise ValueError('Finalized traces are immutable; snapshot sequences must increase')
            trace.update(dataset='live', source=source, receivedAt=now())
            trace['summary'] = summary(trace)
            db.execute('INSERT INTO traces(trace_id,source,sequence,outcome,invoice_ref,received_at,payload) VALUES(?,?,?,?,?,?,?) ON CONFLICT(trace_id) DO UPDATE SET sequence=excluded.sequence,outcome=excluded.outcome,invoice_ref=excluded.invoice_ref,received_at=excluded.received_at,payload=excluded.payload',
                       (trace['traceId'], source, trace['sequence'], trace['outcome'], trace.get('invoiceRef'), trace['receivedAt'], canonical(trace)))
            self.event(db, 'telemetry.received', {'traceId': trace['traceId'], 'sequence': trace['sequence'], 'producer': source})
        return {'accepted': True, 'duplicate': False, 'traceId': trace['traceId']}

    def trace_page(self, role, dataset='live', cursor=0, limit=25, query='', outcome='all'):
        if role not in SECURITY | FINANCE:
            raise PermissionError('Trace access is not permitted')
        if dataset not in {'live', 'preview'} or not 1 <= limit <= 100 or cursor < 0 or len(query) > 100 or outcome not in {'all', 'RUNNING', 'COMPLETED', 'NEEDS_APPROVAL', 'BLOCKED', 'ERROR'}:
            raise ValueError('Invalid trace query')
        if dataset == 'preview':
            traces = [t for t in preview_traces() if (not query or query.lower() in (t['traceId']+' '+(t['invoiceRef'] or '')).lower()) and (outcome == 'all' or t['outcome'] == outcome)]
            traces = traces if cursor == 0 else []
            total = len(traces)
            next_cursor = None
        else:
            conditions, values = [], []
            if query:
                conditions.append("(instr(lower(trace_id),?)>0 OR instr(lower(coalesce(invoice_ref,'')),?)>0)")
                values += [query.lower(), query.lower()]
            if outcome != 'all':
                conditions.append('outcome=?'); values.append(outcome)
            where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
            with self.db() as db:
                total = db.execute('SELECT count(*) FROM traces' + where, values).fetchone()[0]
                page_conditions = conditions + (['seq < ?'] if cursor else [])
                page_values = values + ([cursor] if cursor else [])
                page_where = ' WHERE ' + ' AND '.join(page_conditions) if page_conditions else ''
                rows = db.execute('SELECT seq,payload FROM traces' + page_where + ' ORDER BY seq DESC LIMIT ?', page_values+[limit+1]).fetchall()
            traces = [json.loads(row['payload']) for row in rows[:limit]]
            next_cursor = rows[limit-1]['seq'] if len(rows)>limit else None
        return {'dataset': dataset, 'traces': [project_trace(trace, role) for trace in traces], 'total': total,
                'nextCursor': next_cursor, 'fetchedAt': now(), 'pollIntervalSeconds': 5,
                'sourceLabel': 'Synthetic illustration; not customer traffic' if dataset == 'preview' else 'Received producer telemetry; not independent attestation',
                'scope': 'Metrics describe this page of traces. Model cost excludes infrastructure. Missing values remain unknown.'}

    def state(self):
        return {'environment': self.setting('environment', {}), 'resources': self.resources(), 'reviews': self.reviews(),
                'lastImport': self.setting('lastImport', None),
                'policy': self.setting('policy', {}), 'validation': self.setting('validation', None),
                'audit': self.audit()[-200:][::-1], 'auditIntegrity': self.verify_audit(),
                'capabilities': {'inventory': True, 'localPersistence': True, 'erpWrites': False, 'sso': False,
                                 'cloudDiscovery': 'Customer-run CLI collector', 'auditStorage': 'Local SQLite; not WORM',
                                 'costTelemetry': False, 'telemetryIngestion': True, 'roleEnforcement': True},
                'runtime': {'hostname': socket.gethostname(), 'service': 'Kerbline customer console', 'version': '0.3.0',
                            'observedAt': now(), 'status': 'Responding', 'source': 'This server process',
                            'region': os.getenv('CLOUD_REGION') or None, 'revision': os.getenv('K_REVISION') or None,
                            'cloudService': os.getenv('K_SERVICE') or None,
                            'note': 'A responding console does not establish processing-service or infrastructure health.'}}


class Handler(BaseHTTPRequestHandler):
    server_version = 'KerblineConsole/0.3'

    def log_message(self, fmt, *args):
        # Do not log URLs, headers, credentials or request bodies.
        pass

    @property
    def store(self):
        if not hasattr(self, '_request_store'):
            self._request_store = object.__new__(Store)
            self._request_store.path = self.server.store.path
            self._request_store.actor = getattr(self, 'principal', {}).get('subject', 'console-operator')
        return self._request_store

    def respond(self, status, payload, mime='application/json'):
        body = json.dumps(payload, allow_nan=False).encode() if mime == 'application/json' else payload
        self.send_response(status)
        for key, value in {'Content-Type': mime + '; charset=utf-8', 'Content-Length': str(len(body)),
                           'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                           'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                           'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def host_valid(self):
        host = self.headers.get('Host', '')
        local_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}',
                       f'[::1]:{self.server.server_port}'}
        if self.server.public_origin:
            local_hosts.add(urlsplit(self.server.public_origin).netloc)
        return host in local_hosts

    def authorized(self):
        if not self.host_valid():
            self.respond(421, {'error': 'Unrecognised Host. Configure CONSOLE_ORIGIN for reverse proxy access.'})
            return False
        supplied = self.headers.get('Authorization', '')
        self.principal = None
        for identity in self.server.identities:
            if hmac.compare_digest(supplied.encode(), ('Bearer ' + identity['token']).encode()):
                self.principal = {'subject': identity['subject'], 'role': identity['role']}
        if not self.server.identities and not self.server.access_token:
            self.principal = {'subject': 'console-operator', 'role': 'console-admin'}
        elif self.server.access_token and hmac.compare_digest(supplied.encode(), ('Bearer ' + self.server.access_token).encode()):
            self.principal = {'subject': 'console-operator', 'role': 'console-admin'}
        if not self.principal:
            self.respond(401, {'error': 'Enter a valid console role credential. No browser-supplied role is trusted.'})
            return False
        return True

    def allow(self, roles):
        if self.principal['role'] not in roles:
            self.respond(403, {'error': 'This role is not permitted to access this resource'})
            return False
        return True

    def projected_state(self):
        role = self.principal['role']
        all_state = self.store.state()
        if role == 'console-admin':
            result = all_state
        else:
            result = {'environment': {key: all_state['environment'].get(key) for key in ['name', 'stage']},
                      'capabilities': {'erpWrites': False, 'sso': False, 'roleEnforcement': True, 'telemetryIngestion': True}}
            if role == 'ap-clerk':
                result['reviews'] = all_state['reviews']
            if role in SECURITY:
                result.update({key: all_state[key] for key in ['resources', 'policy', 'audit', 'auditIntegrity', 'validation', 'lastImport']})
                result['environment'] = all_state['environment']
        result['access'] = dict(self.principal, allowedViews=ROLES[self.principal['role']],
                                credentialType='Local operator' if not self.server.identities and not self.server.access_token else 'Server-configured role credential',
                                federatedIdentity=False)
        return result

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.host_valid():
            self.respond(421, {'error': 'Unrecognised Host'})
            return
        if path in STATIC:
            filename, mime = STATIC[path]
            self.respond(200, (BASE_DIR / filename).read_bytes(), mime)
            return
        if path == '/api/health':
            self.respond(200, {'status': 'responding', 'scope': 'console process only', 'checkedAt': now()})
            return
        if not self.authorized():
            return
        if path in {'/api/state', '/api/config'}:
            self.respond(200, self.projected_state())
        elif path == '/api/traces':
            if not self.allow(SECURITY | FINANCE):
                return
            params = parse_qs(urlsplit(self.path).query)
            try:
                result = self.store.trace_page(self.principal['role'], params.get('dataset', ['live'])[0], int(params.get('cursor', ['0'])[0]), int(params.get('limit', ['25'])[0]), params.get('q', [''])[0], params.get('outcome', ['all'])[0])
                self.respond(200, result)
            except ValueError:
                self.respond(400, {'error': 'Invalid trace query'})
        elif path == '/api/evidence':
            if not self.allow(SECURITY):
                return
            state = self.store.state()
            state['audit'] = self.store.audit()
            self.respond(200, {'exportedAt': now(), 'scope': 'Local console records; no WORM or production-readiness certification', 'evidence': state})
        else:
            self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        ingestion = urlsplit(self.path).path == '/api/telemetry/ingest'
        if ingestion:
            if not self.host_valid():
                self.respond(421, {'error': 'Unrecognised Host'}); return
            supplied = self.headers.get('Authorization', '')
            producer = next((item for item in self.server.producers if hmac.compare_digest(supplied.encode(), ('Bearer ' + item['token']).encode())), None)
            if not producer:
                self.respond(401, {'error': 'A dedicated telemetry producer credential is required'}); return
            self.principal = {'subject': producer['subject'], 'role': 'telemetry-producer'}
        elif not self.authorized():
            return
        origin = self.headers.get('Origin')
        expected = self.server.public_origin or f'http://{self.headers.get("Host")}'
        if origin and origin != expected:
            self.respond(403, {'error': 'Cross-origin mutation rejected'})
            return
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.respond(415, {'error': 'Use application/json'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                self.respond(413, {'error': 'Request must be between 1 byte and 1 MB'})
                return
            raw = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite number')))
            if not isinstance(raw, dict):
                raise ValueError('Expected a JSON object')
            path = urlsplit(self.path).path
            permissions = {'/api/environment': {'console-admin'}, '/api/resources/import': {'console-admin','it-security-admin'},
                           '/api/resources/remove': {'console-admin','it-security-admin'}, '/api/policy': {'console-admin','it-security-admin'},
                           '/api/validate': {'console-admin'}, '/api/reviews': {'console-admin','ap-clerk'},
                           '/api/reviews/action': {'console-admin','ap-clerk'}, '/api/activate': {'console-admin'},
                           '/api/invoices/process': {'console-admin','ap-clerk'}, '/api/run-scenario': {'console-admin'},
                           '/api/telemetry/ingest': {'telemetry-producer'}}
            if path in permissions and not self.allow(permissions[path]):
                return
            if path == '/api/telemetry/ingest':
                result = self.store.ingest_trace(raw, self.principal['subject'])
            elif path == '/api/environment':
                value = {key: text(raw.get(key), 300) for key in ('name', 'organisation', 'provider', 'account', 'region', 'network', 'owner')}
                value['name'] = required_text(raw.get('name'), 'environment name', 100)
                value['stage'] = raw.get('stage', 'test')
                value['deploymentMode'] = raw.get('deploymentMode', 'customer-cloud')
                if value['stage'] not in {'test', 'staging', 'production'} or value['deploymentMode'] not in {'customer-cloud', 'managed', 'hybrid'}:
                    raise ValueError('Invalid environment stage or deployment mode')
                self.store.save_setting('environment', value)
                result = value
            elif path == '/api/resources/import':
                result = {'imported': self.store.import_resources(raw)}
            elif path == '/api/resources/remove':
                self.store.delete_resource(required_text(raw.get('id'), 'resource id'))
                result = {'removed': True}
            elif path == '/api/policy':
                value = {'approvalThresholdAud': number(raw.get('approvalThresholdAud'), 'approval threshold'),
                         'dailyBudgetAud': number(raw.get('dailyBudgetAud'), 'daily budget'),
                         'confidenceThreshold': number(raw.get('confidenceThreshold'), 'confidence threshold', 0, 1),
                         'budgetAction': raw.get('budgetAction'), 'bankChangeRule': 'Callback verification required',
                         'status': 'DRAFT_NOT_ENFORCED', 'savedAt': now(), 'version': secrets.token_hex(4)}
                if value['budgetAction'] not in {'draft-queue', 'hard-stop'}:
                    raise ValueError('Invalid budget action')
                self.store.save_setting('policy', value)
                result = value
            elif path == '/api/validate':
                result = self.store.validate()
            elif path == '/api/reviews':
                result = self.store.create_review(raw)
            elif path == '/api/reviews/action':
                result = self.store.review_action(raw)
            elif path in {'/api/invoices/process', '/api/run-scenario', '/api/activate'}:
                self.respond(409, {'error': 'Not connected: live processing and ERP writes are disabled. No action was attempted.'})
                return
            else:
                self.respond(404, {'error': 'Not found'})
                return
            self.respond(200, result)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
            self.respond(400, {'error': str(exc)})
        except Exception:
            self.respond(500, {'error': 'Console storage operation failed. No successful outcome has been recorded by the UI.'})


def create_server(host='127.0.0.1', port=8787, data_path=None, token=None, origin=None, identities=None, producers=None):
    identities = identities or []
    producers = producers or []
    credentials = [item.get('token') for item in identities + producers] + ([token] if token else [])
    if len(credentials) != len(set(credentials)) or any(not isinstance(t, str) or len(t) < 8 for t in credentials):
        raise ValueError('Credentials must be distinct strings of at least 8 characters')
    for item in identities:
        if item.get('role') not in ROLES or not isinstance(item.get('subject'), str) or not item['subject']:
            raise ValueError('Invalid server role identity')
    for item in producers:
        if not isinstance(item.get('subject'), str) or not item['subject'] or item['subject'] in {i['subject'] for i in identities}:
            raise ValueError('Invalid telemetry producer identity')
    if host not in {'127.0.0.1', 'localhost', '::1'} and not (token or identities):
        raise ValueError('Set CONSOLE_ACCESS_TOKEN before binding outside loopback')
    if origin and (urlsplit(origin).scheme not in {'http', 'https'} or urlsplit(origin).path not in {'', '/'}):
        raise ValueError('CONSOLE_ORIGIN must be an http(s) origin without a path')
    server = ThreadingHTTPServer((host, port), Handler)
    server.store = Store(data_path or os.getenv('KERBLINE_DATA_PATH', str(BASE_DIR / 'data' / 'console.sqlite3')))
    server.access_token = token
    server.identities = identities
    server.producers = producers
    server.public_origin = origin.rstrip('/') if origin else None
    return server


def main():
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '8787'))
    credentials_path = os.getenv('CONSOLE_CREDENTIALS_PATH')
    credentials = json.loads(Path(credentials_path).read_text()) if credentials_path else {}
    server = create_server(host, port, token=os.getenv('CONSOLE_ACCESS_TOKEN'), origin=os.getenv('CONSOLE_ORIGIN'),
                           identities=credentials.get('identities'), producers=credentials.get('producers'))
    print(f'Kerbline console: http://{host}:{port} — ERP writes disabled', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
