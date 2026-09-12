"""
Vision QC provider — swappable backend for AI-based photo review.

Currently backed by Gemini (model name centralized in
app.shared.gemini_config — see that module for why). qc_agent.py calls only
is_configured() / run_vision_qc() here, never the Gemini API directly, so
switching providers (OpenAI, a self-hosted VLM, Anthropic) means rewriting
this one file — not the QC control flow, the OpenCV pre-pass routing, or
the fail-open behavior in qc_agent.py.

generate_from_images() is for COI/WSIB (compliance_validator.py) and
this QC path. Floor plans are derived from aligned 360 stations, not Gemini.
"""
import base64
import json
import logging
import os

import httpx

from ..shared.gemini_config import gemini_generate_content_url, get_gemini_model

logger = logging.getLogger(__name__)

_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def _api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "") or _GEMINI_API_KEY


def is_configured() -> bool:
    """Whether a vision QC provider is available to call."""
    return bool(_api_key())


def _detect_mime(image_bytes: bytes) -> str:
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


async def run_vision_qc(image_bytes: bytes, prompt: str) -> dict:
    """
    Send an image + prompt to the vision QC provider and return its parsed
    JSON response (expected keys: score, status, issues, notes).

    Raises on any failure (network, non-2xx, unparseable response) —
    callers must fail open on exception rather than block delivery on a
    provider outage.
    """
    b64 = base64.b64encode(image_bytes).decode()
    mime = _detect_mime(image_bytes)

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 256,
        },
    }

    model = get_gemini_model()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{gemini_generate_content_url(model)}?key={_api_key()}",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        # Fail open is the caller's job (qc_agent.py) — this must still be
        # LOUD in error monitoring, not just a caught-and-forgotten
        # exception. This is exactly how the gemini-1.5-flash 404s went
        # unnoticed for as long as they did: the pipeline degraded silently
        # to OpenCV-only with nothing visibly wrong anywhere.
        logger.error(f"Gemini vision QC call failed (model={model}): {e}")
        raise

    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "{}")
    ).strip()

    # Strip markdown fences if the model wraps its response in ```json ... ```
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    return json.loads(text)


async def generate_from_images(
    prompt: str,
    images: list,
    *,
    max_output_tokens: int = 4096,
    temperature: float = 0.2,
    timeout: float = 90.0,
) -> str:
    """Multi-image Gemini Vision call. Same endpoint (model centralized in
    app.shared.gemini_config) as COI/WSIB/QC.

    `images` is a list of raw image bytes. Returns the model text (JSON expected).
    Raises if GEMINI_API_KEY is missing or the request fails.
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    parts = [{"text": prompt}]
    for img in images:
        if not img:
            continue
        raw = img if isinstance(img, (bytes, bytearray)) else bytes(img)
        parts.append({
            "inline_data": {
                "mime_type": _detect_mime(raw),
                "data": base64.b64encode(raw).decode(),
            }
        })
    if len(parts) < 2:
        raise RuntimeError("No images to send to Gemini Vision")
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }
    model = get_gemini_model()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                gemini_generate_content_url(model),
                params={"key": api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"Gemini generate_from_images call failed (model={model}): {e}")
        raise
    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    ).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text
