"""Guided Visit video capture (GO): one continuous phone video per floor or room.

This is the *owned Virtual Visit* input (Gaussian splat pipeline, see
visit-worker/ and ship/visit-pipeline-design.md). It is deliberately separate
from ``kind=splat`` in capture.py, which is the 360-station "3D tour" despite
its name. Sessions here use ``capture_sessions.kind = 'visit'``.

Data model (existing Bricks-OS tables, no new tables):
  capture_sessions  kind='visit', one in-progress session per job
  capture_rooms     one row per *recording* (a floor walk or a single room);
                    ``frames`` holds the recording length in whole seconds,
                    ``tone`` is bad (uploading) / ok (all parts uploaded)
  capture_frames    one row per uploaded part: role='video_part',
                    station_index=part number (1-based), room_id=recording
  capture_pipeline  Recorded → Reconstructing → QC → Delivered

Storage layout (brxs-deliverables):
  capture/{sid}/visit/{rid}/part-00001.bin …   raw byte ranges of the .mp4/.mov
  capture/{sid}/visit/{rid}/manifest.json      size, duration, lens, room marks
  capture/{sid}/visit/job.json                 worker spec written on submit

Resumable upload: the phone splits the file into fixed ``part_size`` byte
ranges, PUTs each to its own signed URL and commits it. On reconnect it asks
which parts are committed and sends only the rest. The worker concatenates the
parts in order (visit-worker/worker/entry_job.sh, CAPTURE_PARTS).

Limits mirror what SplatTour publishes for the same capture method: 15 min and
4 GB per video, at least 1 MB.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ..services.capture_entitlements import capture_forbidden_message, job_capture_entitlements, kind_purchased
from ..services.storage import (
    STORAGE_BUCKET,
    create_signed_upload_url,
    delete_objects,
    download_bytes,
    get_signed_download_url,
    upload_bytes,
)
from ..shared.auth import verify_internal_api_key
from ..shared.database import get_db
from ..shared.errors import api_error

router = APIRouter(prefix="/api/capture/visit-video", tags=["capture"])
logger = logging.getLogger("capture.visit_video")

KIND = "visit"
MAX_BYTES = 4_000_000_000
MIN_BYTES = 1_000_000
MAX_SECONDS = 15 * 60
MAX_RECORDINGS = 12
MAX_ROOM_MARKS = 40
PART_SIZE = 8 * 1024 * 1024
# The Fair-Use Blob proxy PUT goes through a Vercel function (4.5 MB body cap).
PART_SIZE_PROXY = 4 * 1024 * 1024
SIGN_BATCH = 20
PART_ROLE = "video_part"

PIPELINE = (
    ("Recorded", "0 recordings", "active"),
    ("Reconstructing", "Frames, camera poses and per-room splats", "todo"),
    ("QC", "Automatic checks + human review", "todo"),
    ("Delivered", "Virtual Visit on the listing file", "todo"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recording_dir(session_id: str, recording_id: str) -> str:
    return f"capture/{session_id}/visit/{recording_id}"


def part_path(session_id: str, recording_id: str, n: int) -> str:
    return f"{recording_dir(session_id, recording_id)}/part-{int(n):05d}.bin"


def manifest_path(session_id: str, recording_id: str) -> str:
    return f"{recording_dir(session_id, recording_id)}/manifest.json"


def job_spec_path(session_id: str) -> str:
    return f"capture/{session_id}/visit/job.json"


# ---------------------------------------------------------------------------
# Data access. REST first; Postgres helpers when PostgREST is Fair-Use 402.
# Tests swap ``repo`` for an in-memory fake.
# ---------------------------------------------------------------------------

def _quota(e: Exception) -> bool:
    from ..shared.pg_fallback import _is_rest_quota_error
    return _is_rest_quota_error(e)


def _kind_constraint_missing(e: Exception) -> bool:
    t = str(e).lower()
    return "capture_sessions_kind_check" in t or ("23514" in t and "kind" in t)


class Repo:
    async def in_progress_session(self, job_id: str) -> Optional[dict]:
        try:
            db = await get_db()
            res = await db.table("capture_sessions").select("*").eq("job_id", job_id).eq(
                "kind", KIND).eq("status", "in_progress").order("created_at", desc=True).limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_select_session_in_progress
            return pg_select_session_in_progress(job_id, KIND)

    async def latest_session(self, job_id: str) -> Optional[dict]:
        try:
            db = await get_db()
            res = await db.table("capture_sessions").select("*").eq("job_id", job_id).eq(
                "kind", KIND).order("created_at", desc=True).limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_select_session_latest
            return pg_select_session_latest(job_id, KIND)

    async def session_by_id(self, session_id: str, job_id: str) -> Optional[dict]:
        try:
            db = await get_db()
            res = await db.table("capture_sessions").select("*").eq("id", session_id).eq(
                "job_id", job_id).limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_select_session_by_id
            return pg_select_session_by_id(session_id, job_id)

    async def create_session(self, job_id: str) -> dict:
        now = _now()
        db = await get_db()
        inserted = await db.table("capture_sessions").insert({
            "job_id": job_id, "status": "in_progress", "kind": KIND,
            "frame_count": 0, "created_at": now, "updated_at": now,
        }).execute()
        session = (inserted.data or [None])[0]
        if not session:
            api_error(500, "Could not create capture session", "session_create_failed")
        await db.table("capture_pipeline").insert([
            {"session_id": session["id"], "job_id": job_id, "title": t, "detail": d, "status": s, "position": i}
            for i, (t, d, s) in enumerate(PIPELINE)
        ]).execute()
        return session

    async def recordings(self, session_id: str) -> list[dict]:
        try:
            db = await get_db()
            res = await db.table("capture_rooms").select("*").eq("session_id", session_id).order(
                "position", desc=False).execute()
            return res.data or []
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_select_rooms
            return pg_select_rooms(session_id)

    async def insert_recording(self, session_id: str, job_id: str, name: str, position: int, seconds: int) -> dict:
        row = {"session_id": session_id, "job_id": job_id, "name": name, "frames": seconds,
               "tone": "bad", "position": position}
        try:
            db = await get_db()
            res = await db.table("capture_rooms").insert(row).execute()
            return (res.data or [None])[0] or {}
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_insert_room
            return pg_insert_room(session_id, job_id, name=name, frames=seconds, tone="bad", position=position) or {}

    async def update_recording(self, recording_id: str, fields: dict) -> None:
        try:
            db = await get_db()
            await db.table("capture_rooms").update(fields).eq("id", recording_id).execute()
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_update_room
            pg_update_room(recording_id, fields)

    async def delete_recording(self, session_id: str, recording_id: str) -> None:
        db = await get_db()
        await db.table("capture_frames").delete().eq("session_id", session_id).eq("room_id", recording_id).execute()
        await db.table("capture_rooms").delete().eq("session_id", session_id).eq("id", recording_id).execute()

    async def parts(self, session_id: str, recording_id: str) -> list[dict]:
        try:
            db = await get_db()
            res = await db.table("capture_frames").select("id, station_index, storage_path, room_id, role").eq(
                "session_id", session_id).eq("room_id", recording_id).eq("role", PART_ROLE).execute()
            return res.data or []
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_select_frames
            return [f for f in pg_select_frames(session_id)
                    if f.get("room_id") == recording_id and f.get("role") == PART_ROLE]

    async def insert_part(self, row: dict) -> None:
        try:
            db = await get_db()
            await db.table("capture_frames").insert(row).execute()
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_insert_frame
            pg_insert_frame(row)

    async def update_session(self, session_id: str, fields: dict) -> None:
        try:
            db = await get_db()
            await db.table("capture_sessions").update({**fields, "updated_at": _now()}).eq("id", session_id).execute()
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_update_session
            pg_update_session(session_id, fields)

    async def pipeline(self, session_id: str) -> list[dict]:
        try:
            db = await get_db()
            res = await db.table("capture_pipeline").select("*").eq("session_id", session_id).order(
                "position", desc=False).execute()
            return res.data or []
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_select_pipeline
            return pg_select_pipeline(session_id)

    async def update_pipeline(self, session_id: str, title: str, status: str, detail: Optional[str] = None) -> None:
        fields: dict[str, Any] = {"status": status}
        if detail is not None:
            fields["detail"] = detail
        try:
            db = await get_db()
            await db.table("capture_pipeline").update(fields).eq("session_id", session_id).eq("title", title).execute()
        except Exception as e:
            if not _quota(e):
                raise
            from ..shared.pg_capture import pg_update_pipeline
            pg_update_pipeline(session_id, title, fields)


class Blobs:
    async def read_json(self, path: str) -> Optional[dict]:
        raw = await download_bytes(path)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    async def write_json(self, path: str, data: dict) -> bool:
        return await upload_bytes(path, json.dumps(data, separators=(",", ":")).encode(), "application/json")

    async def sign_put(self, path: str) -> Optional[dict]:
        return await create_signed_upload_url(path)

    async def sign_get(self, path: str) -> Optional[str]:
        return await get_signed_download_url(path, expiry=6 * 3600)

    async def remove(self, paths: list[str]) -> bool:
        return await delete_objects(paths)


repo = Repo()
blobs = Blobs()


async def _job(authorization, x_user_id, job_id, x_internal_api_key=None) -> dict:
    from .capture import _actor_and_job
    _, job = await _actor_and_job(authorization, x_user_id, job_id, x_internal_api_key)
    if not kind_purchased(job, KIND):
        api_error(403, capture_forbidden_message(KIND, job_capture_entitlements(job)), "capture_kind_not_purchased")
    return job


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"\s+")


def clean_name(raw: Any, fallback: str) -> str:
    name = _NAME_RE.sub(" ", str(raw or "")).strip()[:60]
    return name or fallback


def validate_recording(payload: dict) -> dict:
    """Return the normalized manifest fields or raise 400/413."""
    try:
        size = int((payload or {}).get("size_bytes") or 0)
        duration = float((payload or {}).get("duration_s") or 0)
    except (TypeError, ValueError):
        api_error(400, "size_bytes and duration_s must be numbers", "bad_visit_recording")
    if size > MAX_BYTES:
        api_error(413, "Recording is over 4 GB. Record again in 1080p or keep the floor under 15 minutes.", "visit_recording_too_large")
    if size < MIN_BYTES:
        api_error(400, "Recording is too small to be a walk-through (under 1 MB).", "visit_recording_too_small")
    if duration <= 0:
        api_error(400, "duration_s is required", "bad_visit_recording")
    # Phones overshoot maxDuration by a frame or two; anything past 15 min is ignored by the worker.
    if duration > MAX_SECONDS + 5:
        api_error(413, "Recording is over 15 minutes. Record one video per floor.", "visit_recording_too_long")
    scope = str((payload or {}).get("scope") or "floor").strip().lower()
    if scope not in ("floor", "room"):
        api_error(400, "scope must be floor or room", "bad_visit_recording")
    try:
        floor_index = max(0, min(9, int((payload or {}).get("floor_index") or 0)))
    except (TypeError, ValueError):
        floor_index = 0
    marks_in = (payload or {}).get("rooms") or []
    if not isinstance(marks_in, list) or len(marks_in) > MAX_ROOM_MARKS:
        api_error(400, f"rooms must be a list of at most {MAX_ROOM_MARKS} marks", "bad_visit_rooms")
    marks: list[dict] = []
    last_t1 = 0.0
    for i, m in enumerate(marks_in):
        if not isinstance(m, dict):
            api_error(400, f"Room mark {i + 1} is not an object", "bad_visit_rooms")
        try:
            t0 = float(m.get("t0"))
            t1 = float(m.get("t1"))
        except (TypeError, ValueError):
            api_error(400, f"Room mark {i + 1} needs numeric t0/t1", "bad_visit_rooms")
        t0 = max(0.0, t0)
        t1 = min(duration, t1)
        if t1 <= t0 or t0 < last_t1 - 0.5:
            api_error(400, f"Room mark {i + 1} overlaps or is empty", "bad_visit_rooms")
        last_t1 = t1
        marks.append({"name": clean_name(m.get("name"), f"Room {i + 1}"), "t0": round(t0, 2), "t1": round(t1, 2)})
    coach = (payload or {}).get("coach")
    coach_clean: dict = {}
    if isinstance(coach, dict):
        for k in ("turn_fast_pct", "tilt_pct", "shake_pct", "still_pct", "max_turn_dps", "pauses", "avg_walk_mps"):
            v = coach.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v):
                coach_clean[k] = round(float(v), 2)
    return {
        "size_bytes": size,
        "duration_s": round(duration, 2),
        "scope": scope,
        "floor_index": floor_index,
        "rooms": marks,
        "content_type": str((payload or {}).get("content_type") or "video/mp4")[:40],
        "width": int((payload or {}).get("width") or 0) or None,
        "height": int((payload or {}).get("height") or 0) or None,
        "lens": str((payload or {}).get("lens") or "")[:60] or None,
        "coach": coach_clean or None,
        "capture": clean_capture_meta((payload or {}).get("capture")),
    }


_CAPTURE_STR_KEYS = ("guide", "platform", "device", "device_status", "lens", "stabilization", "hdr", "mime", "codec")
_CAPTURE_NUM_KEYS = ("guide_version", "width", "height", "fps", "bitrate")
_LOCK_KINDS = ("exposure", "focus", "whiteBalance")
_LOCK_STATES = ("locked", "metering-locked", "auto", "unsupported", "failed")


def clean_capture_meta(raw: Any) -> Optional[dict]:
    """Guided-capture settings the phone reports (what was locked, lens, format, device policy).

    Advisory metadata for QC and the Visit worker; never trusted for access decisions. Unknown
    keys are dropped, strings are capped, lock states are limited to a known set.
    """
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for k in _CAPTURE_STR_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:60]
    for k in _CAPTURE_NUM_KEYS:
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v < 1e9:
            out[k] = round(float(v), 2)
    locks = raw.get("locks")
    if isinstance(locks, dict):
        clean = {k: locks[k] for k in _LOCK_KINDS if locks.get(k) in _LOCK_STATES}
        if clean:
            out["locks"] = clean
    return out or None


def recording_complete(row: Optional[dict], man: Optional[dict]) -> bool:
    """The database row is the source of truth for "fully uploaded".

    complete_recording() sets capture_rooms.tone = 'ok' after every part is
    committed. The manifest.json copy in storage is rewritten at the same time,
    but storage reads of an overwritten object can be served stale by the CDN
    for 60-90 s, so a manifest that still says complete=false must not block a
    submit (prod smoke 2026-10-11: complete then submit -> 409 for ~1 min).
    """
    return (row or {}).get("tone") == "ok" or bool((man or {}).get("complete"))


def committed_numbers(parts: list[dict]) -> list[int]:
    return sorted({int(p.get("station_index") or 0) for p in parts if int(p.get("station_index") or 0) > 0})


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

async def _recording_view(session_id: str, row: dict) -> dict:
    rid = row.get("id")
    man = await blobs.read_json(manifest_path(session_id, rid)) or {}
    parts = await repo.parts(session_id, rid)
    done = committed_numbers(parts)
    total = int(man.get("parts_total") or 0)
    return {
        "id": rid,
        "name": row.get("name"),
        "position": row.get("position") or 0,
        "scope": man.get("scope") or "floor",
        "floor_index": man.get("floor_index") or 0,
        "duration_s": man.get("duration_s") or row.get("frames") or 0,
        "size_bytes": man.get("size_bytes") or 0,
        "part_size": man.get("part_size") or PART_SIZE,
        "parts_total": total,
        "parts_committed": len(done),
        "complete": recording_complete(row, man),
        "rooms": man.get("rooms") or [],
    }


def _limits() -> dict:
    return {"max_bytes": MAX_BYTES, "min_bytes": MIN_BYTES, "max_seconds": MAX_SECONDS,
            "max_recordings": MAX_RECORDINGS, "sign_batch": SIGN_BATCH}


async def _bundle(job: dict, session: Optional[dict]) -> dict:
    if not session:
        return {"session_id": None, "status": None, "job_id": job.get("id"), "address": job.get("address") or "",
                "recordings": [], "pipeline": [], "limits": _limits()}
    sid = session["id"]
    rows = await repo.recordings(sid)
    recs = [await _recording_view(sid, r) for r in rows]
    pipe = await repo.pipeline(sid)
    return {
        "session_id": sid,
        "status": session.get("status"),
        "job_id": job.get("id"),
        "address": job.get("address") or "",
        "submitted_at": session.get("submitted_at"),
        "recordings": recs,
        "pipeline": [{"title": p.get("title"), "detail": p.get("detail"), "status": p.get("status")} for p in pipe],
        "limits": _limits(),
    }


async def _open_session(job_id: str) -> dict:
    s = await repo.in_progress_session(job_id)
    if not s:
        api_error(409, "No Visit recording in progress for this job", "session_not_found")
    return s


async def _own_recording(session: dict, recording_id: str) -> dict:
    for r in await repo.recordings(session["id"]):
        if r.get("id") == recording_id:
            return r
    api_error(404, "Recording not found in this session", "recording_not_found")
    return {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/sessions")
async def get_visit_session(
    job_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    job = await _job(authorization, x_user_id, job_id)
    return await _bundle(job, await repo.latest_session(job_id))


@router.post("/sessions")
async def ensure_visit_session(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    job_id = (payload or {}).get("job_id") or ""
    job = await _job(authorization, x_user_id, job_id)
    session = await repo.in_progress_session(job_id)
    if not session:
        try:
            session = await repo.create_session(job_id)
        except HTTPException:
            raise
        except Exception as e:
            if _kind_constraint_missing(e):
                api_error(503, "Visit recording needs the capture_sessions 'visit' kind migration applied.",
                          "capture_visit_kind_missing")
            raise
    return await _bundle(job, session)


@router.post("/recordings")
async def create_recording(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    job_id = (payload or {}).get("job_id") or ""
    await _job(authorization, x_user_id, job_id)
    fields = validate_recording(payload)
    session = await _open_session(job_id)
    sid = session["id"]
    rows = await repo.recordings(sid)
    if len(rows) >= MAX_RECORDINGS:
        api_error(409, f"A Visit takes at most {MAX_RECORDINGS} recordings", "visit_too_many_recordings")
    fallback = f"Floor {fields['floor_index'] + 1}" if fields["scope"] == "floor" else f"Room {len(rows) + 1}"
    name = clean_name((payload or {}).get("name"), fallback)
    if any((r.get("name") or "").lower() == name.lower() for r in rows):
        api_error(409, f"There is already a recording called {name}", "visit_duplicate_name")
    position = (max(int(r.get("position") or 0) for r in rows) + 1) if rows else 0
    row = await repo.insert_recording(sid, job_id, name, position, int(round(fields["duration_s"])))
    rid = row.get("id")
    if not rid:
        api_error(500, "Could not create the recording", "recording_create_failed")
    # Probe signing once: the Blob proxy fallback has a smaller body cap.
    probe = await blobs.sign_put(part_path(sid, rid, 1))
    if not probe or not probe.get("signed_url"):
        api_error(503, "Could not mint upload URLs right now", "signed_upload_unavailable")
    part_size = PART_SIZE if probe.get("token") else PART_SIZE_PROXY
    parts_total = max(1, math.ceil(fields["size_bytes"] / part_size))
    manifest = {**fields, "version": 1, "session_id": sid, "recording_id": rid, "job_id": job_id, "name": name,
                "part_size": part_size, "parts_total": parts_total, "complete": False, "created_at": _now()}
    if not await blobs.write_json(manifest_path(sid, rid), manifest):
        api_error(502, "Could not save the recording manifest", "manifest_write_failed")
    return {"ok": True, "session_id": sid, "recording_id": rid, "name": name, "part_size": part_size,
            "parts_total": parts_total, "committed": [], "sign_batch": SIGN_BATCH}


@router.get("/recordings/{recording_id}")
async def recording_status(
    recording_id: str,
    job_id: str = Query(...),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Which parts the server already has, so the phone resumes where it stopped."""
    await _job(authorization, x_user_id, job_id)
    session = await repo.latest_session(job_id)
    if not session:
        api_error(404, "Recording not found", "recording_not_found")
    row = await _own_recording(session, recording_id)
    man = await blobs.read_json(manifest_path(session["id"], recording_id)) or {}
    done = committed_numbers(await repo.parts(session["id"], recording_id))
    return {"ok": True, "session_id": session["id"], "recording_id": recording_id, "name": row.get("name"),
            "part_size": man.get("part_size") or PART_SIZE, "parts_total": man.get("parts_total") or 0,
            "committed": done, "complete": recording_complete(row, man)}


@router.post("/recordings/{recording_id}/sign")
async def sign_parts(
    recording_id: str,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    job_id = (payload or {}).get("job_id") or ""
    await _job(authorization, x_user_id, job_id)
    session = await _open_session(job_id)
    await _own_recording(session, recording_id)
    man = await blobs.read_json(manifest_path(session["id"], recording_id)) or {}
    total = int(man.get("parts_total") or 0)
    raw = (payload or {}).get("parts") or []
    if not isinstance(raw, list) or not raw or len(raw) > SIGN_BATCH:
        api_error(400, f"parts must list 1–{SIGN_BATCH} part numbers", "bad_parts")
    out = []
    for n in raw:
        try:
            n = int(n)
        except (TypeError, ValueError):
            api_error(400, "part numbers must be integers", "bad_parts")
        if n < 1 or n > total:
            api_error(400, f"part {n} is outside 1–{total}", "bad_parts")
        path = part_path(session["id"], recording_id, n)
        signed = await blobs.sign_put(path)
        if not signed or not signed.get("signed_url"):
            api_error(503, "Could not mint upload URLs right now", "signed_upload_unavailable")
        out.append({"part": n, "path": path, "signed_url": signed.get("signed_url"), "token": signed.get("token")})
    return {"ok": True, "bucket": STORAGE_BUCKET, "parts": out}


@router.post("/recordings/{recording_id}/commit")
async def commit_part(
    recording_id: str,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    job_id = (payload or {}).get("job_id") or ""
    await _job(authorization, x_user_id, job_id)
    session = await _open_session(job_id)
    await _own_recording(session, recording_id)
    man = await blobs.read_json(manifest_path(session["id"], recording_id)) or {}
    total = int(man.get("parts_total") or 0)
    part_size = int(man.get("part_size") or PART_SIZE)
    try:
        n = int((payload or {}).get("part"))
        size = int((payload or {}).get("size") or 0)
    except (TypeError, ValueError):
        api_error(400, "part and size must be integers", "bad_parts")
    if n < 1 or n > total:
        api_error(400, f"part {n} is outside 1–{total}", "bad_parts")
    expected = part_size if n < total else int(man.get("size_bytes") or 0) - part_size * (total - 1)
    if size != expected:
        api_error(400, f"part {n} should be {expected} bytes, got {size}", "bad_part_size")
    existing = await repo.parts(session["id"], recording_id)
    done = committed_numbers(existing)
    if n not in done:
        await repo.insert_part({
            "session_id": session["id"], "room_id": recording_id, "job_id": job_id,
            "storage_path": part_path(session["id"], recording_id, n), "station_index": n,
            "role": PART_ROLE, "content_type": "application/octet-stream", "created_at": _now(),
        })
        done = sorted(done + [n])
    return {"ok": True, "part": n, "committed_count": len(done), "parts_total": total}


@router.post("/recordings/{recording_id}/complete")
async def complete_recording(
    recording_id: str,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    job_id = (payload or {}).get("job_id") or ""
    await _job(authorization, x_user_id, job_id)
    session = await _open_session(job_id)
    sid = session["id"]
    await _own_recording(session, recording_id)
    man = await blobs.read_json(manifest_path(sid, recording_id))
    if not man:
        api_error(404, "Recording manifest missing", "manifest_missing")
    total = int(man.get("parts_total") or 0)
    done = committed_numbers(await repo.parts(sid, recording_id))
    missing = [n for n in range(1, total + 1) if n not in done]
    if missing:
        api_error(409, f"{len(missing)} part(s) still uploading", "visit_parts_missing")
    if not man.get("complete"):
        man = {**man, "complete": True, "completed_at": _now()}
        if not await blobs.write_json(manifest_path(sid, recording_id), man):
            api_error(502, "Could not save the recording manifest", "manifest_write_failed")
    await repo.update_recording(recording_id, {"tone": "ok"})
    rows = await repo.recordings(sid)
    n_done = 0
    for r in rows:
        if r.get("id") == recording_id or r.get("tone") == "ok":
            n_done += 1
    await repo.update_pipeline(sid, "Recorded", "active", f"{n_done} recording{'s' if n_done != 1 else ''} uploaded")
    return {"ok": True, "recording_id": recording_id, "complete": True}


@router.delete("/recordings/{recording_id}")
async def discard_recording(
    recording_id: str,
    job_id: str = Query(...),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Re-record: drop the row, its part rows, and its stored manifest + part objects."""
    await _job(authorization, x_user_id, job_id)
    session = await _open_session(job_id)
    sid = session["id"]
    await _own_recording(session, recording_id)
    # Collect every object this recording may own before the rows go: committed
    # parts (from capture_frames), every part number the manifest planned (a part
    # can be uploaded but never committed), and the manifest itself.
    paths = {manifest_path(sid, recording_id)}
    for p in await repo.parts(sid, recording_id):
        if p.get("storage_path"):
            paths.add(p["storage_path"])
    man = await blobs.read_json(manifest_path(sid, recording_id)) or {}
    try:
        planned = int(man.get("parts_total") or 0)
    except (TypeError, ValueError):
        planned = 0
    for n in range(1, min(planned, 10_000) + 1):
        paths.add(part_path(sid, recording_id, n))
    await repo.delete_recording(sid, recording_id)
    removed = False
    try:
        removed = await blobs.remove(sorted(paths))
    except Exception:
        logger.exception("Visit recording %s: storage cleanup failed", recording_id)
    return {"ok": True, "recording_id": recording_id, "deleted": True, "files_removed": bool(removed)}


def build_job_spec(session: dict, job_id: str, recs: list[dict]) -> dict:
    """What the GPU worker needs: ordered parts per recording plus room marks (frames.py --rooms)."""
    out = []
    for r in recs:
        out.append({
            "recording_id": r["recording_id"],
            "name": r["name"],
            "scope": r.get("scope") or "floor",
            "floor_index": r.get("floor_index") or 0,
            "duration_s": r.get("duration_s"),
            "size_bytes": r.get("size_bytes"),
            "content_type": r.get("content_type") or "video/mp4",
            "parts": [part_path(session["id"], r["recording_id"], n) for n in range(1, int(r["parts_total"]) + 1)],
            "rooms": r.get("rooms") or ([{"name": r["name"], "t0": 0.0, "t1": r.get("duration_s")}]
                                         if (r.get("scope") == "room") else []),
        })
    return {"version": 1, "kind": KIND, "job_id": job_id, "session_id": session["id"],
            "submitted_at": _now(), "recordings": out,
            "deliver": {"sign": "/api/capture/visit/sign", "deliver": "/api/capture/visit/deliver"}}


@router.post("/submit")
async def submit_visit(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """All recordings uploaded → session submitted, job.json written for the Visit worker queue."""
    job_id = (payload or {}).get("job_id") or ""
    job = await _job(authorization, x_user_id, job_id)
    session = await _open_session(job_id)
    sid = session["id"]
    rows = await repo.recordings(sid)
    if not rows:
        api_error(409, "Record at least one floor before sending", "visit_no_recordings")
    mans = []
    for r in rows:
        man = await blobs.read_json(manifest_path(sid, r["id"])) or {}
        if not recording_complete(r, man):
            api_error(409, f"{r.get('name') or 'A recording'} is still uploading", "visit_parts_missing")
        if not man.get("parts_total"):
            # Row says done but the manifest is unreadable: can't build the worker spec.
            api_error(502, f"{r.get('name') or 'A recording'} manifest is missing", "manifest_missing")
        mans.append({**man, "complete": True, "recording_id": r["id"], "name": r.get("name")})
    spec = build_job_spec(session, job_id, mans)
    if not await blobs.write_json(job_spec_path(sid), spec):
        api_error(502, "Could not queue the Visit for processing", "visit_queue_failed")
    total_parts = sum(int(m.get("parts_total") or 0) for m in mans)
    now = _now()
    await repo.update_session(sid, {"status": "submitted", "submitted_at": now, "frame_count": total_parts,
                                    "output_storage_path": job_spec_path(sid)})
    await repo.update_pipeline(sid, "Recorded", "done", f"{len(mans)} recording{'s' if len(mans) != 1 else ''}")
    await repo.update_pipeline(sid, "Reconstructing", "active", "Queued for the Visit worker")
    session = {**session, "status": "submitted", "submitted_at": now}
    return {**(await _bundle(job, session)), "ok": True, "queued": job_spec_path(sid)}


@router.get("/sessions/{session_id}/source")
async def worker_source(
    session_id: str,
    job_id: str = Query(...),
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Internal (GPU worker launcher): signed GETs for every part, in order, plus room marks."""
    if not verify_internal_api_key(x_internal_api_key):
        api_error(403, "Internal API key required", "forbidden")
    session = await repo.session_by_id(session_id, job_id)
    if not session or session.get("kind") != KIND:
        api_error(404, "Visit session not found", "session_not_found")
    spec = await blobs.read_json(job_spec_path(session_id))
    if not spec:
        api_error(409, "Visit session has not been submitted", "visit_not_submitted")
    out = []
    for r in spec.get("recordings") or []:
        urls = []
        for p in r.get("parts") or []:
            u = await blobs.sign_get(p)
            if not u:
                api_error(502, "Could not sign a part for the worker", "visit_sign_failed")
            urls.append(u)
        out.append({**{k: v for k, v in r.items() if k != "parts"}, "part_urls": urls})
    return {"ok": True, "job_id": job_id, "session_id": session_id, "recordings": out}
