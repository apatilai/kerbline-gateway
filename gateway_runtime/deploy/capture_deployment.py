"""Capture only deployment metadata, never service environment or credentials."""
import json
from pathlib import Path
import subprocess
import sys


def capture(build_id):
    state = Path(__file__).resolve().parents[2] / 'tmp' / 'system-design'
    path = state / 'deployment.json'
    manifest = json.loads(path.read_text())
    def cli(*args):
        options = [] if any(a.startswith('--format=') for a in args) else ['--format=json']
        return json.loads(subprocess.run(['gcloud', *args, '--project=' + manifest['project'], *options],
                                        capture_output=True, text=True, check=True).stdout)
    observations = {}
    for kind in ['gateway', 'adapter']:
        result = cli('run', 'services', 'describe', 'kerbline-pilot-' + kind, '--region=' + manifest['region'],
                     '--format=json(metadata.name,metadata.annotations,spec.template.metadata.annotations,spec.template.spec.serviceAccountName,status.latestReadyRevisionName,status.url,status.traffic)')
        # Keep safe network/deployment annotations; omit operator account addresses.
        annotations = result['metadata'].get('annotations', {})
        result['metadata']['annotations'] = {k: v for k,v in annotations.items() if k.startswith('run.googleapis.com/')}
        observations[kind] = result
        manifest[kind + 'Revision'] = result['status']['latestReadyRevisionName']
    result = cli('builds', 'describe', build_id, '--format=json(results.images)')
    image = result['results']['images'][0]
    manifest.update(image=image['name'], imageDigest=image['digest'], immutableImage=image['name'].split(':')[0] + '@' + image['digest'])
    path.write_text(json.dumps(manifest, indent=2))
    (state / 'deployment-observations.json').write_text(json.dumps(observations, indent=2))
    print('Captured deployed revisions, network configuration and immutable image reference.')


if __name__ == '__main__':
    capture(sys.argv[1])
