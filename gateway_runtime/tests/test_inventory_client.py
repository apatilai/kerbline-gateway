"""Failure preservation and safe local request evidence; no cloud calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('inventory_client', Path(__file__).parents[1] / 'deploy/inventory_client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class SnapshotTests(unittest.TestCase):
    def test_rejected_and_unavailable_requests_preserve_snapshot(self):
        for status, outcome in [(401, 'DENIED'), (403, 'DENIED'), (502, 'FAILED')]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'latest.json').write_text('{"saved": true}')
                with patch.object(client, 'STATE', root), patch.object(client, 'tokens', return_value=('SECRET_USER', 'SECRET_CLIENT')), patch.object(client, 'call', return_value=(status, None)), patch.object(client.subprocess, 'run', return_value=SimpleNamespace(stdout='operator@example.com')):
                    with self.assertRaises(RuntimeError):
                        client.snapshot()
                self.assertEqual((root / 'latest.json').read_text(), '{"saved": true}')
                self.assertFalse(json.loads((root / 'latest-attempt.json').read_text())['success'])
                events = (root / 'requests.json').read_text()
                self.assertNotIn('SECRET_', events)
                event = json.loads(events)[0]
                self.assertEqual(event['outcome'], outcome)
                self.assertEqual(event['releasedFields'], [])

    def test_success_records_only_release_metadata(self):
        result = {'data': 'live-infrastructure', 'modelAttempted': False, 'traceId': 'trace', 'services': [{'resourceId': 'approved-resource', 'name': 'approved-service'}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(client, 'STATE', Path(folder)), patch.object(client, 'tokens', return_value=('SECRET_USER', 'SECRET_CLIENT')), patch.object(client, 'call', return_value=(200, {'result': {'structuredContent': result}})), patch.object(client.subprocess, 'run', return_value=SimpleNamespace(stdout='operator@example.com')):
            client.snapshot()
            event = json.loads((Path(folder) / 'requests.json').read_text())[0]
            self.assertEqual(event['resources'], ['approved-resource'])
            self.assertEqual(event['releasedFields'], ['name', 'resourceId'])
            self.assertNotIn('approved-service', json.dumps(event))
            self.assertIn('not server-verified', event['requesterSource'])
