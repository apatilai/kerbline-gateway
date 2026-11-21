"""Explicit isolated GCP pilot deployment. Run with the operator's gcloud identity.

Never prints tokens. Creates only kerbline-pilot named resources in the pinned test project.
"""
import base64
import json
from pathlib import Path
import subprocess
import sys

PROJECT = 'project-4f0a7dac-0503-4e87-abe'
REGION = 'australia-southeast1'
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT.parent / 'tmp' / 'system-design'
STATE.mkdir(parents=True, exist_ok=True)


def cli(*args, json_output=False):
    proc = subprocess.run(['gcloud', *args, '--project=' + PROJECT, '--quiet', '--format=json'], capture_output=True, text=True)
    if proc.returncode:
        # Do not include command arguments: later calls may include deployment config.
        raise RuntimeError(proc.stderr[-1800:])
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def sa(name):
    return f'{name}@{PROJECT}.iam.gserviceaccount.com'


def token_claims():
    token = subprocess.run(['gcloud', 'auth', 'print-identity-token'], capture_output=True, text=True, check=True).stdout.strip()
    return json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))


def environment(mode, policy):
    path = STATE / f'{mode}-env.json'
    path.write_text(json.dumps({'SERVICE_MODE': mode, 'DEPLOYMENT_REGION': REGION, 'KERBLINE_POLICY_JSON': json.dumps(policy)}))
    return path


def deploy_services(image):
    policy = json.loads((ROOT / 'policy.json').read_text())
    claims = token_claims()
    policy['user_audience'] = claims['aud']
    policy['subjects'] = {claims['sub']: {'role': 'analyst', 'tenant': policy['tenant']}}
    adapter = cli('run', 'deploy', 'kerbline-pilot-adapter', '--image=' + image, '--region=' + REGION,
                  '--service-account=' + sa('kerbline-pilot-adapter'), '--no-allow-unauthenticated',
                  '--ingress=internal',
                  '--min-instances=0', '--max-instances=1', '--memory=256Mi', '--cpu=1', '--concurrency=8',
                  '--timeout=30', '--env-vars-file=' + str(environment('adapter', policy)))
    adapter_url = adapter['status']['url']
    cli('run', 'services', 'add-iam-policy-binding', 'kerbline-pilot-adapter', '--region=' + REGION,
        '--member=serviceAccount:' + sa('kerbline-pilot-runtime'), '--role=roles/run.invoker')
    policy['adapter_url'] = adapter_url
    policy['version'] = 'pilot-readonly-2-synthetic-global'
    policy['vertex'] = {'enabled': True, 'project': PROJECT, 'region': 'global', 'model': 'gemini-3.8-flash', 'synthetic_only': True}
    gateway = cli('run', 'deploy', 'kerbline-pilot-gateway', '--image=' + image, '--region=' + REGION,
                  '--service-account=' + sa('kerbline-pilot-runtime'), '--no-allow-unauthenticated',
                  '--network=kerbline-pilot-vpc', '--subnet=kerbline-pilot-sydney', '--vpc-egress=all-traffic',
                  '--min-instances=0', '--max-instances=1', '--memory=256Mi', '--cpu=1', '--concurrency=8',
                  '--timeout=60', '--env-vars-file=' + str(environment('gateway', policy)))
    gateway_url = gateway['status']['url']
    cli('run', 'services', 'add-iam-policy-binding', 'kerbline-pilot-gateway', '--region=' + REGION,
        '--member=serviceAccount:' + sa('kerbline-pilot-front'), '--role=roles/run.invoker')
    policy['service_callers'] = {
        'gateway': cli('iam', 'service-accounts', 'describe', sa('kerbline-pilot-front'))['uniqueId'],
        'adapter': cli('iam', 'service-accounts', 'describe', sa('kerbline-pilot-runtime'))['uniqueId']}
    policy['service_audiences'] = {'gateway': gateway_url, 'adapter': adapter_url}
    # Publish workload audience bindings after Cloud Run has assigned service URLs.
    for service, mode in [('kerbline-pilot-adapter', 'adapter'), ('kerbline-pilot-gateway', 'gateway')]:
        result = cli('run', 'services', 'update', service, '--region=' + REGION,
                     '--env-vars-file=' + str(environment(mode, policy)))
        if mode == 'gateway':
            gateway = result
        else:
            adapter = result
    # The adapter also verifies the signed caller identity in application code.
    previous = json.loads((STATE / 'deployment.json').read_text()) if (STATE / 'deployment.json').exists() else {}
    manifest = {**previous, 'project': PROJECT, 'region': REGION, 'modelLocation': 'global', 'modelDataScope': 'synthetic only', 'image': image, 'adapterUrl': adapter_url,
                'gatewayUrl': gateway_url, 'gatewayRevision': gateway['status']['latestReadyRevisionName'],
                'adapterRevision': adapter['status']['latestReadyRevisionName'], 'userAudience': claims['aud'], 'issuer': claims['iss']}
    (STATE / 'deployment.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


def api_front():
    m = json.loads((STATE / 'deployment.json').read_text())
    config = {'swagger': '2.0', 'info': {'title': 'Kerbline read-only pilot', 'version': '0.1.0'},
              'schemes': ['https'], 'produces': ['application/json'],
              'securityDefinitions': {'google_user': {'type': 'oauth2', 'flow': 'implicit', 'authorizationUrl': '',
                  'x-google-issuer': m['issuer'], 'x-google-jwks_uri': 'https://www.googleapis.com/oauth2/v3/certs',
                  'x-google-audiences': m['userAudience']}}, 'security': [{'google_user': []}],
              'x-google-backend': {'address': m['gatewayUrl'], 'jwt_audience': m['gatewayUrl'], 'path_translation': 'APPEND_PATH_TO_ADDRESS', 'deadline': 60.0},
              'paths': {path: {method: {'operationId': name, 'responses': {'200': {'description': 'Success'}}}
                               for method, name in methods.items()} for path, methods in {
                  '/mcp': {'post': 'mcpPost', 'get': 'mcpGet'}, '/v1/answer': {'post': 'modelAnswer'}, '/health': {'get': 'health'}}.items()}}
    path = STATE / 'openapi.json'
    path.write_text(json.dumps(config, indent=2))
    cli('api-gateway', 'apis', 'create', 'kerbline-pilot-api')
    cli('api-gateway', 'api-configs', 'create', 'pilot-v1', '--api=kerbline-pilot-api', '--openapi-spec=' + str(path),
        '--backend-auth-service-account=' + sa('kerbline-pilot-front'))
    front = cli('api-gateway', 'gateways', 'create', 'kerbline-pilot-front', '--api=kerbline-pilot-api',
                '--api-config=pilot-v1', '--location=' + REGION)
    m = json.loads((STATE / 'deployment.json').read_text())
    m['frontUrl'] = 'https://' + front['defaultHostname']
    (STATE / 'deployment.json').write_text(json.dumps(m, indent=2))
    print('API Gateway:', m['frontUrl'])


if __name__ == '__main__':
    if sys.argv[1] == 'services':
        deploy_services(sys.argv[2])
    elif sys.argv[1] == 'front':
        api_front()
