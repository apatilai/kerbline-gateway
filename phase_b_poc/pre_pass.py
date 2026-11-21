"""Deterministic Pre-Pass + Pre-VLM Cropped-BBox Escalation via NVIDIA NIM."""
from __future__ import annotations

import json
import hashlib
import time
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from pii_vault import TokenVault, is_valid_abn
try:
    from kerbline_telemetry import Trace, exporter, current_trace
except ImportError:
    Trace = exporter = current_trace = None


@dataclass
class OcrRegion:
    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    pii_subboxes: List[Tuple[int, int, int, int]]


def parse_gst_and_totals(text: str) -> Dict[str, Any]:
    """Extract subtotal, GST, and total and run deterministic 10% AU GST check."""
    def _find_amt(label: str) -> Optional[float]:
        m = re.search(rf"(?i){label}\s*:?\s*\$?\s*([\d,]+\.\d{{2}})", text)
        return float(m.group(1).replace(",", "")) if m else None

    subtotal = _find_amt(r"\bsub[-\s]?total\b")
    gst = _find_amt(r"\bgst\b")
    total = _find_amt(r"\b(?:total\s+due|grand\s+total|total)\b")
    gst_ok = False
    if subtotal is not None and gst is not None and total is not None:
        gst_ok = abs((subtotal + gst) - total) <= 0.02 and abs(gst - round(subtotal * 0.10, 2)) <= 0.05

    abn_match = re.search(r"(?i)abn\s*:?\s*([\d\s]{11,14})", text)
    abn_raw = abn_match.group(1).strip() if abn_match else ""
    return {
        "abn": abn_raw,
        "abn_valid": is_valid_abn(abn_raw) if abn_raw else False,
        "subtotal": subtotal,
        "gst": gst,
        "total": total,
        "gst_arithmetic_ok": gst_ok,
    }


def call_nvidia_nim_vlm(
    api_key: str,
    redacted_crop_b64: str,
    prompt_text: str,
    model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b",
) -> Dict[str, Any]:
    """Send ONLY a PII-redacted cropped bounding-box tile to NVIDIA NIM VLM."""
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 800,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{redacted_crop_b64}"},
                    },
                ],
            }
        ],
    }
    trace = current_trace.get() if current_trace else None
    own_trace = trace is None and Trace is not None
    if own_trace:
        trace = Trace()
    started = time.perf_counter()
    prompt_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result = None
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            result = resp.json()
            return result
    finally:
        if trace:
            trace.hop('OUTBOUND_INFERENCE', 'NVIDIA public API', 'MODEL', 'INFERENCE',
                      status='OK' if result is not None else 'ERROR', network_attempted=True,
                      latency_ms=round((time.perf_counter()-started)*1000,3), transport='TLS', auth_mode='API_KEY')
            trace.usage('BBox_VLM_Crop', model, prompt_hash, result.get('usage') if result else None)
            if own_trace:
                trace.finish('COMPLETED' if result is not None else 'ERROR', complete=True, llm_complete=True,
                             erp_read_complete=True, erp_write_complete=True)
                exporter.emit(trace)


def process_ocr_regions(
    regions: List[OcrRegion],
    page_image_path: str,
    vault: TokenVault,
    crop_out_path: str,
    nvidia_api_key: Optional[str] = None,
    conf_threshold: float = 0.92,
) -> Dict[str, Any]:
    """Collapse OCR regions, tokenise PII, and escalate ONLY low-confidence crops."""
    full_text = "\n".join(r.text for r in regions)
    tokenised_text, minted_tokens = vault.tokenise_text(full_text)
    checks = parse_gst_and_totals(tokenised_text)

    escalated_crops: List[str] = []
    vlm_outputs: List[Any] = []
    for idx, reg in enumerate(regions):
        needs_vlm = reg.confidence < conf_threshold or not checks["gst_arithmetic_ok"]
        if needs_vlm and reg.bbox:
            # Crop & black-box any PII sub-boxes BEFORE VLM escalation
            from PIL import Image
            with Image.open(page_image_path).convert("RGB") as im:
                cropped = im.crop(reg.bbox)
                raw_crop_path = f"{crop_out_path}.{idx}.raw.png"
                redacted_crop_path = f"{crop_out_path}.{idx}.redacted.png"
                cropped.save(raw_crop_path, format="PNG")
            vault.redact_crop_image(raw_crop_path, redacted_crop_path, reg.pii_subboxes)
            escalated_crops.append(redacted_crop_path)

            if nvidia_api_key:
                b64 = vault.encode_image_b64(redacted_crop_path)
                res = call_nvidia_nim_vlm(
                    nvidia_api_key,
                    b64,
                    "Extract only the handwritten/low-confidence numeric amount or note from this redacted crop as JSON.",
                )
                vlm_outputs.append(res)

    return {
        "tokenised_text": tokenised_text,
        "minted_tokens": minted_tokens,
        "deterministic_checks": checks,
        "escalated_crops": escalated_crops,
        "vlm_outputs": vlm_outputs,
    }
