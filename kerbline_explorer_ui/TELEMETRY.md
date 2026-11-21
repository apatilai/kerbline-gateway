# Gateway traffic and per-prompt usage

## What is implemented

The customer console now has two linked, role-filtered workspaces:

- **Live traffic**: request boundaries, per-hop timeline, connection evidence, policy outcomes, prompt hashes, correlation references and usage.
- **Prompt ledger**: one selectable invoice/trace row with all its prompt stages, input, cached, output, reasoning and total tokens, plus estimated AUD model cost.

The received dataset polls every five seconds while visible and not paused or editing a filter. Preview data is a separate, read-only, synthetic dataset, never inserted into the trace database. Preview explicitly illustrates a multi-prompt invoice, an eligible repeat-vendor route, and a bank gate that read the ERP but made no ERP write. No portfolio percentage, compliance certification, residency guarantee or measured savings figure is fabricated.

Trace queries use a maximum 100 rows, default 25, keyset pagination and outcome indexes. Search matches invoice references and opaque trace IDs. Metrics are explicitly scoped to the current page. Prompt content, raw/tokenised prompt previews and break-glass retrieval are not exposed by any endpoint.

## Role enforcement

Set `CONSOLE_CREDENTIALS_PATH` to a protected JSON file containing `identities` and `producers` arrays. Provision strong, distinct credentials through your customer secret manager; never commit the file. The repository's data directory is ignored by Git and is not served over HTTP.

Structure (placeholder values must be replaced):

```json
{
  "identities": [
    {"subject": "finance-user", "role": "cfo", "token": "REPLACE_WITH_STRONG_FINANCE_CREDENTIAL"},
    {"subject": "ap-user", "role": "ap-clerk", "token": "REPLACE_WITH_STRONG_AP_CREDENTIAL"},
    {"subject": "security-user", "role": "it-security-admin", "token": "REPLACE_WITH_STRONG_SECURITY_CREDENTIAL"}
  ],
  "producers": [
    {"subject": "gateway-execution", "token": "REPLACE_WITH_SEPARATE_PRODUCER_CREDENTIAL"}
  ]
}
```

| Role | Visible workspace / API data |
| --- | --- |
| `ap-clerk` | Work queue only. No traces, model counts, infrastructure, audit export or topology in API responses. |
| `finance-controller`, `cfo` | Prompt ledger. Per-prompt costs/counts; no hops, endpoint/tunnel IDs, fingerprints, hashes, producer identity or ERP network counters. |
| `it-security-admin` | Traffic, connections, infrastructure, policies and audit. May register resources and save policy drafts. |
| `ciso-auditor` | Traffic, connections and audit. Read only; mutation endpoints return 403. |
| `console-admin` | All console workspaces. |
| Dedicated producer | May ingest telemetry only. Cannot use viewer credentials for ingestion or producer credentials for UI access when viewer authentication is configured. |

`Authorization: Bearer ...` maps to a server-configured identity. Browser role headers, query parameters and request bodies do not set roles. Audit mutation events use that server-derived subject. UI navigation is filtered for usability, while API projections and endpoint permissions enforce access.

This is **credential-based RBAC, not SSO, MFA, SCIM, enterprise session management or IdP verification**. The default loopback development console remains an unauthenticated local administrator when neither viewer identities nor `CONSOLE_ACCESS_TOKEN` is configured. A producer-only credential file does not secure local viewer access. Configure viewer identities or an operator token before sharing the deployment. Non-loopback binding requires viewer credentials. Use TLS, network controls and a protected persistent volume.

## Ingestion contract

POST a JSON trace snapshot to `/api/telemetry/ingest` with a dedicated producer bearer credential. The server accepts only explicitly allowed fields and rejects unsupported fields, raw prompts/headers, unsafe labels, internal IP strings, recognised banking/numeric PII patterns, invalid hashes, nonfinite counters, inconsistent token totals, unsupported states and malformed timestamps. It never echoes rejected bodies or logs headers. Metadata validation reduces accidental disclosure; the producer must use fixed safe identifiers, not arbitrary document text.

Each snapshot contains:

- `traceId`, optional safe `invoiceRef`, `sequence`, `startedAt`, `endedAt`, `route`, `outcome`.
- `scope`: `COMPONENT` (default) or `REQUEST`. Component instrumentation cannot prove whole-request egress.
- `coverage`: `complete`, `llmComplete`, `erpReadComplete`, `erpWriteComplete`.
- `hops[]`: opaque hop ID, type, safe target alias, target kind, read/write operation, optional observed region, transport enum, endpoint/tunnel alias, certificate SHA-256 fingerprint, auth mode, optional redaction flag, detokenisation count, elapsed latency, network-attempt flag, status and policy verdict.
- `prompts[]`: prompt ID, stage, model, usage counters, optional GPU seconds, estimated model cost, rate-card reference and SHA-256 of the request payload.
- Optional audit, SIEM and WORM correlation references. References do not prove external delivery or retention.

Producer source and received timestamp come from the authenticated collector, not the body. Sources own their trace IDs. Identical retries are idempotent; in-progress updates require increasing sequence numbers; finalized snapshots are immutable. Preview is never an ingestion option.

The collector is not a durable message broker. It uses local SQLite and a local audit chain, not WORM storage. A snapshot's completeness is a **producer assertion**, not independent network attestation. The UI says so.

## Truthful token / cost accounting

- `null` means unknown; zero is an explicit measurement.
- `totalTokens = promptTokens + completionTokens` when all are known.
- Cached tokens are a subset of prompt/input tokens. Reasoning tokens are a subset of completion/output tokens. Do not add them again.
- The SDK handles OpenAI-compatible usage and Vertex-style usage. A provider total inconsistent with the canonical categories stays unknown, rather than being silently recomputed as a known total.
- A fast-path trace requires at least five verified template observations, one explicit zero-token row, zero model cost, complete model-call coverage and no inference hop.
- Zero model cost excludes OCR, GPU/CPU, storage, networking and the console.
- Model costs are **estimates**, never provider billing reconciliation. Non-fast-path estimates require a rate-card reference.
- Optional `KERBLINE_RATE_CARDS_PATH` points to a model-keyed JSON file with `id`, `inputPerMillionAud`, `cachedPerMillionAud`, and `outputPerMillionAud`. Use customer-approved versioned AUD rates. No rates or exchange rates are invented. Missing required usage/rates leaves cost unknown.

## Egress wording

The server derives counts from network-attempted hops, never the outcome label alone.

- **0 ERP EGRESS** requires finalized request scope, complete ERP read and write coverage, and no attempted ERP network hop.
- **0 ERP WRITE EGRESS** requires finalized request scope, complete ERP write coverage, and no attempted or unknown write hop. ERP reads may still have occurred.
- Incomplete, component-scoped or unknown evidence says **ERP egress not proven**.

A server-side bank comparison often reads a supplier record. Therefore a blocked bank change must not automatically be advertised as zero total ERP egress.

## Shared asynchronous producer

`../kerbline_telemetry` is a standard-library-only SDK. It uses a bounded 256-item in-memory queue, a background exporter, and up to three HTTP attempts. Producer credentials stay in the exporter, outside the metadata snapshot. Redirects are rejected; remote collectors require HTTPS. Optional JSONL output uses 0600 permissions. The exporter exposes enabled/queued/dropped/failed/delivered counters.

`emit()` performs metadata validation/copying and a nonblocking enqueue; no HTTP is performed on the caller. This does **not** prove `<0.1 ms` overhead or zero blocking latency. Hashing a large payload also has CPU cost. Benchmark in the actual workload. Queue overflow, process termination and exhausted retries can lose telemetry. Do not use the queue as an audit-retention guarantee. Configure a durable collector or OTel pipeline for production delivery requirements.

Basic usage from the repository root:

```python
from kerbline_telemetry import Trace, exporter

trace = Trace(route="CODE_MODE", scope="COMPONENT", invoice_ref="INV-SAFE-REFERENCE")
# Record actual adapter hops and provider usage; never pass headers or prompt text.
# trace.hop(...)
# trace.usage(stage, model, prompt_sha256, provider_usage)
trace.finish("COMPLETED", complete=True, llm_complete=True)
exporter.emit(trace)
```

For a real deterministic route, call `trace.fast_path(verified_count)` only after the template actually matched; finish with complete model-call coverage. Do not call it for an unseen invoice or merely to populate a demo.

Set `KERBLINE_TELEMETRY_URL` and `KERBLINE_TELEMETRY_TOKEN` in the **producer's** runtime, not its prompt or V8 context. Optional `KERBLINE_TELEMETRY_FILE` writes metadata JSONL. Collector URLs may not contain credentials, query strings or fragments.

## Phase B / C integration

- Phase B groups all VLM calls in one request context. It hashes the actual image/text request payload and reads actual provider usage. The current NVIDIA public endpoint has **unknown inference region**; it is not labelled Australian inference. TLS version and mTLS are not guessed. Requests without usage metadata have unknown usage, not zero.
- Phase C records the actual local sandbox/bridge execution and policy gates. Its ERP adapter remains **synthetic and in-process**. It makes no claim to have contacted customer ERP infrastructure. A direct RPC also checks its execution-role tool allow-list server-side.
- Both emit **component scope**. They cannot establish the entire Gateway request's network coverage or regulatory compliance.
- An optional safe `invoice_ref` ties prompts within a request to a reference. Unsafe reference text is hashed by the SDK rather than queued. A distributed orchestrator is still needed to assemble a complete request across independently deployed components; do not label component events as a complete cross-service trace.
- When the SDK is absent in the legacy standalone images, the health response explicitly reports telemetry unavailable. The telemetry-enabled variants include the SDK.

Build instrumented images from the repository root:

```sh
docker build -f phase_b_poc/Dockerfile.telemetry -t kerbline-phase-b-telemetry .
docker build -f phase_c_poc/Dockerfile.telemetry -t kerbline-phase-c-telemetry .
```

Existing cloud services are **not changed or redeployed** by editing these files. Enable the exporter and deploy the instrumented image through the customer's approved pipeline before expecting their traffic in the console.

## Local verification probe

The local preview has a private producer credential in `data/console-credentials.json`. Start with that file using `CONSOLE_CREDENTIALS_PATH`, then run from the repository root:

```sh
python3 kerbline_explorer_ui/tools/run_local_probe.py --credentials-file kerbline_explorer_ui/data/console-credentials.json
```

This executes the actual local V8/Host RPC bank gate against the existing synthetic ERP adapter, exports its metadata, and prints delivery counters without printing credentials. Its record is `LOCAL-POC-INVOICE`, component scope, with unknown provider costs. It is an execution diagnostic, not a real customer transaction or a paid model invocation.

## Validation

```sh
python3 -m unittest discover -s kerbline_explorer_ui/tests -v
PYTHONPATH=phase_b_poc python3 -m pytest phase_b_poc/test_phase_b.py -q
node --check kerbline_explorer_ui/app.js
node --check kerbline_explorer_ui/traffic.js
```

For a clean integration-test environment, create a virtual environment and install `kerbline_explorer_ui/tests/requirements.txt` first. This avoids unrelated global package conflicts. The console server itself still needs only Python's standard library; producer integration tests also require Node.js.
