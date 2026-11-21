"""Static AST and pattern checker for Kerbline Code Mode scripts."""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

NODE_BIN = shutil.which("node") or "/usr/local/bin/node"

LAYER2_DOMAIN_TOOLS: Set[str] = {
    "match_invoice_to_po",
    "propose_payment_batch",
    "change_supplier_bank_details",
}

ROLE_TOOL_ALLOWLIST = {
    "ap-clerk": {"match_invoice_to_po", "propose_payment_batch", "change_supplier_bank_details"},
    "finance-controller": {"match_invoice_to_po", "propose_payment_batch", "change_supplier_bank_details"},
    "read-only": {"match_invoice_to_po"},
}

FORBIDDEN_PATTERNS = [
    (r"\brequire\s*\(", "Forbidden module loader: require()"),
    (r"\bimport\s+[\w\{\*\'\"]", "Forbidden ES module import"),
    (r"\bimport\s*\(", "Forbidden dynamic import()"),
    (r"\bprocess\b", "Forbidden global: process"),
    (r"\bglobalThis\b|\bglobal\b", "Forbidden global object access"),
    (r"\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\b", "Forbidden network API"),
    (r"\bchild_process\b|\bworker_threads\b|\bfs\b|\bnet\b|\bhttp\b|\bhttps\b", "Forbidden Node built-in reference"),
    (r"\beval\s*\(|\bnew\s+Function\s*\(|\bFunction\s*\(", "Forbidden dynamic code generation"),
    (r"\bReflect\b|\bProxy\b|__proto__|constructor\s*\[|constructor\s*\(", "Forbidden prototype/meta-programming escape"),
    (r"\bwhile\s*\(\s*true\s*\)|\bfor\s*\(\s*;\s*;\s*\)", "Forbidden unbounded loop"),
    (r"\bxero_[a-zA-Z0-9_]+|\bnetsuite_[a-zA-Z0-9_]+", "Forbidden raw Layer 1 vendor tool call"),
]


@dataclass
class AstCheckResult:
    allowed: bool
    tools_invoked: List[str]
    errors: List[str]


def check_code_mode_script(code: str, role: str = "ap-clerk") -> AstCheckResult:
    """Statically validate JS code against security rules and role-permitted Layer 2 tools."""
    errors: List[str] = []
    if not code or not code.strip():
        return AstCheckResult(allowed=False, tools_invoked=[], errors=["Empty script"])

    if len(code) > 32_768:
        errors.append("Script exceeds 32 KB size limit")

    # 1. Forbidden security / escape patterns
    for pattern, reason in FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            errors.append(reason)

    # 2. Extract kerbline.<tool>(...) invocations and verify against Layer 2 + role allow-list
    allowed_for_role = ROLE_TOOL_ALLOWLIST.get(role, set())
    if not allowed_for_role:
        errors.append(f"Unknown or unpermitted role: {role}")

    # Catch bracket notation like kerbline["..."]
    if re.search(r"\bkerbline\s*\[", code):
        errors.append("Dynamic bracket access on kerbline proxy is forbidden; use static dot notation")

    invoked = re.findall(r"\bkerbline\.([a-zA-Z0-9_]+)\s*\(", code)
    tools_invoked = sorted(set(invoked))

    for tool in tools_invoked:
        if tool not in LAYER2_DOMAIN_TOOLS:
            errors.append(f"Tool '{tool}' is not in Layer 2 domainTools allow-list")
        elif tool not in allowed_for_role:
            errors.append(f"Role '{role}' is not permitted to invoke '{tool}'")

    # 3. Validate JS syntax with node --check (wrapped in async IIFE like runner)
    if not errors:
        wrapped = f"(async () => {{\n{code}\n}})()\n"
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=True) as tmp:
            tmp.write(wrapped)
            tmp.flush()
            proc = subprocess.run(
                [NODE_BIN, "--check", tmp.name],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if proc.returncode != 0:
                errors.append(f"JS syntax error: {proc.stderr.strip()}")

    return AstCheckResult(
        allowed=len(errors) == 0,
        tools_invoked=tools_invoked,
        errors=errors,
    )
