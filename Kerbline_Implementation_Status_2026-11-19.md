# Kerbline Gateway — Verified Implementation Status (19 November 2026)

> **Executive Summary:** As of **19 November 2026**, Kerbline is a **working, deployed read-only MCP pilot with real GCP inventory**. It is **not yet** a production-ready enterprise gateway or a complete Code Mode cost-optimisation product. This ledger reflects the actual implementation and recorded test evidence—superseding any forward-looking claims in the `v4.4` target specifications.

**Status Key:** `Deployed` = working in the GCP pilot | `Local` = working locally | `PoC` = limited prototype | `Planned` = not implemented | `Disabled` = intentionally unavailable.

---

## 1. MCP Gateway and Access Control
| Feature | Status | Where We Stand |
| :--- | :--- | :--- |
| Mandatory API Gateway front door | **Deployed** | GCP API Gateway fronts the pilot runtime. |
| Direct backend bypass protection | **Deployed** | Cloud Run IAM plus pinned calling-workload identity. |
| MCP initialization and tool discovery | **Deployed** | Tested with an independent MCP SDK. |
| MCP tool execution | **Deployed** | Supports the approved read-only tools. |
| Streamable HTTP transport | **Deployed, limited** | JSON responses; no resumable SSE or persistent sessions. |
| Signed user identity verification | **Deployed** | Google JWT signature, issuer, audience and expiry checks. |
| Signed internal-client identity | **Deployed** | Required for real infrastructure inventory. |
| Server-controlled role assignments | **Deployed** | Roles come from verified subject bindings. |
| Role-filtered tool discovery | **Deployed** | Unattested clients cannot discover inventory. |
| Tool-level authorisation | **Deployed** | Unapproved tools are denied. |
| Resource-level authorisation | **Deployed** | Explicit resource registry and role scopes. |
| Strict argument validation | **Deployed** | Arbitrary URLs, projects, raw fields and role overrides are rejected. |
| Origin and request-size protection | **Deployed** | Unapproved origins and oversized requests are blocked. |
| Customer enterprise OIDC onboarding | **Planned** | Current pilot uses the configured Google identity. |
| SAML, SCIM and automated deprovisioning | **Planned** | No enterprise identity lifecycle integration yet. |
| General MCP OAuth/client onboarding | **Planned** | Not a plug-and-play public MCP service for arbitrary clients. |

---

## 2. Real Integrations and Infrastructure Visibility
| Feature | Status | Where We Stand |
| :--- | :--- | :--- |
| Real GCP Cloud Run integration | **Deployed** | Reads actual Google Cloud API metadata. |
| Approved service allowlist | **Deployed** | Currently the gateway and adapter services only. |
| Narrow cloud IAM | **Deployed** | `run.services.get` granted at those two services. |
| Actual resource names and full IDs | **Deployed** | Visible in the operator dashboard. |
| Project and deployment region | **Deployed** | Actual approved resource identifiers. |
| Control-plane readiness | **Deployed** | Ready, not ready, reconciling or unknown. |
| Traffic-serving revisions and percentages | **Deployed** | Uses reported traffic allocation. |
| Latest created versus latest ready revision | **Deployed** | Shown separately from serving revisions. |
| Timestamped observations and ingress | **Deployed** | Includes observation and resource update times. |
| Active instance counts and application health | **Planned** | "Ready" does not prove an instance is running. |
| Continuous monitoring and alerts | **Planned** | Inventory refresh is on demand. |
| Azure, AWS and Kubernetes inventory | **Local** | CLI collectors/imports exist; not equivalent live governed integrations. |
| Real ERP/database/mainframe connectors | **Planned** | No customer business system connected yet. |
| General vendor MCP proxy/connector registry | **Planned** | Current adapter integration is specifically implemented. |
| Connector schema pinning and drift management | **Planned** | No complete connector lifecycle system. |

---

## 3. Data Protection and Network Controls
| Feature | Status | Where We Stand |
| :--- | :--- | :--- |
| Internal-only adapter ingress | **Deployed** | Adapter uses internal Cloud Run ingress. |
| Gateway VPC egress | **Deployed** | Dedicated Sydney VPC/subnet and Private Google Access. |
| Private-only customer front door | **Planned** | Current API Gateway front door is public and authenticated. |
| Keyless workload authentication | **Deployed** | Managed identity tokens; no service-account keys created. |
| Positive response-field filtering | **Deployed** | Only approved fields are returned. |
| Environment/secret configuration exclusion | **Deployed** | Inventory requests do not request those fields. |
| Internal-only inventory release | **Deployed** | Requires approved client identity as well as user permission. |
| Inventory blocked from model route | **Deployed** | `/v1/answer` rejects inventory requests. |
| Fail-closed response validation | **Deployed** | Invalid or cross-scope results are rejected. |
| General-purpose PII/DLP detection | **Planned** | Schema filtering is not a general DLP engine. |
| AU-pattern text masking | **PoC** | Limited pattern/checksum detection exists. |
| Production reversible token vault | **Planned** | Existing prototype lacks production storage and lifecycle controls. |
| Trusted automatic visual redaction | **Planned** | No complete server-side visual protection pipeline. |
| Legacy image-to-model HTTP release | **Disabled** | Blocked pending trusted visual detection. |
| Guaranteed Australian-only inference | **Not established** | Existing model tests explicitly use global inference for synthetic data. |

---

## 4. Code Mode, Token Reduction and Cost Visibility
| Feature | Status | Where We Stand |
| :--- | :--- | :--- |
| Bounded local execution plans | **Deployed** | Predefined filtering/aggregation operations. |
| Local aggregation before inference | **Deployed** | Demonstrated with synthetic invoice records. |
| Arbitrary generated JavaScript | **Disabled** | Not enabled in the new runtime. |
| Hardened Code Mode sandbox | **Planned** | No production gVisor/Firecracker execution boundary. |
| Legacy Node VM and Host RPC bridge | **PoC** | Synthetic ERP demonstration; not production isolation. |
| General `search_tools` + `execute_code` experience | **Planned** | Current tool discovery and bounded plans are more limited. |
| Repeat-vendor zero-token template engine | **Planned** | No operational template-learning/matching engine. |
| Document deduplication cache | **Planned** | Not implemented as a complete runtime feature. |
| Semantic cache and provider prefix-cache orchestration | **Planned** | No complete optimisation layer. |
| Adaptive model routing and cascades | **Planned** | No production multi-model router. |
| Conversation/context compaction | **Planned** | Not implemented. |
| Actual provider token usage | **Deployed (synthetic)** | Includes reported input, output and reasoning usage. |
| Usage retained for rejected model answers | **Deployed** | Validation failure does not discard reported consumption. |
| Measured payload reduction | **Verified synthetic benchmark** | `1,839 → 46` input tokens (**97.5%** reduction for that payload). |
| Net AUD savings measurement | **Planned** | Infrastructure, planning, quality and full workflow costs are not reconciled. |
| Per-prompt token/cost ledger | **Local** | Working telemetry views; costs depend on supplied rate cards. |
| Provider billing reconciliation | **Planned** | Estimated cost is not a reconciled invoice. |
| Enforced token budgets, department limits and loop limits | **Planned** | Console budget fields do not establish runtime enforcement. |

---

## 5. Customer UI and Business Workflows
| Feature | Status | Where We Stand |
| :--- | :--- | :--- |
| Real inventory operator dashboard | **Local, connected to GCP** | Shows live-query results from the deployed gateway. |
| Refresh approved inventory | **Working** | Performs a governed read without an LLM call. |
| Failed-refresh handling | **Working** | Preserves and labels older observations. |
| Desktop/mobile layout | **Verified** | Browser checks passed. |
| Guided customer setup | **Local** | Saves environment and readiness metadata. |
| Infrastructure imports, search and details | **Local** | Working persistent console features. |
| Customer presentation view | **Local** | Uses saved records and identifies gaps. |
| Role-filtered console views | **Local** | API-side projections and credential-based permissions. |
| Traffic explorer and prompt ledger | **Local** | Received telemetry and synthetic preview are separated. |
| Policy editor | **Local, draft only** | Does not publish into the deployed runtime. |
| Runtime policy enforcement | **Deployed** | Policy is published through a reviewed deployment revision. |
| Invoice work queue/review references | **Local** | No complete document-processing workflow behind it. |
| AP matching and bank-change guardrail examples | **PoC** | Use synthetic ERP records. |
| Durable approvals and callback verification | **Planned** | No complete approval/resume lifecycle. |
| ERP writes, posting and reconciliation | **Disabled / planned** | No live business mutation workflow. |
| Full PDF OCR pipeline | **Planned** | Basic normalisation and region-processing prototypes exist. |
| Audio transcription, mailbox ingestion and mobile offline capture | **Planned** | Not operational product features. |

---

## 6. Audit, Deployment and Production Operations
| Feature | Status | Where We Stand |
| :--- | :--- | :--- |
| Dedicated GCP pilot deployment | **Deployed** | Separate gateway, adapter, identities and network resources. |
| Deployment scripts and image digest evidence | **Working** | Reproducible pilot steps and recorded revisions. |
| Complete Terraform/Helm customer installation | **Planned** | Deployment scripts are not complete infrastructure-as-code. |
| Versioned policy and digest | **Deployed** | Recorded with execution decisions. |
| Structured Cloud Logging decisions | **Deployed** | Sampled inventory records contained no inventory payloads or bearer tokens. |
| Correlation IDs and evidence export | **Working** | Available in runtime/local evidence. |
| Local hash-chain audit verification | **Local** | Not externally anchored immutable storage. |
| WORM audit retention and SIEM delivery | **Planned** | No complete immutable audit pipeline. |
| Durable request orchestration/queues | **Planned** | No general cross-service workflow engine. |
| Multi-customer tenant isolation | **Planned** | Current design is one installation/customer boundary. |
| Production HA, recovery and DR validation | **Planned** | No validated production RTO/RPO or availability guarantee. |
| External penetration test/compliance certification | **Not completed** | Test success is not certification. |
| Full independent cross-service/network attestation | **Planned** | Current telemetry is not proof of complete traffic coverage. |

---

## Where That Leaves the Product & Next Milestones

- **What we can truthfully demonstrate today:** An authenticated read-only MCP gateway that enforces approved user, client, tool, and resource access; retrieves real GCP Cloud Run inventory; controls which fields are released; and provides execution evidence (**106 local tests**, **14 live inventory checks**, MCP SDK interoperability, and browser checks recorded).
- **What we cannot yet sell it as:** A complete enterprise DLP gateway, hardened arbitrary-code platform, universal connector gateway, automated AP product, or proven net-cost-saving service.
- **Highest-Value Next Engineering Work:**
  1. **Customer enterprise identity** (OIDC onboarding + SAML/SCIM lifecycle).
  2. **Runtime policy administration connected to the UI** (publishing reviewed, signed policy revisions from the console into the live runtime).
  3. **One real business-system read-only adapter** (e.g., read-only Xero/ERP adapter with positive field filtering and strict argument validation).
