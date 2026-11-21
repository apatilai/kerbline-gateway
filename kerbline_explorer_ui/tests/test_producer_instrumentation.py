import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'phase_b_poc'))
sys.path.insert(0,str(ROOT/'phase_c_poc'))
sys.path.insert(0,str(ROOT/'kerbline_explorer_ui'))
from kerbline_telemetry import Trace, current_trace
from telemetry import validate_trace
import pre_pass
from app import host_rpc_bridge, main
from fastapi.testclient import TestClient


class InstrumentationTests(unittest.TestCase):
    def test_health_reports_truthful_runtime_and_exporter_status(self):
        response=TestClient(main.app).get("/health")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["sandbox"],"node-vm-poc")
        self.assertIn("enabled",response.json()["telemetry"])

    def test_multiple_vlm_calls_group_under_one_trace_without_raw_payload(self):
        trace=Trace(invoice_ref='INV-SAFE-REFERENCE')
        context=current_trace.set(trace)
        response=MagicMock()
        response.json.return_value={'usage':{'prompt_tokens':25,'completion_tokens':5,'total_tokens':30},'choices':[{'message':{'content':'sensitive response never exported'}}]}
        try:
            with patch.object(pre_pass.httpx,'Client') as client:
                client.return_value.__enter__.return_value.post.return_value=response
                for _ in range(2):
                    pre_pass.call_nvidia_nim_vlm('secret-api-key','secret-image-data','secret-raw-prompt')
            trace.finish('COMPLETED',complete=True,llm_complete=True)
            safe=validate_trace(trace.snapshot())
            self.assertEqual(len(safe['prompts']),2)
            self.assertEqual(len(safe['hops']),2)
            self.assertTrue(all(h['region'] is None for h in safe['hops']))
            self.assertTrue(all(h['transport']=='TLS' for h in safe['hops']))
            for value in ['secret-api-key','secret-image-data','secret-raw-prompt','sensitive response']:
                self.assertNotIn(value,json.dumps(safe))
        finally:current_trace.reset(context)

    def test_failed_vlm_request_preserves_error_and_records_unknown_usage(self):
        trace=Trace();context=current_trace.set(trace)
        try:
            with patch.object(pre_pass.httpx,'Client') as client:
                client.return_value.__enter__.return_value.post.side_effect=RuntimeError('provider secret error')
                with self.assertRaises(RuntimeError):pre_pass.call_nvidia_nim_vlm('secret','image','prompt')
            trace.finish('ERROR')
            safe=validate_trace(trace.snapshot())
            self.assertEqual(safe['hops'][0]['status'],'ERROR')
            self.assertIsNone(safe['prompts'][0]['totalTokens'])
            self.assertNotIn('provider secret error',json.dumps(safe))
        finally:current_trace.reset(context)

    def test_bridge_blocks_disallowed_rpc_server_side(self):
        bridge=host_rpc_bridge.HostRPCBridge(role='read-only')
        with self.assertRaises(PermissionError):bridge.handle_rpc('propose_payment_batch',{})

    def test_real_v8_execution_emits_component_scoped_bank_block(self):
        captured=[]
        script='const result = await kerbline.change_supplier_bank_details({supplierAbn: "33051775556"}); return {status: result.status};'
        with patch.object(host_rpc_bridge.exporter,'emit',side_effect=lambda trace:captured.append(trace.snapshot()) or True):
            result=host_rpc_bridge.run_v8_sandbox(script,invoice_ref='LOCAL-POC-INVOICE')
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['status'],'BLOCK_UNTIL_CALLBACK_VERIFIED')
        self.assertEqual(len(captured),1)
        safe=validate_trace(captured[0])
        self.assertEqual(safe['scope'],'COMPONENT')
        self.assertEqual(safe['outcome'],'BLOCKED')
        self.assertTrue(any(h['hopType']=='FRAUD_GATE' for h in safe['hops']))
        self.assertNotIn('33051775556',json.dumps(safe))
        self.assertEqual(safe['prompts'],[])


if __name__=='__main__':unittest.main()
