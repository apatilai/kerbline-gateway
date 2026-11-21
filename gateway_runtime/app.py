"""Read-only MCP pilot. No arbitrary code, writes, raw-result release or user role input."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from inventory import TOOL as INVENTORY_TOOL, InventoryArguments, InventoryCall, permitted_resources, read_services, validate_result

import postgres_mcp

PROTOCOL = "2025-06-18"
MAX_BODY = 65_536
POLICY_PATH = Path(os.environ.get("POLICY_PATH", "policy.json"))
MODE = os.environ.get("SERVICE_MODE", "gateway")
if MODE not in {'gateway', 'adapter'}:
    raise RuntimeError('Unsupported service mode')
LOG = logging.getLogger("kerbline.decisions")
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    LOG.addHandler(logging.StreamHandler())


def load_policy():
    configured = os.environ.get("KERBLINE_POLICY_JSON")
    raw = configured.encode() if configured else POLICY_PATH.read_bytes()
    policy = json.loads(raw)
    # A deployed revision binds policy and identity mappings to one immutable image.
    if not policy.get("version") or not policy.get("tenant"):
        raise RuntimeError("Invalid policy")
    policy["digest"] = hashlib.sha256(raw).hexdigest()
    return policy


POLICY = load_policy()
app = FastAPI(title="Kerbline read-only MCP pilot", docs_url=None, redoc_url=None, openapi_url=None)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: Literal["internal_app", "external_model"] = "external_model"
    status: Literal["overdue", "open", "all"] = "overdue"
    operation: Literal["summary", "group_by_currency"] = "summary"


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(max_length=80)
    arguments: Arguments = Field(default_factory=Arguments)
    meta: dict | None = Field(default=None, alias="_meta")


def identity(request: Request):
    token = request.headers.get("x-kerbline-user-token", "")
    if not token or len(token) > 16_384 or not POLICY.get("user_audience"):
        raise HTTPException(401, "Verified user token required")
    try:
        claims = id_token.verify_oauth2_token(token, GoogleRequest(), audience=POLICY["user_audience"])
    except Exception:
        raise HTTPException(401, "Invalid user identity") from None
    # No email, header, request argument or LLM-produced role is authority.
    binding = POLICY["subjects"].get(claims.get("sub"))
    if not binding or binding.get("tenant") != POLICY["tenant"] or binding.get("role") not in POLICY["roles"]:
        raise HTTPException(403, "Identity has no policy binding")
    return {"subject": claims["sub"], **binding}


def service_identity(request: Request):
    """Pin the caller workload too, including when an administrator has Run invoker."""
    expected = POLICY.get('service_callers', {}).get(MODE)
    audience = POLICY.get('service_audiences', {}).get(MODE)
    header = request.headers.get('authorization', '')
    if not expected or not audience or not header.startswith('Bearer '):
        raise HTTPException(401, 'Authenticated service front door required')
    try:
        claims = id_token.verify_oauth2_token(header[7:], GoogleRequest(), audience=audience)
    except Exception:
        raise HTTPException(401, 'Invalid calling service') from None
    if claims.get('sub') != expected:
        raise HTTPException(403, 'Calling service is not approved')


def allowed_tools(user):
    return POLICY["roles"][user["role"]]["tools"]


def inventory_client_identity(request):
    config = POLICY.get('inventory', {})
    token = request.headers.get('x-kerbline-client-token', '')
    if not token or len(token) > 16_384 or not config.get('client_subject') or not config.get('client_audience'):
        raise HTTPException(401, 'Approved internal client identity required')
    try:
        claims = id_token.verify_oauth2_token(token, GoogleRequest(), audience=config['client_audience'])
    except Exception:
        raise HTTPException(401, 'Invalid internal client identity') from None
    if claims.get('sub') != config['client_subject']:
        raise HTTPException(403, 'Client is not approved for infrastructure release')


def emit(event):
    # Fixed metadata only. No prompts, arguments, tokens, upstream errors or records.
    LOG.info(json.dumps({"kerbline": True, **event}, separators=(",", ":")))


@app.middleware("http")
async def boundary(request: Request, call_next):
    if os.environ.get('K_SERVICE'):
        import asyncio
        try:
            await asyncio.to_thread(service_identity, request)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    if request.headers.get("origin") and request.headers["origin"] not in POLICY.get("allowed_origins", []):
        return JSONResponse({"detail": "Origin denied"}, status_code=403)
    # Bound streamed input as well as declared Content-Length; never trust the header.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    request._body = bytes(body)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def db_rows(status: str):
    """Real parameterised SQLite read over explicitly synthetic, isolated pilot data."""
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE invoices(id TEXT,tenant TEXT,status TEXT,currency TEXT,amount_cents INTEGER,supplier_name TEXT,bank_account TEXT)")
        db.executemany("INSERT INTO invoices VALUES(?,?,?,?,?,?,?)", [
            (f"TEST-{i:04d}", "kerbline-test" if i <= 200 else "other-tenant",
             "overdue" if i % 3 else "open", "AUD", i * 123,
             f"Synthetic Supplier {i}", f"SYNTHETIC-ACCOUNT-{i}") for i in range(1, 206)
        ])
        db.row_factory = sqlite3.Row
        result = db.execute("SELECT * FROM invoices WHERE tenant=? AND (?='all' OR status=?) LIMIT 201",
                            (POLICY["tenant"], status, status)).fetchall()
        return [dict(row) for row in result]


@app.get("/health")
def health(request: Request):
    if MODE == 'gateway':
        identity(request)
    live_inventory = bool(POLICY.get('inventory', {}).get('resources'))
    return {"service": f"kerbline-{MODE}-pilot", "policyVersion": POLICY["version"], "policyDigest": POLICY["digest"],
            "data": "mixed" if live_inventory else "synthetic", "writes": False, "arbitraryCode": False,
            "dataScopes": {"invoices": "synthetic", "cloudRunInventory": "live-infrastructure" if live_inventory else "not-configured"},
            "networkIsolation": "Consult deployment manifest; health is not network attestation",
            "revision": os.getenv("K_REVISION"), "region": os.getenv("DEPLOYMENT_REGION", "unknown")}


def rpc_result(result):
    return {"content": [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}], "structuredContent": result, "isError": False}


def rpc_error(message_id, code, message):
    return JSONResponse({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})


def tool_description(name, user_facing):
    if name == postgres_mcp.TOOL:
        description = 'Approved tenant API inventory; internal clients only; model release disabled'
        schema = postgres_mcp.Arguments.model_json_schema()
    elif name == INVENTORY_TOOL:
        description = 'Approved Cloud Run metadata for trusted internal clients only; no model release'
        schema = InventoryArguments.model_json_schema()
    elif name == 'read_cloud_run_inventory':
        description = 'Read only exact approved Cloud Run resource aliases; metadata projection only'
        schema = {'type': 'object', 'properties': {'services': {'type': 'array', 'minItems': 1, 'maxItems': 10,
                  'items': {'type': 'string', 'pattern': '^[a-z][a-z0-9_-]{0,39}$'}}}, 'required': ['services'], 'additionalProperties': False}
    else:
        description = 'Read-only synthetic invoice aggregation' if user_facing else 'Read synthetic invoice records for approved gateway'
        schema = Arguments.model_json_schema() if user_facing else {'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['overdue', 'open', 'all']}}, 'additionalProperties': False}
    return {'name': name, 'description': description, 'inputSchema': schema,
            'annotations': {'readOnlyHint': True, 'destructiveHint': False}}


async def read_adapter(status):
    result = await call_adapter('read_invoices', {'status': status})
    rows = result['rows']
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError('Adapter result outside contract')
    return rows


async def call_adapter(tool, arguments):
    url = POLICY.get("adapter_url", "")
    if not url.startswith("https://") or not url.endswith(".run.app"):
        raise HTTPException(503, "Approved adapter is not configured")
    # Exact revision configuration, no caller-supplied URL, automatic redirects disabled.
    import asyncio
    credential = await asyncio.to_thread(id_token.fetch_id_token, GoogleRequest(), url)
    payload = {"jsonrpc": "2.0", "id": "adapter-call", "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        async with client.stream('POST', url + "/mcp", json=payload,
                                 headers={"Authorization": "Bearer " + credential, "Accept": "application/json, text/event-stream",
                                          "MCP-Protocol-Version": PROTOCOL}) as response:
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 1_000_000:
                    raise ValueError("Adapter response too large")
            body = json.loads(chunks)
    result = body.get("result", {})
    if result.get("isError") or "error" in body:
        raise ValueError("Adapter rejected operation")
    return result['structuredContent']


async def execute_inventory(user, call, request_id, request):
    import asyncio
    event = {'traceId': request_id, 'scope': 'TOOL_EXECUTION', 'tool': INVENTORY_TOOL,
             'policyVersion': POLICY['version'], 'policyDigest': POLICY['digest'],
             'subjectSha256': hashlib.sha256(user['subject'].encode()).hexdigest(),
             'destination': 'internal_app', 'adapterAttempted': False, 'modelAttempted': False}
    try:
        rule = POLICY['roles'][user['role']]
        if INVENTORY_TOOL not in rule['tools'] or 'internal_app' not in rule['destinations']:
            raise HTTPException(403, 'Policy denied inventory access')
        await asyncio.to_thread(inventory_client_identity, request)
        try:
            approved = permitted_resources(POLICY, user['role'], call.arguments.services)
        except PermissionError:
            raise HTTPException(403, 'Resource scope denied') from None
        event['adapterAttempted'] = True
        result = await call_adapter('read_cloud_run_inventory', {'services': list(approved)})
        safe = validate_result(result, approved)
        event.update(verdict='ALLOW', resourcesRead=len(safe['services']), modelTokens=0)
        return {**safe, 'traceId': request_id, 'policyVersion': POLICY['version'],
                'releasePolicy': 'approved-internal-client-only', 'modelAttempted': False,
                'modelTokens': 0, 'costScope': 'gateway execution only; cloud API and infrastructure costs excluded'}
    except HTTPException:
        event['verdict'] = 'DENY'
        raise
    except Exception:
        event['verdict'] = 'ERROR'
        raise HTTPException(502, 'Inventory unavailable or failed release validation; no partial result released') from None
    finally:
        emit(event)


def release(rows, operation):
    """Positive output schema; raw strings and unknown fields never pass through."""
    groups = {}
    for row in rows:
        if row.get("tenant") != POLICY["tenant"]:
            raise ValueError("Adapter tenant boundary violation")
        currency = row.get("currency")
        amount = row.get("amount_cents")
        if currency not in {"AUD", "USD", "NZD"} or type(amount) is not int or not 0 <= amount <= 100_000_000:
            raise ValueError("Adapter value outside contract")
        group = groups.setdefault(currency, {"currency": currency, "count": 0, "totalCents": 0})
        group["count"] += 1
        group["totalCents"] += amount
    # Currency remains explicit; never sum unlike currencies.
    result = {"groups": sorted(groups.values(), key=lambda x: x["currency"]), "recordCount": len(rows), "data": "synthetic"}
    return result


async def execute(user, call, request_id):
    start = time.perf_counter()
    event = {"traceId": request_id, "scope": "TOOL_EXECUTION", "policyVersion": POLICY["version"], "policyDigest": POLICY["digest"],
             "subjectSha256": hashlib.sha256(user["subject"].encode()).hexdigest(), "tool": call.name if call.name in {"invoice_summary", "execute_plan"} else "unknown",
             "destination": call.arguments.destination, "adapterAttempted": False, "modelAttempted": False}
    try:
        rule = POLICY["roles"][user["role"]]
        if call.name not in rule["tools"] or call.arguments.destination not in rule["destinations"]:
            raise HTTPException(403, "Policy denied tool or destination")
        if call.name == "invoice_summary" and call.arguments.operation != "summary":
            raise HTTPException(403, "Plan operation requires execute_plan")
        event["adapterAttempted"] = True
        rows = await read_adapter(call.arguments.status)
        safe = release(rows, call.arguments.operation)
        event.update(verdict="ALLOW", recordsRead=len(rows), releasedFields=["currency", "count", "totalCents", "recordCount"],
                     modelTokens=0, modelCostAud=0, modelCoverage="complete-no-inference")
        return {**safe, "traceId": request_id, "policyVersion": POLICY["version"],
                "execution": "bounded-declarative-plan", "modelTokens": 0, "modelCostAud": 0,
                "costScope": "gateway tool execution only; caller model usage and infrastructure excluded"}
    except HTTPException:
        event["verdict"] = "DENY"
        raise
    except Exception:
        event["verdict"] = "ERROR"
        raise HTTPException(502, "Adapter or release validation failed") from None
    finally:
        event["latencyMs"] = round((time.perf_counter() - start) * 1000, 2)
        emit(event)


@app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
async def mcp(request: Request):
    import asyncio
    user = await asyncio.to_thread(identity, request) if MODE == "gateway" else None
    # Adapter service uses Cloud Run IAM; no application-level anonymous production mode.
    if request.method != "POST":
        return Response(status_code=405, headers={"Allow": "POST"})
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "JSON required")
    accept = request.headers.get("accept", "")
    if "application/json" not in accept or "text/event-stream" not in accept:
        raise HTTPException(406, "MCP Accept types required")
    try:
        body = await request.json()
    except Exception:
        return rpc_error(None, -32700, "Parse error")
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        return rpc_error(None, -32600, "Invalid request")
    message_id = body.get("id")
    if message_id is not None and (type(message_id) not in (str, int) or len(str(message_id)) > 128):
        return rpc_error(None, -32600, "Invalid request id")
    method = body["method"]
    params = body.get("params", {})
    if not isinstance(params, dict):
        return rpc_error(message_id, -32602, "Invalid params")
    if "id" not in body:
        if method == "notifications/initialized":
            return Response(status_code=202)
        return Response(status_code=400)
    if method != "initialize" and request.headers.get("mcp-protocol-version", PROTOCOL) != PROTOCOL:
        raise HTTPException(400, "Unsupported MCP protocol")
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": f"kerbline-{MODE}", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        names = allowed_tools(user) if user else ["read_invoices", "read_cloud_run_inventory"]
        if user and (INVENTORY_TOOL in names or postgres_mcp.TOOL in names):
            if request.headers.get('x-kerbline-client-token'):
                await asyncio.to_thread(inventory_client_identity, request)
            else:
                names = [name for name in names if name not in (INVENTORY_TOOL, postgres_mcp.TOOL)]
        result = {'tools': [tool_description(name, user is not None) for name in names]}
    elif method == "tools/call":
        if MODE == "adapter":
            args = params.get("arguments", {})
            if params.get('name') == 'read_cloud_run_inventory':
                if not isinstance(args, dict) or set(args) != {'services'}:
                    return rpc_error(message_id, -32602, 'Invalid inventory adapter arguments')
                try:
                    validated = InventoryArguments.model_validate(args)
                except ValidationError:
                    return rpc_error(message_id, -32602, 'Invalid inventory adapter arguments')
                if validated.services is None:
                    return rpc_error(message_id, -32602, 'Explicit approved services required')
                try:
                    result = rpc_result(await read_services(POLICY, validated.services))
                except Exception:
                    raise HTTPException(502, 'Cloud inventory read failed') from None
                return {'jsonrpc': '2.0', 'id': message_id, 'result': result}
            if params.get("name") != "read_invoices" or not isinstance(args, dict) or set(args) - {"status"} or args.get("status", "overdue") not in ("overdue", "open", "all"):
                return rpc_error(message_id, -32602, "Invalid adapter call")
            result = rpc_result({"rows": db_rows(args.get("status", "overdue")), "data": "synthetic"})
        else:
            if params.get('name') == postgres_mcp.TOOL:
                try:
                    postgres_mcp.Call.model_validate(params)
                except ValidationError:
                    return rpc_error(message_id, -32602, 'Invalid database tool arguments')
                result = rpc_result(await postgres_mcp.execute(POLICY, user, request, inventory_client_identity, emit))
                return {'jsonrpc':'2.0','id':message_id,'result':result}
            try:
                call = (InventoryCall if params.get('name') == INVENTORY_TOOL else ToolCall).model_validate(params)
            except ValidationError:
                return rpc_error(message_id, -32602, "Invalid tool arguments")
            result = rpc_result(await execute_inventory(user, call, str(uuid.uuid4()), request) if call.name == INVENTORY_TOOL else await execute(user, call, str(uuid.uuid4())))
    else:
        return rpc_error(message_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


async def vertex_call(action, contents):
    """Only the pinned regional provider receives schema-minimised synthetic data."""
    import asyncio
    import google.auth
    config = POLICY.get('vertex', {})
    region = config.get('region')
    if not config.get('enabled') or region not in {'australia-southeast1', 'global'} or config.get('model') != 'gemini-3.8-flash':
        raise HTTPException(503, 'Approved inference destination is disabled')
    if region == 'global' and not (config.get('synthetic_only') is True and POLICY['tenant'] == 'kerbline-test'):
        raise HTTPException(503, 'Global inference is permitted only for the isolated synthetic pilot')
    project = config.get('project', '')
    if not project or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in project):
        raise HTTPException(503, 'Invalid model destination')
    def credentials():
        creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        creds.refresh(GoogleRequest())
        return creds.token
    token = await asyncio.to_thread(credentials)
    host = 'aiplatform.googleapis.com' if region == 'global' else 'australia-southeast1-aiplatform.googleapis.com'
    url = f'https://{host}/v1/projects/{project}/locations/{region}/publishers/google/models/gemini-3.8-flash:{action}'
    payload = {'contents': [{'role': 'user', 'parts': [{'text': contents}]}]}
    if action == 'generateContent':
        payload['generationConfig'] = {'temperature': 0, 'maxOutputTokens': 512, 'responseMimeType': 'application/json',
            'responseSchema': {'type': 'OBJECT', 'properties': {'recordCount': {'type': 'INTEGER'},
                'totalCents': {'type': 'INTEGER'}, 'currency': {'type': 'STRING'}}, 'required': ['recordCount', 'totalCents', 'currency']}}
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        r = await client.post(url, json=payload, headers={'Authorization': 'Bearer ' + token})
        r.raise_for_status()
        return r.json()


@app.post('/v1/answer')
async def answer(request: Request):
    if MODE != 'gateway':
        raise HTTPException(404, 'Not found')
    import asyncio
    user = await asyncio.to_thread(identity, request)
    try:
        raw = await request.json()
        if isinstance(raw, dict) and raw.get('name') in (INVENTORY_TOOL, postgres_mcp.TOOL):
            raise HTTPException(403, 'Real infrastructure metadata is not permitted on the model route')
        call = ToolCall.model_validate(raw)
    except (ValidationError, ValueError):
        raise HTTPException(400, 'Invalid tool request') from None
    # Model-bound route overrides no policy: internal-only declarations cannot be relabelled.
    if call.arguments.destination != 'external_model':
        raise HTTPException(403, 'Model release requires external_model destination')
    if not POLICY.get('vertex', {}).get('enabled'):
        raise HTTPException(503, 'Inference disabled')
    trace_id = str(uuid.uuid4())
    safe = await execute(user, call, trace_id)
    if len(safe['groups']) != 1:
        raise HTTPException(422, 'Model answer requires one currency')
    expected = {'recordCount': safe['recordCount'], 'totalCents': safe['groups'][0]['totalCents'], 'currency': safe['groups'][0]['currency']}
    prompt = 'Return the supplied invoice aggregate unchanged as JSON. ' + json.dumps(expected, separators=(',', ':'))
    event = {'traceId': trace_id, 'stage': 'MODEL_RELEASE', 'policyVersion': POLICY['version'],
             'modelAttempted': True, 'destination': 'Vertex AI ' + POLICY['vertex']['region'], 'promptSha256': hashlib.sha256(prompt.encode()).hexdigest()}
    try:
        result = await vertex_call('generateContent', prompt)
        # A billed model call still consumed tokens if its answer fails validation.
        usage = {k: v for k, v in result.get('usageMetadata', {}).items() if k in {
            'promptTokenCount', 'candidatesTokenCount', 'totalTokenCount', 'cachedContentTokenCount', 'thoughtsTokenCount'} and type(v) is int and v >= 0}
        event['usage'] = usage
        text = ''.join(part.get('text', '') for part in result['candidates'][0]['content']['parts'] if not part.get('thought'))
        model_answer = json.loads(text)
        if model_answer != expected or any(type(model_answer[k]) != type(v) for k, v in expected.items()):
            raise ValueError('Model output did not match source aggregate')
        event.update(verdict='ALLOW', usage=usage)
        return {'answer': model_answer, 'traceId': trace_id, 'model': POLICY['vertex']['model'], 'modelLocation': POLICY['vertex']['region'], 'usage': usage,
                'estimatedCostAud': None, 'costStatus': 'No approved rate card; not provider billing', 'data': 'synthetic'}
    except Exception:
        event['verdict'] = 'ERROR'
        raise HTTPException(502, 'Model response unavailable or failed validation') from None
    finally:
        emit(event)
