"""Explicit synthetic UI preview. Never persisted or mixed into received telemetry."""
from datetime import datetime, timedelta, timezone
import hashlib
from telemetry import validate_trace, summary


def preview_traces():
    at = datetime.now(timezone.utc) - timedelta(minutes=4)
    def time(offset):
        return (at + timedelta(seconds=offset)).isoformat()
    def hop(identifier, kind, target, target_kind, operation, **kwargs):
        return {'hopId':identifier,'hopType':kind,'targetSystem':target,'targetKind':target_kind,'operation':operation,
                'region':'australia-southeast1','transport':'TLS_1_3','authMode':'SCOPED_SERVICE_ACCOUNT',
                'status':'OK','latencyMs':18,'networkAttempted':kind.startswith('OUTBOUND'),**kwargs}
    def prompt(identifier, stage, inp, out, cached, reasoning, cost):
        return {'promptId':identifier,'stage':stage,'model':'example-regional-model','promptTokens':inp,'completionTokens':out,
                'cachedTokens':cached,'reasoningTokens':reasoning,'totalTokens':inp+out,'gpuSeconds':None,'estimatedCostAud':cost,
                'rateCardId':'illustrative-rate-card','promptSha256':hashlib.sha256(identifier.encode()).hexdigest()}
    incoming=hop('hop-in','INBOUND_CLIENT','Customer AP portal','CLIENT','INGRESS',transport='MTLS_WEBSOCKET',authMode='MTLS',
                 mtlsCertFingerprint=hashlib.sha256(b'synthetic-certificate-not-a-real-cert').hexdigest(),tunnelOrEndpointId='example-private-tunnel',piiRedacted=None)
    traces=[
        {'traceId':'preview-code-mode','invoiceRef':'EXAMPLE-INV-A','sequence':1,'startedAt':time(0),'endedAt':time(3.7),
         'outcome':'NEEDS_APPROVAL','route':'CODE_MODE','coverage':{'complete':True,'llmComplete':True,'erpReadComplete':True,'erpWriteComplete':True},
         'auditCorrelationId':'example-audit-A','siemCorrelationId':None,'wormCorrelationId':None,
         'hops':[incoming,
                 hop('hop-vlm','OUTBOUND_INFERENCE','Regional crop model','MODEL','INFERENCE',piiRedacted=True,latencyMs=620,tunnelOrEndpointId='example-model-endpoint'),
                 hop('hop-code','OUTBOUND_INFERENCE','Regional code model','MODEL','INFERENCE',piiRedacted=True,latencyMs=810,tunnelOrEndpointId='example-model-endpoint'),
                 hop('hop-erp','OUTBOUND_CUSTOMER_INFRA','Example ERP','ERP','READ',authMode='PER_USER_OAUTH',latencyMs=145,region=None,detokenisedFieldCount=0),
                 hop('hop-approval','INTERNAL','Approval gate','INTERNAL','POLICY',transport='LOCAL_IN_PROCESS',latencyMs=1,policyVerdict='REQUIRE_FINANCE_APPROVAL')],
         'prompts':[prompt('prompt-crop','BBox_VLM_Crop',1200,180,800,0,0.0014),prompt('prompt-code','Code_Mode_Gen',2400,420,1600,120,0.0035)]},
        {'traceId':'preview-repeat-vendor','invoiceRef':'EXAMPLE-INV-B','sequence':1,'startedAt':time(30),'endedAt':time(30.42),
         'outcome':'COMPLETED','route':'TEMPLATE_FAST_PATH','templateVerifiedCount':12,
         'coverage':{'complete':True,'llmComplete':True,'erpReadComplete':True,'erpWriteComplete':True},
         'hops':[incoming,hop('hop-template','INTERNAL','Verified vendor template','INTERNAL','OTHER',transport='LOCAL_IN_PROCESS',status='SKIPPED_FAST_PATH',latencyMs=8),
                 hop('hop-erp','OUTBOUND_CUSTOMER_INFRA','Example ERP','ERP','READ',authMode='PER_USER_OAUTH',region=None,latencyMs=91)],
         'prompts':[{'promptId':'prompt-no-model','stage':'Repeat_Vendor_Fast_Path','model':None,'promptTokens':0,'cachedTokens':0,
                     'completionTokens':0,'reasoningTokens':0,'totalTokens':0,'estimatedCostAud':0,'gpuSeconds':None,'promptSha256':None,'rateCardId':None}]},
        {'traceId':'preview-bank-block','invoiceRef':'EXAMPLE-INV-C','sequence':1,'startedAt':time(60),'endedAt':time(61.12),
         'outcome':'BLOCKED','route':'EXCEPTION','coverage':{'complete':True,'llmComplete':True,'erpReadComplete':True,'erpWriteComplete':True},
         'hops':[incoming,hop('hop-master','OUTBOUND_CUSTOMER_INFRA','Example ERP supplier master','ERP','READ',region=None,latencyMs=112,authMode='PER_USER_OAUTH'),
                 hop('hop-fraud','FRAUD_GATE','Bank change control','INTERNAL','POLICY',transport='LOCAL_IN_PROCESS',status='BLOCKED_CALLBACK_REQUIRED',latencyMs=2,policyVerdict='CALLBACK_REQUIRED')],
         'prompts':[]}
    ]
    results=[]
    for raw in traces:
        trace=validate_trace(dict(raw, scope='REQUEST'))
        trace.update(dataset='preview',source='Synthetic illustration',receivedAt=time(65),summary=summary(trace))
        results.append(trace)
    return results
