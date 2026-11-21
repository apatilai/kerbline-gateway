import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store, create_server

spec=importlib.util.spec_from_file_location('collector',Path(__file__).resolve().parents[1]/'tools'/'collect_inventory.py')
collector=importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'console.sqlite3'
        self.server=create_server(port=0,data_path=self.path,token='test-token')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,body=None,token='test-token',headers=None):
        request=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Authorization':f'Bearer {token}',**({'Content-Type':'application/json'} if body is not None else {}),**(headers or {})})
        try:
            response=urlopen(request)
        except HTTPError as exc:
            response=exc
        with response:
            content=response.read()
            return response.status,json.loads(content) if response.headers.get_content_type()=='application/json' else content

    def resource(self,**kwargs):
        return {'provider':'gcp','name':'customer-processor','account':'customer-project','region':'australia-southeast1',
                'resourceId':'//run.googleapis.com/projects/customer-project/locations/australia-southeast1/services/customer-processor',**kwargs}

    def test_empty_state_has_no_invented_resources_or_costs(self):
        code,result=self.request('/api/state')
        self.assertEqual(code,200)
        self.assertEqual(result['resources'],[])
        self.assertEqual(result['reviews'],[])
        self.assertFalse(result['capabilities']['erpWrites'])
        self.assertFalse(result['capabilities']['costTelemetry'])
        self.assertIsNone(result['runtime']['region'])

    def test_token_required(self):
        self.assertEqual(self.request('/api/state',token='wrong')[0],401)
        self.assertEqual(self.request('/api/resources/import',{'resources':[self.resource()]},token='wrong')[0],401)
        self.assertEqual(self.server.store.resources(),[])

    def test_cross_origin_mutation_rejected(self):
        self.assertEqual(self.request('/api/environment',{'name':'Prod'},headers={'Origin':'https://untrusted.example'})[0],403)
        self.assertEqual(self.server.store.setting('environment',{}),{})

    def test_host_rebinding_rejected(self):
        self.assertEqual(self.request('/api/state',headers={'Host':'attacker.example'})[0],421)

    def test_static_allowlist_hides_database_and_historical_demo(self):
        for path in ['/server.py','/data/console.sqlite3','/explorer.html','/../server.py','/tests/test_console.py']:
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/')[0],200)

    def test_environment_persists_without_activating(self):
        self.assertEqual(self.request('/api/environment',{'name':'Customer Prod','stage':'production','region':'customer-region'})[0],200)
        self.assertEqual(Store(self.path).setting('environment',{})['name'],'Customer Prod')
        self.assertFalse(self.request('/api/state')[1]['capabilities']['erpWrites'])

    def test_import_is_reported_and_cannot_forge_verified(self):
        self.assertEqual(self.request('/api/resources/import',{'source':'Customer CLI','resources':[self.resource(verification='verified',runtimeStatus='Ready') ]})[0],200)
        r=self.request('/api/state')[1]['resources'][0]
        self.assertEqual(r['verification'],'reported')
        self.assertEqual(r['runtimeStatus'],'Ready')
        self.assertEqual(r['observedAt'],'')
        self.assertEqual(r['name'],'customer-processor')

    def test_runtime_configuration_and_coverage_notes_persist_without_extra_fields(self):
        resource=self.resource(configuration={'image':'customer/image@sha256:123','cpu':'1000m','memory':'512Mi','secret':'must-not-persist'})
        code,_=self.request('/api/resources/import',{'resources':[resource],'coverageNotes':['Cloud Run only; not all resources.']})
        self.assertEqual(code,200)
        _,state=self.request('/api/state')
        self.assertEqual(state['resources'][0]['configuration']['cpu'],'1000m')
        self.assertNotIn('secret',state['resources'][0]['configuration'])
        self.assertEqual(state['lastImport']['coverageNotes'],['Cloud Run only; not all resources.'])

    def test_upsert_preserves_other_resources(self):
        self.request('/api/resources/import',{'resources':[self.resource(),self.resource(name='other',resourceId='other-id')]})
        self.request('/api/resources/import',{'resources':[self.resource(version='rev-2')]})
        self.assertEqual(len(self.server.store.resources()),2)
        self.assertIn('rev-2',[r['version'] for r in self.server.store.resources()])

    def test_invalid_import_atomic_and_future_timestamp_rejected(self):
        self.assertEqual(self.request('/api/resources/import',{'resources':[self.resource(),{'name':'bad'}]})[0],400)
        self.assertEqual(self.server.store.resources(),[])
        self.assertEqual(self.request('/api/resources/import',{'resources':[self.resource(observedAt='2999-01-01T00:00:00Z')]})[0],400)
        self.assertEqual(self.request('/api/resources/import',{'resources':[self.resource(observedAt='2026-01-01T00:00:00')]})[0],400)

    def test_remove_only_local_resource(self):
        self.request('/api/resources/import',{'resources':[self.resource()]})
        resource_id=self.server.store.resources()[0]['id']
        self.assertEqual(self.request('/api/resources/remove',{'id':resource_id})[0],200)
        self.assertEqual(self.server.store.resources(),[])
        self.assertEqual(self.server.store.audit()[-1]['action'],'inventory.removed')

    def test_policy_is_draft_and_validated(self):
        body={'approvalThresholdAud':5000,'dailyBudgetAud':150,'confidenceThreshold':0.92,'budgetAction':'draft-queue'}
        code,p=self.request('/api/policy',body)
        self.assertEqual(code,200)
        self.assertEqual(p['status'],'DRAFT_NOT_ENFORCED')
        self.assertEqual(self.request('/api/policy',{**body,'confidenceThreshold':1.1})[0],400)
        self.assertEqual(self.request('/api/policy',{**body,'dailyBudgetAud':float('nan')})[0],400)
        self.assertEqual(self.request('/api/policy',{**body,'approvalThresholdAud':True})[0],400)

    def test_readiness_never_auto_activates(self):
        self.request('/api/environment',{'name':'Production','stage':'production'})
        self.request('/api/resources/import',{'resources':[self.resource()]})
        _,report=self.request('/api/validate',{})
        self.assertFalse(report['readyForWrites'])
        self.assertTrue(any(c['state']=='unavailable' for c in report['checks']))
        for endpoint in ['/api/activate','/api/invoices/process','/api/run-scenario']:
            self.assertEqual(self.request(endpoint,{})[0],409)

    def test_manual_reviews_no_fabricated_checks_and_durable_rejection(self):
        code,review=self.request('/api/reviews',{'invoiceNo':'INV-1','supplier':'Supplier','totalAud':100})
        self.assertEqual(code,200)
        self.assertEqual(review['status'],'UNVERIFIED')
        self.assertEqual(review['checks'],'Not run')
        self.assertEqual(self.request('/api/reviews/action',{'id':review['id'],'action':'approve'})[0],400)
        code,review=self.request('/api/reviews/action',{'id':review['id'],'action':'reject','reason':'Missing supporting evidence'})
        self.assertEqual(code,200)
        self.assertEqual(Store(self.path).reviews()[0]['status'],'REJECTED')
        self.assertEqual(review['erpWrite'],'Not attempted')
        self.assertEqual(self.request('/api/reviews/action',{'id':review['id'],'action':'reject','reason':'again'})[0],400)

    def test_audit_chain_detects_modification(self):
        self.request('/api/environment',{'name':'Customer'})
        self.request('/api/validate',{})
        self.assertTrue(self.server.store.verify_audit()['valid'])
        with self.server.store.db() as db:
            db.execute("UPDATE audit SET payload=? WHERE seq=1",(json.dumps({'at':'changed','actor':'attacker','action':'tampered','details':{}}),))
        self.assertFalse(self.server.store.verify_audit()['valid'])

    def test_evidence_export_contains_explicit_limits(self):
        self.request('/api/environment',{'name':'Customer'})
        code,export=self.request('/api/evidence')
        self.assertEqual(code,200)
        self.assertIn('no WORM',export['scope'])
        self.assertEqual(export['evidence']['audit'][0]['actor'],'console-operator')

    def test_json_only_and_bounded_body(self):
        self.assertEqual(self.request('/api/environment',{'name':'x'},headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('/api/environment',{'name':'x'},headers={'Content-Length':'1000001'})[0],413)

    def test_non_loopback_requires_token(self):
        with self.assertRaises(ValueError):
            create_server(host='0.0.0.0',port=0,data_path=self.path)


class CollectorTests(unittest.TestCase):
    @patch.object(collector,'run_json')
    def test_gcp_collects_customer_names_and_ready_revision(self,run):
        run.side_effect=[[{'displayName':'kerbline-customer-worker','name':'//run/resource','assetType':'run.googleapis.com/Service','location':'australia-southeast1'},
                          {'displayName':'unrelated','name':'//other','assetType':'x'}],
                         {'status':{'latestReadyRevisionName':'customer-rev-42','conditions':[{'type':'Ready','status':'True'}]}}]
        output=collector.collect('gcp','customer-project')
        self.assertEqual(len(output['resources']),1)
        self.assertEqual(output['resources'][0]['account'],'customer-project')
        self.assertEqual(output['resources'][0]['version'],'customer-rev-42')
        self.assertIn('--project=customer-project',run.call_args_list[1][0][0])

    @patch.object(collector,'run_json')
    def test_cloud_run_mode_collects_configuration_without_environment_fields(self,run):
        run.side_effect=[[{'metadata':{'name':'kerbline-worker'},'status':{'latestReadyRevisionName':'rev-1','conditions':[{'type':'Ready','status':'True'}]}}],
                         {'spec':{'template':{'spec':{'containers':[{'image':'customer/image','resources':{'limits':{'cpu':'1000m','memory':'512Mi'}}}]}}}}]
        output=collector.collect_cloud_run('customer-project','customer-region')
        self.assertEqual(output['resources'][0]['configuration']['memory'],'512Mi')
        self.assertEqual(output['resources'][0]['version'],'rev-1')
        self.assertNotIn('.env',run.call_args_list[1][0][0][-1])

    @patch.object(collector,'run_json')
    def test_aws_wrong_account_fails_closed(self,run):
        run.return_value={'Account':'unexpected-account'}
        with self.assertRaises(ValueError):
            collector.collect('aws','expected-account','ap-southeast-2')
        self.assertEqual(run.call_count,1)

    @patch.object(collector,'run_json')
    def test_kubernetes_no_env_or_secret_export(self,run):
        run.return_value={'items':[{'kind':'Deployment','metadata':{'name':'kerbline-worker'},
                                   'spec':{'replicas':3,'template':{'spec':{'containers':[{'env':[{'name':'SECRET','value':'do-not-export'}]}]}}},
                                   'status':{'readyReplicas':2}}]}
        output=collector.collect('kubernetes','customer-context',namespace='finance')
        self.assertEqual(output['resources'][0]['runtimeStatus'],'2 / 3 replicas ready')
        self.assertNotIn('do-not-export',json.dumps(output))
        self.assertNotIn('secrets',run.call_args[0][0])

    @patch.object(collector,'run_json')
    def test_partial_gcp_failure_reported_not_healthy(self,run):
        run.side_effect=[[{'name':'//run/kerbline-worker','assetType':'run.googleapis.com/Service','location':'au'}],ValueError('permission denied')]
        result=collector.collect('gcp','customer')
        self.assertEqual(result['resources'][0]['runtimeStatus'],'')
        self.assertTrue(any('unavailable' in note for note in result['coverageNotes']))


if __name__=='__main__':
    unittest.main()
