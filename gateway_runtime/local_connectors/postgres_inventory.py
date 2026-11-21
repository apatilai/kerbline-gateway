"""Local PostgreSQL inventory pilot. Fixed query, dedicated DB login, no model route."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / '.local/postgres-reader.json'
STATE = ROOT / 'tmp/postgres'
ROLE = 'kerbline_inventory_reader'
CONTAINER = 'relayops-postgres'
VIEW = 'kerbline_inventory.approved_apis'
FIELDS = {'id', 'tenant_id', 'name', 'base_path', 'enabled', 'auth_type'}
QUERY = '''SELECT json_build_object(
 'database', current_database(), 'role', current_user,
 'readOnly', current_setting('transaction_read_only'),
 'rows', COALESCE((SELECT json_agg(r) FROM
 (SELECT id, tenant_id, name, base_path, enabled, auth_type
 FROM kerbline_inventory.approved_apis ORDER BY id LIMIT 51) r), '[]'::json));'''


def read_config():
    if CONFIG.stat().st_mode & 0o077:
        raise PermissionError('Reader credential file must be private')
    config = json.loads(CONFIG.read_text())
    uuid.UUID(config['tenantId'])
    return config


def execute(sql, config):
    # Password travels via stdin, never command arguments or browser responses.
    command = ['docker', '--context', 'desktop-linux', 'exec', '-i', CONTAINER, 'sh', '-c',
               'IFS= read -r PGPASSWORD; export PGPASSWORD; '
               'export PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000"; '
               'exec psql -X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U kerbline_inventory_reader -d relayops']
    result = subprocess.run(command, input=config['password'] + '\n' + sql, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Database read rejected or unavailable')
    if len(result.stdout.encode()) > 262144:
        raise ValueError('Database response exceeds limit')
    return result.stdout


def project(raw, tenant):
    if raw.get('role') != ROLE or raw.get('readOnly') != 'on':
        raise ValueError('Restricted read-only session required')
    rows = raw.get('rows')
    if not isinstance(rows, list) or len(rows) > 51:
        raise ValueError('Invalid bounded result')
    for row in rows:
        if set(row) != FIELDS or row['tenant_id'] != tenant or type(row['enabled']) is not bool:
            raise ValueError('Invalid release schema or tenant')
        uuid.UUID(row['id'])
        for key in ('name', 'base_path', 'auth_type'):
            if row[key] is not None and (not isinstance(row[key], str) or len(row[key]) > 256):
                raise ValueError('Invalid field value')
    return rows[:50], len(rows) > 50


def write(name, value):
    STATE.mkdir(parents=True, exist_ok=True)
    target = STATE / name
    temporary = target.with_suffix('.new')
    temporary.write_text(json.dumps(value, indent=2))
    os.replace(temporary, target)


def snapshot():
    started = datetime.now(timezone.utc).isoformat()
    try:
        config = read_config()
        raw = json.loads(execute(QUERY, config))
        rows, truncated = project(raw, config['tenantId'])
        result = {'observedAt': datetime.now(timezone.utc).isoformat(), 'requestId': str(uuid.uuid4()),
                  'source': 'Live local PostgreSQL via Docker exec; not the GCP MCP path',
                  'database': 'relayops', 'container': CONTAINER, 'role': ROLE,
                  'tenantId': config['tenantId'], 'view': VIEW, 'fields': sorted(FIELDS),
                  'rows': rows, 'truncated': truncated, 'rowLimit': 50,
                  'readOnly': True, 'modelAttempted': False, 'modelTokens': 0,
                  'identityBoundary': 'Loopback operator nonce; dedicated PostgreSQL login. No per-user enterprise SSO.'}
        write('latest.json', result)
        write('attempt.json', {'success': True, 'startedAt': started, 'completedAt': result['observedAt']})
        return result
    except Exception:
        write('attempt.json', {'success': False, 'startedAt': started,
                               'error': 'Database refresh failed; previous observations preserved.'})
        raise RuntimeError('Database refresh failed; previous observations preserved.') from None


if __name__ == '__main__':
    snapshot()
    print('Read approved tenant API inventory using restricted PostgreSQL login; no model call.')
