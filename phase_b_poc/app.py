"""FastAPI entrypoint for Phase B Cloud Run service (Docling/OCR + PII Vault + Pre-VLM BBox)."""
from __future__ import annotations

import os
import tempfile
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pii_vault import TokenVault
from pre_pass import OcrRegion, parse_gst_and_totals, process_ocr_regions, Trace, exporter, current_trace
from doc_normaliser import normalise_to_markdown

app = FastAPI(title="Kerbline Phase B Perception & Tokenisation Service")
vault = TokenVault()


class RegionIn(BaseModel):
    text: str
    confidence: float
    bbox: List[int]
    pii_subboxes: List[List[int]] = []


class ProcessRequest(BaseModel):
    regions: List[RegionIn]
    image_b64: str
    escalate_vlm: bool = False
    invoice_ref: str | None = None


class NormaliseRequest(BaseModel):
    filename: str
    file_b64: str
    invoice_ref: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "region": os.getenv("CLOUD_RUN_REGION"), "telemetry": exporter.health() if exporter else {"enabled": False, "reason": "SDK not installed"}}


def _process_invoice(req: ProcessRequest):
    import base64

    if req.escalate_vlm:
        raise HTTPException(status_code=503, detail="Image inference release is disabled until trusted server-side visual detection is integrated")
    api_key = os.getenv("NVIDIA_API_KEY") if req.escalate_vlm else None
    if req.escalate_vlm and not api_key:
        raise HTTPException(status_code=500, detail="NVIDIA_API_KEY secret not mounted")

    with tempfile.TemporaryDirectory() as tmp:
        page_path = os.path.join(tmp, "page.png")
        crop_prefix = os.path.join(tmp, "crop")
        with open(page_path, "wb") as f:
            f.write(base64.b64decode(req.image_b64))

        regions = [
            OcrRegion(
                text=r.text,
                confidence=r.confidence,
                bbox=(r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3]),
                pii_subboxes=[(b[0], b[1], b[2], b[3]) for b in r.pii_subboxes],
            )
            for r in req.regions
        ]
        result = process_ocr_regions(
            regions=regions,
            page_image_path=page_path,
            vault=vault,
            crop_out_path=crop_prefix,
            nvidia_api_key=api_key,
        )
        return result


@app.post("/v1/phase-b/normalise")
def normalise_document(req: NormaliseRequest):
    import base64

    try:
        raw_bytes = base64.b64decode(req.file_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 payload: {exc}")

    norm = normalise_to_markdown(req.filename, raw_bytes)
    md = norm.pop("markdown")
    tokenised_md, minted = vault.tokenise_text(md) if md else ("", [])
    checks = parse_gst_and_totals(tokenised_md) if tokenised_md else {}
    return {
        **norm,
        "tokenisedMarkdown": tokenised_md,
        "mintedTokens": minted,
        "deterministicChecks": checks,
    }


@app.post("/v1/phase-b/process")
def process_invoice(req: ProcessRequest):
    trace = Trace(invoice_ref=req.invoice_ref) if Trace else None
    context = current_trace.set(trace) if current_trace else None
    try:
        result = _process_invoice(req)
        if trace:
            trace.finish('COMPLETED', complete=True, llm_complete=True, erp_read_complete=True, erp_write_complete=True)
            result['traceId'] = trace.data['traceId']
            result['telemetryQueued'] = exporter.emit(trace)
        return result
    except Exception:
        if trace:
            trace.finish('ERROR')
            exporter.emit(trace)
        raise
    finally:
        if current_trace:
            current_trace.reset(context)
