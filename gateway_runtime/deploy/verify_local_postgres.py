"""Live local DB boundary checks; fixed SQL, no business-row mutation."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gateway_runtime/local_connectors'))
from postgres_inventory import execute, read_config, snapshot, write

config = read_config()
checks = []
def check(name, passed):
    checks.append({'name': name, 'passed': bool(passed)})
    print(name, 'PASS' if passed else 'FAIL')
def denied(name, sql):
    try:
        execute(sql, config)
    except RuntimeError:
        check(name, True)
    else:
        check(name, False)

result = snapshot()
check('dedicated_login_readonly_session', result['role'] == 'kerbline_inventory_reader' and result['readOnly'])
check('single_pinned_tenant', all(x['tenant_id'] == config['tenantId'] for x in result['rows']))
check('only_six_approved_fields', all(set(x) == {'id','tenant_id','name','base_path','enabled','auth_type'} for x in result['rows']))
flags = json.loads(execute("SELECT json_build_object('superuser',rolsuper,'createRole',rolcreaterole,'createDb',rolcreatedb,'bypassRls',rolbypassrls) FROM pg_roles WHERE rolname=current_user;",config))
check('no_admin_role_attributes', not any(flags.values()))
accessible = json.loads(execute("SELECT COALESCE(json_agg(table_schema || '.' || table_name), '[]'::json) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');",config))
check('only_approved_view_accessible', accessible == ['kerbline_inventory.approved_apis'])
denied('base_table_and_secret_column_denied', 'SELECT jwt_secret FROM public.apis LIMIT 0;')
denied('credential_table_denied', 'SELECT * FROM public.test_credentials LIMIT 0;')
# WHERE false ensures no rows could change even if an incorrect grant were introduced.
denied('update_denied_even_if_readonly_default_overridden', 'BEGIN READ WRITE; UPDATE kerbline_inventory.approved_apis SET enabled=false WHERE false; ROLLBACK;')
denied('insert_denied_even_if_readonly_default_overridden', 'BEGIN READ WRITE; INSERT INTO kerbline_inventory.approved_apis(id) SELECT id FROM kerbline_inventory.approved_apis WHERE false; ROLLBACK;')
check('bounded_release_no_model', len(result['rows']) <= 50 and result['modelAttempted'] is False)
report = {'observedAt': datetime.now(timezone.utc).isoformat(), 'source': 'Live local PostgreSQL; not GCP',
          'tests': checks, 'passed': sum(x['passed'] for x in checks), 'total': len(checks),
          'businessRowsModified': False, 'modelRequestsMade': 0}
write('checks.json', report)
if report['passed'] != report['total']:raise SystemExit(1)
