"""Exercise the deployed front door without persisting or printing credentials."""
import json
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request

STATE = Path(__file__).resolve().parents[2] / 'tmp' / 'system-design'


def user_token():
    return subprocess.run(['gcloud', 'auth', 'print-identity-token'], capture_output=True, text=True, check=True).stdout.strip()


def request(url, payload=None, token=None, extra=None, method=None):
    headers = {'Accept': 'application/json, text/event-stream'}
    if token:
        headers.update({'Authorization': 'Bearer ' + token, 'X-Kerbline-User-Token': token})
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    headers.update(extra or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers=headers, method=method)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=70) as response:
            data = response.read().decode()
            return response.status, json.loads(data) if data else None, round((time.perf_counter()-start)*1000, 2)
    except urllib.error.HTTPError as response:
        # Error bodies can contain provider metadata. Persist only the status.
        return response.code, None, round((time.perf_counter()-start)*1000, 2)


def verify():
    manifest = json.loads((STATE / 'deployment.json').read_text())
    front = manifest['frontUrl']
    token = user_token()
    results = []
    def check(name, response, expected, validate=None):
        status, body, ms = response
        ok = status in expected and (validate(body) if validate else True)
        results.append({'name': name, 'passed': ok, 'httpStatus': status, 'elapsedMs': ms})
        print(name, 'PASS' if ok else 'FAIL', status)
        return body
    def rpc(method, params=None):
        return {'jsonrpc': '2.0', 'id': 1, 'method': method, **({'params': params} if params is not None else {})}
    check('front_requires_auth', request(front + '/mcp', rpc('tools/list')), [401, 403])
    check('gateway_rejects_anonymous_direct', request(manifest['gatewayUrl'] + '/mcp', rpc('tools/list')), [401, 403])
    check('gateway_rejects_direct_user_bypass', request(manifest['gatewayUrl'] + '/mcp', rpc('tools/list'), token), [401, 403])
    check('adapter_rejects_anonymous_direct', request(manifest['adapterUrl'] + '/mcp', rpc('tools/list')), [401, 403, 404])
    check('front_rejects_forged_jwt', request(front + '/mcp', rpc('tools/list'), 'not-a-valid-jwt'), [401, 403])
    check('app_revalidates_user', request(front + '/mcp', rpc('tools/list'), token, {'X-Kerbline-User-Token': 'forged'}), [401])
    check('origin_denied', request(front + '/mcp', rpc('tools/list'), token, {'Origin': 'https://unapproved.example'}), [403])
    check('initialize', request(front + '/mcp', rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'kerbline-verifier', 'version': '1'}}), token), [200], lambda x: x['result']['protocolVersion'] == '2025-06-18')
    check('tools_list', request(front + '/mcp', rpc('tools/list'), token), [200], lambda x: {t['name'] for t in x['result']['tools']} == {'invoice_summary', 'execute_plan'})
    check('write_tool_denied', request(front + '/mcp', rpc('tools/call', {'name': 'change_supplier_bank_details'}), token), [403])
    check('role_spoofing_denied', request(front + '/mcp', rpc('tools/call', {'name': 'invoice_summary', 'arguments': {'role': 'admin'}}), token), [200], lambda x: x.get('error', {}).get('code') == -32602)
    check('raw_field_request_denied', request(front + '/mcp', rpc('tools/call', {'name': 'invoice_summary', 'arguments': {'fields': ['bank_account']}}), token), [200], lambda x: x.get('error', {}).get('code') == -32602)
    check('arbitrary_code_denied', request(front + '/mcp', rpc('tools/call', {'name': 'execute_code', 'arguments': {}}), token), [403])
    def summary_valid(body):
        out = body['result']['structuredContent']
        return out['recordCount'] == 134 and out['groups'] == [{'currency': 'AUD', 'count': 134, 'totalCents': 1656441}] and not any(s in json.dumps(out) for s in ('bank_account','supplier_name','SYNTHETIC-ACCOUNT'))
    summary = check('live_adapter_and_release', request(front + '/mcp', rpc('tools/call', {'name': 'invoice_summary'}), token), [200], summary_valid)
    answer = check('explicit_global_synthetic_vertex_answer', request(front + '/v1/answer', {'name': 'invoice_summary'}, token), [200],
                   lambda x: x['answer'] == {'recordCount': 134, 'totalCents': 1656441, 'currency': 'AUD'} and x.get('usage', {}).get('promptTokenCount', 0) > 0 and x.get('modelLocation') == 'global')
    health = check('deployed_runtime', request(front + '/health', token=token), [200], lambda x: x['writes'] is False and x['arbitraryCode'] is False)
    report = {'observedAt': datetime.now(timezone.utc).isoformat(), 'deployment': manifest, 'dataset': 'synthetic; no customer systems or PII', 'tests': results,
              'passed': sum(x['passed'] for x in results), 'total': len(results),
              'summary': summary, 'answer': answer, 'health': health}
    (STATE / 'cloud-test-results.json').write_text(json.dumps(report, indent=2))
    if not all(x['passed'] for x in results):
        raise SystemExit(1)


if __name__ == '__main__':
    verify()
