"""On-disk layout for Matterport-style 360 stations."""
from __future__ import annotations

import json
from typing import Optional

ROLES = ("pan", "equirect", "usdz", "manifest", "roomplan")
CONTENT_TYPES = {
    "pan": "image/jpeg",
    "equirect": "image/jpeg",
    "usdz": "model/vnd.usdz+zip",
    "manifest": "application/json",
    "roomplan": "application/json",
}


def station_dir(session_id: str, station_index: int) -> str:
    return f"capture/{session_id}/stations/{int(station_index)}"


def pan_path(session_id: str, station_index: int, k: int) -> str:
    return f"{station_dir(session_id, station_index)}/pan/{int(k)}.jpg"


def equirect_path(session_id: str, station_index: int) -> str:
    return f"{station_dir(session_id, station_index)}/equirect.jpg"


def station_manifest_path(session_id: str, station_index: int) -> str:
    return f"{station_dir(session_id, station_index)}/station.json"


def session_manifest_path(session_id: str) -> str:
    return f"capture/{session_id}/manifest.json"


def roomplan_json_path(session_id: str) -> str:
    return f"capture/{session_id}/roomplan.json"


def roomplan_usdz_path(session_id: str) -> str:
    return f"capture/{session_id}/roomplan.usdz"


def parse_station_index_from_path(storage_path: str) -> Optional[int]:
    parts = [p for p in str(storage_path or "").split("/") if p]
    # capture/{sid}/stations/{n}/...
    try:
        i = parts.index("stations")
        return int(parts[i + 1])
    except (ValueError, IndexError):
        return None


def role_from_path(storage_path: str) -> str:
    p = str(storage_path or "")
    if p.endswith("equirect.jpg"):
        return "equirect"
    if "/pan/" in p:
        return "pan"
    if p.endswith("station.json") or p.endswith("manifest.json"):
        return "manifest"
    if p.endswith(".usdz"):
        return "usdz"
    if p.endswith("roomplan.json"):
        return "roomplan"
    return "pan"


def dump_station_manifest(meta: dict) -> bytes:
    return json.dumps(meta, indent=2).encode("utf-8")


MIN_PANS_FOR_SUBMIT = 8


def stations_have_compile_input(stations: list[dict], *, kind: str, has_roomplan: bool) -> tuple[bool, str]:
    """Submit gate. Refuses a walkthrough compiled from casual stills.

    Floor plan is derived from aligned stations (or RoomPlan). Essentials
    jobs capture 4 room corners, not a 360 dome — do not demand equirects.
    """
    if kind == "floorplan":
        if has_roomplan:
            return True, "roomplan"
        if stations:
            return True, "stations"
        return False, "no_stations"
    if not stations:
        return False, "no_stations"
    for st in stations:
        if st.get("equirect_path") or st.get("equirect_bytes"):
            return True, "equirect"
        pans = st.get("pans") or []
        if any(p.get("bytes") for p in pans):
            from .equirect_stitcher import station_is_stitchable
            if station_is_stitchable(pans):
                return True, "pan_burst"
        if len(pans) >= MIN_PANS_FOR_SUBMIT:
            return True, "pan_burst"
    return False, "no_equirects"

def select_rebuild_stations(stations: list[dict]) -> list[dict]:
    """Stations that already have a stored equirect. Pans-only rows are skipped."""
    out = []
    for st in stations or []:
        if st.get("equirect_bytes") or st.get("equirect_path") or st.get("file"):
            out.append(st)
    return out


def stations_have_stored_equirects(stations: list[dict]) -> tuple[bool, str]:
    """Rebuild gate: needs at least one stored 360. Never treats pans as enough."""
    if not stations:
        return False, "no_stations"
    ready = select_rebuild_stations(stations)
    if not ready:
        return False, "no_equirects"
    return True, "equirect"
