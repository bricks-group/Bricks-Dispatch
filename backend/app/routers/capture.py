"""Phone-capture endpoints for owned-splat frames and in-app floor plans.

JPEG bytes do NOT go through POST /api/uploads (professional stills, 4MB
Vercel cap). The app takes a picture, asks this router for a signed upload
URL in brxs-deliverables at capture/{session_id}/{n}.jpg, PUTs the file
straight to Storage, then commits the frame row.

kind=splat  — path-confined 3D tour frames (existing shutter kind enum).
kind=floorplan — room-corner / wall tracing frames. Separate session so a
job can have both if both were purchased. Phone frames are NOT sent to BoxBrownie.
Virtual visit (Gaussian splat / SuperSplat) is not this shutter.

Tables live in Bricks-OS (capture_sessions / rooms / pipeline / frames) and
are service-role-only, same as jobs/uploads.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ..services.capture_entitlements import (
    capture_forbidden_message,
    job_capture_entitlements,
    kind_purchased,
)
from ..services.storage import STORAGE_BUCKET, create_signed_upload_url
from ..shared.auth import get_vendor_member, is_vendor_admin, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.job_scope import job_visible_to

router = APIRouter(prefix="/api/capture", tags=["capture"])

_VALID_KINDS = ("splat", "floorplan")

_PIPELINES = {
    "splat": (
        ("Captured", "0 frames", "active"),
        ("Splatting", "Queued — reconstruction worker not running yet", "todo"),
        ("QC", "Auto + human review", "todo"),
        ("Delivered", "Splat tour to agent", "todo"),
    ),
    "floorplan": (
        ("Captured", "0 frames", "active"),
        ("Tracing", "Queued — floor-plan tracer not running yet", "todo"),
        ("QC", "Human review of traced plan", "todo"),
        ("Delivered", "Floor plan to listing file", "todo"),
    ),
}

_FLOORPLAN_NOTE = "Floor plan capture submitted"


def _normalize_kind(raw) -> str:
    k = str(raw or "splat").strip().lower().replace("-", "").replace("_", "")
    if k in ("floorplan", "floorplans"):
        return "floorplan"
    if k in ("splat", "splats", "tour", "3dtour", "3d"):
        return "splat"
    api_error(400, "kind must be splat or floorplan", "bad_capture_kind")
    return "splat"


def _missing_table(exc: Exception) -> bool:
    text = str(exc).lower()
    return "does not exist" in text or "42p01" in text or "undefined table" in text


def _job_meta(job: dict) -> tuple[str, str]:
    address = (job.get("address") or "Property").strip() or "Property"
    parts = []
    job_type = (job.get("job_type") or "").strip()
    if job_type:
        parts.append(job_type.upper())
    sqft = job.get("sqft")
    if sqft:
        parts.append(f"{sqft} SQFT")
    return address, " · ".join(parts)


def _tone(frames: int, kind: str = "splat") -> str:
    if kind == "floorplan":
        if frames >= 4:
            return "ok"
        if frames >= 2:
            return "warn"
        return "bad"
    if frames >= 20:
        return "ok"
    if frames >= 8:
        return "warn"
    return "bad"


def _derive_guidance(rooms: list[dict], kind: str = "splat") -> Optional[dict]:
    if not rooms:
        return None
    uncovered = [r for r in rooms if (r.get("n") or 0) == 0]
    if uncovered:
        name = uncovered[0]["name"]
        if kind == "floorplan":
            return {
                "room": name,
                "message": f"Walk the {name} — photograph each corner and wall. Tracing frames, not HDR stills.",
            }
        return {"room": name, "message": f"Head to the {name} — it hasn't been captured yet."}
    weakest = sorted(rooms, key=lambda r: r.get("n") or 0)[0]
    min_ok = 4 if kind == "floorplan" else 20
    if weakest and (weakest.get("n") or 0) < min_ok:
        name = weakest["name"]
        if kind == "floorplan":
            return {
                "room": name,
                "message": f"The {name} still needs more corners and walls before submit.",
            }
        return {"room": name, "message": f"Coverage in the {name} is thin — add a few more angles before submitting."}
    return None


def _empty_session(job: Optional[dict] = None, kind: str = "splat") -> dict:
    address, meta = _job_meta(job or {})
    return {
        "sessionId": None,
        "currentRoomId": None,
        "address": address,
        "meta": meta,
        "kind": kind,
        "rooms": [],
        "pipeline": [],
        "frameCount": 0,
        "guidance": None,
        "estimatedDeliveryHours": None,
        "capture": job_capture_entitlements(job or {}),
    }


def _shape_session(
    job: dict,
    session: dict,
    rooms_rows: list,
    pipeline_rows: list,
    preferred_room_id: Optional[str] = None,
) -> dict:
    kind = session.get("kind") or "splat"
    rooms = [
        {
            "name": r.get("name") or "Room",
            "tone": r.get("tone") or "bad",
            "n": r.get("frames") or 0,
            "id": r.get("id"),
        }
        for r in (rooms_rows or [])
    ]
    pipeline = [
        {
            "title": p.get("title") or "",
            "detail": p.get("detail") or "",
            "status": p.get("status") or "todo",
        }
        for p in (pipeline_rows or [])
    ]
    address, meta = _job_meta(job)
    frame_count = session.get("frame_count") or 0
    current_room = rooms[0] if rooms else None
    if preferred_room_id:
        current_room = next((r for r in rooms if r.get("id") == preferred_room_id), current_room)
    guidance = _derive_guidance(rooms, kind)
    if guidance and not preferred_room_id:
        current_room = next((r for r in rooms if r["name"] == guidance["room"]), current_room)
    return {
        "sessionId": session.get("id"),
        "currentRoomId": (current_room or {}).get("id"),
        "address": address,
        "meta": meta,
        "kind": kind,
        "status": session.get("status"),
        "rooms": rooms,
        "pipeline": pipeline,
        "frameCount": frame_count,
        "guidance": guidance,
        "estimatedDeliveryHours": None,
        "outputStoragePath": session.get("output_storage_path"),
        "capture": job_capture_entitlements(job),
    }


async def _actor_and_job(authorization: Optional[str], x_user_id: Optional[str], job_id: str) -> tuple[dict, dict]:
    if not job_id:
        api_error(400, "job_id is required", "missing_job_id")
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    res = await db.table("jobs").select(
        "id, address, job_type, sqft, vendor_id, assignee_id, status, checklist, booking_id, "
        "package_id, package_name, add_ons, client_instructions, property_details"
    ).eq("id", job_id).maybe_single().execute()
    job = res.data or {}
    if not job_visible_to(
        job, actor_id, actor_member,
        assigned_only=True,
        admin_all=is_vendor_admin(actor_member),
    ):
        api_error(404, "Job not found", "job_not_found")
    return actor_member, job


async def _load_session_bundle(
    db, job_id: str, session: Optional[dict], job: dict, preferred_room_id: Optional[str] = None,
) -> dict:
    if not session:
        return _empty_session(job)
    sid = session["id"]
    rooms = await db.table("capture_rooms").select("id, name, frames, tone, position").eq(
        "session_id", sid
    ).order("position", desc=False).execute()
    pipeline = await db.table("capture_pipeline").select("title, detail, status, position").eq(
        "session_id", sid
    ).order("position", desc=False).execute()
    return _shape_session(job, session, rooms.data or [], pipeline.data or [], preferred_room_id)


async def _latest_session(db, job_id: str, kind: str) -> Optional[dict]:
    res = await db.table("capture_sessions").select("*").eq("job_id", job_id).eq(
        "kind", kind
    ).order("created_at", desc=True).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


async def _in_progress_session(db, job_id: str, kind: str) -> Optional[dict]:
    res = await db.table("capture_sessions").select("*").eq("job_id", job_id).eq(
        "status", "in_progress"
    ).eq("kind", kind).order("created_at", desc=True).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


@router.get("/sessions")
async def get_session(
    job_id: str,
    kind: str = Query(default="splat"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Return the latest capture session of this kind for a job, or an honest empty shape."""
    kind = _normalize_kind(kind)
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    try:
        db = await get_db()
        session = await _latest_session(db, job_id, kind)
        payload = await _load_session_bundle(db, job_id, session, job)
        payload["kind"] = kind
        return payload
    except Exception as e:
        if _missing_table(e):
            return _empty_session(job, kind)
        raise


@router.post("/sessions")
async def ensure_session(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Create capture_sessions + four pipeline rows + a default room if needed.

    kind is splat | floorplan. A job may have one in-progress session of each.
    """
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    flags = job_capture_entitlements(job)
    if not kind_purchased(job, kind):
        api_error(403, capture_forbidden_message(kind, flags), "capture_kind_not_purchased")
    try:
        db = await get_db()
        existing = await _in_progress_session(db, job_id, kind)
        if existing:
            return await _load_session_bundle(db, job_id, existing, job)

        now = datetime.now(timezone.utc).isoformat()
        inserted = await db.table("capture_sessions").insert({
            "job_id": job_id,
            "status": "in_progress",
            "kind": kind,
            "frame_count": 0,
            "created_at": now,
            "updated_at": now,
        }).execute()
        session = (inserted.data or [None])[0]
        if not session:
            api_error(500, "Could not create capture session", "session_create_failed")

        sid = session["id"]
        await db.table("capture_pipeline").insert([
            {
                "session_id": sid,
                "job_id": job_id,
                "title": title,
                "detail": detail,
                "status": status,
                "position": i,
            }
            for i, (title, detail, status) in enumerate(_PIPELINES[kind])
        ]).execute()
        await db.table("capture_rooms").insert({
            "session_id": sid,
            "job_id": job_id,
            "name": "Room 1",
            "frames": 0,
            "tone": "bad",
            "position": 0,
        }).execute()
        session = await _in_progress_session(db, job_id, kind) or session
        return await _load_session_bundle(db, job_id, session, job)
    except Exception as e:
        if _missing_table(e):
            return _empty_session(job, kind)
        raise


@router.post("/rooms")
async def add_room(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Add a named room to the in-progress session of this kind (floor-plan walk)."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    name = str((payload or {}).get("name") or "").strip()
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    try:
        db = await get_db()
        session = await _in_progress_session(db, job_id, kind)
        if not session:
            api_error(409, "No in-progress capture session", "session_not_found")
        sid = session["id"]
        existing = await db.table("capture_rooms").select("id, name, position").eq(
            "session_id", sid
        ).order("position", desc=False).execute()
        rows = existing.data or []
        next_pos = (max((r.get("position") or 0) for r in rows) + 1) if rows else 0
        if not name:
            name = f"Room {len(rows) + 1}"
        inserted = await db.table("capture_rooms").insert({
            "session_id": sid,
            "job_id": job_id,
            "name": name,
            "frames": 0,
            "tone": "bad",
            "position": next_pos,
        }).execute()
        room = (inserted.data or [None])[0]
        return await _load_session_bundle(
            db, job_id, session, job, preferred_room_id=(room or {}).get("id"),
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if _missing_table(e):
            api_error(503, "Capture tables are not applied yet", "capture_tables_missing")
        raise


@router.post("/frames/sign")
async def sign_frame_upload(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Return a signed upload URL for capture/{session_id}/{n}.jpg."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    try:
        db = await get_db()
        session = await _in_progress_session(db, job_id, kind)
        if not session:
            api_error(409, "No in-progress capture session", "session_not_found")
        sid = session["id"]
        n = int(session.get("frame_count") or 0) + 1
        path = f"capture/{sid}/{n}.jpg"
        signed = await create_signed_upload_url(path)
        if not signed or not (signed.get("token") or signed.get("signed_url")):
            api_error(
                503,
                "Could not mint a signed upload URL for capture frames",
                "signed_upload_unavailable",
            )
        rooms = await db.table("capture_rooms").select("id").eq("session_id", sid).order(
            "position", desc=False
        ).limit(1).execute()
        room_id = (payload or {}).get("room_id") or ((rooms.data or [{}])[0].get("id") if rooms.data else None)
        return {
            "session_id": sid,
            "room_id": room_id,
            "kind": kind,
            "n": n,
            "path": signed.get("path") or path,
            "token": signed.get("token"),
            "signed_url": signed.get("signed_url"),
            "bucket": STORAGE_BUCKET,
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if _missing_table(e):
            api_error(503, "Capture tables are not applied yet", "capture_tables_missing")
        raise


@router.post("/frames/commit")
async def commit_frame(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Insert capture_frames and bump room + session counts after a successful PUT."""
    job_id = (payload or {}).get("job_id") or ""
    session_id = (payload or {}).get("session_id") or ""
    room_id = (payload or {}).get("room_id")
    storage_path = (payload or {}).get("storage_path") or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    if not session_id or not storage_path:
        api_error(400, "session_id and storage_path are required", "missing_frame_fields")
    if not str(storage_path).startswith(f"capture/{session_id}/"):
        api_error(400, "storage_path does not match this session", "bad_storage_path")
    try:
        db = await get_db()
        session_res = await db.table("capture_sessions").select("*").eq("id", session_id).eq(
            "job_id", job_id
        ).maybe_single().execute()
        session = session_res.data or {}
        if not session or session.get("status") != "in_progress":
            api_error(409, "No in-progress capture session", "session_not_found")
        kind = session.get("kind") or "splat"

        now = datetime.now(timezone.utc).isoformat()
        await db.table("capture_frames").insert({
            "session_id": session_id,
            "room_id": room_id or None,
            "job_id": job_id,
            "storage_path": storage_path,
            "created_at": now,
        }).execute()

        new_count = int(session.get("frame_count") or 0) + 1
        await db.table("capture_sessions").update({
            "frame_count": new_count,
            "updated_at": now,
        }).eq("id", session_id).execute()

        if room_id:
            room_res = await db.table("capture_rooms").select("id, frames").eq("id", room_id).eq(
                "session_id", session_id
            ).maybe_single().execute()
            room = room_res.data or {}
            if room:
                room_frames = int(room.get("frames") or 0) + 1
                await db.table("capture_rooms").update({
                    "frames": room_frames,
                    "tone": _tone(room_frames, kind),
                }).eq("id", room_id).execute()

        await db.table("capture_pipeline").update({
            "detail": f"{new_count} frames",
            "status": "active",
        }).eq("session_id", session_id).eq("title", "Captured").execute()

        session["frame_count"] = new_count
        return await _load_session_bundle(db, job_id, session, job, preferred_room_id=room_id)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if _missing_table(e):
            api_error(503, "Capture tables are not applied yet", "capture_tables_missing")
        raise


async def _note_floorplan_on_job(db, job: dict, job_id: str, sid: str, n: int, now: str) -> None:
    """Checklist + output_storage_path. Do NOT send phone frames to BoxBrownie."""
    checklist = job.get("checklist") or []
    if not isinstance(checklist, list):
        checklist = []
    already = any(
        isinstance(c, dict) and (c.get("label") or "") == _FLOORPLAN_NOTE
        for c in checklist
    )
    if not already:
        checklist = list(checklist) + [{"label": _FLOORPLAN_NOTE, "completed": True}]
        await db.table("jobs").update({
            "checklist": checklist,
            "updated_at": now,
        }).eq("id", job_id).execute()
    # Placeholder status lives on the session; listing-file viewer is not wired.
    # media_files.floor_plan is the BoxBrownie stills path — leave it alone.


@router.post("/submit")
async def submit_session(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Mark the session submitted. Next pipeline stage stays todo until a worker exists."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    try:
        db = await get_db()
        session = await _in_progress_session(db, job_id, kind)
        if not session:
            latest = await _latest_session(db, job_id, kind)
            if latest and latest.get("status") == "submitted":
                return {"ok": True, "session": await _load_session_bundle(db, job_id, latest, job)}
            api_error(409, "No in-progress capture session to submit", "session_not_found")

        now = datetime.now(timezone.utc).isoformat()
        n = int(session.get("frame_count") or 0)
        sid = session["id"]
        output_path = f"capture/{sid}/"
        await db.table("capture_sessions").update({
            "status": "submitted",
            "submitted_at": now,
            "updated_at": now,
            "output_storage_path": output_path,
        }).eq("id", sid).execute()
        await db.table("capture_pipeline").update({
            "status": "done",
            "detail": f"{n} frames",
        }).eq("session_id", sid).eq("title", "Captured").execute()

        if kind == "floorplan":
            # Tracer does not exist yet — never paint Tracing as active.
            await db.table("capture_pipeline").update({
                "status": "todo",
                "detail": "Queued — floor-plan tracer not running yet",
            }).eq("session_id", sid).eq("title", "Tracing").execute()
            await _note_floorplan_on_job(db, job, job_id, sid, n, now)
        else:
            # Worker does not exist yet — never paint Splatting as active.
            await db.table("capture_pipeline").update({
                "status": "todo",
                "detail": "Queued — reconstruction worker not running yet",
            }).eq("session_id", sid).eq("title", "Splatting").execute()

        session["status"] = "submitted"
        session["submitted_at"] = now
        session["output_storage_path"] = output_path
        return {"ok": True, "session": await _load_session_bundle(db, job_id, session, job)}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if _missing_table(e):
            api_error(503, "Capture tables are not applied yet", "capture_tables_missing")
        raise
