"""Host RPC Bridge, Mock Xero Sandbox (Supplier Master + 30-line PO), Return-Path Re-Tokenisation, and V8 Runner."""

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from kerbline_telemetry import Trace, exporter
except ImportError:
    Trace = exporter = None

NODE_BIN = shutil.which("node") or "/usr/local/bin/node"
SANDBOX_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sandbox" / "v8_runner.mjs"
VAULT_HMAC_KEY = os.environ.get("KERBLINE_POC_VAULT_KEY", "kerbline-poc-ephemeral-hmac-key").encode()

# --- Synthetic Xero Sandbox Data (Zero Real PII) ---
XERO_SUPPLIER_MASTER: Dict[str, Dict[str, Any]] = {
    "33051775556": {
        "supplierId": "xero-sup-telstra-001",
        "name": "Telstra Corporation Ltd (Synthetic)",
        "abn": "33051775556",
        "bsb": "062-000",
        "accountNumber": "10458821",
        "trustedCallbackPhone": "+61 2 8000 1234",
    },
    "83605345721": {
        "supplierId": "xero-sup-aws-002",
        "name": "AWS Australia Pty Ltd (Synthetic)",
        "abn": "83605345721",
        "bsb": "012-003",
        "accountNumber": "88231004",
        "trustedCallbackPhone": "+61 2 9000 5678",
    },
}

# 30-line Purchase Order (PO-2026-0842) totaling A$14,250.00 ex GST (A$15,675.00 incl. GST)
XERO_PURCHASE_ORDERS: Dict[str, Dict[str, Any]] = {
    "PO-2026-0842": {
        "poNumber": "PO-2026-0842",
        "supplierAbn": "33051775556",
        "currency": "AUD",
        "subtotalExGst": 14250.00,
        "gst": 1425.00,
        "totalInclGst": 15675.00,
        "lineItems": [
            {
                "lineNo": i,
                "sku": f"TEL-SVC-{i:03d}",
                "description": f"Managed WAN & SIP Trunk Site #{i:02d}",
                "qty": 1,
                "unitPriceAud": 475.00,
                "lineTotalAud": 475.00,
            }
            for i in range(1, 31)
        ],
    }
}

XERO_DELIVERY_DOCKETS: Dict[str, Dict[str, Any]] = {
    "PO-2026-0842": {
        "docketId": "DD-2026-0911",
        "poNumber": "PO-2026-0842",
        "receivedLines": 30,
        "status": "RECEIVED_IN_FULL",
    }
}


class TokenVault:
    """Non-deterministic HMAC-salted AU PII tokeniser for return-path re-tokenisation."""

    def __init__(self) -> None:
        self.retokenised_count = 0

    def _mint_token(self, prefix: str, raw_value: str) -> str:
        salt = secrets.token_hex(4)
        digest = hmac.new(VAULT_HMAC_KEY, f"{salt}:{raw_value}".encode(), hashlib.sha256).hexdigest()[:10]
        self.retokenised_count += 1
        return f"[{prefix}_{digest}]"

    def retokenise_payload(self, obj: Any) -> Any:
        """Recursively scan RPC response payloads and mask raw BSB/Account/TFN values."""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                lk = k.lower()
                if lk in {"bsb", "accountnumber", "account_number", "tfn"} and isinstance(v, str):
                    prefix = "AU_BSB_TOK" if "bsb" in lk else ("AU_TFN_TOK" if "tfn" in lk else "AU_ACCT_TOK")
                    out[k] = self._mint_token(prefix, v)
                else:
                    out[k] = self.retokenise_payload(v)
            return out
        if isinstance(obj, list):
            return [self.retokenise_payload(item) for item in obj]
        if isinstance(obj, str):
            # Mask inline BSB (XXX-XXX) patterns
            obj = re.sub(r"\b\d{3}-\d{3}\b", lambda m: self._mint_token("AU_BSB_TOK", m.group(0)), obj)
            return obj
        return obj


class HostRPCBridge:
    """Executes Layer 2 domain tools on behalf of the V8 isolate and enforces server-side fraud gates."""

    def __init__(self, role: str = "ap-clerk", trace=None) -> None:
        self.trace = trace
        self.role = role
        self.vault = TokenVault()
        self.audit_events: List[Dict[str, Any]] = []

    def handle_rpc(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from app.ast_checker import ROLE_TOOL_ALLOWLIST
        if tool not in ROLE_TOOL_ALLOWLIST.get(self.role, set()):
            raise PermissionError('RPC is not allowed for this execution role')
        started = time.perf_counter()
        result = self._handle_rpc(tool, args)
        if self.trace:
            blocked = str(result.get('status', '')).startswith('BLOCK')
            self.trace.hop('FRAUD_GATE' if blocked else 'INTERNAL', 'Synthetic ERP adapter', 'INTERNAL',
                           'POLICY' if tool != 'match_invoice_to_po' else 'READ',
                           status='BLOCKED_CALLBACK_REQUIRED' if blocked else 'OK',
                           network_attempted=False, transport='LOCAL_IN_PROCESS', auth_mode='NONE',
                           latency_ms=round((time.perf_counter()-started)*1000,3),
                           policy_verdict='CALLBACK_REQUIRED' if blocked else None)
        return result

    def _handle_rpc(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool == "match_invoice_to_po":
            raw_res = self._match_invoice_to_po(args)
        elif tool == "propose_payment_batch":
            raw_res = self._propose_payment_batch(args)
        elif tool == "change_supplier_bank_details":
            raw_res = self._change_supplier_bank_details(args)
        else:
            raise ValueError(f"Unauthorized Layer 2 tool: {tool}")

        # Mandatory return-path re-tokenisation before payload enters V8/LLM
        safe_res = self.vault.retokenise_payload(raw_res)
        self.audit_events.append({"tool": tool, "status": safe_res.get("status", "OK")})
        return safe_res

    def _match_invoice_to_po(self, args: Dict[str, Any]) -> Dict[str, Any]:
        po_number = str(args.get("poNumber", ""))
        inv_total = float(args.get("invoiceTotalAud", 0.0))
        inv_lines = int(args.get("invoiceLineCount", 0))

        po = XERO_PURCHASE_ORDERS.get(po_number)
        docket = XERO_DELIVERY_DOCKETS.get(po_number)
        if not po or not docket:
            return {"matched": False, "reason": f"PO or docket not found for {po_number}"}

        variance = round(inv_total - po["totalInclGst"], 2)
        lines_match = inv_lines == len(po["lineItems"]) and docket["receivedLines"] == len(po["lineItems"])
        matched = abs(variance) <= 0.01 and lines_match

        return {
            "matched": matched,
            "poNumber": po_number,
            "supplierAbn": po["supplierAbn"],
            "poLineCount": len(po["lineItems"]),
            "docketStatus": docket["status"],
            "poTotalAud": po["totalInclGst"],
            "varianceAud": variance,
            "rawPoPayloadBytesKeptInBridge": len(json.dumps(po)),
        }

    def _propose_payment_batch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        supplier_abn = str(args.get("supplierAbn", ""))
        po_number = str(args.get("poNumber", ""))
        total_aud = float(args.get("totalAmountAud", 0.0))
        proposed_bsb = str(args.get("bsb", "")).strip()
        proposed_acct = str(args.get("accountNumber", "")).strip()

        supplier = XERO_SUPPLIER_MASTER.get(supplier_abn)
        if not supplier:
            return {"status": "BLOCKED", "reason": f"Unknown supplier ABN {supplier_abn}"}

        # SERVER-SIDE BANK-DETAIL DIFFING:
        # Explicitly ignores any agent-supplied args.bankDetailsChanged flag.
        bsb_mismatch = bool(proposed_bsb and proposed_bsb != supplier["bsb"])
        acct_mismatch = bool(proposed_acct and proposed_acct != supplier["accountNumber"])
        if bsb_mismatch or acct_mismatch:
            return {
                "status": "BLOCK_UNTIL_CALLBACK_VERIFIED",
                "reason": "SERVER_SIDE_BANK_DIFF_MISMATCH",
                "ignoredAgentFlag": args.get("bankDetailsChanged"),
                "trustedNumberSource": "erp_vendor_master",
                "trustedCallbackPhone": supplier["trustedCallbackPhone"],
                "supplierId": supplier["supplierId"],
            }

        idempotency_key = hashlib.sha256(f"{supplier_abn}:{po_number}:{total_aud}".encode()).hexdigest()[:16]
        dry_run_plan = {
            "action": "xero_create_draft_payment_batch",
            "supplierId": supplier["supplierId"],
            "poNumber": po_number,
            "totalAmountAud": total_aud,
            "bsb": supplier["bsb"],
            "accountNumber": supplier["accountNumber"],
            "idempotencyKey": idempotency_key,
        }

        if total_aud > 5000.0:
            return {
                "status": "PAUSE_WITH_REDACTED_MEDIA_PREVIEW",
                "approverRole": "finance-controller",
                "dryRunPlan": dry_run_plan,
                "idempotencyKey": idempotency_key,
            }

        return {
            "status": "DRY_RUN_READY_FOR_EXECUTION",
            "dryRunPlan": dry_run_plan,
            "idempotencyKey": idempotency_key,
        }

    def _change_supplier_bank_details(self, args: Dict[str, Any]) -> Dict[str, Any]:
        supplier_abn = str(args.get("supplierAbn", ""))
        supplier = XERO_SUPPLIER_MASTER.get(supplier_abn)
        return {
            "status": "BLOCK_UNTIL_CALLBACK_VERIFIED",
            "reason": "ALWAYS_HITL_BANK_CHANGE",
            "trustedNumberSource": "erp_vendor_master",
            "trustedCallbackPhone": supplier["trustedCallbackPhone"] if supplier else "UNKNOWN_SUPPLIER",
        }


def _run_v8_sandbox(code: str, role: str = "ap-clerk", trace=None) -> Dict[str, Any]:
    """Execute AST-verified JS inside air-gapped V8 isolate with Host RPC Bridge."""
    t0 = time.perf_counter()
    bridge = HostRPCBridge(role=role, trace=trace)

    proc = subprocess.Popen(
        [
            NODE_BIN,
            "--max-old-space-size=64",
            "--disallow-code-generation-from-strings",
            str(SANDBOX_SCRIPT_PATH),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={},  # Zero environment variables / zero secrets in V8 process
    )

    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"type": "init", "code": code}) + "\n")
        proc.stdin.flush()

        final_msg: Optional[Dict[str, Any]] = None
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            mtype = msg.get("type")
            if mtype == "rpc_request":
                try:
                    res = bridge.handle_rpc(msg["tool"], msg.get("args", {}))
                    proc.stdin.write(json.dumps({"type": "rpc_response", "id": msg["id"], "result": res}) + "\n")
                except Exception as exc:
                    proc.stdin.write(json.dumps({"type": "rpc_response", "id": msg["id"], "error": str(exc)}) + "\n")
                proc.stdin.flush()
            elif mtype in {"result", "error"}:
                final_msg = msg
                break

        proc.wait(timeout=35)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

        if final_msg is None or final_msg.get("type") == "error":
            return {
                "ok": False,
                "error": final_msg or {"code": "SANDBOX_CRASH", "message": proc.stderr.read()},
                "elapsedMs": elapsed_ms,
                "auditEvents": bridge.audit_events,
                "retokenisedFields": bridge.vault.retokenised_count,
            }

        return {
            "ok": True,
            "result": final_msg.get("result"),
            "elapsedMs": elapsed_ms,
            "auditEvents": bridge.audit_events,
            "retokenisedFields": bridge.vault.retokenised_count,
        }
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream:
                stream.close()



def run_v8_sandbox(code: str, role: str = "ap-clerk", invoice_ref=None) -> Dict[str, Any]:
    """Execute the existing PoC with component-scoped, metadata-only telemetry."""
    trace = Trace(route='CODE_MODE', invoice_ref=invoice_ref) if Trace else None
    try:
        result = _run_v8_sandbox(code, role, trace)
        statuses = {event.get('status') for event in result.get('auditEvents', [])}
        outcome = ('ERROR' if not result.get('ok') else 'BLOCKED' if any(str(status).startswith('BLOCK') for status in statuses)
                   else 'NEEDS_APPROVAL' if 'PAUSE_WITH_REDACTED_MEDIA_PREVIEW' in statuses else 'COMPLETED')
        if trace:
            trace.finish(outcome, complete=True, llm_complete=True, erp_read_complete=True, erp_write_complete=True)
            result['traceId'] = trace.data['traceId']
            result['telemetryQueued'] = exporter.emit(trace)
        return result
    except Exception:
        if trace:
            trace.finish('ERROR')
            exporter.emit(trace)
        raise
