"""Trusted local operator client. No bearer credential reaches the browser or disk."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'tmp/inventory'
CLIENT = 'kerbline-inventory-client@project-4f0a7dac-0503-4e87-abe.iam.gserviceaccount.com'
AUDIENCE = 'https://kerbline.internal/inventory'


def tokens():
    user = subprocess.run(['gcloud', 'auth', 'print-identity-token'], capture_output=True, text=True, check=True).stdout.strip()
    # Call generateIdToken directly: gcloud impersonation also requests an access
    # token, which would need a broader grant than this identity-only client uses.
    access = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, check=True).stdout.strip()
    req = urllib.request.Request('https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' + CLIENT + ':generateIdToken',
                                 data=json.dumps({'audience': AUDIENCE, 'includeEmail': False}).encode(),
                                 headers={'Authorization': 'Bearer ' + access, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as response:
        client = json.load(response)['token']
    return user, client


def call(payload, *, user, client=None, path='/mcp', extra=None):
    manifest = json.loads((ROOT / 'tmp/system-design/deployment.json').read_text())
    headers = {'Authorization': 'Bearer ' + user, 'X-Kerbline-User-Token': user,
               'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    if client:
        headers['X-Kerbline-Client-Token'] = client
    headers.update(extra or {})
    req = urllib.request.Request(manifest['frontUrl'] + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def snapshot():
    STATE.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    request_id = str(uuid.uuid4())
    status = None
    account = subprocess.run(['gcloud', 'config', 'get-value', 'account'], capture_output=True, text=True).stdout.strip()
    event = {'requestId': request_id, 'startedAt': now, 'tool': 'cloud_run_inventory',
             'requester': account or 'Unknown', 'requesterSource': 'Local gcloud account configuration; not server-verified attribution',
             'client': CLIENT, 'requestedScope': 'Configured approved Cloud Run services', 'evidenceSource': 'Local operator client'}
    try:
        user, client = tokens()
        status, body = call({'jsonrpc': '2.0', 'id': 'inventory-view', 'method': 'tools/call',
                             'params': {'name': 'cloud_run_inventory', 'arguments': {}}}, user=user, client=client)
        if status != 200 or not body or body.get('error') or body.get('result', {}).get('isError'):
            raise ValueError('Inventory request rejected')
        result = body['result']['structuredContent']
        if result.get('data') != 'live-infrastructure' or result.get('modelAttempted') is not False:
            raise ValueError('Invalid inventory response')
        result['fetchedAt'] = datetime.now(timezone.utc).isoformat()
        write('latest.json', result)
        write('latest-attempt.json', {'observedAt': now, 'success': True})
        record({**event, 'completedAt': result['fetchedAt'], 'outcome': 'ALLOWED', 'httpStatus': status,
                'traceId': result.get('traceId'), 'policyVersion': result.get('policyVersion'),
                'releasePolicy': result.get('releasePolicy'), 'modelAttempted': result.get('modelAttempted'),
                'resources': [x['resourceId'] for x in result['services']],
                'releasedFields': sorted(set(k for x in result['services'] for k in x)),
                'policyEvidence': 'Successful governed response; individual policy-stage timings are not emitted'})
        print('Fetched approved live Cloud Run metadata; no model request made.')
        return result
    except Exception:
        record({**event, 'completedAt': datetime.now(timezone.utc).isoformat(),
                'outcome': 'DENIED' if status in (401, 403) else 'FAILED', 'httpStatus': status,
                'resources': [], 'releasedFields': [], 'policyEvidence': 'No successful release received by this client; rejection stage not available'})
        write('latest-attempt.json', {'observedAt': now, 'success': False,
                                     'error': 'Inventory refresh failed; saved observations have not been replaced.'})
        raise RuntimeError('Inventory refresh failed; inspect identities, approved resource grants and service health') from None


def record(event):
    target = STATE / 'requests.json'
    try:
        history = json.loads(target.read_text())
    except (FileNotFoundError, ValueError):
        history = []
    write('requests.json', (history + [event])[-100:])


def write(name, value):
    target = STATE / name
    temp = target.with_suffix('.new')
    temp.write_text(json.dumps(value, indent=2))
    os.replace(temp, target)


if __name__ == '__main__':
    snapshot()
