import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import test_gateway as base
import postgres_mcp as pg

TENANT='00000000-0000-0000-0000-000000000def'

def release(trace='trace'):
    return {'traceId':trace,'tenantId':TENANT,'rows':[],'truncated':False,'observedAt':'2026-11-20T00:00:00+00:00','modelAttempted':False}

class GatewayTests(unittest.TestCase):
    rpc=base.GatewayTests.rpc
    def setUp(self):
        base.GatewayTests.setUp(self)
        self.policy['roles']=deepcopy(self.policy['roles'])
        self.policy['roles']['analyst']['tools']=[pg.TOOL]
        self.policy['postgres']={'transport':'local-loopback','subjects':{'user-1':{'tool':pg.TOOL,'tenantId':TENANT}}}
        self.auth=patch.object(base.gateway,'inventory_client_identity')
        self.auth.start();self.addCleanup(self.auth.stop)

    def test_unknown_tenant_sql_url_and_external_destination_denied(self):
        for arguments in [{'tenantId':TENANT},{'sql':'SELECT 1'},{'url':'http://evil'},{'destination':'external_model'}]:
            with patch.object(pg.httpx,'AsyncClient') as transport:
                response=self.rpc(params={'name':pg.TOOL,'arguments':arguments})
                self.assertEqual(response.json()['error']['code'],-32602)
                transport.assert_not_called()

    def test_wrong_tenant_binding_denied_before_adapter(self):
        del self.policy['postgres']['subjects']['user-1']
        with patch.object(pg.httpx,'AsyncClient') as transport:
            self.assertEqual(self.rpc(params={'name':pg.TOOL}).status_code,403)
            transport.assert_not_called()

    def test_expired_user_denied(self):
        base.gateway.id_token.verify_oauth2_token.side_effect=ValueError('expired')
        with patch.object(pg.httpx,'AsyncClient') as transport:
            self.assertEqual(self.rpc(params={'name':pg.TOOL}).status_code,401)
            transport.assert_not_called()

    def test_cloud_transport_fails_closed(self):
        with patch.dict(os.environ,{'K_SERVICE':'cloud'}),patch.object(base.gateway,'service_identity'),patch.object(pg.httpx,'AsyncClient') as transport:
            self.assertEqual(self.rpc(params={'name':pg.TOOL}).status_code,503)
            transport.assert_not_called()

    def test_model_route_denied_before_any_model_call(self):
        with patch.object(base.gateway,'vertex_call') as model:
            response=self.client.post('/v1/answer',headers=self.headers,json={'name':pg.TOOL,'arguments':{}})
            self.assertEqual(response.status_code,403)
            model.assert_not_called()

    def test_adapter_outage_no_partial_release(self):
        with patch.object(pg,'key',return_value='private'),patch.object(pg.httpx,'AsyncClient',side_effect=RuntimeError('SECRET_CANARY')):
            response=self.rpc(params={'name':pg.TOOL})
        self.assertEqual(response.status_code,502)
        self.assertNotIn('SECRET_CANARY',response.text)

class ReleaseTests(unittest.TestCase):
    def test_standard_mcp_metadata_carries_no_authority(self):
        call=pg.Call.model_validate({'name':pg.TOOL,'arguments':{},'_meta':{'role':'admin','tenantId':'other'}})
        self.assertEqual(call.arguments.destination,'internal_app')
        with self.assertRaises(HTTPException):pg.binding({'postgres':{'subjects':{}}},'unbound')

    def test_cross_tenant_trace_and_extra_fields_rejected(self):
        for key,value in [('tenantId','other'),('traceId','wrong'),('secret','CANARY'),('modelAttempted',True)]:
            raw=release();raw[key]=value
            with self.assertRaises(ValueError):pg.validate_release(raw,TENANT,'trace')

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(pg.adapter)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        policy={'user_audience':'user-audience','inventory':{'client_audience':'client-audience','client_subject':'client'},
                'postgres':{'subjects':{'user':{'tool':pg.TOOL,'tenantId':TENANT}}}}
        (Path(self.temp.name)/'postgres-mcp-policy.json').write_text(json.dumps(policy))
        self.patches=[patch.object(pg,'LOCAL',Path(self.temp.name)),patch.object(pg,'key',return_value='gateway-key'),
          patch.object(pg.id_token,'verify_oauth2_token',side_effect=[{'sub':'user'},{'sub':'client'}]),
          patch.object(pg.db,'read_config',return_value={'tenantId':TENANT}),
          patch.object(pg.db,'execute',return_value=json.dumps({'role':pg.db.ROLE,'readOnly':'on','rows':[]}))]
        for p in self.patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
        self.trace='00000000-0000-0000-0000-000000000001'
        self.headers={'Authorization':'Bearer gateway-key','X-Kerbline-User-Token':'signed-user','X-Kerbline-Client-Token':'signed-client','X-Kerbline-Trace-Id':self.trace}
        self.payload={'jsonrpc':'2.0','id':self.trace,'method':'tools/call','params':{'name':'read_postgres_api_inventory','arguments':{}}}

    def test_verified_identities_and_trace_preserved(self):
        response=self.client.post('/mcp',headers=self.headers,json=self.payload)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['result']['structuredContent']['traceId'],self.trace)

    def test_direct_adapter_bypass_denied(self):
        self.headers.pop('Authorization')
        self.assertEqual(self.client.post('/mcp',headers=self.headers,json=self.payload).status_code,401)
        pg.db.execute.assert_not_called()

    def test_wrong_database_tenant_denied(self):
        pg.db.read_config.return_value={'tenantId':'00000000-0000-0000-0000-000000000002'}
        self.assertEqual(self.client.post('/mcp',headers=self.headers,json=self.payload).status_code,403)
        pg.db.execute.assert_not_called()

    def test_expired_client_denied(self):
        pg.id_token.verify_oauth2_token.side_effect=[{'sub':'user'},ValueError('expired')]
        self.assertEqual(self.client.post('/mcp',headers=self.headers,json=self.payload).status_code,401)
        pg.db.execute.assert_not_called()

    def test_database_outage_safe(self):
        pg.db.execute.side_effect=RuntimeError('SECRET')
        response=self.client.post('/mcp',headers=self.headers,json=self.payload)
        self.assertEqual(response.status_code,502)
        self.assertNotIn('SECRET',response.text)
