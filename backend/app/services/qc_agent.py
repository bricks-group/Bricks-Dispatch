"""
QC Vision Agent — automated photo quality review.

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

The vision-model call itself lives behind qc_vision_provider.py — this
module only owns the QC decision flow (OpenCV pre-pass routing, fail-open
handling), not any particular provider's API shape.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from .media_intake import is_listing_still_delivery_row, is_visit_file
from .qc_vision_provider import is_configured as _vision_qc_configured, run_vision_qc

logger = logging.getLogger(__name__)

QC_ENABLED = os.environ.get("USE_QC_AGENT", "true").lower() == "true"

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


def _image_unavailable_hold(detail: str) -> dict:
    """QC hold when a listing still cannot be reviewed (no bytes / no URL).

    Same spirit as download_bytes miss → unedited_original_hold: never leave
    qc_status=pending so finalize would deliver an unreviewed still. Late-fold
    prune + hold notices already understand rejected + image_unavailable.
    """
    detail = (detail or "could not review listing still").strip()
    return {
        "qc_status": "rejected",
        "qc_score": 0,
        "qc_notes": f"image_unavailable — {detail}",
        "qc_source": "unavailable",
    }


def _qc_payload(status, score, notes, source, cv_result=None) -> dict:
    """QC row fields + PropTx people soft-fail metadata for Frame/GO."""
    payload = {
        "qc_status": status,
        "qc_score": score,
        "qc_notes": (notes or "")[:500],
        "qc_source": source,
    }
    cv_result = cv_result or {}
    # Soft-fail fields (exact names for Frame/GO MLS quarantine)
    for key in (
        "qc_people_detected",
        "qc_face_count",
        "mls_quarantine",
        "qc_soft_fail",
        "qc_soft_fail_reasons",
    ):
        if key in cv_result:
            payload[key] = cv_result[key]
    if cv_result.get("mls_quarantine") or cv_result.get("qc_people_detected"):
        # Ensure flagged path for GO; never escalate to rejected solely for people
        if payload["qc_status"] == "passed":
            payload["qc_status"] = "flagged"
            payload["qc_score"] = min(int(payload.get("qc_score") or 85), 85)
        extra = " PropTx 5.01(d) persons/faces — mls_quarantine soft-fail"
        if extra.strip() not in (payload.get("qc_notes") or ""):
            payload["qc_notes"] = ((payload.get("qc_notes") or "") + extra)[:500]
    return payload


async def run_qc_on_upload(
    upload_id: str,
    image_url: str,
    package: str = "",
    is_exterior: bool = False,
    is_twilight: bool = False,
) -> dict:
    """
    Runs QC on a single upload: OpenCV coded pre-pass first, vision QC only when needed.
    Returns dict with: qc_status, qc_score, qc_notes, qc_source.
    OpenCV can hold (flagged/rejected) even when vision is unset. Vision outage
    fails open only when OpenCV did not already flag/reject.
    """
    if not QC_ENABLED:
        return {"qc_status": "passed", "qc_score": 100, "qc_notes": "QC agent disabled", "qc_source": "disabled"}

    vision_configured = _vision_qc_configured()
    image_bytes = await _fetch_image_bytes(image_url)
    if not image_bytes:
        # Honesty: never auto-pass an unreviewed listing still. Same spirit as
        # download_bytes miss → unedited_original_hold — hold with a clear
        # reason late-fold prune + hold notices already understand (rejected).
        logger.warning(
            "QC upload %s: image_unavailable — holding (bytes not fetched)",
            upload_id,
        )
        return _image_unavailable_hold(
            "could not fetch listing still for QC review"
        )

    # OpenCV pre-pass: always run when bytes are available (holds without Gemini).
    _high_value = package in ("luxury", "estate")
    cv_result = None
    cv_source = None
    cv_status = None
    try:
        from .qc_opencv import analyze_image_cv
        cv_result = analyze_image_cv(image_bytes, is_exterior=is_exterior, is_twilight=is_twilight)
        cv_source = cv_result.get("source", "opencv")
        cv_status = cv_result.get("status", "passed")
    except ImportError:
        cv_result = None
        cv_source = "opencv_unavailable"
        cv_status = None

    # No vision provider: OpenCV alone can pass / flag / reject (hold).
    if not vision_configured:
        if cv_result is not None and cv_source == "opencv":
            notes = f"OpenCV QC (no vision provider): {cv_status}"
            if cv_result.get("issues"):
                notes += " Issues: " + "; ".join(cv_result["issues"])
            logger.info(
                f"QC upload {upload_id}: OpenCV {cv_status} without vision "
                f"(score={cv_result.get('score')})"
            )
            return _qc_payload(
                cv_status,
                cv_result.get("score", 100),
                notes,
                "opencv",
                cv_result,
            )
        logger.warning(
            f"QC: no vision QC provider configured — skipping QC for upload {upload_id}"
        )
        return {
            "qc_status": "passed",
            "qc_score": 100,
            "qc_notes": "QC skipped (no provider configured)",
            "qc_source": "skipped",
        }

    # Vision configured: skip vision if OpenCV is confident AND not high-value.
    if cv_result is not None and cv_source == "opencv" and cv_status == "passed" and not _high_value:
        notes = "OpenCV pre-pass: passed"
        if cv_result.get("issues"):
            notes += " Issues: " + "; ".join(cv_result["issues"])
        logger.info(
            f"QC upload {upload_id}: OpenCV passed — vision QC skipped "
            f"(package={package or 'unknown'})"
        )
        return _qc_payload("passed", cv_result.get("score", 100), notes, "opencv", cv_result)

    if cv_result is not None and cv_source == "opencv":
        logger.info(
            f"QC upload {upload_id}: OpenCV {cv_status} "
            f"(score={cv_result.get('score')}, high_value={_high_value}) — running vision QC"
        )

    try:
        result = await run_vision_qc(image_bytes, _QC_PROMPT)
        score = int(result.get("score", 100))
        status = result.get("status", "passed")
        notes = result.get("notes", "")
        issues = result.get("issues", [])
        if issues:
            notes = notes + " Issues: " + "; ".join(issues)

        logger.info(f"QC upload {upload_id}: score={score} status={status} source=vision")
        # Re-run lightweight people soft-fail on bytes so vision path also carries flags
        cv_people = {}
        try:
            from .mls_people_detect import people_soft_fail_metadata
            cv_people = people_soft_fail_metadata(image_bytes)
        except Exception:
            pass
        return _qc_payload(status, score, notes, "vision", cv_people)

    except Exception as e:
        logger.error(f"QC: vision provider call failed for upload {upload_id}: {e}")
        # Keep OpenCV hold when it already flagged/rejected; fail-open only otherwise.
        if (
            cv_result is not None
            and cv_source == "opencv"
            and cv_status in ("flagged", "rejected")
        ):
            notes = f"OpenCV hold kept after vision error ({type(e).__name__}): {cv_status}"
            if cv_result.get("issues"):
                notes += " Issues: " + "; ".join(cv_result["issues"])
            logger.warning(
                f"QC upload {upload_id}: retaining OpenCV {cv_status} after vision failure"
            )
            return _qc_payload(
                cv_status,
                cv_result.get("score", 100),
                notes,
                "opencv",
                cv_result,
            )
        return {
            "qc_status": "passed",
            "qc_score": 100,
            "qc_notes": f"QC skipped (error: {type(e).__name__})",
            "qc_source": "skipped",
        }


async def _persist_qc(db, upload_id: str, fields: dict) -> None:
    """Write QC fields via REST; Postgres on Fair-Use 402.

    Without the fallback the first QC write raised under PostgREST 402 and
    finalize logged "QC agent failed (continuing to delivery)" — stills went
    to the client with no verdict.
    """
    try:
        await db.table("uploads").update(fields).eq("id", upload_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_upload(upload_id, fields):
            raise


async def run_qc_for_job(job_id: str) -> dict:
    """
    Runs QC on all done uploads for a job. Updates qc_status on each upload.
    Returns summary: {total, passed, flagged, rejected, count_check}.
    """
    from ..shared.database import get_db
    from ..services.storage import get_signed_download_url
    from .qc_opencv import verify_deliverable_count

    db = await get_db()

    from ..shared.database import maybe_single_row
    try:
        job_res = await db.table("jobs").select("package_name").eq("id", job_id).maybe_single().execute()
        package_name = maybe_single_row(job_res).get("package_name", "") or ""
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        package_name = (pg_select_job_by_id(job_id) or {}).get("package_name", "") or ""

    try:
        res = await db.table("uploads").select(
            "id, file_url, processed_url, qc_variant_url, qc_status, qc_notes, processing_status, filename, provider"
        ).eq("job_id", job_id).execute()
        all_uploads = res.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
        if not _is_rest_quota_error(e):
            raise
        all_uploads = pg_select_uploads_by_job_id(job_id)
    # Only finished rows. processing_status='pending' / 'processing' is still
    # in enhance: a verdict on the raw file would stick (has_qc_verdict) and
    # the enhanced listing still would never be reviewed.
    uploads = [u for u in all_uploads if u.get("processing_status") in ("done", "skipped", None)]

    counts = {"total": 0, "passed": 0, "flagged": 0, "rejected": 0}
    image_upload_count = 0

    from .qc_media_kinds import (
        has_qc_verdict,
        is_legacy_non_image_asset_pass,
        is_legacy_unreadable_floorplan_hold,
        qc_kind_for_upload,
        run_floorplan_qc,
        run_video_qc,
        run_visit_qc,
    )

    for u in uploads:
        filename = (u.get("filename") or "").lower()
        kind = qc_kind_for_upload(u)

        # Keep prior QC unless this is a stuck floor plan held as unreadable_image
        # by the old listing-photo check, a Visit/video auto-passed as
        # "Non-image asset — QC skipped" (pre-container QC), or an
        # unedited-original row that must stay stamped held.
        # qc_status defaults to 'pending' on insert — that is not a verdict.
        existing_qc = str(u.get("qc_status") or "").strip()
        if has_qc_verdict(u):
            is_original = str(u.get("provider") or "").lower() == "original"
            recheck_legacy_plan = kind == "floorplan" and is_legacy_unreadable_floorplan_hold(u)
            recheck_legacy_non_image = kind in ("visit", "video") and is_legacy_non_image_asset_pass(u)
            if not recheck_legacy_plan and not recheck_legacy_non_image and not (
                is_original and is_listing_still_delivery_row(u)
            ):
                st = existing_qc.lower()
                counts["total"] += 1
                counts[st] = counts.get(st, 0) + 1
                if kind == "still" and is_listing_still_delivery_row(u):
                    image_upload_count += 1
                continue

        # Visit / 3D (.ply/.splat/.spz): container check can hold a broken file
        # back. Polycam URL / link-only Visit (no Visit extension) stays skipped.
        # Not counted toward the package photo count. Visit is not a listing
        # still (is_listing_still_delivery_row is False), so this runs alone.
        if kind == "visit" or is_visit_file(filename):
            raw_name = u.get("filename") or ""
            if not is_visit_file(raw_name):
                await _persist_qc(db, u["id"], {
                    "qc_status": "passed",
                    "qc_score": 100,
                    "qc_notes": "Visit link — QC skipped",
                    "qc_at": datetime.now(timezone.utc).isoformat(),
                })
                counts["passed"] += 1
                counts["total"] += 1
                continue
            src = u.get("processed_url") or u.get("file_url")
            if not src:
                # No path to review — hold. Leaving pending would let finalize
                # deliver an unreviewed Visit (only rejected is pruned).
                await _persist_qc(db, u["id"], {
                    **_image_unavailable_hold(
                        "no storage path for Visit QC review"
                    ),
                    "qc_at": datetime.now(timezone.utc).isoformat(),
                })
                counts["total"] += 1
                counts["rejected"] += 1
                continue
            # HTTPS (public Blob) passthrough — signing an https URL as a
            # Storage path returns None; the old continue left pending.
            signed = src if str(src).startswith("https://") else await get_signed_download_url(src, expiry=300)
            if not signed:
                await _persist_qc(db, u["id"], {
                    **_image_unavailable_hold(
                        "could not sign Visit for QC review"
                    ),
                    "qc_at": datetime.now(timezone.utc).isoformat(),
                })
                counts["total"] += 1
                counts["rejected"] += 1
                continue
            qc = await run_visit_qc(u["id"], signed, filename=raw_name)
            await _persist_qc(db, u["id"], {
                "qc_status": qc["qc_status"],
                "qc_score": qc["qc_score"],
                "qc_notes": qc["qc_notes"],
                "qc_at": datetime.now(timezone.utc).isoformat(),
            })
            counts["total"] += 1
            counts[qc["qc_status"]] = counts.get(qc["qc_status"], 0) + 1
            continue

        # Drone / reel video and floor plans get their own check (can hold the
        # file back) instead of the listing-photo check. Not counted toward the
        # package photo count.
        if kind in ("video", "floorplan") and is_listing_still_delivery_row(u):
            src = u.get("processed_url") or u.get("file_url")
            label = "video" if kind == "video" else "floor plan"
            if not src:
                await _persist_qc(db, u["id"], {
                    **_image_unavailable_hold(
                        f"no storage path for {label} QC review"
                    ),
                    "qc_at": datetime.now(timezone.utc).isoformat(),
                })
                counts["total"] += 1
                counts["rejected"] += 1
                continue
            # HTTPS passthrough — same as Visit / listing stills. Sign miss
            # used to continue with pending and finalize delivered unreviewed.
            signed = src if str(src).startswith("https://") else await get_signed_download_url(src, expiry=300)
            if not signed:
                await _persist_qc(db, u["id"], {
                    **_image_unavailable_hold(
                        f"could not sign {label} for QC review"
                    ),
                    "qc_at": datetime.now(timezone.utc).isoformat(),
                })
                counts["total"] += 1
                counts["rejected"] += 1
                continue
            if kind == "video":
                qc = await run_video_qc(u["id"], signed)
            else:
                qc = await run_floorplan_qc(u["id"], signed, filename=u.get("filename") or "")
            await _persist_qc(db, u["id"], {
                "qc_status": qc["qc_status"],
                "qc_score": qc["qc_score"],
                "qc_notes": qc["qc_notes"],
                "qc_at": datetime.now(timezone.utc).isoformat(),
            })
            counts["total"] += 1
            counts[qc["qc_status"]] = counts.get(qc["qc_status"], 0) + 1
            continue

        # HDR bracket siblings (non-primary exposures) share the primary's
        # processed_url and never reach the listing. QC the merged primary
        # once: re-reviewing each sibling re-fetched the full-res edit per
        # exposure, inflated the deliverable count ~5x, and made one held
        # still read as five in the owner hold notice.
        if not is_listing_still_delivery_row(u):
            continue

        # Auto-edit failed → unedited original stays held. Do not let a vision /
        # OpenCV pass re-score it into client delivery (every still auto-edited).
        if str(u.get("provider") or "").lower() == "original":
            from .post_production import unedited_original_hold

            await _persist_qc(db, u["id"], {
                **unedited_original_hold(),
                "qc_at": datetime.now(timezone.utc).isoformat(),
            })
            counts["total"] += 1
            counts["rejected"] += 1
            image_upload_count += 1
            continue

        # Prefer the small QC-review variant over the full-res file — cuts
        # Supabase Storage egress per review since QC only needs enough
        # resolution to judge sharpness/exposure/composition, not the original.
        path = u.get("qc_variant_url") or u.get("processed_url") or u.get("file_url")
        if not path:
            # No path to review — hold. Leaving qc_status=pending would let
            # finalize deliver an unreviewed still (only rejected is pruned).
            await _persist_qc(db, u["id"], {
                **_image_unavailable_hold(
                    "no storage path for listing still QC review"
                ),
                "qc_at": datetime.now(timezone.utc).isoformat(),
            })
            counts["total"] += 1
            counts["rejected"] += 1
            image_upload_count += 1
            continue

        # HTTPS processed_url (Autoenhance / public Blob) must not be re-signed —
        # Visit/video/floorplan and finalize already passthrough. Signing an
        # https URL as a Storage path returns None; the old `continue` left
        # qc_status=pending and finalize delivered the still unreviewed.
        if str(path).startswith("https://"):
            signed_url = path
        else:
            signed_url = await get_signed_download_url(path, expiry=300)
        if not signed_url:
            await _persist_qc(db, u["id"], {
                **_image_unavailable_hold(
                    "could not sign listing still for QC review"
                ),
                "qc_at": datetime.now(timezone.utc).isoformat(),
            })
            counts["total"] += 1
            counts["rejected"] += 1
            image_upload_count += 1
            continue

        # Heuristic: filenames containing "ext" or "exterior" are exterior shots
        is_exterior = any(kw in filename for kw in ("ext", "exterior", "front", "back", "drone"))

        # Real dusk shots are dark on purpose — exempt from the underexposed check.
        from .media_intake import infer_media_type

        is_twilight = infer_media_type(filename=filename, file_url=u.get("file_url")) == "twilight"

        qc = await run_qc_on_upload(
            u["id"], signed_url, package=package_name, is_exterior=is_exterior, is_twilight=is_twilight
        )
        # Soft-fail metadata (mls_quarantine / qc_people_*) is returned to callers
        # and summarized in qc_notes — only known uploads columns are persisted.
        persist = {
            "qc_status": qc["qc_status"],
            "qc_score": qc["qc_score"],
            "qc_notes": qc["qc_notes"],
            "qc_at": datetime.now(timezone.utc).isoformat(),
        }
        await _persist_qc(db, u["id"], persist)

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
