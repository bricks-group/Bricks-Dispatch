"""Which GO in-app capture modes a job actually purchased.

GO capture modes:
  - 3D tour — path-confined walkthrough. API kind stays ``splat``.
  - Floor plan — room-corner tracing frames.
  - Virtual visit — free-roam Gaussian splat add-on. Vendors deliver via
    Polycam URL or .ply/.splat upload in GO (owned GPU cook later).

Professional HDR stills stay on the vendor camera.

Detection uses real ``jobs`` columns: package_id, package_name, job_type,
add_ons (jsonb), checklist, client_instructions, property_details.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Capture API kind=splat is the path-confined 3D tour session. UX copy says
# "3D tour", never "virtual visit". kind=visit is the free-roam Virtual Visit
# add-on (Polycam / .ply / .splat bridge until owned GPU cook).
KIND_TOUR = "splat"
KIND_FLOORPLAN = "floorplan"
KIND_VISIT = "visit"

# Current www packages that include a floor plan (photo + drone standard
# tiers). Photos-only explicitly drops it. Legacy Standard+ also included it.
_PHOTO_PACKAGES_WITH_FLOORPLAN = {
    "essentials",
    "story",
    "showcase",
    "suite",
    "full",
    "standard",
    "premium",
    "luxury",
    "estate",
}

# Path-confined 3D tour packages.
# Legacy Showcase/Full suite; live booking-api ladder Premium+ (Standard is
# photo+drone+floor plan only). Visit/Gaussian is never granted by package id.
_PACKAGES_WITH_TOUR = {
    "showcase",
    "suite",
    "full",
    "premium",
    "luxury",
    "estate",
}

_TOUR_ADDON_KEYS = {
    "tour",
    "virtual_tour",
    "virtualtour",
    "3d_tour",
    "3dtour",
    "path_tour",
}

_FLOORPLAN_ADDON_KEYS = {
    "floorplan",
    "floorplan_2d",
    "floor_plan",
    "floor_plan_only",
    "2d_floor_plan",
    "floorplans",
}

_VISIT_ADDON_KEYS = {
    "visit",
    "virtual_visit",
    "virtualvisit",
    "gaussian_splat",
    "supersplat",
    "extended3d",
    "extended3d_addon",
}

_PHOTOS_ONLY_IDS = {
    "photosonly",
    "photos_only",
    "photo_only",
    "photos",
}

_TOUR_JOB_TYPES = {"3d_tour", "3dtour", "virtual_tour", "virtualtour"}
_FLOORPLAN_JOB_TYPES = {"floorplan", "floor_plan", "floor_plan_only"}

_SPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _canon(raw: Any) -> str:
    text = _NON_ALNUM.sub(" ", str(raw or "").lower())
    return _SPACE.sub(" ", text).strip()


def _compact(raw: Any) -> str:
    return _canon(raw).replace(" ", "")


def _addon_records(add_ons: Any) -> list[dict[str, str]]:
    if add_ons is None or add_ons == "":
        return []
    data = add_ons
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = [data]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, str):
            out.append({"key": _compact(item), "text": _canon(item)})
            continue
        if not isinstance(item, dict):
            continue
        if item.get("included") is False or item.get("enabled") is False:
            continue
        texts = []
        keys = []
        for field in ("key", "id", "sku", "name", "label", "type", "kind"):
            val = item.get(field)
            if val:
                texts.append(_canon(val))
                keys.append(_compact(val))
        if keys or texts:
            out.append({"key": keys[0] if keys else "", "text": " ".join(texts)})
    return out


def _checklist_labels(checklist: Any) -> list[str]:
    if not isinstance(checklist, list):
        return []
    labels = []
    for item in checklist:
        if isinstance(item, str):
            labels.append(_canon(item))
        elif isinstance(item, dict):
            labels.append(_canon(item.get("label") or item.get("name") or ""))
    return [x for x in labels if x]


def _package_id(job: dict) -> str:
    return _compact(job.get("package_id") or "")


def _package_name(job: dict) -> str:
    return _canon(job.get("package_name") or "")


def _job_type(job: dict) -> str:
    return _compact(job.get("job_type") or "")


def _haystack(job: dict, addons: list[dict[str, str]], labels: list[str]) -> str:
    parts = [
        _canon(job.get("package_id")),
        _package_name(job),
        _canon(job.get("job_type")),
        _canon(job.get("property_details")),
        _canon(job.get("client_instructions")),
        " ".join(a["text"] for a in addons),
        " ".join(labels),
    ]
    return _SPACE.sub(" ", " ".join(p for p in parts if p)).strip()


def _photos_only(job: dict, blob: str) -> bool:
    pid = _package_id(job)
    if pid in _PHOTOS_ONLY_IDS:
        return True
    if "photos only" in blob or "photo only" in blob:
        return True
    if "no floor plan" in blob:
        return True
    return False


def _has_floorplan_line(addons: list[dict[str, str]], labels: list[str], blob: str) -> bool:
    for rec in addons:
        if rec["key"] in _FLOORPLAN_ADDON_KEYS:
            return True
        if "floor plan" in rec["text"] or "floorplan" in rec["text"].replace(" ", ""):
            return True
    for label in labels:
        compact = label.replace(" ", "")
        if compact == "floorplancapturesubmitted":
            continue
        if "floor plan" in label or compact.startswith("floorplan"):
            return True
    if "floor plan only" in blob or "floorplan only" in blob.replace(" ", " "):
        return True
    if _package_id({"package_id": blob}) in _FLOORPLAN_ADDON_KEYS:
        return True
    return False


def _photo_package_includes_floorplan(job: dict, blob: str) -> bool:
    """Photo packages include a floor plan. Small–XL photo, Essentials+."""
    pid = _package_id(job)
    if pid in _PHOTO_PACKAGES_WITH_FLOORPLAN:
        return True
    name = _package_name(job)
    for token in _PHOTO_PACKAGES_WITH_FLOORPLAN:
        if token in name.split() or name.startswith(token + " "):
            return True
    # Small / Mid / Large / XL photo (footprint-banded photo package).
    if re.search(r"\b(small|mid|large|xl)\b.*\bphoto", name) or re.search(
        r"\bphoto.*\b(small|mid|large|xl)\b", name
    ):
        return True
    if _job_type(job) == "photo":
        return True
    if "hdr photo" in blob or "hdr photography" in blob:
        return True
    return False


def _is_non_photo_standalone(job: dict, blob: str, addons: list[dict[str, str]]) -> bool:
    """Drone-only / reel-only / visit-only / tour-only — no photo package."""
    if _photo_package_includes_floorplan(job, blob):
        return False
    if "hdr photo" in blob or "hdr photography" in blob:
        return False
    jt = _job_type(job)
    if jt in _TOUR_JOB_TYPES or jt in _FLOORPLAN_JOB_TYPES:
        return True
    if jt in {"drone", "video", "reel", "twilight", "visit", "virtual_visit"}:
        return True
    keys = {a["key"] for a in addons}
    photoish = keys & {"photo", "photos", "photography", "hdr"}
    if photoish:
        return False
    standalone = keys & {"drone", "reel", "video", "twilight", "rush", "visit", "tour"}
    return bool(standalone) and not photoish



def _legacy_gaussian_visit_copy(blob: str) -> bool:
    """True when copy still describes old Premium as Gaussian Visit, not path tour."""
    if "path confined" in blob or "pathconfined" in blob.replace(" ", ""):
        return False
    return (
        "gaussian" in blob
        or "supersplat" in blob
        or "free roam" in blob
        or "free-roam" in blob
    )


def _has_tour_line(addons: list[dict[str, str]], labels: list[str], blob: str, job: dict) -> bool:
    if _job_type(job) in _TOUR_JOB_TYPES:
        return True
    for rec in addons:
        if rec["key"] in _TOUR_ADDON_KEYS:
            return True
        text = rec["text"]
        if "virtual visit" in text or "gaussian" in text or "supersplat" in text:
            continue
        if "virtual tour" in text or text.replace(" ", "") in {"3dtour", "tour"}:
            return True
    pid = _package_id(job)
    legacy_visit = _legacy_gaussian_visit_copy(blob)
    if pid in _PACKAGES_WITH_TOUR and not legacy_visit:
        return True
    name = _package_name(job)
    name_tokens = set(name.split())
    if name_tokens & {"showcase", "suite", "premium", "luxury", "estate"} and not legacy_visit:
        return True
    if name.startswith("full suite") and not legacy_visit:
        return True
    for label in labels:
        if "virtual visit" in label or "gaussian" in label:
            continue
        if "virtual tour" in label or "3d tour" in label:
            return True
    cleaned = blob
    for phrase in (
        "virtual visit",
        "gaussian splat 3d virtual tour",
        "gaussian splat",
        "supersplat",
        "free roam",
        "free-roam",
    ):
        cleaned = cleaned.replace(phrase, " ")
    cleaned = _SPACE.sub(" ", cleaned).strip()
    if "path confined" in cleaned or "pathconfined" in cleaned.replace(" ", ""):
        return True
    if "virtual tour" in cleaned or "3d tour" in cleaned:
        return True
    return False


def _has_visit(addons: list[dict[str, str]], blob: str) -> bool:
    for rec in addons:
        if rec["key"] in _VISIT_ADDON_KEYS:
            return True
        if "virtual visit" in rec["text"] or "gaussian splat" in rec["text"] or "supersplat" in rec["text"]:
            return True
    if "virtual visit" in blob or "gaussian splat" in blob or "supersplat" in blob:
        return True
    return False


def job_capture_entitlements(job: dict | None) -> dict[str, bool]:
    """Return purchased in-app capture flags for a jobs row.

    ``tour`` — path-confined 3D tour (API kind=splat). Not virtual visit.
    ``floorplan`` — in-app floor plan capture.
    ``visit`` — virtual visit add-on purchased; deliver via GO Polycam/.ply/.splat.
    """
    job = job or {}
    addons = _addon_records(job.get("add_ons"))
    labels = _checklist_labels(job.get("checklist"))
    blob = _haystack(job, addons, labels)
    photos_only = _photos_only(job, blob)
    visit = _has_visit(addons, blob)
    tour = _has_tour_line(addons, labels, blob, job)

    explicit_fp = _has_floorplan_line(addons, labels, blob)
    if photos_only and not explicit_fp:
        floorplan = False
    elif explicit_fp:
        floorplan = True
    elif _job_type(job) in _FLOORPLAN_JOB_TYPES:
        floorplan = True
    elif photos_only:
        floorplan = False
    elif _photo_package_includes_floorplan(job, blob) and not _is_non_photo_standalone(job, blob, addons):
        floorplan = True
    else:
        floorplan = False

    return {
        "tour": bool(tour),
        "floorplan": bool(floorplan),
        "visit": bool(visit),
    }


def kind_purchased(job: dict | None, kind: str) -> bool:
    flags = job_capture_entitlements(job)
    if kind == KIND_FLOORPLAN:
        return flags["floorplan"]
    if kind == KIND_TOUR:
        return flags["tour"]
    if kind == KIND_VISIT:
        return flags["visit"]
    return False


def capture_forbidden_message(kind: str, flags: dict[str, bool] | None = None) -> str:
    flags = flags or {}
    if kind == KIND_FLOORPLAN:
        return "This job has no floor plan. In-app floor plan capture is only for jobs that purchased one."
    if kind == KIND_TOUR:
        if flags.get("visit") and not flags.get("tour"):
            return (
                "This job has no 3D tour. Open Virtual Visit in GO to paste a Polycam URL "
                "or upload a .ply / .splat."
            )
        return "This job has no 3D tour. Start Capture does not shoot a tour that was not purchased."
    if kind == KIND_VISIT:
        return "This job has no Virtual Visit add-on. Free-roam Visit is only for jobs that purchased it."
    return "That capture mode is not on this job."


def stamp_capture(job: dict | None) -> dict:
    """Attach a ``capture`` block onto a job payload (mutates and returns).

    Also ensures a one-visit shoot checklist is present for Today / job UIs.
    """
    row = job or {}
    flags = job_capture_entitlements(row)
    row["capture"] = flags
    try:
        from .job_checklist import effective_checklist, is_trade_job, public_items
        if is_trade_job(row):
            # Trade jobs: saved ticks and proof, stable ids, no storage paths.
            row["checklist"] = public_items(effective_checklist(row))
            row["checklist_title"] = "Job checklist"
            row["trade_job"] = True
            return row
        row["trade_job"] = False
    except Exception:
        pass
    try:
        from .visit_checklist import ensure_visit_checklist
        existing = row.get("checklist") or []
        if not existing:
            row["checklist"] = ensure_visit_checklist(row)
        else:
            # Reseed only if legacy return-visit wording
            labels = " ".join(
                str(i.get("label") if isinstance(i, dict) else i).lower() for i in existing
            )
            if "return visit" in labels or "twilight return" in labels or "second appointment" in labels:
                row["checklist"] = ensure_visit_checklist(row)
            else:
                # Client-service and trade jobs: swap an untouched generic list.
                from .visit_checklist import swap_untouched_checklist
                swapped = swap_untouched_checklist(row)
                if swapped is not None:
                    row["checklist"] = swapped
    except Exception:
        pass
    try:
        from .visit_checklist import checklist_title
        row["checklist_title"] = checklist_title(row)
    except Exception:
        row["checklist_title"] = "Shoot checklist"
    return row
