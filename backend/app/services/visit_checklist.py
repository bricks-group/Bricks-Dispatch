"""One-visit shoot checklist — Arrive → capture modes → Submit. No return visits."""
from __future__ import annotations

import uuid
from typing import Any

from .capture_entitlements import job_capture_entitlements


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return getattr(obj, "__dict__", {}) or {}


def _addon_blob(job: dict) -> str:
    parts = []
    for item in job.get("add_ons") or []:
        if isinstance(item, dict):
            if item.get("included") is False or item.get("enabled") is False:
                continue
            parts.append(str(item.get("key") or item.get("id") or item.get("name") or ""))
        else:
            parts.append(str(item))
    parts.append(str(job.get("job_type") or ""))
    parts.append(str(job.get("package_name") or ""))
    parts.append(str(job.get("package_id") or ""))
    return " ".join(parts).lower()


_CONDITION = [
    "Room-by-room photos: every wall, floor, window and fixture",
    "Close-ups of any damage or wear",
    "Match the angles from the last report, if there is one",
]
_LISTING = ["Listing photos of every room", "Floor plan capture"]
_LEASING = ["Photos of the space", "Floor plan capture", "Virtual Visit capture"]
_ACQUISITION = ["Aerial and roof", "Facade close-ups", "Interior record", "Virtual Visit capture"]
_WATCH = ["Aerial and roof", "Match the angles from the last visit for the side-by-side"]

# Client services (PM, commercial, investor, REIT) and their plan visits, keyed
# by package_id. Each lists only what that service delivers (gap audit: an
# aerial-only job must not ask for full-property HDR stills).
CLIENT_SERVICE_STEPS: dict[str, list[str]] = {
    "pm_listing_set": _LISTING,
    "pm_listing_video": _LISTING + ["Short vertical walkthrough clips"],
    "pm_condition_report": _CONDITION,
    "pm_turnover": _CONDITION + _LISTING,
    "pm_building_set": ["Exterior photos", "Drone / aerial", "Amenities and common areas"],
    "cre_leasing_s": _LEASING,
    "cre_leasing_m": _LEASING,
    "cre_leasing_l": _LEASING,
    "cre_aerial": ["Drone stills: site, access and context", "Short aerial video"],
    "inv_acquisition": _ACQUISITION,
    "inv_acquisition_mapped": _ACQUISITION + ["Mapping flight for the orthomosaic and 3D site model"],
    # Plan visits. A Rentals credit spent on another PM service arrives with
    # that service's key instead.
    "pm_rentals_5": _CONDITION + _LISTING,
    "pm_rentals_12": _CONDITION + _LISTING,
    "pm_rentals_25": _CONDITION + _LISTING,
    "cre_leasing_desk_s": _LEASING,
    "cre_leasing_desk_m": _LEASING,
    "inv_watch_quarterly": _WATCH,
    "inv_watch_monthly": _WATCH,
}

_GENERIC_STILLS = "hdr / stills (full property)"


def client_service_key(job: Any) -> str | None:
    key = str(_as_dict(job).get("package_id") or "").strip().lower()
    return key if key in CLIENT_SERVICE_STEPS else None


def _client_checklist(key: str) -> list[dict]:
    labels = ["Arrive & confirm property access", *CLIENT_SERVICE_STEPS[key], "Submit / deliver"]
    return [
        {"id": str(uuid.uuid4()), "label": f"{n}. {label}", "completed": False}
        for n, label in enumerate(labels, 1)
    ]


def seed_visit_checklist(job: Any) -> list[dict]:
    """Build a numbered one-visit checklist from package entitlements / add-ons.

    Never labels return visits or twilight-as-second-appointment.
    """
    j = _as_dict(job)
    key = client_service_key(j)
    if key:
        return _client_checklist(key)
    flags = job_capture_entitlements(j)
    blob = _addon_blob(j)
    jt = str(j.get("job_type") or "photo").lower()

    items: list[dict] = []

    def add(label: str) -> None:
        items.append({"id": str(uuid.uuid4()), "label": label, "completed": False})

    add("1. Arrive & confirm property access")
    add("2. HDR / stills (full property)")

    step = 3
    if "drone" in blob or jt == "drone":
        add(f"{step}. Drone / aerial (same visit)")
        step += 1
    if "reel" in blob or "video" in blob or jt in ("reel", "video"):
        add(f"{step}. Motion reel (same visit)")
        step += 1
    if flags.get("tour"):
        add(f"{step}. 3D tour capture (same visit)")
        step += 1
    if flags.get("floorplan"):
        add(f"{step}. Floor plan capture (same visit)")
        step += 1
    if flags.get("visit"):
        add(f"{step}. Virtual Visit deliverable (same visit)")
        step += 1
    if "twilight" in blob or jt == "twilight":
        add(f"{step}. Twilight exteriors (same visit — stay on site)")
        step += 1

    add(f"{step}. Submit / deliver package")
    return items


def ensure_visit_checklist(job: Any) -> list[dict]:
    """Return existing checklist if it already has one-visit structure; else seed."""
    j = _as_dict(job)
    existing = j.get("checklist") or []
    if isinstance(existing, list) and existing:
        labels = " ".join(
            str(i.get("label") if isinstance(i, dict) else i).lower() for i in existing
        )
        # Reject legacy return-visit wording — reseed.
        if "return visit" in labels or "twilight return" in labels or "second appointment" in labels:
            return seed_visit_checklist(j)
        # A client-service job seeded with the generic listing checklist before
        # it had its own: reseed while nothing is ticked yet.
        untouched = not any(isinstance(i, dict) and i.get("completed") for i in existing)
        if client_service_key(j) and _GENERIC_STILLS in labels and untouched:
            return seed_visit_checklist(j)
        if "arrive" in labels or "hdr" in labels or "one visit" in labels or "same visit" in labels:
            # Normalize ids
            out = []
            for item in existing:
                if isinstance(item, dict):
                    row = dict(item)
                    if not row.get("id"):
                        row["id"] = str(uuid.uuid4())
                    out.append(row)
                else:
                    out.append({"id": str(uuid.uuid4()), "label": str(item), "completed": False})
            return out
    return seed_visit_checklist(j)
