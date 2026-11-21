"""Read approved Cloud Run resources and project only non-secret operational fields."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import Annotated, Literal

import google.auth
from google.auth.transport.requests import Request as GoogleRequest
import httpx
from pydantic import BaseModel, ConfigDict, Field

TOOL = 'cloud_run_inventory'
RESOURCE = re.compile(r'^projects/[a-z][a-z0-9-]{4,62}/locations/[a-z]+-[a-z]+[0-9]/services/[a-z][a-z0-9-]{0,62}$')
ALIAS = re.compile(r'^[a-z][a-z0-9_-]{0,39}$')
FIELDS = 'name,generation,observedGeneration,terminalCondition(state),latestReadyRevision,latestCreatedRevision,trafficStatuses(revision,percent),reconciling,ingress,updateTime'


class InventoryArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    destination: Literal['internal_app'] = 'internal_app'
    services: list[Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')]] | None = Field(default=None, min_length=1, max_length=10)


class InventoryCall(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: Literal['cloud_run_inventory']
    arguments: InventoryArguments = Field(default_factory=InventoryArguments)
    meta: dict | None = Field(default=None, alias='_meta')


def permitted_resources(policy, role, requested):
    registry = policy.get('inventory', {}).get('resources', {})
    allowed = policy['roles'][role].get('inventory_resources', [])
    selected = list(dict.fromkeys(allowed if requested is None else requested))
    if not selected or len(selected) > 10 or any(alias not in allowed or alias not in registry for alias in selected):
        raise PermissionError('Resource scope denied')
    result = {}
    for alias in selected:
        name = registry[alias]
        if not ALIAS.fullmatch(alias) or not isinstance(name, str) or not RESOURCE.fullmatch(name):
            raise ValueError('Invalid approved resource configuration')
        result[alias] = name
    return result


def revision(value, resource):
    if value is None:
        return None
    prefix = resource + '/revisions/'
    if not isinstance(value, str):
        raise ValueError('Revision outside approved resource')
    short = value[len(prefix):] if value.startswith(prefix) else value
    if not short.startswith(resource.split('/')[-1] + '-') or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', short):
        raise ValueError('Invalid revision identifier')
    return short


def project_service(raw, alias, resource, observed_at):
    if not isinstance(raw, dict) or raw.get('name') != resource:
        raise ValueError('Cloud resource identity mismatch')
    state = raw.get('terminalCondition', {}).get('state')
    reconciled = (raw.get('generation') is not None and raw.get('generation') == raw.get('observedGeneration') and raw.get('reconciling') is not True)
    readiness = ('READY' if state == 'CONDITION_SUCCEEDED' and reconciled else
                 'NOT_READY' if state == 'CONDITION_FAILED' else
                 'RECONCILING' if raw.get('reconciling') is True else 'UNKNOWN')
    traffic = raw.get('trafficStatuses')
    active = []
    if traffic is not None:
        if not isinstance(traffic, list) or len(traffic) > 100:
            raise ValueError('Invalid traffic contract')
        for target in traffic:
            percent = target.get('percent')
            # Missing percentages are unknown, never guessed from latestReadyRevision.
            if percent is None:
                continue
            if type(percent) is not int or not 0 <= percent <= 100:
                raise ValueError('Invalid traffic percentage')
            if percent:
                active.append({'revision': revision(target.get('revision'), resource), 'percent': percent})
                if active[-1]['revision'] is None:
                    raise ValueError('Traffic target has no revision')
        if sum(x['percent'] for x in active) > 100:
            raise ValueError('Inconsistent traffic allocation')
    ingress = raw.get('ingress')
    if ingress not in {'INGRESS_TRAFFIC_ALL', 'INGRESS_TRAFFIC_INTERNAL_ONLY', 'INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER'}:
        ingress = 'UNKNOWN'
    updated = raw.get('updateTime')
    if updated is not None:
        parsed = datetime.fromisoformat(updated.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Missing update timezone')
        updated = parsed.isoformat()
    parts = resource.split('/')
    return {'alias': alias, 'name': parts[-1], 'resourceId': resource, 'project': parts[1], 'region': parts[3],
            'readiness': readiness, 'readinessSource': 'Cloud Run control plane; not an application health probe',
            'activeRevisions': active, 'trafficComplete': traffic is not None and sum(x['percent'] for x in active) == 100,
            'latestReadyRevision': revision(raw.get('latestReadyRevision'), resource),
            'latestCreatedRevision': revision(raw.get('latestCreatedRevision'), resource),
            'ingress': ingress, 'updatedAt': updated, 'observedAt': observed_at,
            'activeInstanceCount': None, 'instanceCountStatus': 'Not queried; ready services may scale to zero'}


def access_token():
    creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
    creds.refresh(GoogleRequest())
    return creds.token


async def read_services(policy, aliases):
    registry = policy.get('inventory', {}).get('resources', {})
    if not aliases or len(aliases) > 10 or any(alias not in registry for alias in aliases):
        raise PermissionError('Adapter resource scope denied')
    # The adapter also pins exact resources; it never enumerates a project or follows caller URLs.
    resources = {}
    for alias in aliases:
        resource = registry[alias]
        if not isinstance(resource, str) or not RESOURCE.fullmatch(resource):
            raise ValueError('Invalid resource configuration')
        resources[alias] = resource
    token = await asyncio.to_thread(access_token)
    observed = datetime.now(timezone.utc).isoformat()
    results = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for alias, resource in resources.items():
            async with client.stream('GET', 'https://run.googleapis.com/v2/' + resource,
                                     params={'fields': FIELDS}, headers={'Authorization': 'Bearer ' + token}) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 262_144:
                        raise ValueError('Cloud response too large')
            import json
            results.append(project_service(json.loads(data), alias, resource, observed))
    return {'data': 'live-infrastructure', 'services': results, 'observedAt': observed}


def validate_result(result, approved):
    """Validate the second trust boundary; do not relay unknown adapter fields."""
    if not isinstance(result, dict) or result.get('data') != 'live-infrastructure':
        raise ValueError('Invalid inventory envelope')
    entries = result.get('services')
    if not isinstance(entries, list) or len(entries) != len(approved):
        raise ValueError('Incomplete approved inventory')
    safe = []
    seen = set()
    for entry in entries:
        alias = entry.get('alias')
        if alias not in approved or alias in seen or entry.get('resourceId') != approved[alias]:
            raise ValueError('Resource outside user scope')
        seen.add(alias)
        # Reconstruct a provider-shaped subset and reuse the same identifier/enum validation.
        resource = approved[alias]
        prefix = resource + '/revisions/'
        def full(v):
            return prefix + v if isinstance(v, str) else v
        readiness = entry.get('readiness')
        if readiness not in {'READY', 'NOT_READY', 'RECONCILING', 'UNKNOWN'}:
            raise ValueError('Invalid readiness state')
        targets = entry.get('activeRevisions')
        if not isinstance(targets, list):
            raise ValueError('Invalid traffic projection')
        observed = entry.get('observedAt')
        if not isinstance(observed, str) or datetime.fromisoformat(observed).tzinfo is None:
            raise ValueError('Invalid observation timestamp')
        raw = {'name': resource,
               'trafficStatuses': [{'revision': full(t.get('revision')), 'percent': t.get('percent')} for t in targets],
               'latestReadyRevision': full(entry.get('latestReadyRevision')), 'latestCreatedRevision': full(entry.get('latestCreatedRevision')),
               'ingress': entry.get('ingress'), 'updateTime': entry.get('updatedAt')}
        projected = project_service(raw, alias, resource, observed)
        projected['readiness'] = readiness
        if entry.get('trafficComplete') is not True:
            projected['trafficComplete'] = False
        safe.append(projected)
    return {'data': 'live-infrastructure', 'services': safe}
