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
    select_rebuild_stations,
    session_manifest_path,
    session_tags_path,
    station_manifest_path,
    stations_have_compile_input,
    stations_have_stored_equirects,
)
from .tour_builder import TourBuildError, build_tour_html, build_tour_json, normalize_tag, normalize_tags
from .equirect_stitcher import (
    StitchError,
    is_equirect_image,
    station_is_stitchable,
    stitch_pan_to_equirect,
)
from .floorplan_tracer import FloorplanError, derive_floorplan, infer_station_poses, parse_roomplan_json
from .storage import download_bytes, get_signed_download_url, upload_bytes

logger = logging.getLogger(__name__)


class CaptureWorkerError(RuntimeError):
    pass


# Live OS API host (bricks-os-api domains: api.brxs.ca + brokerage-api.brxs.ca).
_DEFAULT_OS_API_URL = "https://api.brxs.ca"


def public_tour_embed_url(job_id: str, session_id: str) -> Optional[str]:
    """Public tour iframe URL on the OS API. Defaults when BRICKS_OS_API_URL is unset."""
    os_url = (os.environ.get("BRICKS_OS_API_URL") or "").strip().rstrip("/")
    if not os_url:
        os_url = _DEFAULT_OS_API_URL
    if not job_id or not session_id:
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
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error
            from ..shared.pg_capture import pg_select_rooms
            if _is_rest_quota_error(e):
                rooms = pg_select_rooms(session_id)
            else:
                rooms = []
    for r in rooms or []:
        room_name[r.get("id")] = r.get("name") or "Room"

    try:
        frames_res = await db.table("capture_frames").select(
            "id, room_id, storage_path, created_at"
        ).eq("session_id", session_id).order("created_at", desc=False).execute()
        frames = _data(frames_res)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error
        from ..shared.pg_capture import pg_select_frames
        if not _is_rest_quota_error(e):
            raise
        frames = pg_select_frames(session_id)

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
            st["tags"] = normalize_tags(meta.get("tags"), station_index=idx)
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
        signed = await get_signed_download_url(storage_path, expiry=60 * 60 * 24 * 7)
        existing = await db.table("media_files").select("id").eq("storage_path", storage_path).limit(1).execute()
        if existing.data:
            row_id = existing.data[0].get("id")
            if row_id and signed:
                await db.table("media_files").update({
                    "file_url": signed,
                    "file_name": f"floorplan-{session_id}.png",
                    "label": "Floor plan",
                }).eq("id", row_id).execute()
            return
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



async def load_session_tags(session_id: str) -> list[dict] | None:
    """Session-level amenity tags. None = no tags.json yet (keep station tags)."""
    raw = await download_bytes(session_tags_path(session_id))
    if not raw:
        return None
    try:
        meta = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    by_id = {}
    for t in normalize_tags(meta.get("tags") if isinstance(meta, dict) else meta):
        by_id[t["id"]] = t
    return list(by_id.values())


async def save_session_tags(session_id: str, tags: list[dict]) -> list[dict]:
    clean = normalize_tags(tags)
    payload = json.dumps({"version": 1, "tags": clean}, indent=2).encode("utf-8")
    ok = await upload_bytes(session_tags_path(session_id), payload, "application/json", upsert=True)
    if not ok:
        raise CaptureWorkerError("Could not store amenity tags")
    grouped: dict[int, list] = {}
    for t in clean:
        idx = t.get("station_index")
        if idx is None:
            continue
        grouped.setdefault(int(idx), []).append(t)
    for idx, group in grouped.items():
        man_path = station_manifest_path(session_id, idx)
        raw = await download_bytes(man_path)
        meta = {}
        if raw:
            try:
                meta = json.loads(raw.decode("utf-8"))
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["station_index"] = idx
        meta["tags"] = group
        await upload_bytes(man_path, json.dumps(meta).encode("utf-8"), "application/json", upsert=True)
    return clean


def attach_tags_to_stations(stations: list[dict], tags: list[dict] | None) -> list[dict]:
    """Overlay tags.json onto compiled stations. None keeps per-station tags; [] clears."""
    if tags is None:
        out = []
        for st in stations:
            row = dict(st)
            idx = int(row.get("station_index") if row.get("station_index") is not None else 0)
            row["tags"] = normalize_tags(row.get("tags"), station_index=idx)
            out.append(row)
        return out
    by_idx: dict[int, list] = {}
    for t in normalize_tags(tags):
        idx = t.get("station_index")
        if idx is None:
            continue
        by_idx.setdefault(int(idx), []).append(t)
    out = []
    for st in stations:
        row = dict(st)
        idx = int(row.get("station_index") if row.get("station_index") is not None else 0)
        row["tags"] = by_idx.get(idx, [])
        out.append(row)
    return out

def _compiled_station(st: dict, jpeg: bytes | None = None) -> dict:
    idx = int(st.get("station_index") if st.get("station_index") is not None else 0)
    scene_rel = st.get("file") or st.get("panorama") or f"scenes/{idx}.jpg"
    if "/" not in str(scene_rel):
        scene_rel = f"scenes/{scene_rel}"
    if not str(scene_rel).startswith("scenes/"):
        # hosted tour.json already uses scenes/n.jpg
        pass
    return {
        "id": st.get("id") or f"station-{idx}",
        "station_index": idx,
        "room": st.get("room") or st.get("title") or "Room",
        "label": st.get("label") or st.get("title") or st.get("room") or f"Station {idx}",
        "heading": st.get("heading"),
        "file": scene_rel,
        "bytes": jpeg if jpeg is not None else st.get("bytes") or st.get("equirect_bytes"),
        "tags": normalize_tags(st.get("tags"), station_index=idx),
    }


def rebuild_tour_payload(stations, *, address="", title="3D tour") -> tuple[str, str]:
    """Rewrite tour HTML/JSON from already-compiled station dicts. Never stitches."""
    ready = select_rebuild_stations(stations)
    if not ready:
        raise CaptureWorkerError(
            "Rebuild needs stored equirects — recapture stations or retry compile from pans"
        )
    compiled = [_compiled_station(st) for st in ready]
    try:
        html = build_tour_html(compiled, address=address or "", title=title or "3D tour")
        meta = build_tour_json(compiled)
    except TourBuildError as e:
        raise CaptureWorkerError(str(e)) from e
    return html, meta


async def _publish_tour_player(
    job: dict,
    session: dict,
    compiled: list[dict],
    *,
    upload_scenes: bool,
) -> str:
    job_id = job["id"]
    sid = session["id"]
    if not compiled:
        raise CaptureWorkerError("No equirect stations to publish")
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
    if upload_scenes:
        for c in compiled:
            raw = c.get("bytes")
            if not raw:
                continue
            await upload_bytes(f"{prefix}/{c['file']}", raw, "image/jpeg", upsert=True)
    # Listing CaptureCard reads capture_sessions + /api/public/tours/{job}/{session}.
    # Do not register phone tours into property_visits — that table is Virtual Visit
    # only (Polycam URL / .ply/.splat). Mixing them made VisitViewer show tours as visits.
    _ = public_tour_embed_url(job_id, sid)
    return index_path


async def load_existing_equirect(session_id: str, station: dict, *, job_id: str | None = None) -> bytes | None:
    """Return stored equirect JPEG bytes. Never stitches pans."""
    if station.get("equirect_bytes"):
        raw = station["equirect_bytes"]
        if raw and is_equirect_image(raw):
            return raw
    path = station.get("equirect_path")
    if path:
        raw = await download_bytes(path)
        if raw and is_equirect_image(raw):
            station["equirect_bytes"] = raw
            return raw
    if job_id is not None:
        idx = station.get("station_index")
        file = station.get("file") or station.get("panorama") or (f"scenes/{idx}.jpg" if idx is not None else None)
        if file:
            raw = await download_bytes(f"tours/{job_id}/{session_id}/{file}")
            if raw and is_equirect_image(raw):
                station["equirect_bytes"] = raw
                return raw
    return None


async def _load_hosted_tour_scenes(job_id: str, session_id: str) -> list[dict]:
    """Rebuild source: already-hosted tour.json + scene JPEGs."""
    raw = await download_bytes(f"tours/{job_id}/{session_id}/tour.json")
    if not raw:
        return []
    try:
        meta = json.loads(raw.decode("utf-8"))
    except Exception:
        return []
    scenes = meta.get("scenes") or []
    out = []
    for i, sc in enumerate(scenes):
        file = sc.get("panorama") or sc.get("file") or f"scenes/{i}.jpg"
        jpeg = await download_bytes(f"tours/{job_id}/{session_id}/{file}")
        if jpeg and not is_equirect_image(jpeg):
            jpeg = None
        idx = sc.get("station_index", i)
        out.append({
            "id": sc.get("id") or f"station-{idx}",
            "station_index": idx,
            "room": sc.get("room") or sc.get("title") or "Room",
            "label": sc.get("title") or sc.get("room") or f"Station {idx}",
            "heading": sc.get("heading"),
            "file": file,
            "bytes": jpeg if jpeg and is_equirect_image(jpeg) else None,
            "equirect_path": f"tours/{job_id}/{session_id}/{file}",
            "tags": normalize_tags(sc.get("tags"), station_index=idx),
        })
    return out


async def compile_tour(db, job: dict, session: dict, stations: list[dict]) -> str:
    compiled = []
    sid = session["id"]
    for st in stations:
        jpeg = await ensure_station_equirect(sid, st)
        compiled.append(_compiled_station(st, jpeg))
    if not compiled:
        raise CaptureWorkerError("No equirect stations after stitch")
    compiled = attach_tags_to_stations(compiled, await load_session_tags(sid))
    return await _publish_tour_player(job, session, compiled, upload_scenes=True)


async def rebuild_tour_from_equirects(db, job: dict, session: dict) -> str:
    """Refresh hosted tour HTML/JSON from stored 360s. Does not recapture or stitch."""
    sid = session["id"]
    job_id = job["id"]
    compiled = await _load_hosted_tour_scenes(job_id, sid)
    if not compiled:
        stations = await load_station_records(db, sid)
        ok, reason = stations_have_stored_equirects(stations)
        if not ok:
            raise CaptureWorkerError(
                "Rebuild needs stored equirects — recapture stations or retry compile from pans"
                if reason != "no_stations"
                else "Rebuild needs at least one stored 360 station"
            )
        compiled = []
        for st in select_rebuild_stations(stations):
            jpeg = await load_existing_equirect(sid, st, job_id=job_id)
            if not jpeg:
                continue
            compiled.append(_compiled_station(st, jpeg))
        if not compiled:
            raise CaptureWorkerError(
                "Rebuild needs stored equirects — recapture stations or retry compile from pans"
            )
        compiled = attach_tags_to_stations(compiled, await load_session_tags(sid))
        return await _publish_tour_player(job, session, compiled, upload_scenes=True)
    # Hosted scenes already exist — rewrite player HTML/JSON only.
    compiled = attach_tags_to_stations(
        [_compiled_station(st) for st in compiled],
        await load_session_tags(sid),
    )
    return await _publish_tour_player(job, session, compiled, upload_scenes=False)


async def _upload_floorplan_png(job: dict, session: dict, png: bytes) -> str:
    """Write PNG to the delivered path when present, else the standard visit path."""
    sid = session["id"]
    job_id = job["id"]
    candidates = []
    existing = (session.get("output_storage_path") or "").strip()
    if existing and existing.endswith(".png"):
        candidates.append(existing)
    candidates.append(f"visits/{job_id}/floorplan-{sid}.png")
    candidates.append(f"capture/{sid}/plan.png")
    for path in candidates:
        if await upload_bytes(path, png, "image/png", upsert=True):
            return path
    raise CaptureWorkerError("Could not upload floor-plan PNG")


async def compile_floorplan(db, job: dict, session: dict, stations: list[dict]) -> str:
    sid = session["id"]
    roomplan = await load_roomplan(sid)
    # Best-effort stitch. Plan still compiles from station poses when corners are not a full 360.
    for st in stations:
        try:
            await ensure_station_equirect(sid, st)
        except CaptureWorkerError as e:
            logger.warning("station %s stitch skipped for plan: %s", st.get("station_index"), e)
    stations = infer_station_poses(stations)
    try:
        png = derive_floorplan(stations, address=(job.get("address") or "").strip(), roomplan=roomplan)
    except FloorplanError as e:
        raise CaptureWorkerError(str(e)) from e
    path = await _upload_floorplan_png(job, session, png)
    await _attach_floorplan_media(db, job, path, sid)
    return path


async def rebuild_floorplan_from_stations(db, job: dict, session: dict) -> str:
    """Regenerate floor-plan PNG from stored stations/RoomPlan. No recapture, no stitch."""
    sid = session["id"]
    stations = await load_station_records(db, sid)
    roomplan = await load_roomplan(sid)
    ok, reason = stations_have_compile_input(stations, kind="floorplan", has_roomplan=bool(roomplan))
    if not ok:
        raise CaptureWorkerError(
            "Rebuild needs stored stations or RoomPlan — recapture corners first"
            if reason == "no_stations"
            else "Rebuild needs stored station poses or RoomPlan"
        )
    stations = infer_station_poses(stations)
    try:
        png = derive_floorplan(stations, address=(job.get("address") or "").strip(), roomplan=roomplan)
    except FloorplanError as e:
        raise CaptureWorkerError(str(e)) from e
    path = await _upload_floorplan_png(job, session, png)
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
