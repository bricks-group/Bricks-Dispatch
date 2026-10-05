"""
Post-production callback and reconciliation endpoints.

Providers call back here when processing is complete:
  POST /api/v1/post-production/callback/fotello
  POST /api/v1/post-production/callback/autoenhance
  POST /api/v1/post-production/callback/boxbrownie

BoxBrownie handles: photo editing, retouching, day-to-dusk, item removal, and virtual staging.
BoxBrownie uses polling (not webhooks) — this endpoint handles any push
notifications they may send for completed orders.

Gaussian splat / 3D visits are handled entirely by Polycam (embed URL or a
manually uploaded .ply/.splat file) — no backend processing needed; see
_finalize_job_if_complete()'s splat_url resolution below.

Internal reconciliation (admin-only, for stuck uploads):
  POST /api/v1/post-production/reconcile
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from ..services.media_intake import (
    infer_media_type,
    is_listing_still_delivery_row,
    is_pdf_file,
    is_visit_file,
    pick_visit_upload,
)
from ..services.qc_agent import run_qc_for_job
from ..services.qc_media_kinds import (
    has_qc_verdict,
    is_legacy_non_image_asset_pass,
    is_legacy_unreadable_floorplan_hold,
)
from ..services.storage import get_signed_download_url, SIGNED_URL_EXPIRY
from ..shared.auth import get_own_internal_api_key
from ..shared.database import get_db


def _maybe_single_row(res) -> dict:
    """postgrest maybe_single().execute() is None when there is no row.

    Reading .data on that None is AttributeError (CORS-less HTTP 500). Treat
    null/non-dict data as no row so enhance-provider webhooks can ignore
    unknown ids with the existing 2xx received:true convention.
    """
    data = getattr(res, "data", None) if res is not None else None
    return data if isinstance(data, dict) else {}


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/post-production", tags=["post-production"])


def _verify_callback_secret(secret: Optional[str]) -> None:
    """Verify webhook secret token. Read at request time so late-injected env vars work."""
    _secret = os.environ.get("POST_PRODUCTION_CALLBACK_SECRET", "")
    if not _secret:
        raise HTTPException(status_code=500, detail="Callback secret not configured")
    if secret != _secret:
        raise HTTPException(status_code=401, detail="Invalid callback secret")


async def _mark_upload_done(upload_id: str, processed_url: Optional[str]) -> None:
    db = await get_db()
    update = {
        "processing_status": "done",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    if processed_url:
        update["processed_url"] = processed_url
    try:
        await db.table("uploads").update(update).eq("id", upload_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_upload(upload_id, update):
            raise



async def _list_delivered_capture_sessions(db, job_id: str) -> list[dict]:
    """Delivered floorplan / path-tour (kind=splat) sessions for capture-only finalize.

    Fair-Use: PostgREST 402 → count helpers + latest-session PG reads.
    """
    out: list[dict] = []
    for kind in ("floorplan", "splat"):
        rows: list = []
        try:
            res = await db.table("capture_sessions").select(
                "id, kind, status, output_storage_path"
            ).eq("job_id", job_id).eq("kind", kind).eq("status", "delivered").execute()
            rows = getattr(res, "data", None) or []
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error
            from ..shared.pg_capture import pg_count_delivered, pg_select_session_latest
            if not _is_rest_quota_error(e):
                raise
            if pg_count_delivered(job_id, kind) > 0:
                latest = pg_select_session_latest(job_id, kind)
                if latest and latest.get("status") == "delivered":
                    rows = [latest]
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                out.append(row)
    return out


def _delivery_urls_from_capture_sessions(job_id: str, sessions: list[dict]) -> list[dict]:
    """Public OS floor/tour URLs for media_delivery (listing / Companion)."""
    from ..services.capture_worker import public_floorplan_url, public_tour_embed_url

    urls: list[dict] = []
    for s in sessions:
        kind = (s.get("kind") or "").strip()
        sid = s.get("id") or ""
        if not sid:
            continue
        if kind == "floorplan":
            url = public_floorplan_url(job_id, sid)
            if url:
                urls.append({"url": url, "filename": "Floor plan", "session_id": sid, "kind": "floorplan"})
        elif kind == "splat":
            url = public_tour_embed_url(job_id, sid)
            if url:
                urls.append({"url": url, "filename": "3D tour", "session_id": sid, "kind": "splat"})
    return urls


def _merge_missing_capture_urls(existing_urls, capture_urls: list[dict]) -> list[dict] | None:
    """Append floor/tour URLs missing from an existing media_delivery.delivery_urls.

    Stills often finalize before capture compile finishes. Later capture deliver
    must fold public floor/tour links into media_delivery without re-running QC,
    payout, or agent email. Returns None when nothing new to add.
    """
    existing = list(existing_urls or []) if isinstance(existing_urls, list) else []
    if isinstance(existing_urls, str):
        import json
        try:
            parsed = json.loads(existing_urls)
            existing = list(parsed) if isinstance(parsed, list) else []
        except Exception:
            existing = []
    seen_sids = {
        str(u.get("session_id"))
        for u in existing
        if isinstance(u, dict) and u.get("session_id")
    }
    seen_kinds = {
        str(u.get("kind"))
        for u in existing
        if isinstance(u, dict) and str(u.get("kind") or "") in ("floorplan", "splat")
    }
    added: list[dict] = []
    for cu in capture_urls or []:
        if not isinstance(cu, dict):
            continue
        sid = str(cu.get("session_id") or "")
        kind = str(cu.get("kind") or "")
        if sid and sid in seen_sids:
            continue
        if not sid and kind and kind in seen_kinds:
            continue
        added.append(cu)
        if sid:
            seen_sids.add(sid)
        if kind:
            seen_kinds.add(kind)
    if not added:
        return None
    return existing + added



def _normalize_visit_delivery_urls(visit_urls: list | None) -> list[dict]:
    """Ensure Virtual Visit rows carry kind=visit for Companion gallery honesty.

    Polycam URL rows need http(s) url. Visit file bridge may also carry
    storage_path (GO .ply/.splat/.spz) so OS can re-sign when the signed URL
    expires — keep those even if url is temporarily empty.
    """
    out: list[dict] = []
    for item in visit_urls or []:
        if isinstance(item, str):
            u = item.strip()
            if not u:
                continue
            out.append({"url": u, "filename": "Virtual Visit", "kind": "visit"})
            continue
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("download_url") or "").strip()
        storage_path = str(item.get("storage_path") or "").strip()
        if not url and not storage_path:
            continue
        entry = dict(item)
        if url:
            entry["url"] = url
        if storage_path:
            entry["storage_path"] = storage_path
        entry["filename"] = str(entry.get("filename") or "Virtual Visit").strip() or "Virtual Visit"
        entry["kind"] = "visit"
        out.append(entry)
    return out


def _merge_missing_visit_urls(existing_urls, visit_urls: list | None) -> list[dict] | None:
    """Append Virtual Visit rows missing from media_delivery.delivery_urls.

    Polycam bridge / Visit file deliver must fold kind=visit into delivery so
    Companion can strip from photos and fill visit_url when property_visits is
    empty (Fair-Use parity with floorplan leftovers).
    """
    existing = list(existing_urls or []) if isinstance(existing_urls, list) else []
    if isinstance(existing_urls, str):
        import json
        try:
            parsed = json.loads(existing_urls)
            existing = list(parsed) if isinstance(parsed, list) else []
        except Exception:
            existing = []
    incoming = _normalize_visit_delivery_urls(visit_urls)
    if not incoming:
        return None
    seen_urls = set()
    has_visit_kind = False
    for u in existing:
        if not isinstance(u, dict):
            if isinstance(u, str) and u.strip():
                seen_urls.add(u.strip())
            continue
        url = str(u.get("url") or u.get("download_url") or "").strip()
        if url:
            seen_urls.add(url)
        kind = str(u.get("kind") or "").strip().lower()
        if kind in {"visit", "virtual_visit", "polycam"}:
            has_visit_kind = True
        fname = str(u.get("filename") or "").strip().casefold()
        if fname == "virtual visit":
            has_visit_kind = True
    added: list[dict] = []
    for vu in incoming:
        url = str(vu.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        if has_visit_kind and not url:
            continue
        # One Visit product per delivery — skip if a Visit row already present
        if has_visit_kind:
            continue
        added.append(vu)
        has_visit_kind = True
        if url:
            seen_urls.add(url)
    if not added:
        return None
    return existing + added


def _merge_missing_still_urls(
    existing_urls,
    still_urls: list[dict],
    rejected_ids: set[str] | frozenset[str] | None = None,
) -> list[dict] | None:
    """Reconcile listing stills into an existing media_delivery.delivery_urls.

    Capture-only (or Visit-only / partial) finalize can create media_delivery before
    enhance finishes more stills. Later enhance/reconcile must fold those photos in
    without re-running QC, payout, or agent email.

    Also:
    - Prune upload-based still rows whose upload_id/id is QC-rejected (rejected_ids).
      Keep floorplan/tour/Visit/session rows that are not uploads-based stills.
    - Backfill kind onto hollow existing still rows from fresh still_urls (do not
      overwrite a non-empty kind). Optionally refresh qc_flagged from fresh.
    - Append stills missing by upload_id (unchanged).

    Returns None when nothing durable changed.
    """
    existing = list(existing_urls or []) if isinstance(existing_urls, list) else []
    if isinstance(existing_urls, str):
        import json
        try:
            parsed = json.loads(existing_urls)
            existing = list(parsed) if isinstance(parsed, list) else []
        except Exception:
            existing = []
    rejected = {str(x) for x in (rejected_ids or ()) if x}
    fresh_by_uid: dict[str, dict] = {}
    for su in still_urls or []:
        if not isinstance(su, dict):
            continue
        uid = str(su.get("upload_id") or su.get("id") or "")
        if uid:
            fresh_by_uid[uid] = su

    changed = False
    out: list = []
    seen_uids: set[str] = set()
    for u in existing:
        if not isinstance(u, dict):
            out.append(u)
            continue
        uid = str(u.get("upload_id") or u.get("id") or "")
        # Drop QC-rejected upload rows (session/Visit leftovers without matching
        # upload_id stay; rejected_ids only comes from uploads.qc_status).
        if uid and uid in rejected:
            changed = True
            continue
        row = u
        if uid and uid in fresh_by_uid:
            fresh = fresh_by_uid[uid]
            existing_kind = str(row.get("kind") or "").strip()
            fresh_kind = str(fresh.get("kind") or "").strip()
            need_kind = (not existing_kind) and bool(fresh_kind)
            need_qc = (
                "qc_flagged" in fresh
                and row.get("qc_flagged") != fresh.get("qc_flagged")
            )
            if need_kind or need_qc:
                row = dict(u)
                if need_kind:
                    row["kind"] = fresh_kind
                if need_qc:
                    row["qc_flagged"] = fresh.get("qc_flagged")
                changed = True
        if uid:
            seen_uids.add(uid)
        out.append(row)

    for su in still_urls or []:
        if not isinstance(su, dict):
            continue
        uid = str(su.get("upload_id") or su.get("id") or "")
        if not uid or uid in seen_uids:
            continue
        if uid in rejected:
            continue
        out.append(su)
        seen_uids.add(uid)
        changed = True

    if not changed:
        return None
    return out



def _listing_delivery_filename(upload: dict) -> str:
    """Advertise .jpg when listing bytes are JPEG (enhance / HEIC as_is convert).

    Canada uploads keep capture filename/mime; after AE/inhouse or HEIC→JPEG,
    processed_url is enhanced/{job}/{id}.jpg. Companion delivery_urls + emails
    must not say .heic/.png/.webp while the bytes are JPEG. Floorplan PNG/PDF
    as_is keep their real extension (processed path matches source).
    """
    if not isinstance(upload, dict):
        return "still.jpg"
    fname = str(upload.get("filename") or "").strip() or "still"
    path = str(upload.get("processed_url") or upload.get("file_url") or "").strip()
    path_l = path.lower().split("?", 1)[0]
    if not path_l.endswith((".jpg", ".jpeg")):
        return fname
    if "." in fname:
        root, ext = fname.rsplit(".", 1)
        if ext and len(ext) <= 8 and ext.lower() not in ("jpg", "jpeg"):
            return f"{root}.jpg" if root else f"{fname}.jpg"
        return fname
    return f"{fname}.jpg"


def _delivery_url_kind_for_upload(upload: dict) -> Optional[str]:
    """Tag GO leftovers in delivery_urls (Canada: no media_type col).

    Companion strips kind=floorplan (and filename cues) from the client photo
    gallery while still reading floor_plan_url from these rows. Keep the upload
    in delivery_urls — do not drop it.

    kind=drone / kind=reel stamp aerial/DJI/reel leftovers so OS listing/share
    (and any delivery_urls consumer) can section them out of Photos — parity
    with floorplan kind tags.

    kind=staging / kind=twilight ARE stamped for media_delivery / OS
    photos_from_delivery_urls partition honesty when filenames are hollow.
    They still belong in Photos (not stripped like floor/tour/Visit; not
    sectioned like drone/reel). HDR/photo stay untagged (Photos).
    """
    if not isinstance(upload, dict):
        return None
    filename = upload.get("filename")
    mime = upload.get("mime_type")
    file_url = upload.get("file_url") or upload.get("processed_url")
    if is_pdf_file(filename, mime):
        return "floorplan"
    mt = infer_media_type(filename, mime, upload.get("media_type"), file_url)
    if mt == "floorplan":
        return "floorplan"
    if mt == "drone":
        return "drone"
    if mt in ("reel", "video"):
        return "reel"
    if mt == "staging":
        return "staging"
    if mt == "twilight":
        return "twilight"
    return None


def _still_delivery_url_entry(upload: dict, url: str, expires_at: str) -> dict:
    """listing still delivery_urls row; floorplan/drone/reel/staging/twilight get kind stamps."""
    entry = {
        "upload_id": upload["id"],
        "filename": _listing_delivery_filename(upload),
        "url": url,
        "expires_at": expires_at,
        "qc_flagged": upload.get("qc_status") == "flagged",
    }
    kind = _delivery_url_kind_for_upload(upload)
    if kind:
        entry["kind"] = kind
    # Editor honesty for Fair-Use desk/share when uploads join is empty.
    prov = str(upload.get("provider") or "").strip()
    if prov:
        entry["provider"] = prov
    return entry


async def _listing_still_delivery_urls(uploads: list) -> list[dict]:
    """Build photo delivery_urls entries from listing still uploads (not Visit/siblings)."""
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_EXPIRY)).isoformat()
    out: list[dict] = []
    for u in uploads or []:
        if not isinstance(u, dict):
            continue
        if u.get("qc_status") == "rejected":
            continue
        if u.get("processing_status") == "failed":
            continue
        # Do not fold in-flight stills — wait until enhance marks them done.
        if u.get("processing_status") in ("processing", "pending"):
            continue
        if not is_listing_still_delivery_row(u):
            continue
        path = u.get("processed_url") or u.get("file_url")
        if not path:
            continue
        if str(path).startswith("https://"):
            url = path
        else:
            url = await get_signed_download_url(path)
        if not url:
            continue
        out.append(_still_delivery_url_entry(u, url, expires_at))
    return out


async def _fold_late_into_existing_delivery(
    job_id: str,
    existing: dict,
    *,
    visit_urls: list | None = None,
) -> bool:
    """If media_delivery exists, merge stills + late floor/tour/Visit.

    Runs QC on listing stills / Visit files missing qc_status before fold, and
    re-checks floor plans still held as unreadable_image from the old listing-photo
    QC plus Visit/video auto-passed as Non-image asset (pre-container QC); still no payout/client email. Owner gets a QC-hold notice only for files
    that just ran QC and were held.
    """
    db = await get_db()

    async def _fetch_uploads():
        try:
            res_uploads = await db.table("uploads").select(
                "id, filename, file_url, processed_url, processing_status, qc_status, qc_notes, provider"
            ).eq("job_id", job_id).execute()
            return res_uploads.data or []
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
            if not _is_rest_quota_error(e):
                raise
            return pg_select_uploads_by_job_id(job_id)

    uploads = await _fetch_uploads()

    # Capture-first / Visit-first can create media_delivery before enhance QC.
    # Full finalize runs run_qc_for_job before delivery_urls; late-fold must too
    # for ready listing stills that still lack qc_status (else rejected prune misses).
    def _ready_still_missing_qc(u: dict) -> bool:
        if not isinstance(u, dict):
            return False
        if u.get("processing_status") == "failed":
            return False
        if u.get("processing_status") in ("processing", "pending"):
            return False
        if not is_listing_still_delivery_row(u):
            return False
        if not (u.get("processed_url") or u.get("file_url")):
            return False
        # qc_status defaults to 'pending' on insert — not a verdict.
        return not has_qc_verdict(u)

    def _ready_legacy_floorplan_unreadable_recheck(u: dict) -> bool:
        """Floor plan/PDF held as unreadable_image by old photo QC — re-run plan check."""
        if not isinstance(u, dict):
            return False
        if u.get("processing_status") == "failed":
            return False
        if u.get("processing_status") in ("processing", "pending"):
            return False
        if not is_listing_still_delivery_row(u):
            return False
        if not (u.get("processed_url") or u.get("file_url")):
            return False
        return is_legacy_unreadable_floorplan_hold(u)

    def _ready_visit_file_missing_qc(u: dict) -> bool:
        """Visit .ply/.splat/.spz missing qc_status — run Visit container check.

        Visit is not a listing still, so _ready_still_missing_qc skips it; without
        this late-fold would never hold a broken Visit uploaded after first delivery.
        """
        if not isinstance(u, dict):
            return False
        if u.get("processing_status") == "failed":
            return False
        if u.get("processing_status") in ("processing", "pending"):
            return False
        if not is_visit_file(u.get("filename")):
            return False
        if not (u.get("processed_url") or u.get("file_url")):
            return False
        # qc_status defaults to 'pending' on insert — not a verdict.
        return not has_qc_verdict(u)

    def _ready_legacy_non_image_recheck(u: dict) -> bool:
        """Visit/video auto-passed as Non-image asset — re-run container check.

        Pre-container-QC rows keep qc_status=passed, so missing-qc predicates miss
        them; without this a broken .ply/.mp4 stayed on the listing forever.
        """
        if not isinstance(u, dict):
            return False
        if u.get("processing_status") == "failed":
            return False
        if u.get("processing_status") in ("processing", "pending"):
            return False
        if not (u.get("processed_url") or u.get("file_url")):
            return False
        return is_legacy_non_image_asset_pass(u)

    need_qc = [
        u for u in (uploads or [])
        if _ready_still_missing_qc(u)
        or _ready_legacy_floorplan_unreadable_recheck(u)
        or _ready_visit_file_missing_qc(u)
        or _ready_legacy_non_image_recheck(u)
    ]
    if need_qc:
        try:
            qc_counts = await run_qc_for_job(job_id)
            logger.info(
                "Job %s: late-fold QC complete — %s (%s upload(s) needed QC / legacy floor-plan / Visit / Non-image recheck)",
                job_id,
                qc_counts,
                len(need_qc),
            )
            from ..services.qc_hold_notify import notify_owner_qc_hold

            await notify_owner_qc_hold(
                db, job_id, qc_counts, only_ids=[u.get("id") for u in need_qc]
            )
        except Exception as e:
            logger.error(
                "Job %s: late-fold QC agent failed (continuing to merge): %s",
                job_id,
                e,
            )
        uploads = await _fetch_uploads()

    still_urls = await _listing_still_delivery_urls(uploads)
    sessions = await _list_delivered_capture_sessions(db, job_id)
    capture_urls = _delivery_urls_from_capture_sessions(job_id, sessions or [])
    raw_urls = existing.get("delivery_urls") if isinstance(existing, dict) else None

    rejected_ids = {
        str(u.get("id") or "")
        for u in (uploads or [])
        if isinstance(u, dict) and u.get("qc_status") == "rejected" and u.get("id")
    }
    merged_stills = _merge_missing_still_urls(
        raw_urls, still_urls, rejected_ids=rejected_ids or None
    )
    cur = merged_stills if merged_stills is not None else raw_urls
    merged_capture = _merge_missing_capture_urls(cur, capture_urls)
    cur2 = merged_capture if merged_capture is not None else cur
    merged_visit = _merge_missing_visit_urls(cur2, visit_urls)
    if merged_stills is None and merged_capture is None and merged_visit is None:
        return False
    merged = (
        merged_visit
        if merged_visit is not None
        else (merged_capture if merged_capture is not None else merged_stills)
    )
    await _patch_media_delivery(job_id, {"delivery_urls": merged})
    logger.info(
        "Job %s: folded late stills/capture/Visit into existing media_delivery (%s URL(s) total)",
        job_id,
        len(merged),
    )
    return True


# Back-compat alias for older call sites / tests
async def _fold_capture_into_existing_delivery(job_id: str, existing: dict) -> bool:
    return await _fold_late_into_existing_delivery(job_id, existing)


async def _unstick_processing_without_delivery(job_id: str) -> None:
    """Mark-complete claims processing; empty stills + no capture must not stay there."""
    db = await get_db()
    fields = {
        "status": "completed",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db.table("jobs").update(fields).eq("id", job_id).eq(
            "status", "processing"
        ).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            logger.warning("Job %s: unstick processing failed: %s", job_id, e)
            return
        # PG path has no status predicate — only unstick when still processing.
        from ..shared.pg_fallback import pg_select_job_by_id
        row = pg_select_job_by_id(job_id) or {}
        if row.get("status") == "processing":
            pg_update_job(job_id, fields)


async def _finalize_job_if_complete(job_id: str, *, visit_bridge: bool = False, visit_delivery_urls: list | None = None) -> None:
    """
    Called after each upload is marked done.
    If all uploads for the job are in state 'done', generate delivery
    record, sign URLs, update job status, and send notification.

    visit_bridge=True: GO /visit/deliver already registered property_visits
    (Polycam URL or signed .ply/.splat/.spz). No uploads row exists for that
    path, so without this flag empty-uploads would unstick without
    media_delivery / delivered / payout. Skip re-register and agent photo
    email (notify_capture_delivered already ran for Virtual Visit).
    visit_delivery_urls: Virtual Visit rows (kind=visit) to fold into
    media_delivery so Companion can fill visit_url when property_visits is
    empty (floorplan leftover parity).
    """
    db = await get_db()

    try:
        res_uploads = await db.table("uploads").select(
            "id, filename, file_url, processed_url, processing_status, qc_status, provider"
        ).eq("job_id", job_id).execute()
        uploads = res_uploads.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
        if not _is_rest_quota_error(e):
            raise
        uploads = pg_select_uploads_by_job_id(job_id)

    if not uploads:
        # Floor-plan / 3D-tour-only jobs have capture_sessions, not uploads.
        # Mark complete still claims status=processing via process_job_assets —
        # deliver when a capture session is ready; otherwise unstick processing.
        # Visit Polycam bridge (/visit/deliver) has neither uploads nor capture
        # sessions — visit_bridge keeps us from unsticking without delivery.
        capture_sessions = await _list_delivered_capture_sessions(db, job_id)
        if not capture_sessions and not visit_bridge:
            logger.info(
                f"Job {job_id}: no uploads and no delivered capture — unstick processing"
            )
            await _unstick_processing_without_delivery(job_id)
            return
        uploads = []  # fall through as capture-only / Visit-bridge
        if capture_sessions:
            logger.info(
                f"Job {job_id}: capture-only finalize "
                f"({len(capture_sessions)} delivered session(s))"
            )
        elif visit_bridge:
            logger.info(
                f"Job {job_id}: Visit-bridge finalize "
                f"(property_visits already registered; no uploads)"
            )
    else:
        capture_sessions = None

    # Idempotency: if media_delivery already exists, do not re-run QC / email / payout.
    # Still fold late listing stills (capture-first / reconcile) + late floor/tour.
    existing = None
    try:
        existing_delivery = await db.table("media_delivery").select(
            "id, delivery_urls"
        ).eq("job_id", job_id).maybe_single().execute()
        existing = _maybe_single_row(existing_delivery) or None
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_media_delivery_by_job_id
        if not _is_rest_quota_error(e):
            raise
        existing = pg_select_media_delivery_by_job_id(job_id)
    if existing:
        try:
            folded = await _fold_late_into_existing_delivery(
                job_id, existing, visit_urls=visit_delivery_urls
            )
            if folded:
                logger.info(
                    f"Job {job_id}: media_delivery existed — merged late stills/capture/Visit; skip full finalize"
                )
            else:
                logger.info(
                    f"Job {job_id}: media_delivery already exists — skipping duplicate finalization"
                )
        except Exception as e:
            logger.warning(
                f"Job {job_id}: fold late stills/capture into existing media_delivery failed: {e}"
            )
        return

    # Block finalization while any uploads are still in-flight or awaiting submission.
    # "pending" = provider submission failed/queued and never progressed; these are timed
    # out to "failed" by the reconcile endpoint after 2 h, at which point finalization proceeds.
    in_flight = [u for u in uploads if u.get("processing_status") in ("processing", "pending")]
    if in_flight:
        logger.info(f"Job {job_id}: {len(in_flight)} upload(s) still in-flight (processing/pending) — waiting")
        return

    # Run QC vision agent on all processed uploads before delivery
    try:
        qc_counts = await run_qc_for_job(job_id)
        logger.info(f"Job {job_id}: QC complete — {qc_counts}")
        from ..services.qc_hold_notify import notify_owner_qc_hold

        await notify_owner_qc_hold(db, job_id, qc_counts)
    except Exception as e:
        logger.error(f"Job {job_id}: QC agent failed (continuing to delivery): {e}")

    # Re-fetch uploads after QC (qc_status now populated)
    try:
        res_uploads = await db.table("uploads").select(
            "id, filename, file_url, processed_url, processing_status, qc_status, provider"
        ).eq("job_id", job_id).execute()
        uploads = res_uploads.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
        if not _is_rest_quota_error(e):
            raise
        uploads = pg_select_uploads_by_job_id(job_id)

    # Build delivery URL list — prefer processed_url, fallback to signed original
    # Rejected uploads are excluded; flagged uploads are included with a warning label
    delivery_urls = []
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_EXPIRY)).isoformat()

    for u in uploads:
        # Skip rejected uploads — not delivered
        if u.get("qc_status") == "rejected":
            logger.info(f"Job {job_id}: upload {u['id']} ({u.get('filename')}) excluded — QC rejected")
            continue
        # Skip failed uploads — processing failed, raw file_url not deliverable
        if u.get("processing_status") == "failed":
            logger.info(f"Job {job_id}: upload {u['id']} ({u.get('filename')}) excluded — processing failed")
            continue
        # Skip Visit/3D + HDR bracket siblings (non-primary share primary processed_url)
        if not is_listing_still_delivery_row(u):
            reason = "bracket sibling" if str(u.get("provider") or "").lower() == "bracket_sibling" else "Visit/3D asset"
            logger.info(f"Job {job_id}: upload {u['id']} ({u.get('filename')}) excluded from photo list — {reason}")
            continue
        path = u.get("processed_url") or u.get("file_url")
        if not path:
            continue
        # Provider-returned HTTPS URLs are already public signed links — no need to re-sign.
        # Calling get_signed_download_url on an HTTPS URL would pass it as a Supabase storage
        # path, causing a 404 error.
        if path.startswith("https://"):
            url = path
        else:
            url = await get_signed_download_url(path)
        if url:
            delivery_urls.append(_still_delivery_url_entry(u, url, expires_at))

    # Find Visit (.splat > .ply > .spz) BEFORE the empty-stills gate so Visit-only
    # (or all-stills-rejected) jobs still register property_visits + deliver.
    # Previously qc_failed returned here and Visit never landed on the listing.
    splat_upload = pick_visit_upload(uploads)

    if capture_sessions is None:
        capture_sessions = await _list_delivered_capture_sessions(db, job_id)
    capture_urls = _delivery_urls_from_capture_sessions(job_id, capture_sessions or [])
    if capture_urls:
        # Append floor/tour public URLs onto stills delivery (or stand alone).
        delivery_urls = list(delivery_urls) + capture_urls

    # Polycam / Visit file bridge: keep kind=visit in delivery_urls (Companion
    # strips from photos + fills visit_url when property_visits is empty).
    visit_rows = _normalize_visit_delivery_urls(visit_delivery_urls)
    if visit_rows:
        merged_visit = _merge_missing_visit_urls(delivery_urls, visit_rows)
        if merged_visit is not None:
            delivery_urls = merged_visit

    if not delivery_urls and not splat_upload and not visit_bridge:
        # Truly nothing to deliver (no listing stills, no Visit, no capture) — don't stay stuck.
        logger.error(f"Job {job_id}: no deliverable URLs after QC — marking qc_failed")
        fields = {
            "status": "qc_failed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await db.table("jobs").update(fields).eq("id", job_id).execute()
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
            if not _is_rest_quota_error(e):
                raise
            if not pg_update_job(job_id, fields):
                raise
        return

    if not delivery_urls and splat_upload:
        logger.info(
            f"Job {job_id}: no listing stills after QC — delivering Visit-only "
            f"({splat_upload.get('filename') or splat_upload.get('id')})"
        )
    elif not delivery_urls and visit_bridge:
        logger.info(
            f"Job {job_id}: no listing stills — delivering Visit-bridge "
            f"(property_visits already registered)"
        )
    elif capture_urls and not any(
        u.get("upload_id") for u in delivery_urls if isinstance(u, dict)
    ) and not splat_upload:
        logger.info(
            f"Job {job_id}: capture-only delivery "
            f"({len(capture_urls)} floor/tour URL(s))"
        )

    # Upsert media_delivery row (idempotent). Fair-Use: PostgREST 402 → Postgres
    # so enhance→listing does not hollow after Blob upload succeeds.
    delivery_row = {
        "job_id": job_id,
        "delivery_urls": delivery_urls,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "notification_status": "pending",
    }
    try:
        await db.table("media_delivery").upsert(
            delivery_row, on_conflict="job_id"
        ).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_upsert_media_delivery
        if not _is_rest_quota_error(e):
            raise
        if not pg_upsert_media_delivery(delivery_row):
            raise

    # Advance job to delivered
    delivered_fields = {
        "status": "delivered",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db.table("jobs").update(delivered_fields).eq("id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, delivered_fields):
            raise

    logger.info(f"Job {job_id}: all {len(uploads)} uploads processed → status=delivered, {len(delivery_urls)} URLs generated")

    # Register the property visit on Bricks-OS now that the job is delivered.
    # Failure is logged loudly — Visit-only deliveries must not silently hollow.
    # visit_bridge: /visit/deliver already called register_property_visit.
    if splat_upload and not visit_bridge:
        # Prefer internal storage path (file_url / non-https processed_url). OS re-signs.
        candidates = [
            splat_upload.get("file_url"),
            splat_upload.get("processed_url"),
        ]
        splat_path = next(
            (p for p in candidates if p and not str(p).startswith("https://")),
            None,
        )
        if splat_path:
            try:
                from ..services.client_comms import register_property_visit
                ok = await register_property_visit(job_id, splat_path)
                if not ok:
                    logger.error(
                        f"Job {job_id}: register_property_visit returned False "
                        f"for {splat_path} — Visit missing on listing"
                    )
            except Exception as e:
                logger.error(f"Job {job_id}: register_property_visit failed: {e}")
        else:
            logger.error(
                f"Job {job_id}: Visit upload {splat_upload.get('id')} has no "
                f"internal storage path — skipping visit-ready"
            )

    # Trigger vendor payout now that job is delivered (best-effort — payout_service is idempotent)
    try:
        from ..services.payout_service import trigger_payout_for_job
        await trigger_payout_for_job(db, job_id)
    except Exception as e:
        logger.error(f"Job {job_id}: payout trigger failed after delivery: {e}")

    # Notify booking agent via Resend (best-effort).
    # visit_bridge: notify_capture_delivered already emailed Virtual Visit —
    # skip the photo-delivery template so agents are not double-messaged.
    if not visit_bridge:
        await _send_delivery_notification(job_id, delivery_urls)


async def _patch_media_delivery(job_id: str, fields: dict) -> None:
    """Update media_delivery via REST; Postgres on Fair-Use 402."""
    db = await get_db()
    try:
        await db.table("media_delivery").update(fields).eq("job_id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_media_delivery
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_media_delivery(job_id, fields):
            raise


async def _send_delivery_notification(job_id: str, delivery_urls: list) -> None:
    """Delegates delivery notification to Booking service (which owns the agent relationship)."""
    db = await get_db()
    job = {}
    try:
        res_job = await db.table("jobs").select("booking_id, address").eq("id", job_id).maybe_single().execute()
        job = _maybe_single_row(res_job)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        job = pg_select_job_by_id(job_id) or {}
    if not job:
        return

    booking_id = job.get("booking_id") or ""
    if not booking_id:
        logger.warning(f"Job {job_id}: no booking_id — skipping delivery notification")
        return

    _internal_api_key = get_own_internal_api_key()
    _booking_api_url = os.environ.get("BOOKING_API_URL", "https://bricks-companion-api.vercel.app")

    if not _internal_api_key:
        logger.info(f"Job {job_id}: INTERNAL_API_KEYS not set — skipping delivery notification")
        return

    # splat_status is not on the live media_delivery schema (Visit registers via
    # property_visits). Keep splat_pending=False — Visit arrives separately.
    splat_pending = False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_booking_api_url}/api/internal/comms/job-delivered",
                headers={"X-Internal-Api-Key": _internal_api_key},
                json={
                    "booking_id": booking_id,
                    "job_id": job_id,
                    "address": job.get("address", ""),
                    "delivery_count": len(delivery_urls),
                    "delivery_urls": delivery_urls[:5],
                    "splat_pending": splat_pending,
                },
            )
            resp.raise_for_status()
            await _patch_media_delivery(job_id, {
                "notified_at": datetime.now(timezone.utc).isoformat(),
                "notification_status": "sent",
            })
            logger.info(f"Job {job_id}: delivery notification delegated to Booking service (splat_pending={splat_pending})")
    except Exception as e:
        logger.error(f"Job {job_id}: delivery notification failed: {e}")
        try:
            await _patch_media_delivery(job_id, {"notification_status": "failed"})
        except Exception as e2:
            logger.error(f"Job {job_id}: media_delivery notify-failed patch failed: {e2}")


# ---------------------------------------------------------------------------
# Fotello callback
# POST /api/v1/post-production/callback/fotello?secret=<CALLBACK_SECRET>
# Payload: {"image_id": "...", "status": "completed", "result_url": "..."}
# ---------------------------------------------------------------------------

@router.post("/callback/fotello")
async def fotello_callback(
    request: Request,
    secret: Optional[str] = None,
):
    _verify_callback_secret(secret)

    body: Dict[str, Any] = await request.json()
    provider_job_id = body.get("image_id") or body.get("job_id")
    status = body.get("status", "")
    processed_url = body.get("result_url") or body.get("output_url") or body.get("url")

    if not provider_job_id:
        raise HTTPException(status_code=422, detail="Missing image_id in Fotello callback")

    if not status or status not in ("completed", "done", "success"):
        logger.info(f"Fotello callback: image {provider_job_id} status={status!r} — not yet complete")
        return {"received": True}

    db = await get_db()
    res = await db.table("uploads").select("id, job_id").eq("provider_job_id", provider_job_id).eq("provider", "fotello").maybe_single().execute()
    row = _maybe_single_row(res)
    if not row:
        logger.warning(f"Fotello callback: no upload found for provider_job_id={provider_job_id}")
        return {"received": True}

    upload_id = row["id"]
    job_id = row["job_id"]

    await _mark_upload_done(upload_id, processed_url)
    # Await in-request: Vercel serverless drops FastAPI BackgroundTasks after the HTTP
    # response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    await _finalize_job_if_complete(job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# Autoenhance callback
# POST /api/v1/post-production/callback/autoenhance?secret=<CALLBACK_SECRET>
# Payload: {"image_id": "...", "enhanced_url": "https://..."}
# ---------------------------------------------------------------------------

@router.post("/callback/autoenhance")
async def autoenhance_callback(
    request: Request,
    secret: Optional[str] = None,
):
    _verify_callback_secret(secret)

    body: Dict[str, Any] = await request.json()
    provider_job_id = body.get("image_id") or body.get("id")
    processed_url = body.get("enhanced_url") or body.get("result_url") or body.get("output_url")
    status = body.get("status", "complete")

    if not provider_job_id:
        raise HTTPException(status_code=422, detail="Missing image_id in Autoenhance callback")

    if status not in ("complete", "completed", "done", "success"):
        logger.info(f"Autoenhance callback: image {provider_job_id} status={status} — not complete")
        return {"received": True}

    db = await get_db()
    res = await db.table("uploads").select("id, job_id").eq("provider_job_id", provider_job_id).eq("provider", "autoenhance").maybe_single().execute()
    row = _maybe_single_row(res)
    if not row:
        logger.warning(f"Autoenhance callback: no upload for provider_job_id={provider_job_id}")
        return {"received": True}

    upload_id = row["id"]
    job_id = row["job_id"]

    await _mark_upload_done(upload_id, processed_url)
    # Await in-request: Vercel serverless drops FastAPI BackgroundTasks after the HTTP
    # response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    await _finalize_job_if_complete(job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# BoxBrownie callback
# POST /api/v1/post-production/callback/boxbrownie?secret=<CALLBACK_SECRET>
# BoxBrownie handles: photo editing, retouching, day-to-dusk, item removal, virtual staging.
# BoxBrownie uses polling (not webhooks) — this endpoint handles any push
# notifications they may send for completed orders.
# Payload: {"order_id": "...", "status": "completed", "result_url": "...",
#           "service_type": "day_to_dusk" | "item_removal" | "photo_editing" | "virtual_staging" | ...}
# ---------------------------------------------------------------------------

@router.post("/callback/boxbrownie")
async def boxbrownie_callback(
    request: Request,
    secret: Optional[str] = None,
):
    _verify_callback_secret(secret)

    body: Dict[str, Any] = await request.json()

    provider_job_id = body.get("order_id") or body.get("image_id") or body.get("job_id")
    status = body.get("status", "")
    processed_url = body.get("result_url") or body.get("output_url") or body.get("url")

    if not provider_job_id:
        raise HTTPException(status_code=422, detail="Missing order_id in BoxBrownie callback")

    if not status or status not in ("completed", "done", "success"):
        logger.info(f"BoxBrownie callback: order {provider_job_id} status={status!r} — not yet complete")
        return {"received": True}

    db = await get_db()
    res = await db.table("uploads").select("id, job_id").eq("provider_job_id", provider_job_id).eq("provider", "boxbrownie").maybe_single().execute()
    row = _maybe_single_row(res)
    if not row:
        logger.warning(f"BoxBrownie callback: no upload found for provider_job_id={provider_job_id}")
        return {"received": True}

    upload_id = row["id"]
    job_id = row["job_id"]

    await _mark_upload_done(upload_id, processed_url)
    # Await in-request: Vercel serverless drops FastAPI BackgroundTasks after the HTTP
    # response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    await _finalize_job_if_complete(job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# Reconciliation: re-check status of uploads stuck in 'processing' > 2h
# POST /api/v1/post-production/reconcile  (internal, requires X-Internal-Api-Key)
# ---------------------------------------------------------------------------

@router.post("/reconcile")
async def reconcile_stuck_uploads(
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """
    Mark Fotello/Autoenhance/BoxBrownie uploads stuck >2h as failed, then re-trigger finalization.
    """
    _internal_api_keys_str = os.environ.get("INTERNAL_API_KEYS", "")
    if not _internal_api_keys_str:
        raise HTTPException(status_code=401, detail="INTERNAL_API_KEYS not configured")
    _keys = [k.strip() for k in _internal_api_keys_str.split(",") if k.strip()]
    if not x_internal_api_key or x_internal_api_key not in _keys:
        raise HTTPException(status_code=403, detail="Forbidden")

    db = await get_db()

    # Track every job that may now be fully done so we await finalize once at the end.
    # Includes BoxBrownie poll successes, PIL/in-house rescues, and timed-out failures.
    # (If PIL finishes every upload for a job, that job never re-enters the stuck list —
    # without this set it would never finalize → hollow listing delivery.)
    job_ids_to_finalize: set = set()

    # --- BoxBrownie: poll all in-flight uploads (no webhooks — polling only) ---
    # Run before the timeout check so completed jobs aren't falsely marked failed.
    from ..services.post_production import poll_boxbrownie_job
    bb_completed = 0
    try:
        res_bb = await db.table("uploads").select("id, job_id, provider_job_id").eq(
            "processing_status", "processing"
        ).eq("provider", "boxbrownie").limit(100).execute()
        for upload in (res_bb.data or []):
            if upload.get("provider_job_id"):
                result_url = await poll_boxbrownie_job(upload["provider_job_id"])
                if result_url:
                    await _mark_upload_done(upload["id"], result_url)
                    if upload.get("job_id"):
                        job_ids_to_finalize.add(upload["job_id"])
                    bb_completed += 1
    except Exception as e:
        logger.error(f"BoxBrownie poll sweep failed: {e}")

    # --- Fotello/Autoenhance/BoxBrownie: mark stuck (>2h) as failed, then finalize ---
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    # Also time out "pending" uploads — these were never successfully submitted to a provider
    # and will never receive a callback. Without this they block _finalize_job_if_complete forever.
    res_stuck = await db.table("uploads").select("id, job_id, provider, file_url, filename").in_(
        "processing_status", ["processing", "pending"]
    ).lt("created_at", cutoff).limit(100).execute()

    stuck = res_stuck.data or []

    # --- In-house enhancement fallback (photo_enhance_service): apply local
    # enhancement to photo assets that are stuck and whose provider keys are
    # unavailable. Still gated the same way as before (last resort, only when
    # neither vendor is configured) -- promoting this to a real Tier-2 ahead
    # of Autoenhance is a separate routing decision (see
    # project_bricks_inhouse_post_production memory / Phase 3), not made here.
    fotello_available = bool(os.environ.get("FOTELLO_API_KEY", ""))
    autoenhance_available = bool(os.environ.get("AUTOENHANCE_API_KEY", ""))
    pil_tier3_count = 0

    if not fotello_available and not autoenhance_available:
        try:
            from ..services.photo_enhance_service import enhance_image, upload_enhanced, upload_qc_variant
            from ..services.storage import get_signed_download_url as _sign_url

            # Non-photo assets (Visit + video/archives) — keep .spz with .splat/.ply
            _SPLAT_EXTS = (".mp4", ".mov", ".avi", ".zip", ".tar", ".ply", ".splat", ".spz")
            _IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")

            for upload in stuck:
                fname = (upload.get("filename") or "").lower()
                # Only process photo-type assets — skip splat source files and 3D assets
                if not any(fname.endswith(ext) for ext in _IMAGE_EXTS):
                    continue
                # Skip boxbrownie — it has its own polling path above
                if upload.get("provider") == "boxbrownie":
                    continue

                file_path = upload.get("file_url", "")
                if not file_path:
                    continue

                try:
                    signed_url = await _sign_url(file_path)
                    if not signed_url:
                        logger.warning(f"PIL Tier-3: could not sign URL for upload {upload['id']} — skipping")
                        continue

                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=30.0) as client:
                        img_resp = await client.get(signed_url)
                        img_resp.raise_for_status()
                        original_bytes = img_resp.content

                    enhanced_bytes = enhance_image(original_bytes)
                    storage_path = await upload_enhanced(db.storage, upload["job_id"], upload["id"], enhanced_bytes)
                    qc_variant_path = await upload_qc_variant(db.storage, upload["job_id"], upload["id"], enhanced_bytes)

                    pil_fields = {
                        "processing_status": "done",
                        "processed_url": storage_path,
                        "qc_variant_url": qc_variant_path,
                        "provider": "bricks_inhouse",
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    try:
                        await db.table("uploads").update(pil_fields).eq("id", upload["id"]).execute()
                    except Exception as up_err:
                        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
                        if not _is_rest_quota_error(up_err):
                            raise
                        if not pg_update_upload(upload["id"], pil_fields):
                            raise

                    if upload.get("job_id"):
                        job_ids_to_finalize.add(upload["job_id"])
                    pil_tier3_count += 1
                    logger.info(
                        f"In-house enhance: processed upload {upload['id']} for job {upload['job_id']} "
                        f"→ {storage_path}"
                    )
                except Exception as pil_err:
                    logger.error(f"In-house enhance: failed for upload {upload['id']}: {pil_err}")
                    # Fall through — will be marked failed below with the rest

        except ImportError as ie:
            logger.warning(f"In-house enhance fallback unavailable (Pillow/numpy/cv2 not installed): {ie}")

    # Re-fetch stuck list after PIL pass — any uploads now "done" should be excluded
    # from the bulk failure sweep so they can proceed to finalization.
    if pil_tier3_count:
        res_stuck = await db.table("uploads").select("id, job_id, provider, file_url, filename").in_(
            "processing_status", ["processing", "pending"]
        ).lt("created_at", cutoff).limit(100).execute()
        stuck = res_stuck.data or []

    stuck_ids = [u["id"] for u in stuck]
    for u in stuck:
        if u.get("job_id"):
            job_ids_to_finalize.add(u["job_id"])

    if stuck_ids:
        fail_fields = {
            "processing_status": "failed",
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await db.table("uploads").update(fail_fields).in_("id", stuck_ids).execute()
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
            if not _is_rest_quota_error(e):
                raise
            for sid in stuck_ids:
                pg_update_upload(sid, fail_fields)
        logger.warning(f"Reconcile: marked {len(stuck_ids)} stuck uploads as failed")

    # Await finalize in-request. Vercel serverless drops FastAPI BackgroundTasks after the
    # HTTP response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    # Finalize is idempotent via the media_delivery guard, so order across job ids does not matter.
    for jid in job_ids_to_finalize:
        await _finalize_job_if_complete(jid)

    # Alert on jobs stuck in post-production > 4 hours (Sentry / external monitoring)
    try:
        res_stuck_jobs = await db.table("jobs").select("id, booking_id, updated_at").in_(
            "status", ["processing", "completed"]
        ).lt("updated_at", (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()).execute()
        stuck_jobs = res_stuck_jobs.data or []
        if stuck_jobs:
            logger.error(
                f"POST-PRODUCTION ALERT: {len(stuck_jobs)} job(s) stuck in processing/completed >4h: "
                f"{[j['id'] for j in stuck_jobs]}"
            )
    except Exception:
        pass

    return {
        "boxbrownie_completed": bb_completed,
        "pil_tier3_applied": pil_tier3_count,
        "timed_out": len(stuck_ids),
        "jobs_queued_for_finalization": len(job_ids_to_finalize),
    }
