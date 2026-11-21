"""Universal CPU-first Document-to-Markdown Normaliser for Kerbline Layer 1."""
from __future__ import annotations

import csv
import io
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_bytes_to_md(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W_NS}body")
    if body is None:
        return ""
    lines: List[str] = []
    for child in body:
        if child.tag == f"{W_NS}p":
            style_el = child.find(f"{W_NS}pPr/{W_NS}pStyle")
            style = style_el.attrib.get(f"{W_NS}val", "") if style_el is not None else ""
            txt = "".join(t.text for t in child.iter(f"{W_NS}t") if t.text).strip()
            if not txt:
                continue
            if "Heading1" in style or style == "Title":
                lines.append(f"# {txt}")
            elif "Heading2" in style:
                lines.append(f"## {txt}")
            elif "Heading3" in style:
                lines.append(f"### {txt}")
            else:
                lines.append(txt)
            lines.append("")
        elif child.tag == f"{W_NS}tbl":
            rows = child.findall(f"{W_NS}tr")
            for r_idx, row in enumerate(rows):
                cells: List[str] = []
                for cell in row.findall(f"{W_NS}tc"):
                    ctext = " ".join(
                        "".join(t.text for t in p.iter(f"{W_NS}t") if t.text).strip()
                        for p in cell.findall(f"{W_NS}p")
                    ).strip()
                    cells.append(ctext.replace("|", "/"))
                lines.append("| " + " | ".join(cells) + " |")
                if r_idx == 0:
                    lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
            lines.append("")
    return "\n".join(lines).strip()


def _csv_bytes_to_md(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return ""
    out = ["| " + " | ".join(c.strip().replace("|", "/") for c in reader[0]) + " |"]
    out.append("| " + " | ".join(["---"] * len(reader[0])) + " |")
    for row in reader[1:]:
        out.append("| " + " | ".join(c.strip().replace("|", "/") for c in row) + " |")
    return "\n".join(out)


def _html_bytes_to_md(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
    text = re.sub(r"(?i)<h1[^>]*>(.*?)</h1>", r"\n# \1\n", text)
    text = re.sub(r"(?i)<h2[^>]*>(.*?)</h2>", r"\n## \1\n", text)
    text = re.sub(r"(?i)<li[^>]*>(.*?)</li>", r"\n- \1", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def normalise_to_markdown(filename: str, data: bytes) -> Dict[str, Any]:
    """Convert digital files to clean Markdown on CPU; flag scanned media for Docling."""
    t0 = time.perf_counter()
    ext = os.path.splitext(filename.lower())[1]
    extractor = "CPU_FAST_PATH"
    gpu_bypassed = True
    md = ""

    if ext == ".docx":
        md = _docx_bytes_to_md(data)
    elif ext in (".csv", ".tsv"):
        md = _csv_bytes_to_md(data)
    elif ext in (".html", ".htm", ".eml"):
        md = _html_bytes_to_md(data)
    elif ext in (".txt", ".md"):
        md = data.decode("utf-8", errors="replace").strip()
    elif ext == ".pdf":
        # Check for native embedded text; scanned/image PDFs route to Docling GPU OCR
        raw_str = data.decode("latin-1", errors="ignore")
        if "/Font" in raw_str and "BT" in raw_str:
            # Simple native text fallback; production uses pypdf/pymupdf
            texts = re.findall(r"\(([^()]{2,200})\)\s*Tj", raw_str)
            md = "\n".join(texts).strip()
        if not md:
            extractor = "DOCLING_OCR_NEEDED"
            gpu_bypassed = False
            md = ""
    else:
        extractor = "DOCLING_OCR_NEEDED"
        gpu_bypassed = False

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    est_tokens = max(1, len(md) // 4) if md else 0
    return {
        "filename": filename,
        "format": ext.lstrip(".") or "unknown",
        "extractor": extractor,
        "gpuBypassed": gpu_bypassed,
        "latencyMs": elapsed_ms,
        "estTokens": est_tokens,
        "markdown": md,
    }
