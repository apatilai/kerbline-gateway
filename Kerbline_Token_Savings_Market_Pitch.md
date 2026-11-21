# Kerbline Gateway: Low-Token Architecture Market Pitch & CFO/CISO ROI Guide

**Why generic multimodal AI agents blow up enterprise budgets—and how Kerbline's 5-layer token-minimisation stack turns unpredictable AI spend into a predictable, sub-cent unit-economic moat.**

---

## 1. The Market Problem: Why Naive Multimodal AI Agents Blow Up Token Bills

Most finance and IT leaders discover the same painful truth when they pilot a generic AI agent (e.g., an LLM wired directly to ERP/CRM MCP servers and document uploads): **a workflow that looks cheap on 5 test invoices becomes financially unviable at 5,000 invoices/month.**

Four compounding multipliers drive this token explosion:

1. **Vision Token Explosion (Full-Page Images):** Sending scanned PDFs or photos directly to a frontier Vision-Language Model (VLM) converts every page into high-resolution image tiles. At standard GPT-4o high-resolution tiling (up to 6 × 512px tiles × 170 tokens + 85 base tokens), a single page costs **~1,105 input tokens**. A 5-page invoice + delivery docket bundle burns **~5,525 vision tokens** before a single word of reasoning occurs.
2. **MCP Schema Bloat:** Connecting an agent directly to raw vendor MCP servers (Xero, NetSuite, M365, SharePoint, Slack/Teams) injects **30–50+ tool schemas** (names, descriptions, nested JSON parameters) into the system prompt on every single turn—typically **15,000–30,000+ input tokens per turn** even before reading the document.
3. **Multi-Turn Agentic Loops:** A standard 3-way PO match (extract invoice → search supplier → fetch open POs → check line-item variances → verify bank details → draft bill) takes **6–10 sequential tool-calling round-trips**. Every round-trip re-sends the entire conversation history, vision payload, and 20,000+ tokens of MCP schemas—multiplying input token consumption by **6×–10×**.
4. **Repeat-Vendor Re-Extraction:** In Australian mid-market AP, **60–75% of monthly invoices come from the same recurring suppliers** (e.g., Telstra, AWS, Bunnings, major freight/wholesale partners). A naive AI agent pays frontier LLM tokens to "re-discover" the exact same invoice layout every single month.

**Result:** A single 5-page invoice processed by a naive VLM + raw-MCP agent routinely consumes **120,000–200,000+ tokens** across 6–10 turns, costing **A$0.45–A$0.90+ per invoice** in raw LLM inference alone—with unpredictable latency (20–60+ seconds) and zero deterministic cost ceiling.

---

## 2. Kerbline's 5-Layer Token-Minimisation Stack (The Cost & Latency Moat)

Kerbline Gateway treats frontier LLMs as an **exception-handling last resort**, not a default OCR and orchestration engine.

| Layer | Mechanism | What It Eliminates | Impact on Cost, Latency & Risk |
| :--- | :--- | :--- | :--- |
| **1. Edge Modality Collapse & Deterministic Pre-Pass** | Local layout OCR (`docling-v2-local` in Sydney) + regex/checksum validators (ABN, BSB, GST arithmetic). Cropped regions escalate to an in-region VLM **only** when OCR confidence `< 0.92` (and only after pre-VLM PII redaction). | **~90–95%** of vision image tokens. | Collapses 5,525 vision tokens → ~600–900 compact JSON tokens; eliminates unredacted PII exposure. |
| **2. Repeat-Vendor Deterministic Template Fast-Path** | After *N* verified invoices from a recurring supplier (e.g., Telstra, AWS), Kerbline promotes a deterministic extraction & PO-matching template. | **100% of LLM tokens** on matching repeat-vendor invoices. | **0 LLM tokens**, sub-second extraction, zero hallucination risk on known layouts. |
| **3. Air-Gapped V8 Code Mode** | Agents see only **2 meta-tools** (`search_tools()`, `execute_code()`) instead of 40+ raw MCP schemas. Multi-step ERP joins, pagination, and validation execute in a single script inside a network-disabled V8 isolate. | **~90%+ of schema bloat** and **6–10× multi-turn loop tax** (aligned with Cloudflare's Code Mode benchmarks: 32% savings on simple tasks, **81% on batch/multi-step tasks**, and 1.17M → ~1k schema token reduction). | Cuts 6–10 LLM round-trips down to **1–2 turns**; slashes p95 latency and eliminates prompt-bloat timeouts. |
| **4. 4-Tier Caching** | (1) 30-day SHA-256 document dedupe; (2) Vendor-template cache; (3) Provider prompt prefix caching (static instructions ≥ 1,024 tokens for **50%+ input discount**); (4) Partitioned semantic cache (tenant + role + scope). | Duplicate invoice reprocessing and redundant prompt prefill costs. | Instant 0-token rejection of duplicate scans; 50%+ discount on remaining LLM prefixes. |
| **5. Adaptive Routing, Structured Outputs & Compaction** | Compact/tier-1 models handle standard exceptions; frontier models trigger only on complex handwriting/disputes. Schema-constrained outputs + rolling compaction (`trigger 6k → ≤ 1.5k`) cap long sessions. | Frontier-model over-provisioning and verbose output tokens. | Keeps blended LLM cost low while preserving high accuracy on edge cases. |

### Why This Is an Unfair Moat (For Both CFOs and CISOs)
- **For the CFO:** Competitors wrapping raw LLM APIs cannot match Kerbline's unit economics without rebuilding local OCR, a V8 sandbox, and deterministic template caching.
- **For the CISO:** Every layer that removes tokens **also shrinks attack surface**—fewer tokens sent to external models means less PII exposure, less prompt-injection surface, and deterministic Layer 2 domain-tool execution.

---

## 3. Concrete AUD ROI & Volume Modelling

### Modelling Assumptions (Transparent & Conservative)
- **Document Profile:** Average 3-page invoice + delivery docket bundle (mix of clean PDFs, scans, and 65% recurring suppliers).
- **Naive VLM + Raw MCP Agent:** ~120,000 input tokens + ~2,500 output tokens across 7 turns per document. At frontier model rates (~US$2.50/1M input, US$10.00/1M output; **1 USD = 1.55 AUD**), raw LLM cost = **~A$0.50 per document**.
- **Kerbline Gateway (5-Layer Stack):**
  - **65% Repeat-Vendor Fast-Path:** 0 LLM tokens (local OCR + deterministic template).
  - **35% Exception / New-Vendor Path:** ~4,500 input tokens (with 50% prefix cache discount on static system prompt) + ~400 output tokens via Code Mode + compact/cascaded models = **~A$0.015–A$0.025** per exception doc (**~A$0.007 blended LLM cost/doc** across all volume).
  - **Kerbline Media Processing:** Indicative **A$0.04/page** (at 3 pages/doc = A$0.12/doc; volume-tiered down at 10k–50k/mo).
- **Human AP Clerk Baseline:** Manual keying + PO chasing = ~8 mins/invoice @ A$45/hr fully loaded (**A$6.00/invoice**). Kerbline reduces manual handling by `>= 50%` (conservative 4 mins saved = **A$3.00/invoice** in reclaimed capacity).

### Monthly Cost & ROI Comparison (AUD, ex GST)

| Monthly Volume | Naive VLM + MCP (LLM API Only) | Kerbline LLM Inference (Blended) | **LLM Token Savings (%)** | Kerbline Platform + Media (Est.)* | Reclaimed AP Clerk Capacity (`>= 50%` time saved) | **Net Monthly Business Value** |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1,000 docs/mo** *(Mid-Market)* | A$500 / mo | **~A$7 / mo** | **~98.6% LLM** *(~75%+ incl. media)* | A$2,500 (Starter) + A$120 media = **A$2,620** | **A$3,000 / mo** *(67 hrs saved)* | **+A$373 / mo** + Fraud & CPS 234/230 Governance |
| **10,000 docs/mo** *(Upper Mid-Market)* | A$5,000 / mo | **~A$70 / mo** | **~98.6% LLM** *(~82%+ incl. media)* | A$5,000 (Growth) + A$800 media* = **A$5,800** | **A$30,000 / mo** *(667 hrs saved)* | **+A$24,130 / mo** *(4.2× ROI)* |
| **50,000 docs/mo** *(Enterprise)* | A$25,000 / mo | **~A$350 / mo** | **~98.6% LLM** *(~85%+ incl. media)* | A$9,000 (Enterprise) + A$3,000 media* = **A$12,000** | **A$150,000 / mo** *(3,333 hrs saved)* | **+A$137,650 / mo** *(11.5× ROI)* |

*\*Assumes volume-discounted media bundles at 10k (A$0.08/doc) and 50k (A$0.06/doc). Even at the standard Starter A$0.04/page list rate, total AI + media cost is **A$0.127/doc vs. A$0.50/doc naive LLM (~75% reduction)** before counting Code Mode's elimination of multi-turn latency.*

---

## 4. How to Pitch & Prove This in a 4-Week PoV (Without Sounding Contrived)

**The GTM Trap to Avoid:** Never tell a CFO, *"We saved you 70% compared to how expensively we could have built it."* A mid-market CFO who currently pays AP clerks or Dext/ApprovalMax has **no existing LLM bill**—so benchmarking against Kerbline's own naive mode as the headline ROI metric feels manufactured.

Instead, structure the PoV pitch around **four CFO-grade proof points**:

1. **Lead with Human-Hour ROI & Fraud Prevention (The Business Case):**
   - Measure the customer's **Week 0 AP clerk handling-time baseline** (minutes/invoice) before turn-on.
   - Prove `>= 50%` handling-time reduction in Weeks 3–4 (e.g., 8 mins → < 4 mins), translating directly into A$3,000–A$30,000+/month of reclaimed finance capacity—plus deterministic blocking of `> A$5k` payments and supplier bank-detail fraud.
2. **Anchor AI Spend on Predictable Unit Economics per Invoice (Cost Certainty):**
   - Pitch the AUD Cost Ledger not as "discounting a fake baseline," but as **guaranteed unit-economic predictability**: *"Under standard agentic AI, a 5-page invoice costs A$0.45–A$0.90 in volatile token spend. Kerbline proves a predictable, sub-cent LLM cost per invoice (and A$0.00 LLM tokens on repeat suppliers) before you sign an annual contract."*
3. **Use the Weeks 1–2 Naive Shadow Baseline as Architectural Proof for IT/Architecture:**
   - Frame the Weeks 1–2 shadow measurement as **AI FinOps due diligence**: proving to the CIO/CFO that when they expand from AP to Claims, KYC, or NDIS Intake (10k–50k docs/mo), Kerbline's gateway prevents the **5×–10× token bill explosion** that plagues DIY Copilot/Bedrock/Claude connectors.
4. **Guarantee Zero Month-End Disruption with Soft-Stop Budget Caps:**
   - Every department gets a configurable daily AUD budget cap: **80% triggers a CFO/IT alert**, and **100% gracefully degrades to Suggest Mode (human queue)** rather than halting urgent month-end invoice processing.
