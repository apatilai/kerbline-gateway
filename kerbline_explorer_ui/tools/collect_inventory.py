#!/usr/bin/env python3
"""Read-only inventory collector. Uses customer-installed CLIs and credentials.

Never exports secret values, environment variables, Kubernetes Secrets or document data.
The output is an observation supplied by a customer, not console verification.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def run_json(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    except FileNotFoundError:
        raise ValueError(f'{command[0]} CLI is not installed')
    except subprocess.TimeoutExpired:
        raise ValueError(f'{command[0]} inventory request timed out')
    if result.returncode:
        # Do not copy CLI error output, which may include account/authentication details.
        raise ValueError(f'{command[0]} inventory request failed (exit {result.returncode}); check your CLI permissions and scope')
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ValueError(f'{command[0]} returned invalid JSON')


def component(resource_type, name):
    value = (resource_type + ' ' + name).lower()
    # Suggested mapping only. Imported mappings can be edited by registering the same resource ID.
    for words, target in [(('perception', 'phase-b', 'docling'), 'perception'), (('phase-c', 'codemode'), 'execution'),
                          (('cloudkms', 'keyvault', 'kms', 'vault'), 'vault'), (('cloudsql', 'sql/', 'rds', 'database', 'postgres'), 'database'),
                          (('audit', 'worm'), 'audit'), (('pubsub', 'queue', 'ingress'), 'ingress'),
                          (('aiplatform', 'vertex', 'model'), 'model'), (('console', 'explorer'), 'console')]:
        if any(word in value for word in words):
            return target
    return 'other'


def collect(provider, scope, region=None, namespace=None, name_contains='kerbline'):
    observed = timestamp()
    resources, notes = [], []
    if provider == 'gcp':
        items = run_json(['gcloud', 'asset', 'search-all-resources', f'--scope=projects/{scope}', '--format=json'])
        notes.append('Cloud Asset Inventory snapshot; supported indexed asset types only. Asset state is not service reachability. Component mappings are suggestions.')
        for item in items:
            name = item.get('displayName') or item.get('name', '').rsplit('/', 1)[-1]
            if name_contains.lower() not in (name + ' ' + item.get('name', '')).lower():
                continue
            kind = item.get('assetType', '')
            r = {'provider':provider, 'name':name, 'resourceId':item.get('name',''), 'account':scope,
                 'region':item.get('location',''), 'type':kind, 'runtimeStatus':item.get('state',''),
                 'component':component(kind,name), 'version':'', 'observedAt':observed}
            if kind == 'run.googleapis.com/Service':
                try:
                    service = run_json(['gcloud', 'run', 'services', 'describe', name, f'--project={scope}', f"--region={r['region']}", '--format=json(status,spec.template.spec.containers[0].image,spec.template.spec.containers[0].resources.limits,spec.template.spec.serviceAccountName)'])
                    status = service.get('status', {})
                    r['version'] = status.get('latestReadyRevisionName', '')
                    spec = service.get('spec', {}).get('template', {}).get('spec', {})
                    containers = spec.get('containers', [])
                    container = containers[0] if containers else {}
                    limits = container.get('resources', {}).get('limits', {})
                    r['configuration'] = {'image': container.get('image', ''), 'cpu': limits.get('cpu', ''), 'memory': limits.get('memory', ''),
                                          'runtimeIdentity': spec.get('serviceAccountName', ''), 'endpoint': status.get('url', '')}
                    ready = next((c for c in status.get('conditions', []) if c.get('type') == 'Ready'), {})
                    r['runtimeStatus'] = f"Provider Ready condition: {ready.get('status', 'Unknown')}"
                except ValueError:
                    notes.append(f'Cloud Run detail unavailable for {name}; inventory record retained without verified runtime details.')
            resources.append(r)
    elif provider == 'azure':
        items = run_json(['az','resource','list','--subscription',scope,'--output','json'])
        notes.append('Azure Resource Manager list snapshot. Provisioning state is not application health. Component mappings are suggestions.')
        for item in items:
            name=item.get('name','')
            if name_contains.lower() not in name.lower():
                continue
            kind=item.get('type','')
            resources.append({'provider':provider,'name':name,'resourceId':item.get('id',''),'account':scope,
                              'region':item.get('location',''),'type':kind,'component':component(kind,name),
                              'runtimeStatus':item.get('properties',{}).get('provisioningState',''),'observedAt':observed})
    elif provider == 'aws':
        if not region:
            raise ValueError('--region is required for AWS')
        identity=run_json(['aws','sts','get-caller-identity','--output','json'])
        if identity.get('Account') != scope:
            raise ValueError('AWS caller account does not match --scope; no inventory collected')
        payload=run_json(['aws','resourcegroupstaggingapi','get-resources','--region',region,'--output','json'])
        notes.append('AWS regional tagging API snapshot. Untagged resources and unsupported services may be absent. No runtime health is queried. Component mappings are suggestions.')
        for item in payload.get('ResourceTagMappingList',[]):
            arn=item.get('ResourceARN','')
            # Read only the Name tag. Do not export arbitrary tags.
            name=next((tag['Value'] for tag in item.get('Tags',[]) if tag.get('Key')=='Name'),arn.rsplit(':',1)[-1])
            if name_contains.lower() not in (name+' '+arn).lower():
                continue
            parts=arn.split(':')
            kind=parts[2] if len(parts)>2 else ''
            resources.append({'provider':provider,'name':name,'resourceId':arn,'account':scope,
                              'region':parts[3] if len(parts)>3 else region,'type':kind,'component':component(kind,name),'runtimeStatus':'','observedAt':observed})
    elif provider == 'kubernetes':
        if not namespace:
            raise ValueError('--namespace is required for Kubernetes')
        payload=run_json(['kubectl','--context',scope,'--namespace',namespace,'get','deployments,statefulsets,daemonsets,services,pods,persistentvolumeclaims','-o','json'])
        notes.append('Selected Kubernetes context and namespace only. Context name is not a verified cloud account. No Secrets, ConfigMaps, pod env or credentials are exported. Component mappings are suggestions.')
        for item in payload.get('items',[]):
            meta=item.get('metadata',{});name=meta.get('name','')
            if name_contains.lower() not in name.lower():
                continue
            kind=item.get('kind','');status=item.get('status',{});spec=item.get('spec',{})
            runtime=status.get('phase','')
            if kind in {'Deployment','StatefulSet'}:
                runtime=f"{status.get('readyReplicas',0)} / {spec.get('replicas',1)} replicas ready"
            elif kind=='DaemonSet':
                runtime=f"{status.get('numberReady',0)} / {status.get('desiredNumberScheduled',0)} scheduled pods ready"
            version=meta.get('annotations',{}).get('deployment.kubernetes.io/revision','')
            resources.append({'provider':provider,'name':name,'resourceId':f"{scope}/{namespace}/{kind}/{name}",'account':scope,
                              'region':'','type':kind,'component':component(kind,name),'version':version,'runtimeStatus':runtime,'observedAt':observed})
    else:
        raise ValueError('Unsupported provider')
    if len(resources)>500:
        raise ValueError('Over 500 matching resources. Use a narrower --name-contains filter; output was not truncated.')
    return {'source':f'Customer-run {provider} CLI collector (reported snapshot)','collectedAt':observed,
            'scope':scope,'nameFilter':name_contains,'coverageNotes':notes,'resources':resources}



def collect_cloud_run(scope, region, name_contains='kerbline'):
    if not region:
        raise ValueError('--region is required with --gcp-mode cloud-run')
    projection = 'json(metadata.name,metadata.namespace,metadata.labels,spec.template.spec.serviceAccountName,status.url,status.latestReadyRevisionName,status.conditions)'
    items = run_json(['gcloud', 'run', 'services', 'list', f'--project={scope}', f'--region={region}', f'--format={projection}'])
    resources, notes = [], ['Cloud Run service inventory in the selected project and region only. Other service types and locations are not covered. Provider Ready is not an end-to-end health check. Component mappings are suggestions.']
    observed = timestamp()
    for item in items:
        meta = item.get('metadata', {})
        name = meta.get('name', '')
        if name_contains.lower() not in name.lower():
            continue
        status = item.get('status', {})
        ready = next((c.get('status') for c in status.get('conditions', []) if c.get('type') == 'Ready'), 'Unknown')
        config = {'runtimeIdentity': item.get('spec', {}).get('template', {}).get('spec', {}).get('serviceAccountName', ''), 'endpoint': status.get('url', '')}
        try:
            runtime = run_json(['gcloud', 'run', 'services', 'describe', name, f'--project={scope}', f'--region={region}', '--format=json(spec.template.spec.containers[0].image,spec.template.spec.containers[0].resources.limits)'])
            containers = runtime.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
            container = containers[0] if containers else {}
            limits = container.get('resources', {}).get('limits', {})
            config.update(image=container.get('image', ''), cpu=limits.get('cpu', ''), memory=limits.get('memory', ''))
        except ValueError:
            notes.append(f'Runtime configuration unavailable for {name}; image and resource limits remain unknown.')
        resources.append({'provider': 'gcp', 'name': name, 'account': scope, 'region': region,
                          'resourceId': f'//run.googleapis.com/projects/{scope}/locations/{region}/services/{name}',
                          'type': 'run.googleapis.com/Service', 'component': component('run.googleapis.com/Service', name),
                          'version': status.get('latestReadyRevisionName', ''), 'runtimeStatus': f'Provider Ready condition: {ready}',
                          'observedAt': observed, 'configuration': config})
    if len(resources) > 500:
        raise ValueError('Over 500 matching services; narrow --name-contains. Output was not truncated.')
    return {'source': 'Customer-run GCP Cloud Run CLI collector (reported snapshot)', 'collectedAt': observed,
            'scope': scope, 'nameFilter': name_contains, 'coverageNotes': notes, 'resources': resources}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',choices=['gcp','azure','aws','kubernetes'],required=True)
    parser.add_argument('--scope',required=True,help='GCP project ID, Azure subscription, AWS account ID or Kubernetes context')
    parser.add_argument('--region');parser.add_argument('--namespace')
    parser.add_argument('--gcp-mode', choices=['cloud-asset','cloud-run'], default='cloud-asset', help='Use cloud-run for service runtime details without Cloud Asset Inventory')
    parser.add_argument('--name-contains',default='kerbline',help='Case-insensitive resource-name substring; default: kerbline')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        manifest=(collect_cloud_run(args.scope,args.region,args.name_contains) if args.provider=='gcp' and args.gcp_mode=='cloud-run'
                  else collect(args.provider,args.scope,args.region,args.namespace,args.name_contains))
        output=Path(args.output)
        # Exclusive creation avoids accidentally overwriting a reviewed inventory file.
        with output.open('x',encoding='utf-8') as handle:
            json.dump(manifest,handle,indent=2)
        print(f"Wrote {len(manifest['resources'])} resource observations to {output}. Review coverageNotes before importing.")
        if not manifest['resources']:
            print('No matching resources found. Check --scope and --name-contains. No resource health is implied.')
    except (ValueError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
