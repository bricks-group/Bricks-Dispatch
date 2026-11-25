"""
RPAS Certificate Validator for Bricks GO.

Three-layer validation:
1. pytesseract OCR (primary) — fast, local, no external API cost. Used when confidence >= 0.7.
2. Gemini Vision (fallback) — used when OCR confidence < 0.7 or pytesseract unavailable.
3. Transport Canada public registry — attempts lookup to confirm cert is on record.

Outcome:
  "verified"   — TC registry confirms cert on file, extraction succeeded
  "extracted"  — Extraction succeeded, TC lookup unavailable or no direct match
  "suspicious" — Extraction detected issues (name mismatch, expired, altered)
  "failed"     — Extraction failed; flag for manual admin review
"""

import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

# Transport Canada RPAS pilot lookup — public registry
# TC exposes a public search table at this URL; we scrape for pilot name and cert level.
_TC_REGISTRY_URL = "https://tc.canada.ca/en/aviation/drone-safety/find-drone-pilot"
_TC_API_HINT = "https://tc.canada.ca"


async def _fetch_image_bytes(image_url: str) -> Optional[bytes]:
    """Download image and return raw bytes."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.error(f"RPAS image fetch failed for {image_url}: {e}")
        return None


def _bytes_to_base64(image_bytes: bytes) -> str:
    """Encode raw image bytes as a base64 string."""
    import base64
    return base64.b64encode(image_bytes).decode()


def _ocr_extract(image_bytes: bytes) -> dict:
    """
    Primary extraction path: pytesseract OCR + regex.
    Returns: {cert_number: str|None, level: str|None, confidence: float, source: "ocr"}
    confidence: 0.0-1.0 based on how many expected fields were found (0.5 per field).
    """
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        # Upscale small images for better OCR accuracy
        if img.width < 1000:
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        # Convert to grayscale for better OCR
        img = img.convert('L')
        text = pytesseract.image_to_string(img, config='--psm 6')
    except ImportError:
        return {"cert_number": None, "level": None, "confidence": 0.0, "source": "ocr_unavailable"}
    except Exception:
        return {"cert_number": None, "level": None, "confidence": 0.0, "source": "ocr_failed"}

    text_upper = text.upper()

    # Transport Canada RPAS pilot certificate number patterns
    # TC uses formats like: XXXX-XXXX, RPAS-XXXXXXXX, or plain numeric 6-8 digits
    cert_patterns = [
        r'\b([A-Z]{4}-[A-Z0-9]{4,8})\b',        # ABCD-12345678
        r'\bRPAS[-\s]?([A-Z0-9]{6,10})\b',       # RPAS-12345678
        r'\bPILOT[-\s]CERT[-\s#:]*([A-Z0-9]{6,12})\b',
        r'\bCERTIFICATE[-\s#:]*([A-Z0-9]{6,12})\b',
        r'\b([0-9]{6,8})\b',                      # plain numeric
    ]

    cert_number = None
    for pattern in cert_patterns:
        m = re.search(pattern, text_upper)
        if m:
            cert_number = m.group(1)
            break

    # Level extraction
    level = None
    if 'ADVANCED' in text_upper:
        level = 'advanced'
    elif 'BASIC' in text_upper:
        level = 'basic'

    # Confidence: 0.5 per field found (cert# + level = 1.0)
    found = sum([cert_number is not None, level is not None])
    confidence = found * 0.5

    return {
        "cert_number": cert_number,
        "level": level,
        "confidence": confidence,
        "source": "ocr",
        "raw_text_length": len(text),
    }


async def _gemini_extract(image_b64: str, mime_type: str = "image/jpeg") -> dict:
    """
    Use Gemini 1.5 Flash Vision to extract RPAS certificate details.
    Returns parsed fields or raises on failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    prompt = """
You are validating a Transport Canada drone pilot certificate (RPAS).
Extract the following fields from this certificate image. Return ONLY a JSON object.

Fields to extract:
{
  "holder_name": "<full name as printed>",
  "certificate_number": "<alphanumeric cert ID>",
  "level": "basic" or "advanced",
  "issue_date": "<YYYY-MM-DD or null>",
  "expiry_date": "<YYYY-MM-DD or null>",
  "issuing_authority": "<e.g. Transport Canada>",
  "is_expired": true or false,
  "is_legitimate": true or false,
  "concerns": ["<list any visual concerns — alterations, inconsistencies, blurriness>"]
}

If a field is not present on the certificate, use null.
If this does not appear to be a valid Transport Canada RPAS certificate, set is_legitimate=false and explain in concerns.
"""
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            _GEMINI_URL,
            params={"key": api_key},
            json=payload,
        )
        resp.raise_for_status()
    data = resp.json()
    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw)


async def _lookup_tc_registry(cert_number: str, holder_name: str) -> dict:
    """
    Attempt to verify against Transport Canada's public RPAS pilot registry.
    TC hosts a searchable HTML table — we do a best-effort scrape.
    Returns {"found": bool, "level": str|None, "name_match": bool|None}.
    Fails open: returns {"found": False, "tc_unavailable": True} on any error.
    """
    if not cert_number and not holder_name:
        return {"found": False, "tc_unavailable": True}

    try:
        search_term = cert_number or holder_name
        async with httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (Bricks RPAS Validator)"},
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                _TC_REGISTRY_URL,
                params={"q": search_term},
            )

        text = resp.text.lower()

        # TC's table contains "basic" or "advanced" labels next to pilot names
        name_lower = holder_name.lower() if holder_name else ""
        cert_lower = cert_number.lower() if cert_number else ""

        cert_on_page = cert_lower and cert_lower in text
        name_on_page = bool(name_lower and all(
            part in text for part in name_lower.split() if len(part) > 2
        ))

        # Detect cert level on page
        level_near_name = None
        if name_on_page or cert_on_page:
            # Find the context around the name/cert on the page
            idx = text.find(cert_lower) if cert_on_page else text.find(name_lower.split()[0])
            snippet = text[max(0, idx - 200): idx + 400] if idx >= 0 else ""
            if "advanced" in snippet:
                level_near_name = "advanced"
            elif "basic" in snippet:
                level_near_name = "basic"

        found = cert_on_page or name_on_page
        return {
            "found": found,
            "cert_on_registry": cert_on_page,
            "name_on_registry": name_on_page,
            "level": level_near_name,
            "name_match": name_on_page if found else None,
            "tc_unavailable": False,
        }

    except Exception as e:
        logger.warning(f"TC registry lookup failed (non-fatal): {e}")
        return {"found": False, "tc_unavailable": True}


def _determine_outcome(extracted: dict, tc_result: dict, vendor_name: str) -> tuple[str, list[str]]:
    """
    Determine validation outcome based on extraction + TC lookup results.
    Returns (outcome_str, issues_list).
    """
    issues: list[str] = list(extracted.get("concerns") or [])

    if not extracted.get("is_legitimate", True):
        return "suspicious", issues + ["Gemini flagged certificate as potentially illegitimate"]

    if extracted.get("is_expired", False):
        return "suspicious", issues + ["Certificate appears expired"]

    # Name consistency check (case-insensitive, partial match ok)
    holder = (extracted.get("holder_name") or "").lower()
    vendor = vendor_name.lower()
    vendor_parts = [p for p in vendor.split() if len(p) > 2]
    name_ok = any(part in holder for part in vendor_parts) or any(part in vendor for part in holder.split() if len(part) > 2)
    if not name_ok and holder and vendor:
        issues.append(f"Name mismatch: cert says '{extracted.get('holder_name')}', vendor is '{vendor_name}'")

    # Determine outcome
    if tc_result.get("found") and not issues:
        return "verified", issues
    elif tc_result.get("found") and issues:
        return "suspicious", issues
    elif tc_result.get("tc_unavailable"):
        # TC was unreachable — extraction-only outcome
        if issues:
            return "suspicious", issues
        if extracted.get("certificate_number") and extracted.get("level"):
            return "extracted", issues
        return "failed", issues + ["Insufficient data extracted from certificate image"]
    else:
        # TC reachable but cert not found
        issues.append("Certificate not found in Transport Canada registry")
        return "suspicious", issues


async def validate_rpas_certificate(
    image_url: str,
    vendor_name: str,
    mime_type: str = "image/jpeg",
) -> dict:
    """
    Full RPAS certificate validation pipeline.

    Extraction order:
      1. pytesseract OCR (primary) — used when confidence >= 0.7
      2. Gemini Vision (fallback) — used when OCR confidence < 0.7 or pytesseract unavailable

    Returns a validation result dict stored on the vendor_documents record.
    Includes 'source' ("ocr" | "gemini") and 'ocr_confidence' (float) fields.
    """
    result: dict = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "outcome": "failed",
        "holder_name": None,
        "certificate_number": None,
        "level": None,
        "expiry_date": None,
        "tc_registry": {},
        "issues": [],
        "source": None,
        "ocr_confidence": None,
    }

    # Fetch raw image bytes once — reused for both OCR and Gemini (as base64)
    image_bytes = await _fetch_image_bytes(image_url)
    if not image_bytes:
        result["issues"] = ["Could not download certificate image"]
        return result

    # Step 1: OCR extraction (primary path)
    ocr_result = _ocr_extract(image_bytes)
    ocr_confidence = ocr_result["confidence"]
    result["ocr_confidence"] = ocr_confidence

    use_ocr = ocr_confidence >= 0.7

    if use_ocr:
        # OCR found enough fields — build an extracted dict in the same shape as Gemini output
        extracted = {
            "holder_name": None,           # OCR does not extract holder name reliably
            "certificate_number": ocr_result["cert_number"],
            "level": ocr_result["level"],
            "issue_date": None,
            "expiry_date": None,
            "issuing_authority": "Transport Canada",
            "is_expired": False,           # OCR cannot determine expiry without date parsing
            "is_legitimate": True,
            "concerns": [],
        }
        result["source"] = "ocr"
        logger.info(
            f"RPAS OCR extraction succeeded for {vendor_name}: "
            f"cert={ocr_result['cert_number']}, level={ocr_result['level']}, "
            f"confidence={ocr_confidence}"
        )
    else:
        # Step 2: Gemini Vision fallback
        if ocr_result["source"] == "ocr":
            logger.info(
                f"RPAS OCR confidence too low ({ocr_confidence}) for {vendor_name} — "
                f"falling back to Gemini Vision"
            )
        else:
            logger.info(
                f"RPAS pytesseract unavailable ({ocr_result['source']}) for {vendor_name} — "
                f"falling back to Gemini Vision"
            )

        image_b64 = _bytes_to_base64(image_bytes)
        try:
            extracted = await _gemini_extract(image_b64, mime_type)
        except Exception as e:
            result["issues"] = [f"Gemini extraction failed: {e}"]
            return result

        result["source"] = "gemini"

    result["holder_name"] = extracted.get("holder_name")
    result["certificate_number"] = extracted.get("certificate_number")
    result["level"] = extracted.get("level")
    result["expiry_date"] = extracted.get("expiry_date")
    result["issuing_authority"] = extracted.get("issuing_authority")

    # Step 3: TC registry lookup
    tc_result = await _lookup_tc_registry(
        cert_number=extracted.get("certificate_number") or "",
        holder_name=extracted.get("holder_name") or "",
    )
    result["tc_registry"] = tc_result

    # Step 4: Determine outcome
    outcome, issues = _determine_outcome(extracted, tc_result, vendor_name)
    result["outcome"] = outcome
    result["issues"] = issues

    logger.info(
        f"RPAS validation for {vendor_name}: outcome={outcome}, "
        f"level={result['level']}, cert={result['certificate_number']}, "
        f"tc_found={tc_result.get('found')}, source={result['source']}, "
        f"ocr_confidence={ocr_confidence}"
    )

    return result


def _rpas_level(raw) -> Optional[str]:
    level = str(raw or "").strip().lower()
    return level if level in ("basic", "advanced") else None


def verified_member_patch(level, certificate_number=None) -> Optional[dict]:
    """vendor_members fields that grant drone dispatch for a verified certificate."""
    level = _rpas_level(level)
    if not level:
        return None
    now = datetime.now(timezone.utc).isoformat()
    patch = {
        "drone_certified": True,
        "rpas_advanced": level == "advanced",
        "rpas_certificate_level": level,
        "rpas_verified_level": level,
        "rpas_verified_at": now,
        "updated_at": now,
    }
    if certificate_number:
        patch["rpas_certificate_number"] = str(certificate_number).strip()
    return patch


def revoked_member_patch() -> dict:
    """vendor_members fields that withdraw drone dispatch."""
    return {
        "drone_certified": False,
        "rpas_advanced": False,
        "rpas_verified_level": None,
        "rpas_verified_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def member_patch_for_validation(validation: dict) -> Optional[dict]:
    """vendor_members update for a registry check result, or None to leave it.

    Only "verified" (Transport Canada lists the certificate) grants drone
    work; the registry's level wins over the level read off the image.
    "suspicious" withdraws it. "extracted" and "failed" change nothing and
    wait for an admin to verify the document.
    """
    outcome = (validation or {}).get("outcome")
    if outcome == "verified":
        tc = validation.get("tc_registry") or {}
        level = _rpas_level(tc.get("level")) or _rpas_level(validation.get("level"))
        return verified_member_patch(level, validation.get("certificate_number"))
    if outcome == "suspicious":
        return revoked_member_patch()
    return None
