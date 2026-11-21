"""Publish approved inventory policy and update only the existing pilot services."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from cloud_pilot import cli, environment, PROJECT, REGION, sa
from inventory_client import AUDIENCE, CLIENT, call

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'tmp/system-design'


def deploy(image):
    saved = json.loads((STATE / 'gateway-env.json').read_text())
    current = saved['KERBLINE_POLICY_JSON']
    user = subprocess.run(['gcloud', 'auth', 'print-identity-token'], capture_output=True, text=True, check=True).stdout.strip()
    status, body = call(None, path='/health', user=user)
    if status != 200 or body.get('policyDigest') != hashlib.sha256(current.encode()).hexdigest():
        raise RuntimeError('Live policy differs from saved configuration; refusing to replace user bindings')
    # Preserve signed user bindings and workload audiences from the established pilot.
    policy = json.loads(current)
    policy['version'] = 'pilot-readonly-3-gcp-inventory'
    resources = {name: f'projects/{PROJECT}/locations/{REGION}/services/kerbline-pilot-{name}' for name in ['gateway', 'adapter']}
    policy['inventory'] = {'resources': resources, 'client_subject': cli('iam', 'service-accounts', 'describe', CLIENT)['uniqueId'],
                           'client_audience': AUDIENCE}
    for name, scopes in [('analyst', ['gateway', 'adapter']), ('auditor', ['gateway'])]:
        role = policy['roles'][name]
        role['tools'] = list(dict.fromkeys([*role['tools'], 'cloud_run_inventory']))
        role['inventory_resources'] = scopes
    revisions = {}
    for service, mode in [('kerbline-pilot-adapter', 'adapter'), ('kerbline-pilot-gateway', 'gateway')]:
        result = cli('run', 'services', 'update', service, '--region=' + REGION, '--image=' + image,
                     '--env-vars-file=' + str(environment(mode, policy)))
        revisions[mode] = result['status']['latestReadyRevisionName']
        print(mode, revisions[mode], flush=True)
    manifest_path = STATE / 'deployment.json'
    manifest = json.loads(manifest_path.read_text())
    manifest.update(image=image, gatewayRevision=revisions['gateway'], adapterRevision=revisions['adapter'],
                    inventoryClient=CLIENT, inventoryClientAudience=AUDIENCE, inventoryResources=resources)
    # A later capture_deployment records the resolved digest; do not preserve an old digest.
    manifest.pop('immutableImage', None);manifest.pop('imageDigest', None)
    manifest_path.write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    deploy(sys.argv[1])
