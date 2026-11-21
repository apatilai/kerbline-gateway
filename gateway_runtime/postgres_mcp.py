"""Tenant-bound PostgreSQL MCP contracts and loopback adapter transport.
Disabled in Cloud Run until a private customer transport is configured and verified.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Request
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from local_connectors import postgres_inventory as db

TOOL = 'postgres_api_inventory'
PROTOCOL = '2025-06-18'
LOCAL = Path(__file__).resolve().parent.parent / '.local'
KEY = LOCAL / 'postgres-adapter-key'

class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    destination: Literal['internal_app'] = 'internal_app'

class Call(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: Literal['postgres_api_inventory']
    arguments: Arguments = Arguments()
    meta: dict | None = Field(default=None, alias='_meta')


def binding(policy, subject):
    entry = policy.get('postgres', {}).get('subjects', {}).get(subject)
    if not entry or entry.get('tool') != TOOL:
        raise HTTPException(403, 'No database tenant binding')
    try:uuid.UUID(entry['tenantId'])
    except (KeyError, ValueError, TypeError):raise HTTPException(403, 'Invalid database tenant binding') from None
    return entry['tenantId']


def key():
    if KEY.stat().st_mode & 0o077:raise ValueError('Private adapter key required')
    return KEY.read_text().strip()


def validate_release(result, tenant, trace):
    if result.get('traceId') != trace or result.get('tenantId') != tenant or result.get('modelAttempted') is not False:
        raise ValueError('Invalid adapter release context')
    if set(result) != {'traceId','tenantId','rows','truncated','observedAt','modelAttempted'} or type(result['truncated']) is not bool:
        raise ValueError('Invalid adapter release schema')
    datetime.fromisoformat(result['observedAt'])
    rows, _ = db.project({'role': db.ROLE, 'readOnly': 'on', 'rows': result['rows']}, tenant)
    if len(result['rows']) > 50:raise ValueError('Too many records')
    return rows


async def execute(policy, user, request, client_check, emit):
    trace = str(uuid.uuid4())
    subject = user['subject']
    event = {'traceId':trace, 'tool':TOOL,'policyVersion':policy['version'],
             'subjectSha256':hashlib.sha256(subject.encode()).hexdigest(),
             'modelAttempted':False,'adapterAttempted':False,'destination':'internal_app'}
    try:
        if TOOL not in policy['roles'][user['role']]['tools']:raise HTTPException(403, 'Tool access denied')
        await asyncio.to_thread(client_check, request)
        tenant = binding(policy, subject)
        # Never silently connect the deployed cloud gateway to an insecure loopback URL.
        if os.getenv('K_SERVICE') or policy.get('postgres', {}).get('transport') != 'local-loopback':
            raise HTTPException(503, 'Private customer adapter transport not configured')
        event['adapterAttempted'] = True
        payload = {'jsonrpc':'2.0','id':trace,'method':'tools/call','params':{'name':'read_postgres_api_inventory','arguments':{}}}
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post('http://127.0.0.1:8797/mcp', json=payload, headers={
                'Authorization':'Bearer '+key(),'X-Kerbline-User-Token':request.headers.get('x-kerbline-user-token',''),
                'X-Kerbline-Client-Token':request.headers.get('x-kerbline-client-token',''), 'X-Kerbline-Trace-Id':trace})
        if response.status_code in (401,403):raise HTTPException(response.status_code, 'Adapter identity or tenant denied')
        response.raise_for_status()
        if len(response.content)>262144:raise ValueError('Oversized adapter response')
        result=response.json()['result']['structuredContent']
        rows=validate_release(result,tenant,trace)
        event.update(verdict='ALLOW',recordsRead=len(rows),releasedFields=sorted(db.FIELDS))
        return {**result,'data':'live-local-postgresql','policyVersion':policy['version'],
                'subjectSha256':event['subjectSha256'],'database':'relayops','role':db.ROLE,'view':db.VIEW,
                'fields':sorted(db.FIELDS),'rowLimit':50,'readOnly':True,'modelTokens':0,
                'path':'Local signed-user MCP gateway → authenticated loopback MCP adapter → tenant-pinned database view',
                'stages':['Verified Google user','Verified internal client','Server-owned tenant binding','Authenticated adapter','Database view read','Gateway release validation'],
                'networkScope':'Local loopback only; not deployed GCP private connectivity'}
    except HTTPException:
        event['verdict']='DENY'
        raise
    except Exception:
        event['verdict']='ERROR'
        raise HTTPException(502,'Database adapter unavailable or release validation failed') from None
    finally:emit(event)


adapter = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@adapter.post('/mcp')
async def adapter_mcp(request:Request):
    # This separate process accepts only the pinned local gateway transport credential.
    if os.getenv('K_SERVICE'):raise HTTPException(503,'Local adapter cannot run as a cloud transport')
    auth=request.headers.get('authorization','')
    if not secrets.compare_digest(auth,'Bearer '+key()):raise HTTPException(401,'Gateway identity required')
    policy=json.loads((LOCAL/'postgres-mcp-policy.json').read_text())
    try:
        user_token=request.headers.get('x-kerbline-user-token','')
        client_token=request.headers.get('x-kerbline-client-token','')
        if not user_token or not client_token or max(len(user_token),len(client_token))>16384:raise ValueError()
        user=await asyncio.to_thread(id_token.verify_oauth2_token,user_token,GoogleRequest(),audience=policy['user_audience'])
        cfg=policy['inventory']
        client=await asyncio.to_thread(id_token.verify_oauth2_token,client_token,GoogleRequest(),audience=cfg['client_audience'])
        if client.get('sub')!=cfg['client_subject']:raise HTTPException(403,'Internal client denied')
    except HTTPException:raise
    except Exception:raise HTTPException(401,'Invalid caller identities') from None
    tenant=binding(policy,user.get('sub'))
    trace=request.headers.get('x-kerbline-trace-id','')
    try:uuid.UUID(trace)
    except ValueError:raise HTTPException(400,'Trace required') from None
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>65536:raise HTTPException(413,'Request too large')
    try:
        raw=json.loads(body)
        if set(raw)!={'jsonrpc','id','method','params'} or raw['jsonrpc']!='2.0' or raw['id']!=trace or raw['method']!='tools/call' or raw['params']!={'name':'read_postgres_api_inventory','arguments':{}}:raise ValueError()
    except Exception:raise HTTPException(400,'Fixed database operation required') from None
    config=await asyncio.to_thread(db.read_config)
    if tenant!=config['tenantId']:raise HTTPException(403,'Tenant not provisioned for this database adapter')
    try:
        raw=json.loads(await asyncio.to_thread(db.execute,db.QUERY,config))
        rows,truncated=db.project(raw,tenant)
    except Exception:raise HTTPException(502,'Database unavailable or invalid release') from None
    result={'traceId':trace,'tenantId':tenant,'rows':rows,'truncated':truncated,
            'observedAt':datetime.now(timezone.utc).isoformat(),'modelAttempted':False}
    return {'jsonrpc':'2.0','id':trace,'result':{'content':[],'structuredContent':result,'isError':False}}
