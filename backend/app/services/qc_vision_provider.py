"""
Vision QC provider — swappable backend for AI-based photo review.

Currently backed by Gemini 1.5 Flash. qc_agent.py calls only
is_configured() / run_vision_qc() here, never the Gemini API directly, so
switching providers (OpenAI, a self-hosted VLM, Anthropic) means rewriting
this one file — not the QC control flow, the OpenCV pre-pass routing, or
the fail-open behavior in qc_agent.py.
"""
import base64
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_GEMINI_VISION_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)


def is_configured() -> bool:
    """Whether a vision QC provider is available to call."""
    return bool(_GEMINI_API_KEY)


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

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_GEMINI_VISION_URL}?key={_GEMINI_API_KEY}",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

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
