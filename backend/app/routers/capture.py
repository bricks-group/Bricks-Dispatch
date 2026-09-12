"""Phone-capture endpoints for Matterport-style 360 stations.

JPEG bytes do NOT go through POST /api/uploads (professional stills, 4MB
Vercel cap). Guided pan frames and equirects go to
capture/{session}/stations/{n}/ via signed PUT.

kind=splat     — path-confined 3D tour (360 stations, compiled on our server).
kind=floorplan — 2D plan derived from those aligned stations (or RoomPlan JSON).
kind=visit     — Virtual Visit add-on: Polycam URL or .ply/.splat onto the listing
                 (owned GPU cook later). No Luma, no BoxBrownie. HDR stills stay camera-only.

Tables live in Bricks-OS (capture_sessions / rooms / pipeline / frames) and
are service-role-only, same as jobs/uploads.
"""
import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ..services.capture_entitlements import (
    capture_forbidden_message,
    job_capture_entitlements,
    kind_purchased,
)
from ..services.capture_layout import (
    CONTENT_TYPES,
    equirect_path,
    pan_path,
    parse_station_index_from_path,
    role_from_path,
    roomplan_json_path,
    roomplan_usdz_path,
    session_manifest_path,
    station_manifest_path,
)
from ..services.capture_worker import (
    CaptureWorkerError,
    load_session_tags,
    load_station_records,
    process_capture_session,
    rebuild_floorplan_from_stations,
    rebuild_tour_from_equirects,
    save_session_tags,
    stations_have_compile_input,
)
from ..services.storage import STORAGE_BUCKET, create_signed_upload_url, get_signed_download_url, upload_bytes
from ..services.tour_builder import normalize_tag, normalize_tags
from ..shared.auth import get_vendor_member, is_vendor_admin, resolve_vendor_actor_id, verify_internal_api_key
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.job_scope import job_visible_to

router = APIRouter(prefix="/api/capture", tags=["capture"])
logger = logging.getLogger("capture")

_VALID_KINDS = ("splat", "floorplan")

_PIPELINES = {
    "splat": (
        ("Captured", "0 stations", "active"),
        ("Building tour", "Stitch 360s and compile walkthrough", "todo"),
        ("QC", "Auto + human review", "todo"),
        ("Delivered", "3D tour to listing file", "todo"),
    ),
    "floorplan": (
        ("Captured", "0 stations", "active"),
        ("Tracing", "Derive plan from aligned stations", "todo"),
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
    """True only when the capture_* table itself is missing.

    Do NOT match generic "does not exist" — PostgREST/Postgres column errors
    ("column X does not exist") would otherwise return an empty session with
    sessionId=null and silently block every GO capture start.
    """
    text = str(exc).lower()
    if "42p01" in text or "undefined_table" in text or "undefined table" in text:
        return True
    # PostgREST schema-cache miss for a relation
    if "could not find the table" in text and "schema cache" in text:
        return True
    if "relation" in text and "does not exist" in text:
        return True
    return False


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
    # frames = pan frames in that room. 8 = stitchable 360, 24 = full dome.
    if kind == "floorplan":
        if frames >= 8:
            return "ok"
        if frames >= 4:
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
                "message": f"Stand in the {name} and complete a 360 station (or import RoomPlan). Not snapshot stills.",
            }
        return {"room": name, "message": f"Stand in the {name} — complete a 360 station sweep, then walk to the next."}
    weakest = sorted(rooms, key=lambda r: r.get("n") or 0)[0]
    min_ok = 8 if kind == "floorplan" else 20
    if weakest and (weakest.get("n") or 0) < min_ok:
        name = weakest["name"]
        if kind == "floorplan":
            return {
                "room": name,
                "message": f"The {name} station sweep is thin — fill the coverage dome before submit.",
            }
        return {"room": name, "message": f"Coverage in the {name} is thin — finish the 360 station before submitting."}
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
        "stationCount": 0,
        "currentStationIndex": 0,
        "stations": [],
        "tags": [],
        "capture": job_capture_entitlements(job or {}),
    }


def _shape_session(
    job: dict,
    session: dict,
    rooms_rows: list,
    pipeline_rows: list,
    preferred_room_id: Optional[str] = None,
    stations_rows: Optional[list] = None,
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
        "stationCount": len(stations_rows or []),
        "currentStationIndex": (stations_rows or [{}])[-1].get("station_index", 0) if stations_rows else 0,
        "stations": stations_rows or [],
        "capture": job_capture_entitlements(job),
    }


async def _actor_and_job(
    authorization: Optional[str],
    x_user_id: Optional[str],
    job_id: str,
    x_internal_api_key: Optional[str] = None,
) -> tuple[dict, dict]:
    """Vendor-assigned job gate. OS listing-file proxies may use X-Internal-Api-Key."""
    if not job_id:
        api_error(400, "job_id is required", "missing_job_id")
    db = await get_db()
    res = await db.table("jobs").select(
        "id, address, job_type, sqft, vendor_id, assignee_id, status, checklist, booking_id, "
        "package_id, package_name, add_ons, client_instructions, property_details"
    ).eq("id", job_id).maybe_single().execute()
    job = res.data or {}
    if not job.get("id"):
        api_error(404, "Job not found", "job_not_found")
    if verify_internal_api_key(x_internal_api_key):
        return {"id": "internal_service", "role": "internal"}, job
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not job_visible_to(
        job, actor_id, actor_member,
        assigned_only=True,
        admin_all=is_vendor_admin(actor_member),
    ):
        api_error(404, "Job not found", "job_not_found")
    return actor_member, job


def _station_public(st: dict) -> dict:
    pans = st.get("pans") or []
    return {
        "station_index": st.get("station_index"),
        "room": st.get("room") or "Room",
        "panCount": len(pans),
        "hasEquirect": bool(st.get("equirect_path") or st.get("equirect_bytes")),
        "heading": st.get("heading"),
        "content_type": "equirect" if (st.get("equirect_path") or st.get("equirect_bytes")) else "pan_burst",
        "tags": normalize_tags(st.get("tags"), station_index=st.get("station_index")),
    }


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
    stations_pub = []
    try:
        raw = await load_station_records(db, sid, rooms.data or [])
        stations_pub = [_station_public(s) for s in raw]
    except Exception:
        stations_pub = []
    payload = _shape_session(job, session, rooms.data or [], pipeline.data or [], preferred_room_id, stations_pub)
    try:
        loaded = await load_session_tags(sid)
        tags = loaded if loaded is not None else []
    except Exception:
        tags = []
    payload["tags"] = tags
    if tags:
        by_idx = {}
        for tg in tags:
            idx = tg.get("station_index")
            if idx is None:
                continue
            by_idx.setdefault(int(idx), []).append(tg)
        for st in payload.get("stations") or []:
            idx = st.get("station_index")
            if idx is not None and int(idx) in by_idx:
                st["tags"] = by_idx[int(idx)]
    return payload


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
            logger.warning("capture tables missing on ensure_session: %s", e)
            return _empty_session(job, kind)
        logger.exception("ensure_session failed job_id=%s kind=%s", job_id, kind)
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



def _role(payload: dict) -> str:
    raw = str((payload or {}).get("role") or (payload or {}).get("content_role") or "pan").strip().lower()
    if raw in ("equirect", "equirectangular", "360"):
        return "equirect"
    if raw in ("usdz", "roomplan"):
        return raw if raw == "usdz" else "roomplan"
    if raw in ("manifest", "station.json"):
        return "manifest"
    return "pan"


def _content_type_for(role: str, payload: dict) -> str:
    given = str((payload or {}).get("content_type") or "").strip()
    if given:
        return given
    return CONTENT_TYPES.get(role, "image/jpeg")


async def _next_station_index(db, session_id: str) -> int:
    try:
        recs = await load_station_records(db, session_id)
        if recs:
            return max(int(s.get("station_index") or 0) for s in recs)
    except Exception:
        pass
    return 0


async def _pan_count(db, session_id: str, station_index: int) -> int:
    try:
        recs = await load_station_records(db, session_id)
        for s in recs:
            if int(s.get("station_index") or 0) == int(station_index):
                return len(s.get("pans") or [])
    except Exception:
        pass
    return 0


@router.post("/stations")
async def ensure_station(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Open a station. Pass new=true (or increment) to start the next 360 sweep."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    try:
        db = await get_db()
        session = await _in_progress_session(db, job_id, kind)
        if not session:
            api_error(409, "No in-progress capture session", "session_not_found")
        sid = session["id"]
        current = await _next_station_index(db, sid)
        want_new = bool((payload or {}).get("new") or (payload or {}).get("increment"))
        idx = current
        recs = []
        try:
            recs = await load_station_records(db, sid)
        except Exception:
            recs = []
        if want_new:
            idx = (max((int(s.get("station_index") or 0) for s in recs), default=-1) + 1) if recs else (current + 1 if current or recs else 0)
            if recs and not want_new:
                idx = current
        elif recs:
            idx = int(recs[-1].get("station_index") or 0)
        else:
            idx = int((payload or {}).get("station_index") or 0)
        room_id = (payload or {}).get("room_id")
        heading = (payload or {}).get("heading")
        pose_x = (payload or {}).get("pose_x")
        pose_y = (payload or {}).get("pose_y")
        if pose_x is None or pose_y is None:
            if recs:
                prev = recs[-1]
                try:
                    ph = float(prev.get("heading")) if prev.get("heading") is not None else 90.0
                except (TypeError, ValueError):
                    ph = 90.0
                rad = math.radians(ph)
                px = float(prev.get("pose_x") or 0.0)
                py = float(prev.get("pose_y") or 0.0)
                if want_new:
                    pose_x = px + 3.0 * math.sin(rad)
                    pose_y = py - 3.0 * math.cos(rad)
                else:
                    pose_x, pose_y = px, py
            else:
                pose_x, pose_y = 0.0, 0.0
        meta = {
            "station_index": idx,
            "room_id": room_id,
            "room": (payload or {}).get("room") or (payload or {}).get("room_name"),
            "heading": heading,
            "pose_x": pose_x,
            "pose_y": pose_y,
            "yaws": [],
            "content_type": "pan_burst",
        }
        man_path = station_manifest_path(sid, idx)
        await upload_bytes(man_path, json.dumps(meta).encode("utf-8"), "application/json", upsert=True)
        bundle = await _load_session_bundle(db, job_id, session, job, preferred_room_id=room_id)
        bundle["currentStationIndex"] = idx
        return bundle
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
    """Signed PUT for a station file. Path encodes station_index + role.

    role=pan       capture/{sid}/stations/{n}/pan/{k}.jpg
    role=equirect  capture/{sid}/stations/{n}/equirect.jpg  (2:1 JPEG)
    role=usdz      capture/{sid}/roomplan.usdz
    role=roomplan  capture/{sid}/roomplan.json
    """
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    try:
        db = await get_db()
        session = await _in_progress_session(db, job_id, kind)
        if not session:
            api_error(409, "No in-progress capture session", "session_not_found")
        sid = session["id"]
        role = _role(payload)
        idx = (payload or {}).get("station_index")
        if idx is None:
            idx = await _next_station_index(db, sid)
        idx = int(idx)
        yaw = (payload or {}).get("yaw")
        if role == "equirect":
            path = equirect_path(sid, idx)
        elif role == "usdz":
            path = roomplan_usdz_path(sid)
        elif role == "roomplan":
            path = roomplan_json_path(sid)
        elif role == "manifest":
            path = station_manifest_path(sid, idx)
        else:
            k = await _pan_count(db, sid, idx)
            k = int((payload or {}).get("pan_index") or k) + (0 if (payload or {}).get("pan_index") is not None else 1)
            if k < 1:
                k = 1
            path = pan_path(sid, idx, k)
        ctype = _content_type_for(role, payload)
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
            "station_index": idx,
            "yaw": yaw,
            "role": role,
            "content_type": ctype,
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
    """Insert capture_frames after a successful PUT. Requires a stations/ path."""
    job_id = (payload or {}).get("job_id") or ""
    session_id = (payload or {}).get("session_id") or ""
    room_id = (payload or {}).get("room_id")
    storage_path = (payload or {}).get("storage_path") or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    if not session_id or not storage_path:
        api_error(400, "session_id and storage_path are required", "missing_frame_fields")
    if not str(storage_path).startswith(f"capture/{session_id}/"):
        api_error(400, "storage_path does not match this session", "bad_storage_path")
    role = _role(payload) if (payload or {}).get("role") else role_from_path(storage_path)
    if "/stations/" not in storage_path and role not in ("usdz", "roomplan"):
        api_error(400, "Upload must land under capture/{session}/stations/{n}/", "bad_storage_path")
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
        idx = (payload or {}).get("station_index")
        if idx is None:
            idx = parse_station_index_from_path(storage_path)
        yaw = (payload or {}).get("yaw")
        row = {
            "session_id": session_id,
            "room_id": room_id or None,
            "job_id": job_id,
            "storage_path": storage_path,
            "created_at": now,
        }
        extra = {k: v for k, v in {
            "station_index": int(idx) if idx is not None else None,
            "yaw": yaw,
            "content_type": _content_type_for(role, payload),
            "role": role,
        }.items() if v is not None}
        try:
            await db.table("capture_frames").insert({**row, **extra}).execute()
        except Exception:
            await db.table("capture_frames").insert(row).execute()

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

        if idx is None:
            idx = parse_station_index_from_path(storage_path) or 0
        man_path = station_manifest_path(session_id, int(idx))
        meta = {
            "station_index": int(idx),
            "room_id": room_id,
            "heading": (payload or {}).get("heading"),
            "pose_x": (payload or {}).get("pose_x"),
            "pose_y": (payload or {}).get("pose_y"),
            "content_type": "equirect" if role == "equirect" else "pan_burst",
            "yaws": [yaw] if yaw is not None else [],
        }
        # Merge yaws if a manifest already exists in this request cycle — best-effort.
        try:
            recs = await load_station_records(db, session_id)
            for s in recs:
                if int(s.get("station_index") or 0) == int(idx):
                    yaws = list(s.get("yaws") or [])
                    if yaw is not None:
                        yaws.append(yaw)
                    meta["yaws"] = yaws
                    meta["room"] = s.get("room")
                    meta["heading"] = s.get("heading") if meta.get("heading") is None else meta["heading"]
                    break
        except Exception:
            pass
        await upload_bytes(man_path, json.dumps(meta).encode("utf-8"), "application/json", upsert=True)

        await db.table("capture_pipeline").update({
            "detail": f"{new_count} frames",
            "status": "active",
        }).eq("session_id", session_id).eq("title", "Captured").execute()

        session["frame_count"] = new_count
        bundle = await _load_session_bundle(db, job_id, session, job, preferred_room_id=room_id)
        bundle["currentStationIndex"] = int(idx)
        return bundle
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if _missing_table(e):
            api_error(503, "Capture tables are not applied yet", "capture_tables_missing")
        raise


async def _note_floorplan_on_job(db, job: dict, job_id: str, sid: str, n: int, now: str) -> None:
    """Checklist only. Phone-origin plans are Bricks-generated, not BoxBrownie."""
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


async def _fail_session(db, sid: str, stage_title: str, error: str, now: str) -> None:
    await db.table("capture_sessions").update({
        "status": "failed",
        "updated_at": now,
    }).eq("id", sid).execute()
    await db.table("capture_pipeline").update({
        "status": "todo",
        "detail": (error or "compile failed")[:500],
    }).eq("session_id", sid).eq("title", stage_title).execute()


_PRODUCT_LABEL = {"splat": "3D tour", "floorplan": "Floor plan", "visit": "Virtual Visit"}


async def _first_delivered_of_kind(db, job_id: str, kind: str) -> bool:
    """True when this job has exactly one delivered session of `kind` (the one just marked)."""
    try:
        res = await db.table("capture_sessions").select("id").eq("job_id", job_id).eq(
            "kind", kind
        ).eq("status", "delivered").execute()
        rows = getattr(res, "data", None) or []
        return len(rows) <= 1
    except Exception:
        return True


async def _notify_capture_session_delivered(db, job: dict, sid: str, kind: str, output_path: str) -> None:
    """Best-effort agent email on first successful compile deliver of this kind.

    Also bumps jobs.updated_at so the OS listing file feels fresh when GO
    capture lands (Activity + desk sort). Skip when not the first of kind.
    """
    job_id = job.get("id") or ""
    if not job_id:
        return
    if not await _first_delivered_of_kind(db, job_id, kind):
        return
    # Touch the job so OS living-file Activity / freshness pick up the deliver.
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await db.table("jobs").update({"updated_at": now}).eq("id", job_id).execute()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Job %s: capture deliver updated_at bump failed: %s", job_id, e
        )
    product = _PRODUCT_LABEL.get(kind, kind)
    delivery_urls = []
    try:
        if kind == "splat":
            from ..services.capture_worker import public_tour_embed_url
            url = public_tour_embed_url(job_id, sid)
            if url:
                delivery_urls.append({"url": url, "filename": "3D tour"})
        elif kind == "floorplan" and output_path:
            from ..services.storage import get_signed_download_url
            signed = await get_signed_download_url(output_path, expiry=60 * 60 * 24)
            if signed:
                delivery_urls.append({"url": signed, "filename": "Floor plan"})
    except Exception:
        delivery_urls = []
    try:
        from ..services.client_comms import notify_capture_delivered
        await notify_capture_delivered(
            job_id,
            products=[product],
            address=(job.get("address") or ""),
            booking_id=(job.get("booking_id") or ""),
            delivery_urls=delivery_urls,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "Job %s: capture delivery notification failed: %s", job_id, e
        )


async def _deliver_session(
    db,
    sid: str,
    output_path: str,
    kind: str,
    now: str,
    *,
    job: dict | None = None,
    notify: bool = False,
) -> None:
    await db.table("capture_sessions").update({
        "status": "delivered",
        "output_storage_path": output_path,
        "updated_at": now,
    }).eq("id", sid).execute()
    await db.table("capture_pipeline").update({"title": "Building tour"}).eq(
        "session_id", sid
    ).eq("title", "Splatting").execute()
    stage = "Tracing" if kind == "floorplan" else "Building tour"
    for title, detail in (
        (stage, "Compiled from 360 stations"),
        ("QC", "Skipped — Bricks-generated"),
        ("Delivered", "On listing file"),
    ):
        await db.table("capture_pipeline").update({
            "status": "done",
            "detail": detail,
        }).eq("session_id", sid).eq("title", title).execute()
    # Agent email only on submit/compile first deliver — never on rebuild (avoid spam).
    if notify and job is not None:
        await _notify_capture_session_delivered(db, job, sid, kind, output_path)


async def _compile_or_fail(db, job: dict, session: dict, kind: str, now: str):
    sid = session["id"]
    await db.table("capture_pipeline").update({"title": "Building tour"}).eq(
        "session_id", sid
    ).eq("title", "Splatting").execute()
    stage = "Tracing" if kind == "floorplan" else "Building tour"
    processing = "tracing" if kind == "floorplan" else "splatting"
    try:
        await db.table("capture_sessions").update({
            "status": processing,
            "updated_at": now,
        }).eq("id", sid).execute()
    except Exception:
        await db.table("capture_sessions").update({
            "status": "splatting",
            "updated_at": now,
        }).eq("id", sid).execute()
        processing = "splatting"
    await db.table("capture_pipeline").update({
        "status": "active",
        "detail": "Stitching 360 stations" if kind != "floorplan" else "Deriving plan from stations",
    }).eq("session_id", sid).eq("title", stage).execute()
    try:
        output = await process_capture_session(db, job, session, kind)
    except CaptureWorkerError as e:
        await _fail_session(db, sid, stage, str(e), datetime.now(timezone.utc).isoformat())
        raise
    except Exception as e:
        await _fail_session(db, sid, stage, str(e), datetime.now(timezone.utc).isoformat())
        raise CaptureWorkerError(str(e)) from e
    await _deliver_session(
        db, sid, output, kind, datetime.now(timezone.utc).isoformat(),
        job=job, notify=True,
    )
    session["status"] = "delivered"
    session["output_storage_path"] = output
    return output


@router.post("/submit")
async def submit_session(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Compile stations into a real deliverable. Refuses if no stations / no equirects."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    try:
        db = await get_db()
        session = await _in_progress_session(db, job_id, kind)
        if not session:
            latest = await _latest_session(db, job_id, kind)
            if latest and latest.get("status") == "delivered":
                return {"ok": True, "session": await _load_session_bundle(db, job_id, latest, job)}
            if latest and latest.get("status") in ("submitted", "splatting", "tracing", "failed"):
                session = latest
            else:
                api_error(409, "No in-progress capture session to submit", "session_not_found")

        now = datetime.now(timezone.utc).isoformat()
        sid = session["id"]
        stations = await load_station_records(db, sid)
        from ..services.capture_worker import load_roomplan
        roomplan = await load_roomplan(sid) if kind == "floorplan" else None
        ok, reason = stations_have_compile_input(stations, kind=kind, has_roomplan=bool(roomplan))
        if not ok:
            if reason == "no_stations":
                msg = (
                    "Submit needs at least one station (or a RoomPlan)"
                    if kind == "floorplan"
                    else "Submit needs at least one 360 station"
                )
                api_error(400, msg, "no_stations")
            api_error(400, "Submit needs equirects or a stitchable 360 pan at each station", "no_equirects")

        n = int(session.get("frame_count") or 0)
        await db.table("capture_sessions").update({
            "submitted_at": now,
            "updated_at": now,
        }).eq("id", sid).execute()
        await db.table("capture_pipeline").update({
            "status": "done",
            "detail": f"{len(stations)} stations · {n} frames",
        }).eq("session_id", sid).eq("title", "Captured").execute()
        if kind == "floorplan":
            await _note_floorplan_on_job(db, job, job_id, sid, n, now)
        try:
            await _compile_or_fail(db, job, session, kind, now)
            return {"ok": True, "session": await _load_session_bundle(db, job_id, session, job)}
        except CaptureWorkerError as e:
            bundle = await _load_session_bundle(db, job_id, await _latest_session(db, job_id, kind) or session, job)
            return {"ok": False, "error": str(e), "session": bundle}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if _missing_table(e):
            api_error(503, "Capture tables are not applied yet", "capture_tables_missing")
        raise


async def _session_by_id(db, session_id: str) -> Optional[dict]:
    res = await db.table("capture_sessions").select("*").eq("id", session_id).maybe_single().execute()
    return res.data or None


async def _run_rebuild_tour(db, job: dict, session: dict) -> dict:
    """Rewrite hosted tour HTML/JSON from stored 360s. No recapture, no stitch."""
    kind = session.get("kind") or "splat"
    if kind != "splat":
        api_error(400, "Rebuild tour player is for 3D tours, not floor plans", "not_a_tour")
    now = datetime.now(timezone.utc).isoformat()
    try:
        output = await rebuild_tour_from_equirects(db, job, session)
    except CaptureWorkerError as e:
        bundle = await _load_session_bundle(db, job["id"], session, job)
        return {"ok": False, "rebuilt": False, "stitched": False, "error": str(e), "session": bundle}
    session["output_storage_path"] = output
    if session.get("status") != "delivered":
        await _deliver_session(db, session["id"], output, "splat", now)
        session["status"] = "delivered"
    else:
        await db.table("capture_sessions").update({
            "output_storage_path": output,
            "updated_at": now,
        }).eq("id", session["id"]).execute()
    bundle = await _load_session_bundle(db, job["id"], session, job)
    return {
        "ok": True,
        "rebuilt": True,
        "stitched": False,
        "outputStoragePath": output,
        "session": bundle,
    }


async def _run_rebuild_floorplan(db, job: dict, session: dict) -> dict:
    """Rewrite floor-plan PNG from stored stations/RoomPlan. No recapture."""
    kind = session.get("kind") or "floorplan"
    if kind != "floorplan":
        api_error(400, "Rebuild floor plan is for floor plans, not 3D tours", "not_a_floorplan")
    now = datetime.now(timezone.utc).isoformat()
    try:
        output = await rebuild_floorplan_from_stations(db, job, session)
    except CaptureWorkerError as e:
        bundle = await _load_session_bundle(db, job["id"], session, job)
        return {"ok": False, "rebuilt": False, "stitched": False, "error": str(e), "session": bundle}
    session["output_storage_path"] = output
    if session.get("status") != "delivered":
        await _deliver_session(db, session["id"], output, "floorplan", now)
        session["status"] = "delivered"
    else:
        await db.table("capture_sessions").update({
            "output_storage_path": output,
            "updated_at": now,
        }).eq("id", session["id"]).execute()
    bundle = await _load_session_bundle(db, job["id"], session, job)
    return {
        "ok": True,
        "rebuilt": True,
        "stitched": False,
        "outputStoragePath": output,
        "session": bundle,
    }




@router.get("/sessions/{session_id}/scenes/{station_index}/url")
async def scene_signed_url(
    session_id: str,
    station_index: int,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Signed GET for a stored 360 so GO can place tags at the current look."""
    db = await get_db()
    session = await _session_by_id(db, session_id)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    job_id = session.get("job_id") or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    idx = int(station_index)
    paths = [
        f"tours/{job['id']}/{session_id}/scenes/{idx}.jpg",
        f"capture/{session_id}/stations/{idx}/equirect.jpg",
    ]
    url = None
    path = None
    for cand in paths:
        url = await get_signed_download_url(cand, expiry=60 * 30)
        if url:
            path = cand
            break
    if not url:
        api_error(404, "No stored 360 for that station", "no_equirects")
    return {"url": url, "path": path, "station_index": idx, "session_id": session_id}


@router.post("/sessions/{session_id}/tags")
async def add_tour_tag(
    session_id: str,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Add an amenity/feature tag at a yaw/pitch on a stored 360. Rebuilds the player."""
    db = await get_db()
    session = await _session_by_id(db, session_id)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    job_id = session.get("job_id") or (payload or {}).get("job_id") or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    tag = normalize_tag(payload or {}, station_index=(payload or {}).get("station_index"), mint_id=True)
    if not tag:
        api_error(400, "Tag needs a label", "missing_tag_label")
    if tag.get("station_index") is None:
        api_error(400, "station_index is required", "missing_station_index")
    existing = await load_session_tags(session_id) or []
    existing.append(tag)
    saved = await save_session_tags(session_id, existing)
    result = await _run_rebuild_tour(db, job, session)
    result["tag"] = tag
    result["tags"] = saved
    return result


@router.delete("/sessions/{session_id}/tags/{tag_id}")
async def delete_tour_tag(
    session_id: str,
    tag_id: str,
    job_id: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    db = await get_db()
    session = await _session_by_id(db, session_id)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    job_id = session.get("job_id") or job_id or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    existing = await load_session_tags(session_id) or []
    kept = [t for t in existing if t.get("id") != tag_id]
    if len(kept) == len(existing):
        api_error(404, "Tag not found", "tag_not_found")
    saved = await save_session_tags(session_id, kept)
    result = await _run_rebuild_tour(db, job, session)
    result["tags"] = saved
    return result


@router.post("/sessions/{session_id}/rebuild-tour")
async def rebuild_tour_for_session(
    session_id: str,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Refresh a compiled 3D tour player from stored equirects. Does not recapture.

    Idempotent: rewrites tours/{job}/{session}/index.html and tour.json. Scene
    JPEGs and the public embed URL stay the same. Photographers use this after
    a player HTML change (in-panorama hotspots) so listing embeds pick it up.
    """
    db = await get_db()
    session = await _session_by_id(db, session_id)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    job_id = session.get("job_id") or (payload or {}).get("job_id") or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    if session.get("job_id") and session["job_id"] != job["id"]:
        api_error(404, "Job not found", "job_not_found")
    return await _run_rebuild_tour(db, job, session)



@router.post("/tags")
async def add_tour_tag_for_job(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Job-scoped add tag. Same as POST /sessions/{id}/tags."""
    job_id = (payload or {}).get("job_id") or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    db = await get_db()
    session_id = (payload or {}).get("session_id") or (payload or {}).get("sessionId")
    session = await _session_by_id(db, session_id) if session_id else None
    if not session:
        session = await _latest_session(db, job_id, "splat")
    if not session:
        api_error(409, "No capture session", "session_not_found")
    return await add_tour_tag(session["id"], payload, authorization, x_user_id, x_internal_api_key)

@router.post("/rebuild-tour")
async def rebuild_tour_for_job(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Job-scoped rebuild. Same as POST /sessions/{id}/rebuild-tour."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind") or "splat")
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    db = await get_db()
    session_id = (payload or {}).get("session_id") or (payload or {}).get("sessionId")
    session = await _session_by_id(db, session_id) if session_id else None
    if session and session.get("job_id") and session["job_id"] != job["id"]:
        api_error(404, "Job not found", "job_not_found")
    if not session:
        session = await _latest_session(db, job_id, kind)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    return await _run_rebuild_tour(db, job, session)


@router.post("/sessions/{session_id}/rebuild-floorplan")
async def rebuild_floorplan_for_session(
    session_id: str,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Refresh a delivered floor-plan PNG from stored stations/RoomPlan. Does not recapture.

    Idempotent: rewrites the same storage path with current floorplan_tracer
    (north arrow, scale, labels). Listing media URL path stays the same.
    """
    db = await get_db()
    session = await _session_by_id(db, session_id)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    job_id = session.get("job_id") or (payload or {}).get("job_id") or ""
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    if session.get("job_id") and session["job_id"] != job["id"]:
        api_error(404, "Job not found", "job_not_found")
    return await _run_rebuild_floorplan(db, job, session)


@router.post("/rebuild-floorplan")
async def rebuild_floorplan_for_job(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Job-scoped rebuild. Same as POST /sessions/{id}/rebuild-floorplan."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind") or "floorplan")
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    db = await get_db()
    session_id = (payload or {}).get("session_id") or (payload or {}).get("sessionId")
    session = await _session_by_id(db, session_id) if session_id else None
    if session and session.get("job_id") and session["job_id"] != job["id"]:
        api_error(404, "Job not found", "job_not_found")
    if not session:
        session = await _latest_session(db, job_id, kind)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    return await _run_rebuild_floorplan(db, job, session)


@router.post("/process")
async def process_session(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Retry compile. Delivered 3D tours rebuild the player from stored equirects."""
    job_id = (payload or {}).get("job_id") or ""
    kind = _normalize_kind((payload or {}).get("kind"))
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    db = await get_db()
    session = await _latest_session(db, job_id, kind)
    if not session:
        api_error(409, "No capture session", "session_not_found")
    if session.get("status") == "delivered" and session.get("output_storage_path"):
        if kind == "splat" or session.get("kind") == "splat":
            return await _run_rebuild_tour(db, job, session)
        if kind == "floorplan" or session.get("kind") == "floorplan":
            return await _run_rebuild_floorplan(db, job, session)
        return {
            "ok": True,
            "rebuilt": False,
            "session": await _load_session_bundle(db, job_id, session, job),
        }
    now = datetime.now(timezone.utc).isoformat()
    try:
        await _compile_or_fail(db, job, session, kind, now)
        return {"ok": True, "rebuilt": True, "session": await _load_session_bundle(db, job_id, session, job)}
    except CaptureWorkerError as e:
        bundle = await _load_session_bundle(db, job_id, await _latest_session(db, job_id, kind) or session, job)
        return {"ok": False, "error": str(e), "session": bundle}

def _normalize_visit_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    lower = url.lower()
    if not (lower.startswith("https://") or lower.startswith("http://")):
        api_error(400, "Virtual Visit URL must start with https://", "bad_visit_url")
    # Prefer https for Polycam / share links
    if lower.startswith("http://"):
        url = "https://" + url[7:]
    return url


def _visit_ext(filename: str) -> str:
    name = (filename or "").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ("splat", "ply", "spz"):
        api_error(400, "Only .splat, .ply, or .spz files are supported", "bad_visit_file")
    return ext


@router.post("/visit/sign")
async def sign_visit_upload(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Signed PUT for a .ply/.splat/.spz Virtual Visit file (bypasses 4.5MB Vercel body)."""
    import uuid as _uuid

    job_id = (payload or {}).get("job_id") or ""
    filename = str((payload or {}).get("filename") or "visit.splat")
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    flags = job_capture_entitlements(job)
    if not kind_purchased(job, "visit"):
        api_error(403, capture_forbidden_message("visit", flags), "capture_kind_not_purchased")
    ext = _visit_ext(filename)
    visit_id = str(_uuid.uuid4())
    storage_path = f"visits/{job_id}/{visit_id}.{ext}"
    signed = await create_signed_upload_url(storage_path)
    if not signed or not signed.get("signed_url"):
        api_error(502, "Could not mint visit upload URL", "visit_sign_failed")
    return {
        "ok": True,
        "storage_path": storage_path,
        "signed_url": signed.get("signed_url"),
        "token": signed.get("token"),
        "path": signed.get("path") or storage_path,
        "content_type": "application/octet-stream",
    }


@router.post("/visit/deliver")
async def deliver_virtual_visit(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Vendor delivers Virtual Visit from GO onto the listing file.

    Body: job_id + either polycam_url / embed_url OR storage_path from /visit/sign.
    Bridge until owned GPU cook: Polycam share URL or uploaded .ply/.splat/.spz.
    """
    job_id = (payload or {}).get("job_id") or ""
    title = str((payload or {}).get("title") or "Virtual Visit").strip() or "Virtual Visit"
    polycam_url = _normalize_visit_url(
        str((payload or {}).get("polycam_url") or (payload or {}).get("embed_url") or "")
    )
    storage_path = str((payload or {}).get("storage_path") or "").strip()
    _, job = await _actor_and_job(authorization, x_user_id, job_id)
    flags = job_capture_entitlements(job)
    if not kind_purchased(job, "visit"):
        api_error(403, capture_forbidden_message("visit", flags), "capture_kind_not_purchased")
    if not polycam_url and not storage_path:
        api_error(400, "Provide a Polycam URL or a storage_path from /visit/sign", "missing_visit_payload")
    if polycam_url and storage_path:
        api_error(400, "Send either a Polycam URL or a file path, not both", "ambiguous_visit_payload")
    if storage_path:
        if not storage_path.startswith(f"visits/{job_id}/"):
            api_error(400, "storage_path must be under visits/{job_id}/", "bad_visit_path")
        ext = storage_path.rsplit(".", 1)[-1].lower()
        if ext not in ("splat", "ply", "spz"):
            api_error(400, "Visit file must be .splat, .ply, or .spz", "bad_visit_file")

    from ..services.client_comms import notify_capture_delivered, register_property_visit

    if polycam_url:
        ok = await register_property_visit(
            job_id,
            title=title,
            embed_url=polycam_url,
            visit_type="url",
        )
        # type=url is also used for hosted tours; title Virtual Visit keeps VisitViewer honest.
        delivery_urls = [{"url": polycam_url, "filename": "Virtual Visit"}]
        output = polycam_url
    else:
        ok = await register_property_visit(
            job_id,
            storage_path=storage_path,
            title=title,
            visit_type="file",
        )
        delivery_urls = []
        try:
            signed = await get_signed_download_url(storage_path, expiry=60 * 60 * 24)
            if signed:
                delivery_urls.append({"url": signed, "filename": "Virtual Visit"})
        except Exception:
            pass
        output = storage_path

    if not ok:
        api_error(502, "Could not register Virtual Visit on the listing file", "visit_register_failed")

    # Freshness for OS listing + agent email (best-effort).
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.table("jobs").update({"updated_at": now}).eq("id", job_id).execute()
    except Exception:
        pass
    try:
        await notify_capture_delivered(
            job_id,
            products=["Virtual Visit"],
            address=(job.get("address") or ""),
            booking_id=(job.get("booking_id") or ""),
            delivery_urls=delivery_urls,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "Job %s: Virtual Visit delivery notification failed: %s", job_id, e
        )

    return {
        "ok": True,
        "job_id": job_id,
        "kind": "visit",
        "title": title,
        "output": output,
        "mode": "url" if polycam_url else "file",
    }
