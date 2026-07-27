"""
QC Vision Agent — automated photo quality review using Gemini 1.5 Flash.

Runs after post-production processing, before delivery to the agent.
Flags or rejects photos that fail quality standards.

QC decisions:
  passed   — photo meets standards, cleared for delivery
  flagged  — minor issues, delivered with warning (agent can request reshoot)
  rejected — serious defects, excluded from delivery, ops team notified

Scoring (0–100):
  90–100: passed
  70–89:  flagged
  0–69:   rejected
"""
import base64
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
QC_ENABLED = os.environ.get("USE_QC_AGENT", "true").lower() == "true"

_GEMINI_VISION_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

_QC_PROMPT = """You are a professional real-estate photography quality inspector.
Evaluate this photo on the following criteria:

1. SHARPNESS — Is the image crisp and in focus (not blurry)?
2. EXPOSURE — Is brightness appropriate? No blown highlights or crushed blacks?
3. COMPOSITION — Is the property the clear subject? No obstructed views?
4. STAGING — Is the space presented cleanly (no clutter, personal items, trash)?
5. COLOUR ACCURACY — Are whites white, not yellow or blue-cast?
6. HORIZON — Are vertical lines straight (no keystone distortion)?

Respond ONLY with a JSON object in this exact format, no markdown:
{
  "score": <integer 0-100>,
  "status": "<passed|flagged|rejected>",
  "issues": ["<issue 1>", "<issue 2>"],
  "notes": "<one sentence summary>"
}

Rules:
- score 90-100 → status: passed
- score 70-89  → status: flagged
- score 0-69   → status: rejected
- "issues" is an empty array [] if there are none
"""


async def _fetch_image_bytes(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.error(f"QC: failed to fetch image for review: {e}")
        return None


async def run_qc_on_upload(
    upload_id: str,
    image_url: str,
    package: str = "",
    is_exterior: bool = False,
) -> dict:
    """
    Runs QC on a single upload: OpenCV coded pre-pass first, Gemini only when needed.
    Returns dict with: qc_status, qc_score, qc_notes, qc_source.
    Falls through to 'passed' if both are unavailable (fail open — never block delivery due to QC outage).
    """
    if not QC_ENABLED:
        return {"qc_status": "passed", "qc_score": 100, "qc_notes": "QC agent disabled", "qc_source": "disabled"}

    if not GEMINI_API_KEY:
        logger.warning(f"QC: GEMINI_API_KEY not set — skipping QC for upload {upload_id}")
        return {"qc_status": "passed", "qc_score": 100, "qc_notes": "QC skipped (no API key)", "qc_source": "skipped"}

    image_bytes = await _fetch_image_bytes(image_url)
    if not image_bytes:
        return {"qc_status": "passed", "qc_score": 100, "qc_notes": "QC skipped (image unavailable)", "qc_source": "skipped"}

    # OpenCV pre-pass: fast coded checks before expensive Gemini call
    _high_value = package in ("luxury", "estate")
    try:
        from .qc_opencv import analyze_image_cv
        cv_result = analyze_image_cv(image_bytes, is_exterior=is_exterior)
        cv_source = cv_result.get("source", "opencv")
        cv_status = cv_result.get("status", "passed")

        # Skip Gemini if OpenCV is confident AND this isn't a high-value package
        if cv_source == "opencv" and cv_status == "passed" and not _high_value:
            notes = "OpenCV pre-pass: passed"
            if cv_result.get("issues"):
                notes += " Issues: " + "; ".join(cv_result["issues"])
            logger.info(f"QC upload {upload_id}: OpenCV passed — Gemini skipped (package={package or 'unknown'})")
            return {
                "qc_status": "passed",
                "qc_score": cv_result.get("score", 100),
                "qc_notes": notes[:500],
                "qc_source": "opencv",
            }

        # OpenCV flagged/rejected or high-value package: fall through to Gemini
        if cv_source == "opencv":
            logger.info(
                f"QC upload {upload_id}: OpenCV {cv_status} "
                f"(score={cv_result.get('score')}, high_value={_high_value}) — running Gemini"
            )
    except ImportError:
        pass

    b64 = base64.b64encode(image_bytes).decode()

    # Detect MIME type from first bytes
    if image_bytes[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    else:
        mime = "image/jpeg"

    payload = {
        "contents": [{
            "parts": [
                {"text": _QC_PROMPT},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 256,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{_GEMINI_VISION_URL}?key={GEMINI_API_KEY}",
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

        # Strip markdown fences if model wraps in ```json ... ```
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        import json
        result = json.loads(text)
        score = int(result.get("score", 100))
        status = result.get("status", "passed")
        notes = result.get("notes", "")
        issues = result.get("issues", [])
        if issues:
            notes = notes + " Issues: " + "; ".join(issues)

        logger.info(f"QC upload {upload_id}: score={score} status={status} source=gemini")
        return {"qc_status": status, "qc_score": score, "qc_notes": notes[:500], "qc_source": "gemini"}

    except Exception as e:
        logger.error(f"QC: Gemini call failed for upload {upload_id}: {e}")
        # Fail open — QC outage must never block delivery
        return {"qc_status": "passed", "qc_score": 100, "qc_notes": f"QC skipped (error: {type(e).__name__})", "qc_source": "skipped"}


async def run_qc_for_job(job_id: str) -> dict:
    """
    Runs QC on all done uploads for a job. Updates qc_status on each upload.
    Returns summary: {total, passed, flagged, rejected, count_check}.
    """
    from ..shared.database import get_db
    from ..services.storage import get_signed_download_url
    from .qc_opencv import verify_deliverable_count

    db = await get_db()

    job_res = await db.table("jobs").select("package_name").eq("id", job_id).maybe_single().execute()
    package_name = (job_res.data or {}).get("package_name", "") or ""

    res = await db.table("uploads").select(
        "id, file_url, processed_url, qc_status, processing_status, filename"
    ).eq("job_id", job_id).execute()
    uploads = [u for u in (res.data or []) if u.get("processing_status") in ("done", "skipped", None, "pending")]

    counts = {"total": 0, "passed": 0, "flagged": 0, "rejected": 0}
    image_upload_count = 0

    for u in uploads:
        filename = (u.get("filename") or "").lower()
        # Skip 3D assets — not reviewable by vision model
        if filename.endswith((".splat", ".ply", ".mp4", ".mov")):
            await db.table("uploads").update({
                "qc_status": "passed",
                "qc_score": 100,
                "qc_notes": "Non-image asset — QC skipped",
                "qc_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", u["id"]).execute()
            counts["passed"] += 1
            counts["total"] += 1
            continue

        # Get a signed URL for the processed or original file
        path = u.get("processed_url") or u.get("file_url")
        if not path:
            continue

        signed_url = await get_signed_download_url(path, expiry=300)
        if not signed_url:
            continue

        # Heuristic: filenames containing "ext" or "exterior" are exterior shots
        is_exterior = any(kw in filename for kw in ("ext", "exterior", "front", "back", "drone"))

        qc = await run_qc_on_upload(u["id"], signed_url, package=package_name, is_exterior=is_exterior)
        await db.table("uploads").update({
            **qc,
            "qc_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", u["id"]).execute()

        status = qc["qc_status"]
        counts["total"] += 1
        counts[status] = counts.get(status, 0) + 1
        image_upload_count += 1

    count_check = verify_deliverable_count(package_name, image_upload_count)
    if not count_check["ok"]:
        logger.warning(
            f"QC job {job_id}: insufficient deliverables for package '{package_name}' "
            f"(expected {count_check['expected']}, got {count_check['actual']})"
        )

    counts["count_check"] = count_check
    logger.info(f"QC job {job_id}: {counts}")
    return counts
