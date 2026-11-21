"""Signed local MCP client. No credentials in saved evidence or browser."""
from datetime import datetime, timezone
import json
import urllib.error
import urllib.request
from inventory_client import tokens
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'gateway_runtime'))
from local_connectors import postgres_inventory as db


def call(payload,user,client=None,path='/mcp'):
    headers={'X-Kerbline-User-Token':user,'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
    if client:headers['X-Kerbline-Client-Token']=client
    request=urllib.request.Request('http://127.0.0.1:8796'+path,data=json.dumps(payload).encode(),headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=45) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as exc:return exc.code,None


def snapshot():
    started=datetime.now(timezone.utc).isoformat()
    try:
        user,client=tokens()
        status,body=call({'jsonrpc':'2.0','id':'local-ui','method':'tools/call','params':{'name':'postgres_api_inventory','arguments':{}}},user,client)
        if status!=200 or not body or body.get('error'):raise ValueError()
        result=body['result']['structuredContent']
        if result.get('data')!='live-local-postgresql' or result.get('modelAttempted') is not False:raise ValueError()
        result['source']='Signed local MCP gateway and authenticated adapter; not deployed GCP'
        result['requestId']=result['traceId']
        db.write('latest.json',result)
        db.write('attempt.json',{'success':True,'startedAt':started,'completedAt':result['observedAt']})
        event={'requestId':result['traceId'],'traceId':result['traceId'],'startedAt':started,'completedAt':result['observedAt'],
               'requester':result['subjectSha256'],'requesterSource':'SHA-256 of Google JWT subject verified by gateway and adapter',
               'outcome':'ALLOWED','httpStatus':status,'policyVersion':result['policyVersion'],
               'releasePolicy':'Bound tenant, six approved fields, internal client only',
               'resources':[result['view']],'releasedFields':result['fields'],
               'policyEvidence':' → '.join(result['stages']), 'evidenceSource':'Local signed MCP gateway'}
        history_path=db.STATE/'mcp-requests.json'
        history=json.loads(history_path.read_text()) if history_path.exists() else []
        db.write('mcp-requests.json',(history+[event])[-100:])
        return result
    except Exception:
        db.write('attempt.json',{'success':False,'startedAt':started,'error':'Signed MCP refresh failed; previous observations preserved.'})
        raise RuntimeError('Signed MCP refresh failed; inspect gateway, identities, tenant policy and adapter') from None

if __name__=='__main__':
    snapshot()
    print('Signed-user MCP database read succeeded. No model request.')
