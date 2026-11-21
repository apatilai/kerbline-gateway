# Kerbline read-only MCP pilot

This directory implements a deployed MCP request path, not a production ERP integration. Invoice records are generated test data; the approved Cloud Run inventory is real. Unrestricted generated code remains disabled.

## Live GCP inventory

`cloud_run_inventory` reads actual, explicitly approved Cloud Run resources through the internal MCP adapter. The initial registry contains only `kerbline-pilot-gateway` and `kerbline-pilot-adapter` in the existing Sydney project. It does not enumerate a project or discover arbitrary services.

Results contain service name/full ID, project, region, control-plane readiness, traffic-serving revisions and percentages, latest ready/created revisions, ingress and observation timestamps. **READY is not an application probe or proof of a running instance.** Instance counts stay unknown; no Monitoring query is made. Missing readiness/traffic evidence stays unknown. The latest-created revision is never substituted for a serving revision. A failed approved-resource read fails the whole request without releasing a partial result.

The adapter's custom role contains only `run.services.get`, granted separately at the two approved services. This feature grants no project-wide viewer/list role or write permission. Google API requests use a partial-response field selector: environment variables, secret references, creator identities, annotations, container configuration and tokens are not requested or returned. Kerbline then validates/project results again against the caller's allowed resources.

Inventory is **internal-client-only**. In addition to the signed user token and server-owned role, `X-Kerbline-Client-Token` must be Google-signed, have the configured inventory audience and identify the approved internal-client service account. A destination string, `_meta` or role header cannot replace that proof. Unattested clients do not discover the tool and cannot call it. `/v1/answer` rejects inventory before adapter/model access. The global synthetic model route remains separate and receives no real inventory.

This proves an approved workload identity, not that a person can never copy authorised UI output elsewhere. Treat the short-lived client token as a bearer credential. The local operator process calls IAM Credentials `generateIdToken` using only `roles/iam.serviceAccountOpenIdTokenCreator` on the dedicated client identity. It creates no key and needs no service-account access-token grant. The browser receives projected data and a local request nonce, never cloud bearer tokens. Enterprise customer IdP/client onboarding remains future work.

Use **Refresh approved inventory** at `http://127.0.0.1:8795`. Each refresh reads GCP through API Gateway and makes no LLM call. Failed refreshes retain the previous timestamped snapshot and show a warning. The separate synthetic-suite button can still make a synthetic model call.

```sh
python gateway_runtime/deploy/inventory_client.py
python gateway_runtime/deploy/verify_inventory.py
python gateway_runtime/deploy/verify_inventory_sdk.py
```

Evidence lives under `tmp/inventory/`, with reviewed results under `output/validation/inventory/`. `deploy/deploy_inventory.py IMAGE` checks the live policy digest against saved configuration before preserving user bindings and publishing the alias/resource and role registry. Expanding access requires both a reviewed policy update and service-level IAM grants. Registering inventory in the other console does not grant access. The default `policy.json` does not enable live inventory by itself.

Generated JavaScript and business-system writes remain disabled. The existing seven-page System Design describes the previous synthetic pilot milestone; this section records the real-inventory increment.

## Implemented boundary

```text
Signed Google user JWT
  → GCP API Gateway validates front-door JWT
  → Cloud Run IAM authenticates the API Gateway workload
  → Kerbline independently verifies that workload and the original user JWT
  → Server-owned subject → tenant → role mapping and tool/destination checks
  → Audience-bound managed service token over the VPC path
  → Internal-ingress MCP database adapter → parameterised tenant-scoped SQLite read
  → Positive release schema → count, currency, integer amount totals
  → Optional fixed model request → exact source-value validation → actual usage
```

The public front door is authenticated; it is **not private-only ingress**. The adapter accepts internal ingress. A separate pilot VPC/subnet enables gateway all-traffic Direct VPC egress and Private Google Access. No NAT, service account keys, customer ERP credentials or write tools are created. The infrastructure and service-identity configuration are as important as the application code; never expose adapter mode as an anonymous web service.

### Identity

`Authorization` authenticates the calling workload on the backend. `X-Kerbline-User-Token` contains the original signed Google user JWT. The gateway verifies signature, issuer, audience and expiry through google-auth. A policy maps the verified `sub` to a tenant and role. Request bodies, email claims, role headers and LLM content cannot set authority. Cloud deployments also pin the workload subject and audience independently of Cloud Run IAM, so a user with direct invoker access cannot simply bypass the front door.

This is a Google-identity pilot. Customer OIDC onboarding, SCIM, session management and revocation integration are not implemented.

### Policy and data release

Policy is loaded once per deployed revision and SHA-256 hashed. Publishing means deploying a reviewed revision, not saving a console draft. Tools are `invoice_summary` and `execute_plan`; the latter accepts only predefined summary/grouping operations. No JavaScript, SQL, free-form prompt, endpoint URL or raw-field selection is accepted. Auditors can discover/call only `invoice_summary`; analysts can use both. Destination permissions are evaluated before adapter access.

The output schema permits counts, bounded currency enums and integer amount totals. Supplier names, invoice identifiers, bank values, arbitrary strings and unexpected fields are not copied into model input. A cross-tenant row or invalid amount causes the entire result to fail closed. This is **schema minimisation, not a general DLP detector, reversible vault or anonymisation guarantee**. For example, an aggregate for a small population may still be sensitive under a customer's policy.

### Model destinations

The default policy disables inference and specifies a regional destination. Live availability probes returned 404 for the checked Sydney model IDs. The isolated `kerbline-test` deployment therefore uses an explicit `pilot-readonly-2-synthetic-global` policy with `synthetic_only: true` and `gemini-3.8-flash` at `global`. This is a deliberate synthetic test configuration, not an automatic failover or Australian-residency claim. The code rejects global inference outside that pilot tenant/configuration.

`POST /v1/answer` accepts the same typed tool request, requires `external_model`, obtains the permitted aggregate, supplies a fixed prompt to Vertex AI, and validates the returned JSON against the computed result. Provider usage is preserved when reported. AUD cost remains unknown without an approved rate card; provider retention and processing location are not proven by this test. The model only restates the aggregate here; that validates integration and release boundaries, not advanced reasoning quality.

### MCP contract

`POST /mcp` implements JSON responses for Streamable HTTP, negotiating `2025-06-18` (standard `_meta` is accepted without granting authority): `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`. GET and DELETE return 405; no resumable SSE/session state is offered. Requests need `Accept: application/json, text/event-stream`. All endpoints reject unapproved browser origins, and streamed request bodies are bounded. This is a deliberately limited MCP implementation, not a general connector registry or complete OAuth-enabled public MCP service.

## Tests and evidence

Use an isolated virtual environment with `requirements.txt`, the existing console test dependencies and an MCP client SDK for the interoperability check. Do not install over the global environment.

```sh
python -m unittest discover -s gateway_runtime/tests -v
python -m unittest discover -s kerbline_explorer_ui/tests -v
PYTHONPATH=phase_b_poc python -m pytest phase_b_poc/test_phase_b.py -q
python gateway_runtime/deploy/verify_cloud.py
python gateway_runtime/deploy/verify_mcp_sdk.py
python gateway_runtime/deploy/token_benchmark.py
```

Cloud verification uses the operator's existing `gcloud` session, captures tokens in memory and never prints them. It checks anonymous/forged access, direct user bypass, origin rejection, handshake, discovery, writes/code denial, field/role spoofing, real adapter execution and model usage. Independent MCP SDK verification exercises client interoperability. Reports are stored under `tmp/system-design`; publish redacted copies only after review. Do not mislabel unit-test mocks as cloud proof.

The token comparison uses actual provider `countTokens` calls over two synthetic payloads with an equivalent question: row data versus local aggregation. It excludes output, code generation/planning, token caching, infrastructure, latency and net AUD savings. The live answer independently validates the computed values. It does not evaluate a model's arithmetic accuracy on the larger baseline.

## Operator UI

```sh
python gateway_runtime/operator_console.py
```

Open `http://127.0.0.1:8795`. The view shows actual saved cloud evidence, resource names, policy/security check results and provider usage. Refreshing the page does not make cloud calls. **Run cloud verification** intentionally performs the checks and one bounded model request. Credentials remain in the operator process; no cloud token is exposed to the browser or saved by this tool. This is a loopback operator diagnostic, not a customer-facing SSO console or continuous monitor.

## Deployment and operation

`deploy/cloud_pilot.py` pins the existing Kerbline test project `project-4f0a7dac-0503-4e87-abe`. It creates/updates only dedicated pilot services, service-level invocation bindings and API Gateway resources. The image build uses a separate pilot builder/source bucket. APIs, three runtime/front identities, the builder, custom predict-only role, repository permissions and VPC/subnet were provisioned explicitly during this run. The script is a reproducible deployment step, not complete idempotent infrastructure-as-code. `front` creates named API resources and is intended for initial setup; rerunning it without handling existing resources fails.

`services IMAGE` deploys a pinned image and startup policy. Service revisions use one maximum instance, zero minimum instances, bounded request timeouts and no anonymous Cloud Run invocation. Preserve `service_callers` and `service_audiences` when changing revisions. A missing workload binding fails closed. The image tag should be replaced with the recorded digest for release promotion.

Cloud Logging receives fixed decision metadata, hashed subject, policy digest, request correlation and usage; it does not receive raw rows, prompts or bearer tokens from this application. Ordinary application logs are not WORM audit storage or transactional audit-before-execution. Cloud platform request logging and retention require a separate customer review.

Pilot resources incur actual GCP build, storage, API Gateway and inference charges. Scale-to-zero does not eliminate every charge. To retire the pilot, remove only its gateway/config/API, two Cloud Run services, VPC/subnet, dedicated identities/role bindings and build bucket/images after retaining required evidence. Do not remove shared repositories or existing Kerbline/aegis services. Deployment tools never disable billing or delete customer resources.

## Legacy fixes in this increment

- Phase B `ProcessRequest` now defines `invoice_ref`; the instrumented route no longer fails on that missing field.
- The legacy image-inference HTTP path refuses release until trusted visual detection is integrated.
- Phase C generated-JavaScript and adversarial-diagnostic HTTP paths are disabled unless an explicit unsafe PoC flag is set. Its role body is therefore not a production authorization path.
- Legacy benchmark output explicitly identifies hardcoded figures as illustrative rather than measured.

These source fixes do not redeploy the older Phase B/C cloud services. The new pilot is separate.

## Remaining production work

Customer IdP lifecycle; one real private system adapter with resource-level permissions; real datastore lifecycle; private-only customer ingress where required; hardened arbitrary-code isolation if needed; general DLP and document protection; durable approvals/idempotent writes; immutable audit and recovery; policy administration integrated with runtime publication; tenant isolation beyond one installation; rate/budget enforcement and operational SLOs; billing reconciliation and a net cost/quality benchmark. Do not present this pilot as compliance certification or guaranteed savings.

The explicit zero model tokens on a tool result apply only to that gateway tool execution. Caller-side AI prompting and consumption of the result are outside this measurement.

## Customer workflow demonstration

Open the local operator console at http://127.0.0.1:8795/.

1. Select **Infrastructure** and click **Refresh approved inventory**. The last successful fetch must advance. Select each service to inspect the actual resource ID, Sydney region, ingress and reported traffic revisions. A READY service may have zero instances; this is control-plane metadata, not an application probe.
2. Open **Selected request details**. Inspect the gateway trace ID, policy version, release policy, returned resource IDs and released field names. The requester shown here comes from local gcloud configuration and is explicitly **not server-verified attribution**. Credentials and resource payload values are not stored in this request history. The local JSON history is capped at 100 entries and is not an immutable audit store.
3. Click **Test an unapproved resource**. This makes a real, read-only request for `unapproved-demo` using the signed identities. The demonstration succeeds only when the gateway returns HTTP 403. The resulting DENIED event must show no returned resources or field names. It does not intentionally interrupt an actual service or modify an IAM grant.
4. Inspect **Customer deployment context** for the project, region, recorded service endpoints and revisions. Compare these deployment records with the freshly observed resource revisions. The last successful end-to-end inventory read is the connection check; continuous availability is not measured.
5. Open **Token efficiency**. The inventory route does not invoke a model. Separately, the synthetic invoice benchmark compares 1,839 row-payload input tokens with 46 aggregate input tokens, measured using provider countTokens. This is not a production cost-savings claim; outputs, planning, infrastructure and net AUD costs are excluded.
6. Open **Verification evidence**. The inventory security suite uses the deployed GCP gateway. The historical synthetic suite is separate and its run button invokes a bounded global model call on synthetic data.

### Failure-test scope

The local runtime tests inject expired-token validation errors and adapter failures. Local client tests exercise 401, 403 and 502 responses and assert that the last successful inventory snapshot is unchanged, failed attempts are recorded, and credentials never enter request history. Browser tests simulate an HTTP 502 and verify that the warning appears while the two saved services remain visible. These are controlled failure tests, not evidence that a real GCP service was taken offline or a genuinely expired provider token was replayed.

Live inventory verification separately tests missing/forged client identities, an unapproved resource, arbitrary project/URL parameters and release to the model route. No writes or generated JavaScript are enabled.

### Next real business-system connection

A local PostgreSQL test integration is now implemented (see below). The synthetic invoice SQLite fixture remains a separate synthetic workflow. For any additional customer connection, obtain the approved test endpoint, database/API type, permitted tables or operations, released-field allowlist, row/tenant restrictions and a customer-managed read-only credential reference. Do not place passwords or access tokens in UI state, request history or chat.

The first business integration must use a fixed server-side query/API operation, parameter validation, tenant/row authorisation, least-privilege read-only credentials, bounded result size and timeout, and a positive output schema. Verify with actual permitted records plus seeded restricted fields that excluded values are absent from responses, logs and model requests. Keep model release disabled until that output policy is validated. This is a prerequisite for the next connection, not an implemented capability claim.


## Implemented local PostgreSQL pilot

The operator console now includes **Business systems · PostgreSQL** and a **Refresh database inventory** action. It connects to the existing `relayops-postgres` PostgreSQL 15 container and `relayops` database through a fixed local Docker exec command. This route is separate from the deployed GCP API Gateway/MCP route. It is not a private network tunnel, a cloud-deployed database connector, or enterprise per-user database authorisation.

The additive setup creates `kerbline_inventory_reader`, a login with no superuser, role-creation, database-creation, replication or RLS-bypass attributes. Its generated password is stored in `.local/postgres-reader.json` with file permissions 0600, excluded from source control and never exposed through the console API. The local Docker operator remains privileged and is outside this restricted database-role boundary.

The only accessible application relation is `kerbline_inventory.approved_apis`, a security-barrier view owned by the database administrator. Its SQL pins tenant `00000000-0000-0000-0000-000000000def`; the client cannot select another tenant or supply SQL. The six released fields are `id`, `tenant_id`, `name`, `base_path`, `enabled`, and `auth_type`. Text fields are bounded to 256 characters. `jwt_secret`, request headers, upstream URLs, descriptions, credentials and unrestricted JSON fields are absent from the view. This is positive field selection, not a general-purpose PII masking engine.

The fixed query has a five-second statement timeout, one-second lock timeout, 15-second subprocess timeout, 256 KiB response cap and a 50-record release limit. It fetches at most 51 records to detect truncation. A second release check rejects unexpected fields, cross-tenant rows, an unexpected connection role or a writable session. Refresh failures preserve the last successful observation. All database results remain in the local operator workflow; no model is called.

One-time setup (already executed; refuses to overwrite existing credentials):

```sh
python gateway_runtime/deploy/setup_local_postgres.py
```

Live permission and release checks:

```sh
python gateway_runtime/deploy/verify_local_postgres.py
```

These checks exercise the actual reader login, tenant and column scope, absence of administrative attributes, denial of credential/base-table reads, and denied no-op inserts/updates even when overriding the session read-only default. The write probes use `WHERE false` so no business row could change even under an incorrect grant. Unit tests separately inject seeded forbidden fields, cross-tenant rows and failures. No restricted canaries are inserted into the existing business database.

Evidence: `tmp/postgres/checks.json`, `tmp/postgres/browser-tests.json`, and `tmp/postgres/unit-tests.log`. The local metadata snapshot includes the approved inventory rows; it is not an immutable audit log. Moving this connection into the GCP MCP path still requires a customer-controlled private network route, deployed secret management and verified user-to-tenant mapping. Do not expose the local Docker socket or database directly to an LLM or the public gateway.

## Signed-user PostgreSQL MCP milestone (20 November 2026)

This supersedes the console's direct local read described above. **Refresh database inventory** now follows:

```text
Loopback operator console (8795)
  → server-side signed Google user + signed internal-client tokens
  → local Kerbline MCP gateway (127.0.0.1:8796/mcp)
  → server-owned subject → database tenant binding
  → separately authenticated local MCP adapter (127.0.0.1:8797/mcp)
  → independently reverified Google user and internal-client identities
  → provisioned-tenant check → restricted PostgreSQL view
  → gateway field/tenant/trace release validation → local UI
```

The user-facing MCP tool is `postgres_api_inventory`. It accepts only an optional `destination: internal_app`; no SQL, URL, table name, field list, role or tenant parameter is accepted. Standard MCP `_meta` is permitted for compatibility and confers no authority. The tool is hidden from discovery without the approved signed client token and blocked from `/v1/answer`. The independent MCP SDK successfully initializes, discovers and calls the tool.

Database tenant selection now comes from `.local/postgres-mcp-policy.json` → `postgres.subjects[verifiedGoogleSubject].tenantId`. The current one-subject test installation maps to the one provisioned database tenant; another tenant must be explicitly provisioned with its own restricted database scope before access can work. This is manual server-side identity binding, not enterprise SSO onboarding or a general multi-tenant connection broker.

The gateway-to-adapter credential is held in `.local/postgres-adapter-key` (0600). This is a host-local transport credential, not mTLS or cloud workload identity. Both HTTP services bind to loopback; no network encryption claim is made for loopback. The adapter also verifies Google user and internal-client JWTs independently. Administrative Docker access remains outside the database role's trust boundary.

The gateway generates a trace UUID that the adapter must echo and that the gateway checks before release. Saved UI request evidence contains this trace, a SHA-256 of the verified user subject, policy version, field names and completed stages. It does not contain bearer credentials. Stages identify completed checks; they are not separately timed spans or immutable audit records. Denials before tool execution may have no gateway trace, and there is no WORM/SIEM guarantee.

### Start the local signed pilot

Run each service from `gateway_runtime` in a separate terminal using the project Python environment:

```sh
POLICY_PATH="$PWD/../.local/postgres-mcp-policy.json" python -m uvicorn app:app --host 127.0.0.1 --port 8796 --no-access-log
python -m uvicorn postgres_mcp:adapter --host 127.0.0.1 --port 8797 --no-access-log
python operator_console.py
```

The existing Google user login and scoped internal-client token mint permission are used by `deploy/postgres_mcp_client.py`. No user tokens are placed in browser JavaScript. Refresh failures preserve the prior snapshot. The older direct-reader CLI is an administrator diagnostic utility; it is no longer the UI refresh route.

### Verification and remaining network dependency

`deploy/verify_postgres_mcp.py` runs the real signed local workflow and independent SDK, tests forged/missing identity, forbidden tenant/SQL/field overrides and blocked model release, briefly pauses the authorised PostgreSQL test container, verifies failure and snapshot preservation, restores the container in a finally block and confirms recovery. Unit tests separately inject expired identities, adapter outages, wrong database tenant, cross-tenant/trace substitution and unknown release fields. These scopes must remain distinct in demonstrations.

There was no existing VPN tunnel in the authorised GCP project when checked. **No private cloud-to-Mac route has been established, and these local MCP changes have not been deployed to Cloud Run.** The cloud code rejects this local-loopback PostgreSQL transport when `K_SERVICE` is set. A stable customer VPN endpoint/private route or an adapter hosted inside the GCP VPC is still required. Moving the database connection to cloud also requires a deployed secret store and customer-managed adapter workload identity; do not expose the Docker socket or PostgreSQL publicly as a shortcut.

Evidence is in `tmp/postgres/mcp-checks.json`, `mcp-browser-tests.json`, and `mcp-unit-tests.log`. No model requests, generated JavaScript, business-data mutations or automatic model-release enablement were part of this milestone.
