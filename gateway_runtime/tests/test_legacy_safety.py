"""Regression coverage for the reviewed legacy HTTP defects."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'phase_b_poc'))
spec = importlib.util.spec_from_file_location('legacy_perception', ROOT / 'phase_b_poc' / 'app.py')
phase_b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = phase_b
spec.loader.exec_module(phase_b)
sys.path.insert(0, str(ROOT / 'phase_c_poc'))
spec_c = importlib.util.spec_from_file_location('legacy_execution', ROOT / 'phase_c_poc' / 'app' / 'main.py')
phase_c = importlib.util.module_from_spec(spec_c)
sys.modules[spec_c.name] = phase_c
spec_c.loader.exec_module(phase_c)


class LegacySafetyTests(unittest.TestCase):
    def test_process_request_accepts_trace_reference(self):
        req = phase_b.ProcessRequest(regions=[], image_b64='', invoice_ref='TEST-1')
        self.assertEqual(req.invoice_ref, 'TEST-1')
        self.assertIsNone(phase_b.ProcessRequest(regions=[], image_b64='').invoice_ref)

    def test_process_http_no_longer_crashes_with_sdk(self):
        response = TestClient(phase_b.app).post('/v1/phase-b/process', json={'regions': [], 'image_b64': '', 'invoice_ref': 'TEST-1'})
        self.assertEqual(response.status_code, 200)

    def test_untrusted_image_release_disabled(self):
        response = TestClient(phase_b.app).post('/v1/phase-b/process', json={'regions': [], 'image_b64': '', 'escalate_vlm': True})
        self.assertEqual(response.status_code, 503)

    def test_legacy_javascript_disabled(self):
        with patch.dict(os.environ, {'KERBLINE_ENABLE_UNSAFE_POC': ''}):
            response = TestClient(phase_c.app).post('/v1/phase-c/execute', json={'code': 'return 1', 'role': 'finance-controller'})
        self.assertEqual(response.status_code, 503)

    def test_benchmark_explicitly_illustrative(self):
        with patch.dict(os.environ, {'KERBLINE_ENABLE_UNSAFE_POC': ''}):
            response = TestClient(phase_c.app).get('/v1/phase-c/benchmark')
        self.assertEqual(response.json()['measurementType'], 'ILLUSTRATIVE_ASSUMPTIONS_NOT_MEASURED')
        self.assertFalse(response.json()['liveV8SandboxExecution']['ok'])
