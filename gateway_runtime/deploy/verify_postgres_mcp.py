"""Live signed local MCP workflow and independent SDK checks. No model calls."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from postgres_mcp_client import call, snapshot
from inventory_client import tokens

ROOT=Path(__file__).resolve().parents[2]
checks=[]
def check(name,passed):
    checks.append({'name':name,'passed':bool(passed)})
    print(name,'PASS' if passed else 'FAIL',flush=True)

def rpc(args=None):
    return {'jsonrpc':'2.0','id':'check','method':'tools/call','params':{'name':'postgres_api_inventory','arguments':args or {}}}

async def main():
    user,client=await asyncio.to_thread(tokens)
    status,body=call(rpc(),user,client)
    result=body['result']['structuredContent'] if status==200 and body else {}
    check('real_signed_mcp_database_read',status==200 and len(result.get('rows',[]))==6)
    check('unified_trace_and_verified_subject_hash',bool(result.get('traceId')) and len(result.get('subjectSha256',''))==64)
    check('model_release_disabled',result.get('modelAttempted') is False)
    check('missing_client_denied',call(rpc(),user)[0]==401)
    check('forged_user_denied',call(rpc(),'forged',client)[0]==401)
    check('model_route_denied',call({'name':'postgres_api_inventory','arguments':{}},user,client,path='/v1/answer')[0]==403)
    for name,args in [('tenant_override',{'tenantId':'another-tenant'}),('sql_override',{'sql':'SELECT 1'}),('secret_field_request',{'fields':['jwt_secret']})]:
        status,body=call(rpc(args),user,client)
        check(name+'_denied',status==200 and body.get('error',{}).get('code')==-32602)
    headers={'X-Kerbline-User-Token':user,'X-Kerbline-Client-Token':client}
    async with httpx2.AsyncClient(headers=headers,timeout=60) as http:
        async with streamable_http_client('http://127.0.0.1:8796/mcp',http_client=http) as streams:
            async with ClientSession(*streams) as session:
                initialized=await session.initialize()
                tools=await session.list_tools()
                check('independent_sdk_discovery', 'postgres_api_inventory' in [t.name for t in tools.tools])
                reply=await session.call_tool('postgres_api_inventory',{})
                data=json.loads(reply.content[0].text)
                check('independent_sdk_database_read',not reply.is_error and len(data['rows'])==6)
    # Pause only the authorised test database, and restore it even when the probe fails.
    before=(ROOT/'tmp/postgres/latest.json').read_bytes()
    paused=False
    try:
        subprocess.run(['docker','--context','desktop-linux','pause','relayops-postgres'],check=True,capture_output=True,timeout=10)
        paused=True
        try:snapshot()
        except RuntimeError:failed=True
        else:failed=False
        check('real_database_unavailable_fails_closed',failed)
        check('real_database_failure_preserves_observation',(ROOT/'tmp/postgres/latest.json').read_bytes()==before)
    finally:
        if paused:subprocess.run(['docker','--context','desktop-linux','unpause','relayops-postgres'],check=True,capture_output=True,timeout=10)
    snapshot()
    check('database_recovered_signed_read',True)
    report={'observedAt':datetime.now(timezone.utc).isoformat(),'source':'Live local signed MCP gateway and adapter; not GCP',
            'tests':checks,'passed':sum(x['passed'] for x in checks),'total':len(checks),'modelRequestsMade':0,
            'privateGcpConnectivity':'Not configured; no existing VPN tunnel'}
    (ROOT/'tmp/postgres/mcp-checks.json').write_text(json.dumps(report,indent=2))
    if report['passed']!=report['total']:raise SystemExit(1)

if __name__=='__main__':asyncio.run(main())
