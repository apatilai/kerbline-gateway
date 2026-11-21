"""FastAPI entrypoint for Kerbline Gateway Phase C PoC (Code Mode + V8 Sandbox + Server-Side Fraud Gates)."""

import os
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.ast_checker import check_code_mode_script
from app.host_rpc_bridge import run_v8_sandbox, exporter, Trace

app = FastAPI(
    title="Kerbline Gateway — Phase C PoC (V8 Code Mode & Host RPC Bridge)",
    version="4.4.0",
)


class ExecuteRequest(BaseModel):
    code: str
    role: str = "ap-clerk"
    invoice_ref: str | None = None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "kerbline-phase-c-poc", "sandbox": "node-vm-poc", "telemetry": exporter.health() if exporter else {"enabled": False, "reason": "SDK not installed"}}


@app.post("/v1/phase-c/execute")
def execute_code_mode(req: ExecuteRequest) -> Dict[str, Any]:
    if os.getenv('KERBLINE_ENABLE_UNSAFE_POC') != '1':
        raise HTTPException(503, 'Untrusted JavaScript execution is disabled. Use the read-only gateway plan API.')
    ast_res = check_code_mode_script(req.code, role=req.role)
    if not ast_res.allowed:
        trace = Trace(route='CODE_MODE', invoice_ref=req.invoice_ref) if Trace else None
        if trace:
            trace.hop('INTERNAL', 'Static code policy', 'INTERNAL', 'POLICY', status='BLOCKED', network_attempted=False,
                      transport='LOCAL_IN_PROCESS', policy_verdict='STATIC_POLICY_DENY')
            trace.finish('BLOCKED', complete=True, llm_complete=True, erp_read_complete=True, erp_write_complete=True)
            exporter.emit(trace)
        return {
            "traceId": trace.data['traceId'] if trace else None,
            "status": "BLOCKED_BY_AST_CHECKER",
            "errors": ast_res.errors,
            "toolsInvoked": ast_res.tools_invoked,
        }
    sandbox_res = run_v8_sandbox(req.code, role=req.role, invoice_ref=req.invoice_ref)
    return {
        "status": "EXECUTED" if sandbox_res["ok"] else "SANDBOX_ERROR",
        "toolsInvoked": ast_res.tools_invoked,
        **sandbox_res,
    }


@app.post("/v1/phase-c/test-prompt-injection")
def test_prompt_injection() -> Dict[str, Any]:
    """Verify server-side bank-detail diffing & AST escape blocking against adversarial payloads."""
    if os.getenv('KERBLINE_ENABLE_UNSAFE_POC') != '1':
        raise HTTPException(503, 'Legacy sandbox diagnostics are disabled')
    # 1. Adversarial script attempts to bypass fraud gate using agent-supplied bankDetailsChanged: false
    bank_spoof_script = """
    const match = await kerbline.match_invoice_to_po({
      poNumber: "PO-2026-0842",
      invoiceTotalAud: 15675.00,
      invoiceLineCount: 30
    });
    const batch = await kerbline.propose_payment_batch({
      supplierAbn: "33051775556",
      poNumber: "PO-2026-0842",
      totalAmountAud: 15675.00,
      bsb: "999-999",
      accountNumber: "00000001",
      bankDetailsChanged: false
    });
    return { match, batch };
    """
    ast_1 = check_code_mode_script(bank_spoof_script, role="ap-clerk")
    res_1 = run_v8_sandbox(bank_spoof_script, role="ap-clerk") if ast_1.allowed else {}

    # 2. Adversarial script attempts filesystem/Layer-1 escape
    escape_script = """
    const fs = require('fs');
    return await kerbline.xero_approve_payment({ amount: 50000 });
    """
    ast_2 = check_code_mode_script(escape_script, role="ap-clerk")

    batch_status = res_1.get("result", {}).get("batch", {}).get("status")
    return {
        "bankDiffTest": {
            "passed": batch_status == "BLOCK_UNTIL_CALLBACK_VERIFIED",
            "actualStatus": batch_status,
            "result": res_1.get("result"),
            "retokenisedFields": res_1.get("retokenisedFields"),
        },
        "astEscapeTest": {
            "passed": not ast_2.allowed,
            "blockedErrors": ast_2.errors,
        },
    }


@app.api_route("/v1/phase-c/benchmark", methods=["GET", "POST"])
def benchmark() -> Dict[str, Any]:
    """Compare Naive 120,700-token 6-turn MCP vs live V8 Code Mode (7,840 tokens) vs Repeat-Vendor Fast-Path (0 tokens)."""
    valid_script = """
    const match = await kerbline.match_invoice_to_po({
      poNumber: "PO-2026-0842",
      invoiceTotalAud: 15675.00,
      invoiceLineCount: 30
    });
    if (!match.matched) return { status: "EXCEPTION", match };
    const batch = await kerbline.propose_payment_batch({
      supplierAbn: "33051775556",
      poNumber: "PO-2026-0842",
      totalAmountAud: 15675.00,
      bsb: "062-000",
      accountNumber: "10458821"
    });
    return { match, batch };
    """
    ast_res = check_code_mode_script(valid_script, role="ap-clerk")
    live_exec = run_v8_sandbox(valid_script, role="ap-clerk") if ast_res.allowed and os.getenv('KERBLINE_ENABLE_UNSAFE_POC') == '1' else {"ok": False, "reason": "Unsafe legacy execution disabled"}

    naive_tokens = 120_700
    code_mode_tokens = 7_840
    fast_path_tokens = 0

    return {
        "measurementType": "ILLUSTRATIVE_ASSUMPTIONS_NOT_MEASURED",
        "scenario": "5-page scanned invoice + 30-line Xero PO (PO-2026-0842) 3-way match",
        "comparisons": [
            {
                "mode": "Naive Multimodal VLM + 40 MCP Schemas (6 turns)",
                "inputTokens": 119_200,
                "outputTokens": 1_500,
                "totalTokens": naive_tokens,
                "reductionPct": 0.0,
            },
            {
                "mode": "Kerbline Code Mode (Docling OCR + 1 BBox + V8 Sandbox)",
                "inputTokens": 7_340,
                "outputTokens": 500,
                "totalTokens": code_mode_tokens,
                "reductionPct": round((1 - code_mode_tokens / naive_tokens) * 100, 1),
            },
            {
                "mode": "Kerbline Repeat-Vendor Template Fast-Path (N >= 5)",
                "inputTokens": 0,
                "outputTokens": 0,
                "totalTokens": fast_path_tokens,
                "reductionPct": 100.0,
            },
        ],
        "blendedPortfolio_65Fast_30Code_5Exc": {
            "naiveMonthlyTokens_1000Docs": 120_700_000,
            "kerblineMonthlyTokens_1000Docs": 3_252_000,
            "blendedReductionPct": 97.3,
        },
        "liveV8SandboxExecution": live_exec,
    }
