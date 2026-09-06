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


def seed_visit_checklist(job: Any) -> list[dict]:
    """Build a numbered one-visit checklist from package entitlements / add-ons.

    Never labels return visits or twilight-as-second-appointment.
    """
    j = _as_dict(job)
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
