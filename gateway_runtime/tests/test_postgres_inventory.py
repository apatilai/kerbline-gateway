"""Local DB release boundary: seeded forbidden fields, cross-tenant rows, failure retention."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('postgres_inventory', Path(__file__).parents[1] / 'local_connectors/postgres_inventory.py')
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
TENANT = '00000000-0000-0000-0000-000000000def'


def sample():
    return {'role': db.ROLE, 'readOnly': 'on', 'rows': [{'id': '00000000-0000-0000-0000-000000000001', 'tenant_id': TENANT, 'name': 'Approved API', 'base_path': '/approved', 'enabled': True, 'auth_type': 'jwt'}]}


class PostgresTests(unittest.TestCase):
    def test_approved_projection(self):
        rows, truncated = db.project(sample(), TENANT)
        self.assertEqual(set(rows[0]), db.FIELDS)
        self.assertFalse(truncated)

    def test_seeded_secret_and_unknown_field_rejected(self):
        for key in ['jwt_secret', 'request_headers', 'password', 'upstream_url']:
            raw = sample()
            raw['rows'][0][key] = 'RESTRICTED_CANARY'
            with self.assertRaises(ValueError):db.project(raw, TENANT)

    def test_cross_tenant_rejected(self):
        with self.assertRaises(ValueError):db.project(sample(), '00000000-0000-0000-0000-000000000002')

    def test_admin_and_writable_sessions_rejected(self):
        for key, value in [('role', 'postgres'), ('readOnly', 'off')]:
            raw = sample();raw[key] = value
            with self.assertRaises(ValueError):db.project(raw, TENANT)

    def test_limit_and_malformed_rows(self):
        raw = sample();raw['rows'] *= 51
        self.assertEqual(len(db.project(raw,TENANT)[0]),50)
        self.assertTrue(db.project(raw,TENANT)[1])
        raw['rows'] *= 2
        with self.assertRaises(ValueError):db.project(raw,TENANT)

    def test_failure_keeps_saved_snapshot_and_safe_error(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(db,'STATE',Path(folder)), patch.object(db,'read_config',side_effect=RuntimeError('SECRET_CANARY')):
            saved = Path(folder) / 'latest.json';saved.write_text('{"prior":true}')
            with self.assertRaises(RuntimeError):db.snapshot()
            self.assertEqual(saved.read_text(),'{"prior":true}')
            attempt=(Path(folder)/'attempt.json').read_text()
            self.assertNotIn('SECRET_CANARY',attempt)
            self.assertFalse(json.loads(attempt)['success'])
