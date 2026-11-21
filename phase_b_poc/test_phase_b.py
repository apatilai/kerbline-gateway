"""Automated Phase B tests: AU PII tokenisation, GST math, and pre-VLM crop redaction."""
from __future__ import annotations

import os
import tempfile

from PIL import Image, ImageDraw

from pii_vault import TokenVault, is_valid_abn, is_valid_medicare, is_valid_tfn
from pre_pass import OcrRegion, process_ocr_regions


def test_au_checksum_validators():
    assert is_valid_abn("51 824 753 556") is True  # ATO example valid ABN
    assert is_valid_abn("51 824 753 557") is False
    assert is_valid_tfn("123456782") is True       # Valid Mod-11 synthetic TFN
    assert is_valid_tfn("123456789") is False
    assert is_valid_medicare("2123456701") is True # Valid synthetic Medicare


def test_tokenisation_and_pre_vlm_crop_redaction():
    vault = TokenVault()
    with tempfile.TemporaryDirectory() as tmp:
        page_path = os.path.join(tmp, "synthetic_invoice.png")
        crop_prefix = os.path.join(tmp, "crop")

        # Create a 600x400 white synthetic invoice image with a red PII box inside region
        im = Image.new("RGB", (600, 400), color=(255, 255, 255))
        draw = ImageDraw.Draw(im)
        draw.rectangle((120, 120, 220, 160), fill=(255, 0, 0))  # Simulated PII area inside crop
        im.save(page_path, format="PNG")

        regions = [
            OcrRegion(
                text=(
                    "Supplier ABN: 51 824 753 556\n"
                    "Contractor TFN: 123456782\n"
                    "BSB: 062-000 Account: 12345678\n"
                    "Subtotal: $1000.00\nGST: $100.00\nTotal: $1100.00"
                ),
                confidence=0.98,
                bbox=(0, 0, 600, 100),
                pii_subboxes=[],
            ),
            OcrRegion(
                text="Handwritten docket note: approved $1100.00",
                confidence=0.81,  # < 0.92 triggers cropped BBox escalation
                bbox=(100, 100, 300, 200),
                pii_subboxes=[(20, 20, 120, 60)],  # relative to cropped box (100,100)
            ),
        ]

        res = process_ocr_regions(
            regions=regions,
            page_image_path=page_path,
            vault=vault,
            crop_out_path=crop_prefix,
            nvidia_api_key=None,  # offline unit test
        )

        # 1. Zero raw PII in tokenised text
        txt = res["tokenised_text"]
        assert "123456782" not in txt
        assert "062-000" not in txt
        assert "12345678" not in txt
        assert len(res["minted_tokens"]) == 3

        # 2. Reversible detokenisation inside vault
        assert set(vault.detokenise(t) for t in res["minted_tokens"]) == {
            "123456782",
            "062-000",
            "12345678",
        }

        # 3. Deterministic GST + ABN check passed
        assert res["deterministic_checks"]["abn_valid"] is True
        assert res["deterministic_checks"]["gst_arithmetic_ok"] is True

        # 4. Only 1 low-confidence region escalated, and PII pixels are black-boxed (0,0,0)
        assert len(res["escalated_crops"]) == 1
        with Image.open(res["escalated_crops"][0]) as cropped_redacted:
            assert cropped_redacted.size == (200, 100)
            assert cropped_redacted.getpixel((50, 40)) == (0, 0, 0)
