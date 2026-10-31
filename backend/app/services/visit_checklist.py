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

# Builder site visits (Site Progress). The generic listing list asked for
# full-property HDR stills and had no aerial step (gap audit, builders 5c).
_SITE_ANGLES = "Aerial stills from the site's fixed angles, matching the last visit"
SITE_STEPS: dict[str, list[str]] = {
    "visual": [_SITE_ANGLES, "Flyover video"],
    "mapped": [
        "Mapping flight on the site's mission, for the orthomosaic and 3D model",
        "Check coverage before you leave: no gaps or blurred passes",
        _SITE_ANGLES,
    ],
}
_SITE_SETUP = "First visit: set ground control points and build the site's flight mission"


def site_checklist_key(job: Any) -> str | None:
    """'visual' or 'mapped' for a builder site visit, else None."""
    try:
        from .commercial_jobs import MAPPED, deliverable_kind
    except Exception:
        return None
    kind = deliverable_kind(_as_dict(job))
    if kind is None:
        return None
    return "mapped" if kind == MAPPED else "visual"


def _site_checklist(job: dict, key: str) -> list[dict]:
    steps = list(SITE_STEPS[key])
    if key == "mapped" and "mapped_setup" in _addon_blob(job):
        steps.insert(0, _SITE_SETUP)
    return _numbered(["Arrive and confirm site access and the flight plan", *steps, "Submit / deliver"])


_GENERIC_STILLS = "hdr / stills (full property)"


def client_service_key(job: Any) -> str | None:
    key = str(_as_dict(job).get("package_id") or "").strip().lower()
    return key if key in CLIENT_SERVICE_STEPS else None


def _client_checklist(key: str) -> list[dict]:
    return _numbered(["Arrive & confirm property access", *CLIENT_SERVICE_STEPS[key], "Submit / deliver"])


# Non-media vendor trades: short proof-of-work lists, not how-to lessons.
# Inspectors and measurers follow their own professional standards, so their
# list is only the report upload and access / lock-up notes.
_ACCESS_LOCKUP = "Confirm access and lock-up notes"
TRADE_STEPS: dict[str, list[str]] = {
    "cleaner": [
        "After photos of each room",
        "Keys left or returned as instructed",
        "Lights off",
        "Doors and windows locked",
        "Garbage out",
        "Note any damage or issues, with a photo",
    ],
    "stager": [
        "Before photos of each room you stage",
        "After photos of each staged room",
        "Inventory of pieces placed: item and room",
    ],
    "destage": [
        "After photos of each room",
        "Inventory check against the install list, with any damage noted",
    ],
    "handyman": [
        "Before photo of each fix",
        "After photo of each fix",
        "Note anything else you found",
    ],
    "inspector": ["Upload your report", _ACCESS_LOCKUP],
    "measurer": ["Upload your report: measurements and plan", _ACCESS_LOCKUP],
}
TRADE_CHECKLIST_TITLE = "Job checklist"
MEDIA_CHECKLIST_TITLE = "Shoot checklist"

_DESTAGE_WORDS = ("destag", "de_stag", "de-stag", "de stag", "pickup", "pick_up", "pick-up", "pick up", "removal")


def _is_destage(j: dict) -> bool:
    blob = " ".join(
        str(j.get(k) or "") for k in ("job_type", "package_id", "package_name")
    ).lower()
    return any(w in blob for w in _DESTAGE_WORDS)


def trade_checklist_key(job: Any) -> str | None:
    """TRADE_STEPS key for a non-media trade job, else None (media keeps its lists)."""
    j = _as_dict(job)
    jt = str(j.get("job_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if jt in ("destage", "destaging", "de_stage", "de_staging", "staging_pickup", "staging_removal"):
        return "destage"
    try:
        from .vendor_profile import job_required_vendor_type
        vtype = job_required_vendor_type(j)
    except Exception:
        vtype = None
    if not vtype or vtype == "photographer" or vtype not in TRADE_STEPS:
        return None
    if vtype == "stager" and _is_destage(j):
        return "destage"
    return vtype


def checklist_title(job: Any) -> str:
    return TRADE_CHECKLIST_TITLE if trade_checklist_key(job) else MEDIA_CHECKLIST_TITLE


def _numbered(labels: list[str]) -> list[dict]:
    return [
        {"id": str(uuid.uuid4()), "label": f"{n}. {label}", "completed": False}
        for n, label in enumerate(labels, 1)
    ]


def seed_visit_checklist(job: Any) -> list[dict]:
    """Build a numbered one-visit checklist from package entitlements / add-ons.

    Never labels return visits or twilight-as-second-appointment.
    """
    j = _as_dict(job)
    trade = trade_checklist_key(j)
    if trade:
        return _numbered(TRADE_STEPS[trade])
    key = client_service_key(j)
    if key:
        return _client_checklist(key)
    site = site_checklist_key(j)
    if site:
        return _site_checklist(j, site)
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
        if (client_service_key(j) or trade_checklist_key(j) or site_checklist_key(j)) and _GENERIC_STILLS in labels and untouched:
            return seed_visit_checklist(j)
        if trade_checklist_key(j):
            # Trade lists have no "arrive" / "hdr" step; keep them (and any ticks).
            return _normalized(existing)
        if "arrive" in labels or "hdr" in labels or "one visit" in labels or "same visit" in labels:
            return _normalized(existing)
    return seed_visit_checklist(j)


def _normalized(existing: list) -> list[dict]:
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


def swap_untouched_checklist(job: Any) -> list[dict] | None:
    """Same rule as job creation, for jobs read back later: a client-service or
    trade job still holding the generic listing checklist gets its own list,
    but only while nothing on it is ticked. Returns None to keep the existing one.
    """
    j = _as_dict(job)
    existing = j.get("checklist") or []
    if not isinstance(existing, list) or not existing:
        return None
    if not (client_service_key(j) or trade_checklist_key(j) or site_checklist_key(j)):
        return None
    labels = " ".join(str(i.get("label") if isinstance(i, dict) else i).lower() for i in existing)
    untouched = not any(isinstance(i, dict) and i.get("completed") for i in existing)
    if _GENERIC_STILLS in labels and untouched:
        fresh = seed_visit_checklist(j)
        job_id = str(j.get("id") or "").strip()
        if job_id:
            # Read-time swap isn't stored, so keep ids stable across reads.
            for n, item in enumerate(fresh, 1):
                item["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bricks-checklist:{job_id}:{n}"))
        return fresh
    return None
