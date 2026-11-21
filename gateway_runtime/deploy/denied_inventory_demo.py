"""Read-only real policy denial demonstration; never requests an approved resource."""
from datetime import datetime, timezone
import subprocess
import uuid
from inventory_client import tokens, call, record, STATE

if __name__ == '__main__':
    STATE.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    user, client = tokens()
    status, _ = call({'jsonrpc': '2.0', 'id': 'denied-demo', 'method': 'tools/call',
                      'params': {'name': 'cloud_run_inventory', 'arguments': {'services': ['unapproved-demo']}}}, user=user, client=client)
    account = subprocess.run(['gcloud', 'config', 'get-value', 'account'], capture_output=True, text=True).stdout.strip()
    record({'requestId': str(uuid.uuid4()), 'startedAt': started, 'completedAt': datetime.now(timezone.utc).isoformat(),
            'requester': account or 'Unknown', 'requesterSource': 'Local gcloud account configuration; not server-verified attribution',
            'tool': 'cloud_run_inventory', 'outcome': 'DENIED' if status == 403 else 'UNEXPECTED', 'httpStatus': status,
            'resources': [], 'releasedFields': [], 'policyEvidence': 'Explicit unapproved resource alias requested; expected HTTP 403',
            'evidenceSource': 'Live policy denial demonstration', 'requestedScope': 'unapproved-demo'})
    if status != 403:
        raise SystemExit('Expected policy denial was not observed')
    print('Live unapproved-resource request denied; no resource payload received.')
