"""
RPAS Certificate Validator for Bricks GO.

Two-layer validation:
1. Gemini Vision — extracts cert details (holder, level, number, expiry) from uploaded image/PDF.
2. Transport Canada public registry — attempts lookup to confirm cert is on record.

Outcome:
  "verified"   — TC registry confirms cert on file, Gemini extracts all fields
  "extracted"  — Gemini extracted fields, TC lookup unavailable or no direct match
  "suspicious" — Gemini detected issues (name mismatch, expired, altered)
  "failed"     — Extraction failed; flag for manual admin review
"""

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


async def _fetch_image_base64(image_url: str) -> Optional[str]:
    """Download image and return base64-encoded bytes."""
    import base64
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            return base64.b64encode(r.content).decode()
    except Exception as e:
        logger.error(f"RPAS image fetch failed for {image_url}: {e}")
        return None


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
    Returns a validation result dict stored on the vendor_documents record.
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
    }

    # Step 1: Gemini Vision extraction
    image_b64 = await _fetch_image_base64(image_url)
    if not image_b64:
        result["issues"] = ["Could not download certificate image"]
        return result

    try:
        extracted = await _gemini_extract(image_b64, mime_type)
    except Exception as e:
        result["issues"] = [f"Gemini extraction failed: {e}"]
        return result

    result["holder_name"] = extracted.get("holder_name")
    result["certificate_number"] = extracted.get("certificate_number")
    result["level"] = extracted.get("level")
    result["expiry_date"] = extracted.get("expiry_date")
    result["issuing_authority"] = extracted.get("issuing_authority")

    # Step 2: TC registry lookup
    tc_result = await _lookup_tc_registry(
        cert_number=extracted.get("certificate_number") or "",
        holder_name=extracted.get("holder_name") or "",
    )
    result["tc_registry"] = tc_result

    # Step 3: Determine outcome
    outcome, issues = _determine_outcome(extracted, tc_result, vendor_name)
    result["outcome"] = outcome
    result["issues"] = issues

    logger.info(
        f"RPAS validation for {vendor_name}: outcome={outcome}, "
        f"level={result['level']}, cert={result['certificate_number']}, "
        f"tc_found={tc_result.get('found')}"
    )

    return result
