"""
Compliance document validator for Bricks GO (Certificate of Insurance / WSIB).

Reuses the extraction + outcome pattern already proven in rpas_validator.py:
Gemini Vision (or PDF) reads the document, we check it against the IC
agreement's requirements (Clause 6), and produce an outcome the onboarding
flow can act on.

Outcome:
  "verified"       — required fields present, not expired, meets minimums
  "expiring_soon"  — valid now but expires within _EXPIRY_WARNING_DAYS
  "expired"        — expiry date has passed
  "insufficient"   — extraction succeeded but a required minimum isn't met
                      (e.g. coverage below $2,000,000, Bricks Group not
                      named as additional insured on a COI)
  "failed"         — extraction failed; flag for manual admin review
"""

import base64
import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

# IC agreement Clause 6 minimums.
_MIN_CGL_COVERAGE_CAD = 2_000_000
_EXPIRY_WARNING_DAYS = 14

_PROMPTS = {
    "coi": """
You are validating a Certificate of Insurance (COI) submitted by an independent
contractor photographer for a Canadian company, Bricks Group. Extract the
following fields from this document. Return ONLY a JSON object.

{
  "insured_name": "<name of the insured party as printed>",
  "insurer_name": "<name of the insurance company>",
  "cgl_coverage_amount_cad": <number, commercial general liability per-occurrence limit in CAD, or null>,
  "names_bricks_group_as_additional_insured": true or false,
  "policy_number": "<policy number or null>",
  "issue_date": "<YYYY-MM-DD or null>",
  "expiry_date": "<YYYY-MM-DD or null>",
  "is_legitimate": true or false,
  "concerns": ["<list any visual concerns - alterations, inconsistencies, blurriness, missing sections>"]
}

If a field is not present on the document, use null. If this does not appear
to be a legitimate certificate of insurance, set is_legitimate=false and
explain in concerns.
""",
    "wsib": """
You are validating a WSIB (Workplace Safety and Insurance Board) Certificate
of Clearance, or a written WSIB exemption declaration, submitted by an
independent contractor photographer in Ontario, Canada. Extract the
following fields from this document. Return ONLY a JSON object.

{
  "document_kind": "clearance_certificate" or "exemption_declaration" or "unknown",
  "holder_name": "<name on the document>",
  "wsib_account_number": "<account number or null>",
  "exemption_category": "<the stated exemption category, or null if this is a clearance certificate>",
  "issue_date": "<YYYY-MM-DD or null>",
  "expiry_date": "<YYYY-MM-DD or null, clearance certificates are typically valid ~90 days>",
  "is_legitimate": true or false,
  "concerns": ["<list any visual concerns - alterations, inconsistencies, blurriness, missing sections>"]
}

If a field is not present on the document, use null. If this does not appear
to be a legitimate WSIB document, set is_legitimate=false and explain in
concerns.
""",
}


async def _fetch_document_bytes(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.error(f"Compliance doc fetch failed for {url}: {e}")
        return None


def _guess_mime_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }.get(ext, "application/pdf")


async def _gemini_extract(doc_b64: str, mime_type: str, doc_type: str) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    prompt = _PROMPTS[doc_type]
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": doc_b64}},
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(_GEMINI_URL, params={"key": api_key}, json=payload)
        resp.raise_for_status()
    data = resp.json()
    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _determine_outcome(doc_type: str, extracted: dict) -> tuple[str, list[str]]:
    issues: list[str] = list(extracted.get("concerns") or [])

    if not extracted.get("is_legitimate", True):
        return "failed", issues + ["Gemini flagged document as potentially illegitimate"]

    if doc_type == "coi":
        coverage = extracted.get("cgl_coverage_amount_cad")
        if coverage is None:
            issues.append("Could not determine CGL coverage amount")
        elif coverage < _MIN_CGL_COVERAGE_CAD:
            issues.append(
                f"CGL coverage ${coverage:,.0f} is below the required "
                f"${_MIN_CGL_COVERAGE_CAD:,.0f} minimum"
            )
        if not extracted.get("names_bricks_group_as_additional_insured"):
            issues.append("Bricks Group is not named as additional insured")
    elif doc_type == "wsib":
        kind = extracted.get("document_kind")
        if kind == "unknown":
            issues.append("Could not determine whether this is a clearance certificate or exemption declaration")
        elif kind == "exemption_declaration" and not extracted.get("exemption_category"):
            issues.append("Exemption declaration is missing a stated exemption category")

    expiry = _parse_date(extracted.get("expiry_date"))
    today = datetime.now(timezone.utc).date()
    if expiry is not None:
        if expiry < today:
            return "expired", issues + [f"Expired on {expiry.isoformat()}"]
        days_left = (expiry - today).days
        if days_left <= _EXPIRY_WARNING_DAYS:
            issues.append(f"Expires in {days_left} day(s) ({expiry.isoformat()})")
            if issues[:-1]:
                return "insufficient", issues
            return "expiring_soon", issues
    else:
        issues.append("Could not determine expiry date")

    if issues:
        return "insufficient", issues

    return "verified", issues


async def validate_compliance_document(
    document_url: str,
    doc_type: str,
    filename: str = "document.pdf",
) -> dict:
    """
    Validate a COI or WSIB document against IC agreement Clause 6.

    Returns a validation result dict shaped for storage on the
    vendor_documents record (`validation_result` column, plus `status`
    derived from `outcome` by the caller).
    """
    if doc_type not in _PROMPTS:
        raise ValueError(f"compliance validation not supported for doc_type={doc_type!r}")

    result: dict = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "outcome": "failed",
        "doc_type": doc_type,
        "expiry_date": None,
        "issues": [],
    }

    doc_bytes = await _fetch_document_bytes(document_url)
    if not doc_bytes:
        result["issues"] = ["Could not download document"]
        return result

    mime_type = _guess_mime_type(filename)
    doc_b64 = base64.b64encode(doc_bytes).decode()

    try:
        extracted = await _gemini_extract(doc_b64, mime_type, doc_type)
    except Exception as e:
        result["issues"] = [f"Gemini extraction failed: {e}"]
        return result

    result["expiry_date"] = extracted.get("expiry_date")
    result["extracted"] = extracted

    outcome, issues = _determine_outcome(doc_type, extracted)
    result["outcome"] = outcome
    result["issues"] = issues

    logger.info(
        f"Compliance validation ({doc_type}): outcome={outcome}, "
        f"expiry={result['expiry_date']}, issues={issues}"
    )

    return result
