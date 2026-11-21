# Kerbline customer console

A working, persistent **customer inventory, readiness and telemetry console**, not a production payment gateway.

See [Gateway traffic and per-prompt usage](TELEMETRY.md) for the live collector, instrumented Phase B/C builds, safe metadata contract, server-enforced roles, usage accounting and illustrated preview.
The previous automatic simulation fallback and browser-only approvals have been removed from the served UI. The legacy `explorer.html` is retained as an unserved historical file; the server serves only its explicit static allow-list.

## Run locally

```sh
cd kerbline_explorer_ui
python3 server.py
```

Open http://127.0.0.1:8787. The default loopback listener is accessible to local users without a token. To require operator authentication, set `CONSOLE_ACCESS_TOKEN` in your environment before starting. No cloud account or upstream service is contacted on startup. This console does not read local cloud credentials.

Records persist in `data/console.sqlite3`. To use another protected location set `KERBLINE_DATA_PATH`. All records belong to **one installation / customer environment**; this is not a multi-tenant service. Deploy separate instances and data volumes for separate customers. SQLite uses a local hash chain for audit consistency, without external anchoring or immutable retention. Do not enter secrets or sensitive invoice contents in metadata fields. Database encryption is a platform responsibility.

## Customer infrastructure visibility

1. Save the customer's environment, account/project/subscription, region, network and operations owner in Guided setup.
2. Run `tools/collect_inventory.py` with the customer's own read-only CLI credentials, or register resources manually.
3. Review and import the generated JSON in Infrastructure.
4. Select resources to see **actual resource name, full ID, account, region, type, mapped component, runtime observation, revision, source and observation/import timestamps**.
5. Refresh inventory by rerunning the collector and importing again. The top-right refresh reloads console records; it does not poll the cloud. Observations older than 15 minutes, or without timestamps, are marked stale/unobserved.

Examples (from this directory):

```sh
python3 tools/collect_inventory.py --provider gcp --gcp-mode cloud-run --scope YOUR_PROJECT --region YOUR_REGION --output inventory-gcp.json
# Optional broader asset inventory (requires Cloud Asset Inventory read access):
python3 tools/collect_inventory.py --provider gcp --scope YOUR_PROJECT --output inventory-assets.json
python3 tools/collect_inventory.py --provider azure --scope YOUR_SUBSCRIPTION --output inventory-azure.json
python3 tools/collect_inventory.py --provider aws --scope YOUR_ACCOUNT_ID --region YOUR_REGION --output inventory-aws.json
python3 tools/collect_inventory.py --provider kubernetes --scope YOUR_CONTEXT --namespace YOUR_NAMESPACE --output inventory-k8s.json
```

The default name filter is `kerbline`. Set `--name-contains` to your deployment prefix. GCP cloud-run mode needs Cloud Run service list/read access and reports the first container image, CPU/memory limits, revision and runtime identity. The broader cloud-asset mode needs Cloud Asset Inventory read access; Cloud Run detail needs service read access. Azure needs subscription resource read access. AWS checks the caller account then uses the regional tagging API (untagged resources may be absent). Kubernetes reads selected workload, service and PVC resources in the chosen namespace. No credentials, secret values, pod environment variables, or arbitrary resource properties are exported. Component mappings are suggestions derived from names/types.

Collector failures return a nonzero exit code rather than creating successful observations. GCP Cloud Run detail failures appear in coverage notes while retaining the asset record. CLI snapshots are **reported**, not independently verified by the server. They cannot prove network isolation, residency, recovery readiness or application health. Read `coverageNotes` in the file before import. Imports merge records, never silently delete missing resources. Empty collection results are not importable. Review/remove old inventory records explicitly; removal does not touch the cloud resource.

Minimal import format:

```json
{
  "source": "Customer infrastructure export",
  "resources": [
    {
      "provider": "gcp",
      "name": "YOUR_REAL_SERVICE_NAME",
      "resourceId": "YOUR_FULL_RESOURCE_ID",
      "account": "YOUR_PROJECT_ID",
      "region": "YOUR_REGION",
      "type": "run.googleapis.com/Service",
      "component": "perception",
      "runtimeStatus": "",
      "version": "",
      "observedAt": ""
    }
  ]
}
```

Blank status, revision and observation time mean unknown. Never fill them with invented values. Supported components: ingress, perception, vault, execution, model, erp, identity, audit, database, console, other. Resource identity uses provider + account + full resource ID (name fallback). Importing the same ID updates the record.

## Features available now

- Resumable-by-navigation eight-step setup with saved environment, inventory and policy metadata; step selection itself is tab-local.
- Infrastructure search, provider filter, detail views, resource registration/removal and JSON export.
- Logical connection mapping with explicit unverified integration states.
- Persisted **draft** policies, not execution policy publication.
- Persisted unverified manual review references, with local rejection reasons. No document upload, OCR execution, automatic approval, bank verification or ERP write is asserted.
- Readiness reports that keep unavailable integrations blocked.
- Local audit chain verification and complete evidence JSON export. UI displays latest 200 events.
- Live traffic explorer and per-invoice prompt ledger with received/preview isolation, timestamped metadata and unknown values preserved. Provider billing reconciliation is not connected.
- Customer presentation mode using current saved records, with explicit integration gaps.
- Server-configured credential roles, API-side field projections and mutation permissions; credentials stay in tab memory. Federated SSO remains unconnected.

## Container deployment

Build with `docker build -t kerbline-console .`. Supply a strong `CONSOLE_ACCESS_TOKEN` through the customer's secret manager and set `CONSOLE_ORIGIN` to the exact external HTTPS origin (no path). Mount a persistent, protected volume at `/data`, writable by UID 10001. Place the container behind the customer's TLS reverse proxy and network access controls. The app refuses non-loopback binding without an access token, checks Host/Origin on requests, limits JSON bodies, and serves only its explicit static files. It does not provide SSO, per-user roles, database encryption or a full production security boundary.

Runtime settings: `HOST`, `PORT`, `KERBLINE_DATA_PATH`, `CONSOLE_ACCESS_TOKEN`, `CONSOLE_ORIGIN`. Optional runtime metadata: `CLOUD_REGION`, `K_SERVICE`, `K_REVISION`. These environment-derived fields are labelled as reported runtime metadata, not residency verification.

## Production gates still required

Actual deployment templates and provisioning; authenticated cloud discovery / monitoring; SSO/SCIM and production business-action authorization; document ingestion/OCR/protection pipeline; verified regional model routing and network controls; encrypted persistent vault; live ERP OAuth and adapter; server-enforced versioned policies; durable approvals and callback evidence; idempotent posting and reconciliation; external immutable audit storage; provider billing reconciliation; recovery and security validation. These cannot truthfully be marked complete by a local inventory import.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check app.js
```
