"""Compile 360 stations into a hosted tour and/or a 2D plan.

Submit refuses when there are no stations / no equirects. Pan bursts are
stitched on this server. Failures raise CaptureWorkerError — never Delivered.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .capture_layout import (
    equirect_path,
    parse_station_index_from_path,
    role_from_path,
    roomplan_json_path,
    session_manifest_path,
    station_manifest_path,
    stations_have_compile_input,
)
from .equirect_stitcher import (
    StitchError,
    is_equirect_image,
    station_is_stitchable,
    stitch_pan_to_equirect,
)
from .floorplan_tracer import FloorplanError, derive_floorplan, parse_roomplan_json
from .storage import download_bytes, get_signed_download_url, upload_bytes
from .tour_builder import TourBuildError, build_tour_html, build_tour_json

logger = logging.getLogger(__name__)


class CaptureWorkerError(RuntimeError):
    pass


def public_tour_embed_url(job_id: str, session_id: str) -> Optional[str]:
    os_url = (os.environ.get("BRICKS_OS_API_URL") or "").rstrip("/")
    if not os_url or not job_id or not session_id:
        return None
    return f"{os_url}/api/public/tours/{job_id}/{session_id}"


def _data(res) -> list:
    data = getattr(res, "data", None)
    if data is None and isinstance(res, dict):
        data = res.get("data")
    return list(data or [])


async def load_station_records(db, session_id: str, rooms: list[dict] | None = None) -> list[dict]:
    """Build station dicts from capture_frames (+ optional capture_stations rows)."""
    room_name = {}
    if rooms is None:
        try:
            rooms_res = await db.table("capture_rooms").select("id, name, position").eq(
                "session_id", session_id
            ).order("position", desc=False).execute()
            rooms = _data(rooms_res)
        except Exception:
            rooms = []
    for r in rooms or []:
        room_name[r.get("id")] = r.get("name") or "Room"

    frames_res = await db.table("capture_frames").select(
        "id, room_id, storage_path, created_at"
    ).eq("session_id", session_id).order("created_at", desc=False).execute()
    frames = _data(frames_res)

    stations: dict[int, dict] = {}
    extras = []
    for fr in frames:
        path = fr.get("storage_path") or ""
        idx = parse_station_index_from_path(path)
        role = role_from_path(path)
        if idx is None:
            extras.append(fr)
            continue
        st = stations.setdefault(idx, {
            "station_index": idx,
            "room_id": fr.get("room_id"),
            "room": room_name.get(fr.get("room_id")) or "Room",
            "pans": [],
            "equirect_path": None,
            "equirect_bytes": None,
            "heading": None,
            "pose_x": None,
            "pose_y": None,
            "yaws": [],
        })
        if role == "equirect":
            st["equirect_path"] = path
        elif role == "pan":
            st["pans"].append({"storage_path": path, "bytes": None, "yaw": None})
        elif role == "manifest":
            st["manifest_path"] = path

    # Legacy frames without stations/ in the path cannot compile a 360 tour.
    for idx, st in stations.items():
        man_path = st.get("manifest_path") or station_manifest_path(session_id, idx)
        raw = await download_bytes(man_path)
        if raw:
            try:
                meta = json.loads(raw.decode("utf-8"))
            except Exception:
                meta = {}
            if meta.get("room"):
                st["room"] = meta["room"]
            st["heading"] = meta.get("heading")
            st["pose_x"] = meta.get("pose_x")
            st["pose_y"] = meta.get("pose_y")
            yaw_list = meta.get("yaws") or []
            for i, pan in enumerate(st["pans"]):
                if i < len(yaw_list):
                    pan["yaw"] = yaw_list[i]
                elif isinstance(yaw_list, list) and i < len(st["pans"]):
                    pass
            st["yaws"] = yaw_list
    return [stations[k] for k in sorted(stations)]


async def _download_pans(station: dict) -> None:
    for pan in station["pans"]:
        if pan.get("bytes"):
            continue
        pan["bytes"] = await download_bytes(pan["storage_path"])


async def ensure_station_equirect(session_id: str, station: dict) -> bytes:
    """Return equirect JPEG bytes, stitching the pan burst if needed."""
    if station.get("equirect_path"):
        raw = await download_bytes(station["equirect_path"])
        if raw and is_equirect_image(raw):
            station["equirect_bytes"] = raw
            return raw
        if raw:
            raise CaptureWorkerError(
                f"Station {station['station_index']} file is not a 2:1 equirect JPEG"
            )
    await _download_pans(station)
    pans = [p for p in station["pans"] if p.get("bytes")]
    if not station_is_stitchable(pans):
        raise CaptureWorkerError(
            f"Station {station['station_index']} has no 360 equirect and pan burst is not stitchable"
        )
    try:
        jpeg = stitch_pan_to_equirect(pans)
    except StitchError as e:
        raise CaptureWorkerError(
            f"Station {station['station_index']} stitch failed: {e}"
        ) from e
    path = equirect_path(session_id, station["station_index"])
    ok = await upload_bytes(path, jpeg, "image/jpeg", upsert=True)
    if not ok:
        raise CaptureWorkerError("Could not store stitched equirect.jpg")
    station["equirect_path"] = path
    station["equirect_bytes"] = jpeg
    return jpeg


async def load_roomplan(session_id: str) -> Optional[dict]:
    raw = await download_bytes(roomplan_json_path(session_id))
    if not raw:
        return None
    try:
        return parse_roomplan_json(raw.decode("utf-8"))
    except Exception:
        return None


async def _attach_floorplan_media(db, job: dict, storage_path: str, session_id: str) -> None:
    booking_id = job.get("booking_id")
    if not booking_id:
        return
    try:
        existing = await db.table("media_files").select("id").eq("storage_path", storage_path).limit(1).execute()
        if existing.data:
            return
        signed = await get_signed_download_url(storage_path, expiry=60 * 60 * 24 * 7)
        await db.table("media_files").insert({
            "booking_id": booking_id,
            "file_type": "floor_plan",
            "file_name": f"floorplan-{session_id}.png",
            "file_url": signed or storage_path,
            "storage_path": storage_path,
            "label": "Floor plan",
            "ai_provider": "bricks",
        }).execute()
    except Exception as e:
        logger.warning("media_files floor_plan insert skipped: %s", e)


async def compile_tour(db, job: dict, session: dict, stations: list[dict]) -> str:
    job_id = job["id"]
    sid = session["id"]
    compiled = []
    for st in stations:
        jpeg = await ensure_station_equirect(sid, st)
        idx = st["station_index"]
        scene_rel = f"scenes/{idx}.jpg"
        compiled.append({
            "id": f"station-{idx}",
            "station_index": idx,
            "room": st.get("room") or "Room",
            "label": st.get("room") or f"Station {idx}",
            "heading": st.get("heading"),
            "file": scene_rel,
            "bytes": jpeg,
        })
    if not compiled:
        raise CaptureWorkerError("No equirect stations after stitch")
    try:
        html = build_tour_html(compiled, address=(job.get("address") or "").strip(), title="3D tour")
        meta = build_tour_json(compiled)
    except TourBuildError as e:
        raise CaptureWorkerError(str(e)) from e
    prefix = f"tours/{job_id}/{sid}"
    index_path = f"{prefix}/index.html"
    if not await upload_bytes(index_path, html.encode("utf-8"), "text/html; charset=utf-8", upsert=True):
        raise CaptureWorkerError("Could not upload tour index.html")
    await upload_bytes(f"{prefix}/tour.json", meta.encode("utf-8"), "application/json", upsert=True)
    man = {
        "kind": "equirect-walkthrough",
        "stations": [{"index": c["station_index"], "room": c["room"], "file": c["file"]} for c in compiled],
    }
    await upload_bytes(session_manifest_path(sid), json.dumps(man).encode(), "application/json", upsert=True)
    for c in compiled:
        await upload_bytes(f"{prefix}/{c['file']}", c["bytes"], "image/jpeg", upsert=True)
    embed = public_tour_embed_url(job_id, sid)
    try:
        from .client_comms import register_property_visit
        await register_property_visit(
            job_id,
            storage_path=index_path,
            title="3D tour",
            embed_url=embed,
            visit_type="url",
        )
    except Exception as e:
        logger.error("visit-ready for tour session %s failed: %s", sid, e)
    return index_path


async def compile_floorplan(db, job: dict, session: dict, stations: list[dict]) -> str:
    sid = session["id"]
    job_id = job["id"]
    roomplan = await load_roomplan(sid)
    # Stitch so plan is derived from aligned 360s, not stills — also fills equirects.
    for st in stations:
        try:
            await ensure_station_equirect(sid, st)
        except CaptureWorkerError as e:
            if not roomplan:
                raise
            logger.warning("station %s stitch skipped for plan: %s", st.get("station_index"), e)
    try:
        png = derive_floorplan(stations, address=(job.get("address") or "").strip(), roomplan=roomplan)
    except FloorplanError as e:
        raise CaptureWorkerError(str(e)) from e
    path = f"visits/{job_id}/floorplan-{sid}.png"
    if not await upload_bytes(path, png, "image/png", upsert=True):
        path = f"capture/{sid}/plan.png"
        if not await upload_bytes(path, png, "image/png", upsert=True):
            raise CaptureWorkerError("Could not upload floor-plan PNG")
    await _attach_floorplan_media(db, job, path, sid)
    return path


async def process_capture_session(db, job: dict, session: dict, kind: str) -> str:
    sid = session["id"]
    stations = await load_station_records(db, sid)
    roomplan = await load_roomplan(sid) if kind == "floorplan" else None
    ok, reason = stations_have_compile_input(stations, kind=kind, has_roomplan=bool(roomplan))
    if not ok:
        if reason == "no_stations":
            raise CaptureWorkerError("Submit needs at least one 360 station")
        raise CaptureWorkerError("Submit needs stitched equirects (or a stitchable 360 pan) at each station")
    if kind == "floorplan":
        return await compile_floorplan(db, job, session, stations)
    return await compile_tour(db, job, session, stations)
