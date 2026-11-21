import asyncio
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import test_gateway as base
gateway = base.gateway
from inventory import project_service, validate_result, read_services

RESOURCE = 'projects/project-4f0a7dac-0503-4e87-abe/locations/australia-southeast1/services/kerbline-pilot-gateway'
NOW = '2026-11-19T01:00:00+00:00'


def provider():
    return {'name': RESOURCE, 'generation': '7', 'observedGeneration': '7', 'terminalCondition': {'state': 'CONDITION_SUCCEEDED'},
            'latestReadyRevision': RESOURCE + '/revisions/kerbline-pilot-gateway-00007-mgj',
            'latestCreatedRevision': RESOURCE + '/revisions/kerbline-pilot-gateway-00008-new',
            'trafficStatuses': [{'revision': 'kerbline-pilot-gateway-00006-old', 'percent': 20}, {'revision': 'kerbline-pilot-gateway-00007-mgj', 'percent': 80}],
            'ingress': 'INGRESS_TRAFFIC_ALL', 'updateTime': NOW,
            'template': {'containers': [{'env': [{'name': 'SECRET', 'value': 'DO_NOT_RELEASE'}]}]}, 'creator': 'DO_NOT_RELEASE@example.com'}


class InventoryTests(unittest.TestCase):
    def test_signed_client_subject_and_audience(self):
        from starlette.requests import Request
        from fastapi import HTTPException
        request = Request({'type': 'http', 'headers': [(b'x-kerbline-client-token', b'signed-client-token')]})
        policy = {'inventory': {'client_subject': 'trusted-client', 'client_audience': 'https://inventory.internal'}}
        with patch.object(gateway, 'POLICY', policy), patch.object(gateway.id_token, 'verify_oauth2_token', return_value={'sub': 'trusted-client'}) as verify:
            gateway.inventory_client_identity(request)
            self.assertEqual(verify.call_args.kwargs['audience'], 'https://inventory.internal')
            verify.return_value = {'sub': 'unapproved-client'}
            with self.assertRaises(HTTPException) as denied:gateway.inventory_client_identity(request)
            self.assertEqual(denied.exception.status_code, 403)
            verify.side_effect = ValueError('invalid signature')
            with self.assertRaises(HTTPException) as denied:gateway.inventory_client_identity(request)
            self.assertEqual(denied.exception.status_code, 401)

    def test_projection_shows_traffic_not_latest_created_as_active(self):
        result = project_service(provider(), 'gateway', RESOURCE, NOW)
        self.assertEqual(result['readiness'], 'READY')
        self.assertEqual([x['percent'] for x in result['activeRevisions']], [20, 80])
        self.assertNotIn('00008-new', json.dumps(result['activeRevisions']))
        self.assertIsNone(result['activeInstanceCount'])
        self.assertNotIn('DO_NOT_RELEASE', json.dumps(result))
        self.assertNotIn('template', result)

    def test_missing_and_stale_status_not_invented_ready(self):
        raw = provider();raw.pop('terminalCondition');raw.pop('trafficStatuses')
        out = project_service(raw, 'gateway', RESOURCE, NOW)
        self.assertEqual(out['readiness'], 'UNKNOWN')
        self.assertEqual(out['activeRevisions'], [])
        self.assertFalse(out['trafficComplete'])
        raw = provider();raw['observedGeneration'] = '6'
        self.assertEqual(project_service(raw, 'gateway', RESOURCE, NOW)['readiness'], 'UNKNOWN')

    def test_other_resource_identity_fails(self):
        raw = provider();raw['name'] = RESOURCE.replace('gateway', 'unapproved')
        with self.assertRaises(ValueError):project_service(raw, 'gateway', RESOURCE, NOW)

    def test_other_resource_revision_fails(self):
        raw = provider();raw['trafficStatuses'][0]['revision'] = 'unapproved-service-00001'
        with self.assertRaises(ValueError):project_service(raw, 'gateway', RESOURCE, NOW)

    def test_bad_traffic_fails(self):
        for percent in (True, -1, 101, '100'):
            raw = provider();raw['trafficStatuses'][0]['percent'] = percent
            with self.assertRaises(ValueError):project_service(raw, 'gateway', RESOURCE, NOW)

    def test_degraded_and_reconciling_reported(self):
        raw = provider();raw['terminalCondition']['state'] = 'CONDITION_FAILED'
        self.assertEqual(project_service(raw, 'gateway', RESOURCE, NOW)['readiness'], 'NOT_READY')
        raw = provider();raw['reconciling'] = True
        self.assertEqual(project_service(raw, 'gateway', RESOURCE, NOW)['readiness'], 'RECONCILING')

    def test_second_boundary_strips_unknown_and_rejects_cross_scope(self):
        row = project_service(provider(), 'gateway', RESOURCE, NOW)
        result = {'data': 'live-infrastructure', 'services': [{**row, 'secret': 'DO_NOT_RELEASE'}]}
        self.assertNotIn('DO_NOT_RELEASE', json.dumps(validate_result(result, {'gateway': RESOURCE})))
        with self.assertRaises(ValueError):validate_result(result, {'adapter': RESOURCE})
        result['services'].append(row)
        with self.assertRaises(ValueError):validate_result(result, {'gateway': RESOURCE})

    def test_adapter_rejects_unapproved_before_credentials(self):
        with patch('inventory.access_token') as token:
            with self.assertRaises(PermissionError):asyncio.run(read_services({'inventory': {'resources': {}}}, ['unapproved']))
        token.assert_not_called()


class InventoryGatewayTests(unittest.TestCase):
    rpc = base.GatewayTests.rpc
    def setUp(self):
        base.GatewayTests.setUp(self)
        self.policy['roles'] = deepcopy(self.policy['roles'])
        for role in self.policy['roles'].values():
            role['tools'].append('cloud_run_inventory');role['inventory_resources'] = ['gateway']
        self.policy['inventory'] = {'resources': {'gateway': RESOURCE}, 'client_subject': 'internal-client-1', 'client_audience': 'https://inventory.internal'}
        self.client_check = patch.object(gateway, 'inventory_client_identity')
        self.client_check.start();self.addCleanup(self.client_check.stop)
        row = project_service(provider(), 'gateway', RESOURCE, NOW)
        self.adapter = patch.object(gateway, 'call_adapter', AsyncMock(return_value={'data': 'live-infrastructure', 'services': [row]}))
        self.mock_adapter = self.adapter.start();self.addCleanup(self.adapter.stop)

    def test_inventory_internal_result_has_real_names_no_inference(self):
        with patch.object(gateway, 'vertex_call') as model:
            r = self.rpc(params={'name': 'cloud_run_inventory'})
        self.assertEqual(r.status_code, 200)
        result = r.json()['result']['structuredContent']
        self.assertEqual(result['services'][0]['resourceId'], RESOURCE)
        self.assertFalse(result['modelAttempted'])
        self.assertEqual(result['releasePolicy'], 'approved-internal-client-only')
        model.assert_not_called()

    def test_expired_identity_denied_before_adapter(self):
        gateway.id_token.verify_oauth2_token.side_effect = ValueError('Token expired')
        r = self.rpc(params={'name': 'cloud_run_inventory'})
        self.assertEqual(r.status_code, 401)
        self.mock_adapter.assert_not_awaited()
        self.assertNotIn('Token expired', r.text)

    def test_health_distinguishes_live_and_synthetic_data(self):
        r = self.client.get('/health', headers=self.headers)
        self.assertEqual(r.json()['data'], 'mixed')
        self.assertEqual(r.json()['dataScopes']['cloudRunInventory'], 'live-infrastructure')

    def test_health_rejects_unbound_user(self):
        gateway.id_token.verify_oauth2_token.return_value = {'sub': 'unbound-user'}
        self.assertEqual(self.client.get('/health', headers=self.headers).status_code, 403)

    def test_inventory_hidden_without_client_attestation(self):
        result = self.rpc('tools/list').json()['result']
        self.assertNotIn('cloud_run_inventory', [x['name'] for x in result['tools']])

    def test_missing_attestation_denies_before_adapter(self):
        from fastapi import HTTPException
        gateway.inventory_client_identity.side_effect = HTTPException(401, 'Approved internal client required')
        self.assertEqual(self.rpc(params={'name': 'cloud_run_inventory'}).status_code, 401)
        self.mock_adapter.assert_not_awaited()

    def test_role_denied_before_adapter(self):
        self.policy['roles']['analyst']['tools'].remove('cloud_run_inventory')
        self.assertEqual(self.rpc(params={'name': 'cloud_run_inventory'}).status_code, 403)
        self.mock_adapter.assert_not_awaited()

    def test_resource_scope_denied_before_adapter(self):
        self.assertEqual(self.rpc(params={'name': 'cloud_run_inventory', 'arguments': {'services': ['unapproved']}}).status_code, 403)
        self.mock_adapter.assert_not_awaited()

    def test_registered_resource_still_requires_role_scope(self):
        self.policy['inventory']['resources']['adapter'] = RESOURCE.replace('pilot-gateway', 'pilot-adapter')
        self.assertEqual(self.rpc(params={'name': 'cloud_run_inventory', 'arguments': {'services': ['adapter']}}).status_code, 403)
        self.mock_adapter.assert_not_awaited()

    def test_external_destination_denied_before_adapter(self):
        r = self.rpc(params={'name': 'cloud_run_inventory', 'arguments': {'destination': 'external_model'}})
        self.assertEqual(r.json()['error']['code'], -32602)
        self.mock_adapter.assert_not_awaited()

    def test_inventory_never_enters_model_route(self):
        with patch.object(gateway, 'vertex_call') as model:
            r = self.client.post('/v1/answer', json={'name': 'cloud_run_inventory', 'arguments': {'destination': 'external_model'}}, headers=self.headers)
        self.assertEqual(r.status_code, 403)
        self.mock_adapter.assert_not_awaited();model.assert_not_called()

    def test_failure_no_partial_data_or_upstream_error(self):
        self.mock_adapter.side_effect = RuntimeError('DO_NOT_RELEASE')
        r = self.rpc(params={'name': 'cloud_run_inventory'})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn('DO_NOT_RELEASE', r.text)
        self.assertNotIn('resourceId', r.text)
