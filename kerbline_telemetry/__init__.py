"""Bounded asynchronous metadata exporter. Never queues request bodies or credentials.

Transport overhead is not guaranteed; benchmark in the deployment environment.
Delivery is best-effort with visible dropped/failed counters, not an audit ledger.
"""
from __future__ import annotations
import atexit
from contextvars import ContextVar
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a producer credential to a redirect target.


class Exporter:
    def __init__(self, url=None, token=None, sink=None, capacity=256):
        self.url = url or os.getenv('KERBLINE_TELEMETRY_URL')
        self.token = token or os.getenv('KERBLINE_TELEMETRY_TOKEN')
        self.sink = sink or os.getenv('KERBLINE_TELEMETRY_FILE')
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}):
                raise ValueError('Telemetry URL must use HTTPS or loopback HTTP')
            if parsed.username or parsed.password or parsed.query or parsed.fragment or not self.token:
                raise ValueError('Telemetry endpoint requires a separate token and no URL credentials/query/fragment')
        self.enabled = bool(self.sink or self.url)
        self.pending = queue.Queue(maxsize=capacity)
        self.dropped = self.failed = self.delivered = 0
        self._stop = threading.Event()
        self._thread = None
        if self.enabled:
            self._thread = threading.Thread(target=self._work, daemon=True, name='kerbline-telemetry')
            self._thread.start()
            atexit.register(self.close)

    def emit(self, trace):
        if not self.enabled:
            return False
        # Only accept our metadata builder. Arbitrary dictionaries cannot enter the queue.
        if not isinstance(trace, Trace):
            raise ValueError('Use the metadata-only Trace builder')
        try:
            self.pending.put_nowait(trace.snapshot())
            return True
        except (queue.Full, ValueError, TypeError):
            self.dropped += 1
            return False

    def _work(self):
        opener = build_opener(NoRedirect)
        while not self._stop.is_set() or not self.pending.empty():
            try:
                item = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                body = json.dumps(item, allow_nan=False).encode()
                if self.sink:
                    path = Path(self.sink)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                    with os.fdopen(descriptor, 'ab') as handle:
                        handle.write(body + b'\n')
                if self.url:
                    request = Request(self.url, data=body, method='POST', headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
                    last_error = None
                    for attempt in range(3):
                        try:
                            with opener.open(request, timeout=3) as response:
                                if response.status != 200:
                                    raise ValueError('Collector rejected telemetry')
                            last_error = None
                            break
                        except Exception as exc:
                            last_error = exc
                            if attempt < 2:
                                self._stop.wait(0.1 * (attempt + 1))
                    if last_error:
                        raise ValueError('Telemetry delivery failed')
                self.delivered += 1
            except Exception:
                self.failed += 1
                # Do not log request/response errors: they may contain secrets or payload data.
            finally:
                self.pending.task_done()

    def flush(self, timeout=5):
        end = time.monotonic() + timeout
        while self.pending.unfinished_tasks and time.monotonic() < end:
            self._stop.wait(0.01)
        return self.pending.unfinished_tasks == 0

    def close(self):
        self._stop.set()
        if self._thread and threading.current_thread() is not self._thread:
            self._thread.join(timeout=0.25)

    def health(self):
        return {'enabled': self.enabled, 'queued': self.pending.qsize(), 'dropped': self.dropped, 'failed': self.failed, 'delivered': self.delivered,
                'delivery': 'Best effort; process termination or queue overflow can lose events'}


# Labels are fixed identifiers or safe metadata. Hash raw prompts at the call site, never retain them.
def safe_label(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 150 or not re.fullmatch(r'[A-Za-z0-9_.:/ +\-\[\]]+', value) or re.search(r'(?i)(bearer|authorization|\b\d{3}[ -]\d{3}\b|\b\d{3}[ -]?\d{3}[ -]?\d{3}\b|\b\d{4}[ -]?\d{5}[ -]?\d\b|(?:[a-f0-9]{0,4}:){2,}[a-f0-9:]+|\b\d{1,3}(?:\.\d{1,3}){3}\b|\b\d{6,12}\b|https?://|@|eyJ[a-zA-Z0-9_-]{16})', value):
        raise ValueError('Unsafe telemetry label')
    return value


def metadata_operation(function):
    def wrapped(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception:
            # Telemetry must never turn a valid business response into an error.
            # Incomplete collection also disables all zero-egress completeness claims.
            self.collection_failed = True
            return False
    return wrapped


class Trace:
    def __init__(self, route='OTHER', scope='COMPONENT', invoice_ref=None):
        self.collection_failed = False
        self.data = {'scope': safe_label(scope), 'traceId': 'tr_' + secrets.token_hex(16), 'sequence': 1, 'startedAt': timestamp(), 'endedAt': None,
                     'route': safe_label(route), 'outcome': 'RUNNING', 'coverage': {'complete': False, 'llmComplete': False, 'erpReadComplete': False, 'erpWriteComplete': False}, 'hops': [], 'prompts': []}
        if invoice_ref:
            try:
                self.data['invoiceRef'] = safe_label(invoice_ref)
            except ValueError:
                self.data['invoiceRef'] = 'inv_' + hashlib.sha256(str(invoice_ref).encode()).hexdigest()[:16]

    @metadata_operation
    def hop(self, hop_type, target, target_kind, operation, *, status='UNKNOWN', latency_ms=None, network_attempted=None, region=None,
            transport='UNKNOWN', auth_mode='UNKNOWN', pii_redacted=None, detokenised_fields=None, policy_verdict=None,
            tunnel_or_endpoint_id=None, mtls_cert_fingerprint=None):
        if mtls_cert_fingerprint is not None and (not isinstance(mtls_cert_fingerprint,str) or not re.fullmatch(r'[a-fA-F0-9]{64}',mtls_cert_fingerprint)):
            raise ValueError('Invalid certificate fingerprint')
        if transport=='MTLS_WEBSOCKET' and not mtls_cert_fingerprint:
            raise ValueError('mTLS requires an observed certificate fingerprint')
        self.data['hops'].append({'hopId': 'hop_' + secrets.token_hex(8), 'hopType': safe_label(hop_type), 'targetSystem': safe_label(target),
                                 'targetKind': safe_label(target_kind), 'operation': safe_label(operation), 'status': safe_label(status),
                                 'latencyMs': latency_ms, 'networkAttempted': network_attempted, 'region': safe_label(region),
                                 'transport': safe_label(transport), 'authMode': safe_label(auth_mode), 'piiRedacted': pii_redacted,
                                 'detokenisedFieldCount': detokenised_fields, 'policyVerdict': safe_label(policy_verdict), 'tunnelOrEndpointId': safe_label(tunnel_or_endpoint_id),
                                 'mtlsCertFingerprint': mtls_cert_fingerprint.lower() if mtls_cert_fingerprint else None})

    @metadata_operation
    def usage(self, stage, model, prompt_sha256, usage=None, *, provider='openai-compatible', rate_card=None):
        usage = usage or {}
        if not re.fullmatch(r'[a-f0-9]{64}', prompt_sha256):
            raise ValueError('Only prompt SHA-256 is accepted')
        if provider == 'vertex':
            inp = usage.get('promptTokenCount'); candidates = usage.get('candidatesTokenCount'); reasoning = usage.get('thoughtsTokenCount')
            out = candidates + (reasoning or 0) if candidates is not None else None
            cached = usage.get('cachedContentTokenCount')
            total = usage.get('totalTokenCount')
        else:
            inp, out, total = usage.get('prompt_tokens'), usage.get('completion_tokens'), usage.get('total_tokens')
            cached = usage.get('prompt_tokens_details', {}).get('cached_tokens')
            reasoning = usage.get('completion_tokens_details', {}).get('reasoning_tokens')
        # Provider totals with unsupported categories are unknown rather than silently double-counted.
        if inp is not None and out is not None:
            total = inp + out if total is None or total == inp + out else None
        rate_card = rate_card or RATE_CARDS.get(model)
        estimated_cost = None
        rate_id = None
        if rate_card and inp is not None and out is not None and cached is not None:
            rate_id = safe_label(rate_card['id'])
            rates = [Decimal(str(rate_card[key])) for key in ['inputPerMillionAud', 'cachedPerMillionAud', 'outputPerMillionAud']]
            if all(value.is_finite() and value >= 0 for value in rates) and 0 <= cached <= inp:
                estimated_cost = float(((Decimal(inp-cached)*rates[0] + Decimal(cached)*rates[1] + Decimal(out)*rates[2])/Decimal(1000000)).quantize(Decimal('0.00000001')))
        self.data['prompts'].append({'promptId': 'prompt_' + secrets.token_hex(8), 'stage': safe_label(stage), 'model': safe_label(model),
                                     'promptSha256': prompt_sha256, 'promptTokens': inp, 'completionTokens': out, 'cachedTokens': cached,
                                     'reasoningTokens': reasoning, 'totalTokens': total, 'gpuSeconds': None, 'estimatedCostAud': estimated_cost, 'rateCardId': rate_id})

    @metadata_operation
    def fast_path(self, verified_count):
        if isinstance(verified_count,bool) or not isinstance(verified_count,int) or verified_count<5 or self.data['prompts'] or any(h['hopType']=='OUTBOUND_INFERENCE' for h in self.data['hops']):
            raise ValueError('Fast path requires >=5 verified templates and no model calls')
        self.data['route']='TEMPLATE_FAST_PATH'
        self.data['templateVerifiedCount']=verified_count
        self.data['prompts']=[{'promptId':'template_'+secrets.token_hex(8),'stage':'Repeat_Vendor_Fast_Path','model':None,
                               'promptTokens':0,'cachedTokens':0,'completionTokens':0,'reasoningTokens':0,'totalTokens':0,
                               'gpuSeconds':None,'estimatedCostAud':0,'promptSha256':None,'rateCardId':None}]
        return True

    def finish(self, outcome, *, complete=False, llm_complete=False, erp_read_complete=False, erp_write_complete=False):
        self.data['outcome'] = safe_label(outcome)
        self.data['endedAt'] = timestamp()
        self.data['coverage'] = {'complete': complete and not self.collection_failed, 'llmComplete': llm_complete and not self.collection_failed, 'erpReadComplete': erp_read_complete and not self.collection_failed, 'erpWriteComplete': erp_write_complete and not self.collection_failed}
        return self

    def snapshot(self):
        # Reject unknown metadata keys before any disk write or network queueing.
        allowed_top = {'templateVerifiedCount','invoiceRef','scope','traceId','sequence','startedAt','endedAt','route','outcome','coverage','hops','prompts'}
        allowed_hop = {'hopId','hopType','targetSystem','targetKind','operation','status','latencyMs','networkAttempted','region','transport','authMode','piiRedacted','detokenisedFieldCount','policyVerdict','tunnelOrEndpointId','mtlsCertFingerprint'}
        allowed_prompt = {'promptId','stage','model','promptSha256','promptTokens','completionTokens','cachedTokens','reasoningTokens','totalTokens','gpuSeconds','estimatedCostAud','rateCardId'}
        if set(self.data)-allowed_top or len(self.data['hops'])>100 or len(self.data['prompts'])>100:
            raise ValueError('Unsupported telemetry payload')
        for key in ['scope','traceId','route','outcome']:
            safe_label(self.data[key])
        if self.data.get('invoiceRef'):
            safe_label(self.data['invoiceRef'])
        if isinstance(self.data['sequence'], bool) or not isinstance(self.data['sequence'], int) or self.data['sequence'] < 1:
            raise ValueError('Invalid sequence')
        def measurement(value, integer=False):
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or (integer and not isinstance(value,int))):
                raise ValueError('Invalid metadata measurement')
        for hop in self.data['hops']:
            if set(hop)-allowed_hop:
                raise ValueError('Unsupported hop metadata')
            for key,value in hop.items():
                if key=='mtlsCertFingerprint':
                    if value is not None and (not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value)):
                        raise ValueError('Invalid certificate fingerprint')
                elif key in {'latencyMs','detokenisedFieldCount'}:
                    measurement(value, key=='detokenisedFieldCount')
                elif key in {'networkAttempted','piiRedacted'}:
                    if value is not None and not isinstance(value,bool):
                        raise ValueError('Invalid metadata flag')
                else:
                    safe_label(value)
        for prompt in self.data['prompts']:
            if set(prompt)-allowed_prompt:
                raise ValueError('Unsupported prompt metadata')
            for key,value in prompt.items():
                if key=='promptSha256':
                    if not (value is None and prompt.get('stage')=='Repeat_Vendor_Fast_Path') and (not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value)):
                        raise ValueError('Invalid prompt digest')
                elif key in {'promptId','stage','model','rateCardId'}:
                    safe_label(value)
                else:
                    measurement(value, key.endswith('Tokens'))
        if set(self.data['coverage']) - {'complete','llmComplete','erpReadComplete','erpWriteComplete'} or not all(isinstance(v,bool) for v in self.data['coverage'].values()):
            raise ValueError('Invalid coverage')
        for key in ['startedAt','endedAt']:
            if self.data[key] is not None:
                if not isinstance(self.data[key],str) or datetime.fromisoformat(self.data[key]).tzinfo is None:
                    raise ValueError('Invalid timestamp')
        return json.loads(json.dumps(self.data, allow_nan=False))


RATE_CARDS = json.loads(Path(os.environ['KERBLINE_RATE_CARDS_PATH']).read_text()) if os.getenv('KERBLINE_RATE_CARDS_PATH') else {}
current_trace = ContextVar('kerbline_current_trace', default=None)
exporter = Exporter()
