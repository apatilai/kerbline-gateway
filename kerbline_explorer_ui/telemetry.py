"""Strict metadata-only telemetry contract and role projections. Never accept prompt text."""
from __future__ import annotations
import hashlib
import json
import math
import re
from datetime import datetime, timezone

ROLES = {
    'ap-clerk': ['queue'],
    'finance-controller': ['costs'],
    'cfo': ['costs'],
    'it-security-admin': ['traffic', 'connections', 'policies', 'infrastructure', 'audit'],
    'ciso-auditor': ['traffic', 'connections', 'audit'],
    'console-admin': ['overview', 'setup', 'traffic', 'infrastructure', 'queue', 'approvals', 'connections', 'policies', 'costs', 'audit', 'admin'],
}
SECURITY = {'console-admin', 'it-security-admin', 'ciso-auditor'}
FINANCE = {'console-admin', 'finance-controller', 'cfo'}
HOP_TYPES = {'INBOUND_CLIENT', 'OUTBOUND_INFERENCE', 'OUTBOUND_CUSTOMER_INFRA', 'FRAUD_GATE', 'INTERNAL'}
TRANSPORTS = {'UNKNOWN', 'LOCAL_IN_PROCESS', 'TLS', 'TLS_1_2', 'TLS_1_3', 'PRIVATE_LINK', 'EXPRESS_ROUTE', 'MTLS_WEBSOCKET'}
AUTH_MODES = {'UNKNOWN', 'NONE', 'OIDC_PKCE', 'PER_USER_OAUTH', 'OBO', 'SCOPED_SERVICE_ACCOUNT', 'MTLS', 'API_KEY'}
OUTCOMES = {'RUNNING', 'COMPLETED', 'NEEDS_APPROVAL', 'BLOCKED', 'ERROR'}
STAGES = {'BBox_VLM_Crop', 'Code_Mode_Gen', 'Repeat_Vendor_Fast_Path', 'Exception_Summary', 'Other'}
HEX = re.compile(r'^[a-fA-F0-9]{64}$')
SENSITIVE = re.compile(r'(?i)(?:bearer\s|authorization|eyJ[a-zA-Z0-9_-]{16}|-----BEGIN|\b\d{3}[ -]\d{3}\b|\b\d{3}[ -]?\d{3}[ -]?\d{3}\b|\b\d{4}[ -]?\d{5}[ -]?\d\b|(?:[a-f0-9]{0,4}:){2,}[a-f0-9:]+|\b\d{1,3}(?:\.\d{1,3}){3}\b|\b\d{6,12}\b|https?://|@)')


def keys(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('Telemetry contains unsupported fields; only metadata allow-list fields are accepted')


def label(value, required=False, limit=150):
    if value is None or value == '':
        if required:
            raise ValueError('Required telemetry identifier is missing')
        return None
    if not isinstance(value, str) or len(value) > limit or not re.fullmatch(r'[A-Za-z0-9_.:/ +\-\[\]]+', value) or SENSITIVE.search(value):
        raise ValueError('Telemetry identifier is invalid or may contain sensitive data')
    return value


def numeric(value, integer=False, maximum=1_000_000_000):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > maximum or (integer and not isinstance(value, int)):
        raise ValueError('Telemetry number must be finite, non-negative and within limits')
    return value


def flag(value):
    if value is not None and not isinstance(value, bool):
        raise ValueError('Telemetry flag must be true, false or null')
    return value


def choice(value, allowed, default=None):
    value = default if value is None else value
    if value not in allowed:
        raise ValueError('Unsupported telemetry state')
    return value


def stamp(value, required=False):
    if not value and not required:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or (result - datetime.now(timezone.utc)).total_seconds() > 300:
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Telemetry timestamps require ISO 8601 timezone-aware times, not in the future')
    return result.astimezone(timezone.utc).isoformat()


def digest(value):
    if value is None:
        return None
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('Expected SHA-256 hex digest')
    return value.lower()


def validate_trace(raw):
    keys(raw, ['scope', 'traceId', 'invoiceRef', 'sequence', 'startedAt', 'endedAt', 'outcome', 'route', 'coverage', 'hops', 'prompts', 'templateVerifiedCount', 'auditCorrelationId', 'siemCorrelationId', 'wormCorrelationId'])
    trace_id = label(raw.get('traceId'), True, 100)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{7,99}', trace_id):
        raise ValueError('traceId must be an opaque identifier starting with a letter (8–100 characters)')
    trace = {'scope': choice(raw.get('scope'), {'REQUEST', 'COMPONENT'}, 'COMPONENT'), 'traceId': trace_id, 'invoiceRef': label(raw.get('invoiceRef'), limit=100),
             'sequence': numeric(raw.get('sequence', 1), True), 'startedAt': stamp(raw.get('startedAt'), True),
             'endedAt': stamp(raw.get('endedAt')), 'outcome': choice(raw.get('outcome'), OUTCOMES),
             'route': choice(raw.get('route'), {'CODE_MODE', 'TEMPLATE_FAST_PATH', 'EXCEPTION', 'OTHER'}),
             'templateVerifiedCount': numeric(raw.get('templateVerifiedCount'), True)}
    if not trace['sequence']:
        raise ValueError('sequence must be positive')
    if trace['endedAt'] and trace['endedAt'] < trace['startedAt']:
        raise ValueError('Trace ends before it starts')
    coverage = raw.get('coverage', {})
    keys(coverage, ['complete', 'llmComplete', 'erpReadComplete', 'erpWriteComplete'])
    trace['coverage'] = {key: flag(coverage.get(key)) is True for key in ['complete', 'llmComplete', 'erpReadComplete', 'erpWriteComplete']}
    if trace['coverage']['complete'] and (not trace['endedAt'] or trace['outcome'] == 'RUNNING'):
        raise ValueError('Complete traces must have an end time and final outcome')
    for key in ['auditCorrelationId', 'siemCorrelationId', 'wormCorrelationId']:
        trace[key] = label(raw.get(key))
    hops = raw.get('hops', [])
    prompts = raw.get('prompts', [])
    if not isinstance(hops, list) or not isinstance(prompts, list) or len(hops) > 100 or len(prompts) > 100:
        raise ValueError('At most 100 hops and prompts per trace snapshot')
    trace['hops'], trace['prompts'] = [], []
    for hop in hops:
        keys(hop, ['hopId', 'hopType', 'targetSystem', 'targetKind', 'operation', 'region', 'transport', 'tunnelOrEndpointId', 'mtlsCertFingerprint', 'authMode', 'piiRedacted', 'detokenisedFieldCount', 'latencyMs', 'status', 'networkAttempted', 'policyVerdict'])
        safe = {key: label(hop.get(key), required=key in {'hopId', 'targetSystem'}) for key in ['hopId', 'targetSystem', 'region', 'tunnelOrEndpointId', 'policyVerdict']}
        safe.update(hopType=choice(hop.get('hopType'), HOP_TYPES),
                    targetKind=choice(hop.get('targetKind'), {'CLIENT', 'MODEL', 'ERP', 'STORAGE', 'INTERNAL', 'OTHER'}),
                    operation=choice(hop.get('operation'), {'READ', 'WRITE', 'INFERENCE', 'INGRESS', 'POLICY', 'OTHER'}),
                    transport=choice(hop.get('transport'), TRANSPORTS, 'UNKNOWN'), authMode=choice(hop.get('authMode'), AUTH_MODES, 'UNKNOWN'),
                    mtlsCertFingerprint=digest(hop.get('mtlsCertFingerprint')), piiRedacted=flag(hop.get('piiRedacted')),
                    detokenisedFieldCount=numeric(hop.get('detokenisedFieldCount'), True), latencyMs=numeric(hop.get('latencyMs')),
                    networkAttempted=flag(hop.get('networkAttempted')), status=choice(hop.get('status'), {'OK', 'BLOCKED_CALLBACK_REQUIRED', 'BLOCKED', 'ERROR', 'SKIPPED_FAST_PATH', 'NOT_ATTEMPTED', 'UNKNOWN'}, 'UNKNOWN'))
        if safe['hopType'] == 'OUTBOUND_INFERENCE' and (safe['targetKind'] != 'MODEL' or safe['operation'] != 'INFERENCE'):
            raise ValueError('Inference hops require a model target and inference operation')
        if safe['hopType'] == 'OUTBOUND_CUSTOMER_INFRA' and safe['targetKind'] == 'ERP' and safe['operation'] not in {'READ','WRITE','OTHER'}:
            raise ValueError('ERP network hops must identify read, write or unknown operation')
        if safe['hopType'] == 'FRAUD_GATE' and (safe['targetKind'] != 'INTERNAL' or safe['networkAttempted'] is not False):
            raise ValueError('Fraud gates are local policy decisions, not external network hops')
        if safe['transport'] == 'MTLS_WEBSOCKET' and not safe['mtlsCertFingerprint']:
            raise ValueError('mTLS transport requires a reported certificate fingerprint')
        trace['hops'].append(safe)
    for prompt in prompts:
        keys(prompt, ['promptId', 'stage', 'model', 'promptTokens', 'cachedTokens', 'completionTokens', 'reasoningTokens', 'totalTokens', 'gpuSeconds', 'estimatedCostAud', 'promptSha256', 'rateCardId'])
        safe = {'promptId': label(prompt.get('promptId'), True), 'stage': choice(prompt.get('stage'), STAGES),
                'model': label(prompt.get('model')), 'promptSha256': digest(prompt.get('promptSha256')), 'rateCardId': label(prompt.get('rateCardId'))}
        for key in ['promptTokens', 'cachedTokens', 'completionTokens', 'reasoningTokens', 'totalTokens']:
            safe[key] = numeric(prompt.get(key), True)
        for key in ['gpuSeconds', 'estimatedCostAud']:
            safe[key] = numeric(prompt.get(key))
        if safe['promptTokens'] is not None and safe['cachedTokens'] is not None and safe['cachedTokens'] > safe['promptTokens']:
            raise ValueError('Cached tokens are a subset of input tokens')
        if safe['completionTokens'] is not None and safe['reasoningTokens'] is not None and safe['reasoningTokens'] > safe['completionTokens']:
            raise ValueError('Reasoning tokens must be included in completionTokens')
        if all(safe[key] is not None for key in ['promptTokens', 'completionTokens', 'totalTokens']) and safe['totalTokens'] != safe['promptTokens'] + safe['completionTokens']:
            raise ValueError('totalTokens must equal input plus output; cache and reasoning are subsets')
        if safe['estimatedCostAud'] is not None and safe['stage'] != 'Repeat_Vendor_Fast_Path' and not safe['rateCardId']:
            raise ValueError('An estimated model cost requires a rate-card reference')
        if safe['stage'] == 'Repeat_Vendor_Fast_Path':
            if trace['route'] != 'TEMPLATE_FAST_PATH' or safe['model'] or any(safe[key] != 0 for key in ['promptTokens', 'cachedTokens', 'completionTokens', 'reasoningTokens', 'totalTokens', 'estimatedCostAud']):
                raise ValueError('Fast-path rows require explicit zero model usage, no model and zero model cost')
        trace['prompts'].append(safe)
    for collection, key in [('hops', 'hopId'), ('prompts', 'promptId')]:
        if len({item[key] for item in trace[collection]}) != len(trace[collection]):
            raise ValueError('Duplicate hop or prompt identifier')
    if trace['route'] == 'TEMPLATE_FAST_PATH':
        if (trace['templateVerifiedCount'] or 0) < 5 or not trace['coverage']['llmComplete'] or len(trace['prompts']) != 1 or trace['prompts'][0]['stage'] != 'Repeat_Vendor_Fast_Path' or any(h['hopType'] == 'OUTBOUND_INFERENCE' for h in trace['hops']):
            raise ValueError('Fast path requires verified count >=5, explicit zero-token row and complete model coverage with no inference hops')
    return trace


def summary(trace):
    prompts = trace['prompts']
    coverage = trace['coverage']
    complete = coverage['complete'] and bool(trace['endedAt'])
    request_scope = trace.get('scope') == 'REQUEST'
    erp_hops = [hop for hop in trace['hops'] if hop['hopType'] == 'OUTBOUND_CUSTOMER_INFRA' and hop['targetKind'] == 'ERP']
    unknown_erp = any(hop['networkAttempted'] is None for hop in erp_hops)
    attempts = [hop for hop in erp_hops if hop['networkAttempted'] is True]
    unknown_writes = any(hop['operation'] in {'WRITE', 'OTHER'} and hop['networkAttempted'] is None for hop in erp_hops)
    writes = [hop for hop in attempts if hop['operation'] in {'WRITE', 'OTHER'}]
    known_total = bool(prompts) and all(p['totalTokens'] is not None for p in prompts)
    known_cost = bool(prompts) and all(p['estimatedCostAud'] is not None for p in prompts)
    tokens = sum(p['totalTokens'] for p in prompts) if coverage['llmComplete'] and known_total else None
    cost = sum(p['estimatedCostAud'] for p in prompts) if coverage['llmComplete'] and known_cost else None
    return {'totalTokens': tokens, 'estimatedModelCostAud': cost, 'promptCount': len(prompts),
            'llmHops': sum(h['hopType'] == 'OUTBOUND_INFERENCE' and h['networkAttempted'] is True for h in trace['hops']) if coverage['llmComplete'] and not any(h['networkAttempted'] is None for h in trace['hops'] if h['hopType']=='OUTBOUND_INFERENCE') else None,
            'erpEgressCount': len(attempts) if coverage['erpReadComplete'] and coverage['erpWriteComplete'] and not unknown_erp else None,
            'erpWriteEgressCount': len(writes) if coverage['erpWriteComplete'] and not unknown_writes else None,
            'zeroErpEgress': request_scope and complete and coverage['erpReadComplete'] and coverage['erpWriteComplete'] and not unknown_erp and not attempts,
            'zeroErpWriteEgress': request_scope and complete and coverage['erpWriteComplete'] and not unknown_writes and not writes,
            'fastPath': trace['route'] == 'TEMPLATE_FAST_PATH', 'complete': complete,
            'latencyMs': round((datetime.fromisoformat(trace['endedAt']) - datetime.fromisoformat(trace['startedAt'])).total_seconds() * 1000, 3) if trace['endedAt'] else None,
            'scope': trace.get('scope'), 'regionEvidence': 'Reported only; residency not attested', 'costScope': 'Estimated model cost only; infrastructure excluded'}


def project_trace(trace, role):
    if role not in SECURITY | FINANCE:
        raise PermissionError('Trace access is not permitted')
    if role in SECURITY:
        return trace
    allowed = ['traceId', 'invoiceRef', 'startedAt', 'endedAt', 'outcome', 'route', 'templateVerifiedCount', 'summary', 'dataset', 'receivedAt']
    safe = {key: trace[key] for key in allowed if key in trace}
    safe['prompts'] = [{key: value for key, value in prompt.items() if key not in {'promptSha256'}} for prompt in trace['prompts']]
    # Even derived ERP network counts and topology metadata are security-only.
    safe['summary'] = {key: trace['summary'][key] for key in ['totalTokens', 'estimatedModelCostAud', 'promptCount', 'llmHops', 'fastPath', 'complete', 'latencyMs', 'costScope']}
    return safe
