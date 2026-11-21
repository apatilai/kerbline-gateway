import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from server import Store, create_server
from telemetry import validate_trace, summary
from telemetry_preview import preview_traces
from kerbline_telemetry import Trace, Exporter


def valid_trace(trace_id='trace-test-request'):
    result=preview_traces()[0]
    for key in ['source','dataset','receivedAt','summary']:
        result.pop(key,None)
    result['traceId']=trace_id
    return result


class ContractTests(unittest.TestCase):
    def test_raw_prompt_headers_and_sensitive_metadata_rejected(self):
        for key in ['prompt','rawPrompt','Authorization','oauthToken','tokenisedPromptPreview']:
            raw=valid_trace();raw[key]='must never be stored'
            with self.assertRaises(ValueError):validate_trace(raw)
        for value in ['Bearer secret-credential','10.1.2.3','BSB 062-000','BSB 062 000','TFN 123 456 782','Medicare 2123 45670 1','pod fd00::1','account 12345678','https://internal.example','eyJabcdefghijkabcdefghijkabcdefghijk']:
            raw=valid_trace();raw['hops'][0]['targetSystem']=value
            with self.assertRaises(ValueError):validate_trace(raw)

    def test_unknown_is_distinct_from_zero(self):
        raw=valid_trace();raw['prompts'][0]['totalTokens']=None
        result=summary(validate_trace(raw))
        self.assertIsNone(result['totalTokens'])
        fast=preview_traces()[1]
        self.assertEqual(fast['summary']['totalTokens'],0)
        self.assertEqual(fast['summary']['estimatedModelCostAud'],0)

    def test_cache_and_reasoning_are_not_double_counted(self):
        trace=validate_trace(valid_trace())
        self.assertEqual(summary(trace)['totalTokens'],4200)
        raw=valid_trace();raw['prompts'][0]['totalTokens']+=800
        with self.assertRaises(ValueError):validate_trace(raw)
        raw=valid_trace();raw['prompts'][0]['cachedTokens']=9999
        with self.assertRaises(ValueError):validate_trace(raw)
        raw=valid_trace();raw['prompts'][0]['reasoningTokens']=9999
        with self.assertRaises(ValueError):validate_trace(raw)

    def test_false_fast_path_rejected(self):
        raw=valid_trace();raw['route']='TEMPLATE_FAST_PATH';raw['templateVerifiedCount']=20
        with self.assertRaises(ValueError):validate_trace(raw)
        raw=preview_traces()[1]
        for key in ['source','dataset','receivedAt','summary']:raw.pop(key,None)
        raw['templateVerifiedCount']=4
        with self.assertRaises(ValueError):validate_trace(raw)

    def test_zero_egress_requires_request_scope_final_coverage(self):
        raw=valid_trace();raw['outcome']='BLOCKED';raw['hops']=[h for h in raw['hops'] if h['targetKind']!='ERP']
        trace=validate_trace(raw)
        self.assertTrue(summary(trace)['zeroErpEgress'])
        raw['coverage']['erpWriteComplete']=False
        self.assertFalse(summary(validate_trace(raw))['zeroErpEgress'])
        raw['coverage']['erpWriteComplete']=True;raw['scope']='COMPONENT'
        self.assertFalse(summary(validate_trace(raw))['zeroErpWriteEgress'])

    def test_erp_reads_preclude_zero_all_egress(self):
        trace=preview_traces()[2]
        self.assertFalse(trace['summary']['zeroErpEgress'])
        self.assertTrue(trace['summary']['zeroErpWriteEgress'])
        trace['hops'][1]['networkAttempted']=None
        self.assertFalse(summary(trace)['zeroErpEgress'])

    def test_unknown_operation_is_not_assumed_read_only(self):
        raw=valid_trace();raw['hops'][3]['operation']='OTHER'
        trace=validate_trace(raw)
        self.assertFalse(summary(trace)['zeroErpWriteEgress'])

    def test_cost_requires_versioned_rate_reference(self):
        raw=valid_trace();raw['prompts'][0].pop('rateCardId')
        with self.assertRaises(ValueError):validate_trace(raw)

    def test_duplicate_prompt_ids_rejected(self):
        raw=valid_trace();raw['prompts'][1]['promptId']=raw['prompts'][0]['promptId']
        with self.assertRaises(ValueError):validate_trace(raw)

    def test_negative_nan_and_boolean_tokens_rejected(self):
        for value in [-1,float('nan'),True,1.5]:
            raw=valid_trace();raw['prompts'][0]['promptTokens']=value
            with self.assertRaises(ValueError):validate_trace(raw)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        identities=[{'subject':role,'role':role,'token':'test-'+role+'-credential'} for role in ['ap-clerk','cfo','finance-controller','it-security-admin','ciso-auditor']]
        cls.server=create_server(port=0,data_path=Path(cls.tmp.name)/'db',token='admin-test-credential',identities=identities,
                                 producers=[{'subject':'trusted-producer','token':'producer-test-credential'},{'subject':'other-producer','token':'other-producer-credential'}])
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
        cls.server.store.save_setting('environment',{'name':'Customer','stage':'production','account':'private-cloud-account','network':'private-network'})
        cls.server.store.import_resources({'resources':[{'name':'sensitive-infra-name','provider':'gcp'}]})
        cls.server.store.ingest_trace(valid_trace(),'trusted-producer')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()

    def request(self,path,body=None,token='admin-test-credential',headers=None):
        req=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,
                    headers={'Authorization':'Bearer '+token,**({'Content-Type':'application/json'} if body is not None else {}),**(headers or {})})
        try:response=urlopen(req)
        except HTTPError as exc:response=exc
        with response:return response.status,json.loads(response.read())

    def test_ap_has_only_queue_and_no_infra_in_response(self):
        code,state=self.request('/api/state',token='test-ap-clerk-credential')
        self.assertEqual(code,200);self.assertEqual(state['access']['allowedViews'],['queue'])
        self.assertNotIn('resources',state);self.assertNotIn('audit',state)
        self.assertNotIn('private-cloud-account',json.dumps(state))
        self.assertEqual(self.request('/api/traces',token='test-ap-clerk-credential')[0],403)
        self.assertEqual(self.request('/api/evidence',token='test-ap-clerk-credential')[0],403)

    def test_finance_gets_prompt_counts_but_no_topology_hash_or_fingerprints(self):
        for dataset in ['live','preview']:
            code,page=self.request('/api/traces?dataset='+dataset,token='test-cfo-credential')
            self.assertEqual(code,200)
            for trace in page['traces']:
                self.assertNotIn('hops',trace);self.assertNotIn('source',trace)
                self.assertNotIn('erpWriteEgressCount',trace['summary'])
                for prompt in trace['prompts']:self.assertNotIn('promptSha256',prompt)
            self.assertNotIn('mtlsCertFingerprint',json.dumps(page))
        self.assertEqual(self.request('/api/evidence',token='test-cfo-credential')[0],403)
        state=self.request('/api/state',token='test-cfo-credential')[1]
        self.assertEqual(state['access']['allowedViews'],['costs']);self.assertNotIn('resources',state)

    def test_security_role_sees_metadata_and_auditor_cannot_mutate(self):
        code,page=self.request('/api/traces?dataset=preview',token='test-ciso-auditor-credential')
        self.assertEqual(code,200);self.assertIn('hops',page['traces'][0]);self.assertIn('promptSha256',page['traces'][0]['prompts'][0])
        self.assertEqual(self.request('/api/resources/remove',{'id':'anything'},token='test-ciso-auditor-credential')[0],403)
        self.assertEqual(self.request('/api/policy',{},token='test-cfo-credential')[0],403)

    def test_browser_role_spoofing_and_console_ingestion_denied(self):
        self.assertEqual(self.request('/api/traces',token='test-ap-clerk-credential',headers={'X-Role':'console-admin'})[0],403)
        self.assertEqual(self.request('/api/telemetry/ingest',valid_trace('trace-rejected-admin'))[0],401)
        self.assertEqual(self.request('/api/state',token='producer-test-credential')[0],401)

    def test_producer_ingestion_duplicate_and_final_immutability(self):
        raw=valid_trace('trace-producer-idempotence')
        code,result=self.request('/api/telemetry/ingest',raw,token='producer-test-credential')
        self.assertEqual(code,200);self.assertFalse(result['duplicate'])
        self.assertTrue(self.request('/api/telemetry/ingest',raw,token='producer-test-credential')[1]['duplicate'])
        raw['sequence']=2
        self.assertEqual(self.request('/api/telemetry/ingest',raw,token='producer-test-credential')[0],400)
        self.assertEqual(self.request('/api/telemetry/ingest',raw,token='other-producer-credential')[0],400)

    def test_partial_snapshot_increases_sequence_and_finalizes(self):
        raw=valid_trace('trace-in-progress');raw['coverage']['complete']=False;raw['outcome']='RUNNING';raw['endedAt']=None
        self.assertEqual(self.request('/api/telemetry/ingest',raw,token='producer-test-credential')[0],200)
        final=valid_trace('trace-in-progress');final['sequence']=2
        self.assertEqual(self.request('/api/telemetry/ingest',final,token='producer-test-credential')[0],200)

    def test_keyset_pagination_search_and_preview_isolation(self):
        for i in range(5):self.server.store.ingest_trace(valid_trace('trace-pagination-'+chr(65+i)),'trusted-producer')
        _,first=self.request('/api/traces?limit=2&q=pagination')
        self.assertEqual(len(first['traces']),2);self.assertEqual(first['total'],5)
        _,second=self.request('/api/traces?limit=2&q=pagination&cursor='+str(first['nextCursor']))
        self.assertFalse({t['traceId'] for t in first['traces']}&{t['traceId'] for t in second['traces']})
        self.assertTrue(all(t['dataset']=='live' and not t['traceId'].startswith('preview-') for t in first['traces']))
        self.assertEqual(self.request('/api/traces?limit=10000')[0],400)
        self.assertEqual(self.request('/api/traces?dataset=preview')[1]['total'],3)

    def test_sensitive_payload_not_persisted(self):
        raw=valid_trace('trace-sensitive-rejected');raw['rawPrompt']='SENSITIVE-NEVER-STORE'
        self.assertEqual(self.request('/api/telemetry/ingest',raw,token='producer-test-credential')[0],400)
        with self.server.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM traces WHERE trace_id=?',(raw['traceId'],)).fetchone()[0],0)
        self.assertNotIn('SENSITIVE-NEVER-STORE',json.dumps(self.server.store.audit()))


class ExporterTests(unittest.TestCase):
    def test_sdk_fast_path_creates_explicit_zero_usage(self):
        trace=Trace(invoice_ref='INV-ZERO-MODEL')
        self.assertTrue(trace.fast_path(5))
        trace.finish('COMPLETED',complete=True,llm_complete=True)
        safe=validate_trace(trace.snapshot())
        self.assertEqual(summary(safe)['totalTokens'],0)
        self.assertEqual(summary(safe)['estimatedModelCostAud'],0)
        self.assertEqual(summary(safe)['llmHops'],0)
        rejected=Trace();self.assertFalse(rejected.fast_path(4))
        rejected.finish('COMPLETED',complete=True,llm_complete=True)
        self.assertFalse(rejected.data['coverage']['complete'])

    def test_sdk_normalizes_reasoning_cache_and_versioned_estimate(self):
        trace=Trace()
        trace.usage('Code_Mode_Gen','example-model',hashlib.sha256(b'no raw prompt retained').hexdigest(),
                    {'prompt_tokens':100,'completion_tokens':30,'total_tokens':130,'prompt_tokens_details':{'cached_tokens':80},'completion_tokens_details':{'reasoning_tokens':10}},
                    rate_card={'id':'test-rate-v1','inputPerMillionAud':1,'cachedPerMillionAud':0.1,'outputPerMillionAud':2})
        safe=validate_trace(trace.finish('COMPLETED',complete=True,llm_complete=True).snapshot())
        self.assertEqual(safe['prompts'][0]['totalTokens'],130)
        self.assertAlmostEqual(safe['prompts'][0]['estimatedCostAud'],0.000088)
        self.assertFalse(summary(safe)['zeroErpEgress'])

    def test_sdk_rejects_unsafe_metadata_without_breaking_business_flow(self):
        trace=Trace();self.assertFalse(trace.hop('OUTBOUND_INFERENCE','Bearer secret','MODEL','INFERENCE'))
        trace.finish('COMPLETED',complete=True,llm_complete=True)
        self.assertFalse(trace.data['coverage']['complete'])
        with tempfile.TemporaryDirectory() as tmp:
            sink=Path(tmp)/'events.jsonl';exporter=Exporter(sink=str(sink))
            trace.data['rawPrompt']='MUST-NOT-QUEUE'
            self.assertFalse(exporter.emit(trace));self.assertEqual(exporter.dropped,1)
            exporter.close();self.assertFalse(sink.exists())

    def test_file_export_is_async_metadata_only_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink=Path(tmp)/'events.jsonl';exporter=Exporter(sink=str(sink))
            trace=Trace();trace.finish('COMPLETED',complete=True)
            self.assertTrue(exporter.emit(trace));self.assertTrue(exporter.flush())
            exporter.close();record=json.loads(sink.read_text())
            self.assertEqual(record['traceId'],trace.data['traceId']);self.assertNotIn('Authorization',sink.read_text())
            self.assertEqual(os.stat(sink).st_mode & 0o777,0o600)

    def test_exporter_disallows_insecure_remote_endpoints_and_url_credentials(self):
        for url in ['http://remote.example/ingest','https://user:secret@example.com/ingest','https://example.com/ingest?token=secret']:
            with self.assertRaises(ValueError):Exporter(url=url,token='credential')


if __name__=='__main__':unittest.main()
