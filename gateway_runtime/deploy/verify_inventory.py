"""Live security and projection checks; no model request and no stored credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path

from inventory_client import tokens, call, snapshot

STATE = Path(__file__).resolve().parents[2] / 'tmp/inventory'


def verify():
    user, client = tokens()
    checks = []
    def rpc(method='tools/call', arguments=None):
        return {'jsonrpc': '2.0', 'id': 'inventory-check', 'method': method,
                **({'params': {'name': 'cloud_run_inventory', 'arguments': arguments or {}}} if method == 'tools/call' else {})}
    def check(name, response, codes, predicate=None):
        code, body = response
        success = code in codes and (predicate(body) if predicate else True)
        checks.append({'name': name, 'passed': success, 'httpStatus': code})
        print(name, 'PASS' if success else 'FAIL', code, flush=True)
        return body
    check('health_identifies_live_scope_and_disabled_writes', call(None, user=user, path='/health'), [200],
          lambda b: b.get('dataScopes', {}).get('cloudRunInventory') == 'live-infrastructure' and b.get('writes') is False and b.get('arbitraryCode') is False)
    check('hidden_from_unattested_client', call(rpc('tools/list'), user=user), [200],
          lambda b: 'cloud_run_inventory' not in [t['name'] for t in b['result']['tools']])
    check('visible_to_trusted_internal_client', call(rpc('tools/list'), user=user, client=client), [200],
          lambda b: 'cloud_run_inventory' in [t['name'] for t in b['result']['tools']])
    check('missing_client_identity_denied', call(rpc(), user=user), [401])
    spoofed = rpc();spoofed['params']['_meta'] = {'trustedClient': True, 'role': 'admin'}
    check('metadata_cannot_attest_internal_client', call(spoofed, user=user), [401])
    check('forged_client_identity_denied', call(rpc(), user=user, client='forged'), [401])
    check('user_token_cannot_be_client_attestation', call(rpc(), user=user, client=user), [401, 403])
    check('unapproved_resource_denied', call(rpc(arguments={'services': ['unapproved']}), user=user, client=client), [403])
    check('arbitrary_project_or_url_denied', call(rpc(arguments={'project': 'other', 'url': 'https://unapproved.example'}), user=user, client=client), [200],
          lambda b: b.get('error', {}).get('code') == -32602)
    check('external_destination_denied', call(rpc(arguments={'destination': 'external_model'}), user=user, client=client), [200],
          lambda b: b.get('error', {}).get('code') == -32602)
    check('real_inventory_blocked_from_model_route', call({'name': 'cloud_run_inventory', 'arguments': {'destination': 'external_model'}},
                                                        user=user, client=client, path='/v1/answer'), [403])
    def valid(body):
        result = body['result']['structuredContent']
        rows = result['services']
        return (result['modelAttempted'] is False and result['data'] == 'live-infrastructure' and len(rows) == 2
                and {x['alias'] for x in rows} == {'adapter', 'gateway'}
                and all(x['region'] == 'australia-southeast1' and x['activeInstanceCount'] is None and x['activeRevisions'] for x in rows)
                and not any(s in json.dumps(result) for s in ('KERBLINE_POLICY_JSON', 'env', 'Authorization', 'containers', 'serviceAccountName')))
    body = check('approved_live_metadata_projected', call(rpc(), user=user, client=client), [200], valid)
    selected = check('explicit_resource_subset', call(rpc(arguments={'services': ['gateway']}), user=user, client=client), [200],
                     lambda b: len(b['result']['structuredContent']['services']) == 1 and b['result']['structuredContent']['services'][0]['alias'] == 'gateway')
    check('client_role_override_denied', call(rpc(arguments={'role': 'admin'}), user=user, client=client), [200],
          lambda b: b.get('error', {}).get('code') == -32602)
    # Separate fresh UI snapshot goes through the same signed MCP path.
    if all(x['passed'] for x in checks):
        snapshot()
    report = {'observedAt': datetime.now(timezone.utc).isoformat(), 'tests': checks, 'passed': sum(x['passed'] for x in checks),
              'total': len(checks), 'modelRequestsMade': 0, 'result': body['result']['structuredContent'] if body and body.get('result') else None}
    (STATE / 'cloud-tests.json').write_text(json.dumps(report, indent=2))
    if report['passed'] != report['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    verify()
