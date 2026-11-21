"""One-time, additive setup in the authorised local test database. No business-row writes."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gateway_runtime/local_connectors'))
from postgres_inventory import CONFIG

TENANT = '00000000-0000-0000-0000-000000000def'
uuid.UUID(TENANT)
if CONFIG.exists():
    raise SystemExit('Reader configuration already exists; refusing to rotate or overwrite')
password = secrets.token_hex(32)
sql = f"""BEGIN;
CREATE ROLE kerbline_inventory_reader LOGIN PASSWORD '{password}'
 NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
ALTER ROLE kerbline_inventory_reader SET default_transaction_read_only = on;
ALTER ROLE kerbline_inventory_reader SET statement_timeout = '5s';
ALTER ROLE kerbline_inventory_reader SET search_path = pg_catalog;
CREATE SCHEMA kerbline_inventory;
REVOKE ALL ON SCHEMA kerbline_inventory FROM PUBLIC;
CREATE VIEW kerbline_inventory.approved_apis WITH (security_barrier=true) AS
 SELECT id, tenant_id, left(name,256) AS name, left(base_path,256) AS base_path,
 enabled, left(auth_type,256) AS auth_type FROM public.apis
 WHERE tenant_id = '{TENANT}'::uuid;
REVOKE ALL ON kerbline_inventory.approved_apis FROM PUBLIC;
GRANT CONNECT ON DATABASE relayops TO kerbline_inventory_reader;
GRANT USAGE ON SCHEMA kerbline_inventory TO kerbline_inventory_reader;
GRANT SELECT ON kerbline_inventory.approved_apis TO kerbline_inventory_reader;
COMMIT;
"""
CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
# Reserve private file before committing setup so a local path conflict cannot overwrite credentials.
fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    result = subprocess.run(['docker','--context','desktop-linux','exec','-i','relayops-postgres',
        'psql','-X','-q','-v','ON_ERROR_STOP=1','-U','postgres','-d','relayops'],
        input=sql, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('Database setup rejected; inspect existing role/schema names')
    with os.fdopen(fd, 'w') as file:
        fd = None
        json.dump({'password': password, 'tenantId': TENANT}, file)
except Exception:
    if fd is not None:os.close(fd)
    CONFIG.unlink(missing_ok=True)
    raise RuntimeError('Local reader setup failed; no credentials printed') from None
print('Created tenant-pinned view and restricted reader. Private local credential file created; no business rows changed.')
