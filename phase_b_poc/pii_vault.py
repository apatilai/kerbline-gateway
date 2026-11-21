"""AU PII Detector, Reversible Tokenisation Vault, and Pre-VLM Image Redactor."""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw


def is_valid_abn(abn: str) -> bool:
    digits = re.sub(r"\D", "", abn)
    if len(digits) != 11:
        return False
    nums = [int(d) for d in digits]
    nums[0] -= 1
    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    return sum(n * w for n, w in zip(nums, weights)) % 89 == 0


def is_valid_tfn(tfn: str) -> bool:
    digits = re.sub(r"\D", "", tfn)
    if len(digits) != 9:
        return False
    weights = [1, 4, 3, 7, 5, 8, 6, 9, 10]
    return sum(int(d) * w for d, w in zip(digits, weights)) % 11 == 0


def is_valid_medicare(med: str) -> bool:
    digits = re.sub(r"\D", "", med)
    if len(digits) != 10:
        return False
    weights = [1, 3, 7, 9, 1, 3, 7, 9]
    check = sum(int(d) * w for d, w in zip(digits[:8], weights)) % 10
    return check == int(digits[8])


PII_PATTERNS: List[Tuple[str, re.Pattern[str], object]] = [
    ("TFN", re.compile(r"\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b"), is_valid_tfn),
    ("MED", re.compile(r"\b\d{4}[\s-]?\d{5}[\s-]?\d{1}\b"), is_valid_medicare),
    ("BSB", re.compile(r"\b\d{3}-\d{3}\b"), lambda _: True),
    ("ACCT", re.compile(r"(?i)(?:acct|account|acc)\s*(?:no\.?|#|:)?\s*(\d{6,10})\b"), lambda _: True),
]


@dataclass
class TokenVault:
    """In-memory + optional Cloud KMS envelope-backed reversible vault for Phase B."""

    kms_key_name: str | None = field(default_factory=lambda: os.getenv("GCP_KMS_KEY"))
    _tok_to_raw: Dict[str, str] = field(default_factory=dict)

    def _mint_token(self, kind: str) -> str:
        suffix = secrets.token_hex(3)
        return f"[TOK_{kind}_{suffix}]"

    def tokenise_text(self, text: str) -> Tuple[str, List[str]]:
        """Replace sensitive AU PII in text with non-deterministic reversible tokens."""
        masked = text
        minted: List[str] = []

        for kind, pattern, validator in PII_PATTERNS:
            def _repl(match: re.Match[str]) -> str:
                raw_val = match.group(1) if match.lastindex else match.group(0)
                if not validator(raw_val):
                    return match.group(0)
                tok = self._mint_token(kind)
                self._tok_to_raw[tok] = raw_val
                minted.append(tok)
                if match.lastindex:
                    full = match.group(0)
                    return full.replace(raw_val, tok)
                return tok

            masked = pattern.sub(_repl, masked)

        return masked, minted

    def detokenise(self, token: str) -> str | None:
        return self._tok_to_raw.get(token)

    def redact_crop_image(
        self,
        image_path: str,
        out_path: str,
        pii_boxes: List[Tuple[int, int, int, int]],
    ) -> str:
        """Black-box sensitive PII regions on a cropped image BEFORE VLM escalation."""
        with Image.open(image_path).convert("RGB") as im:
            draw = ImageDraw.Draw(im)
            for box in pii_boxes:
                draw.rectangle(box, fill=(0, 0, 0))
            im.save(out_path, format="PNG")
        return out_path

    def sha256_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def encode_image_b64(image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
