"""Fulfillment gate: deliver + pay out only a complete, editor-approved order.

Launch review 2026-11-07 (Kam: "close all the gaps"):
  * Client delivery (media_delivery + status=delivered) and vendor payout must
    only happen when EVERY purchased component of the order is present AND the
    job carries an editor sign-off (jobs.editor_approved_at).
  * Raw reel / drone / video clips are source material. They never auto-deliver
    as a finished film; only an upload an editor marked as the final cut
    (provider=editor_final) counts as a film deliverable.
  * QC-flagged / rejected uploads are already excluded upstream (is_qc_held).

FULFILLMENT_GATE=0|false|off disables the gate (emergency only). Default ON.
Requires migration 20261107_fulfillment_gate.sql (jobs.editor_approved_at,
jobs.editor_approved_by, jobs.fulfillment_gate). Without the column every
media job reads as "not approved", so the gate fails safe (holds), never open.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable, Optional

GATE_ENV = "FULFILLMENT_GATE"

PHOTOS = "photos"
DRONE = "drone"
REEL = "reel"
TWILIGHT = "twilight"
FLOORPLAN = "floorplan"
TOUR = "tour"
VISIT = "visit"
STAGING = "staging"

# Provider stamps
PROVIDER_SOURCE_CLIP = "source_clip"   # raw vendor video — never a deliverable
PROVIDER_EDITOR_FINAL = "editor_final"  # editor-approved finished film/master

# www ladder (pricing.ts): Photos only; Essentials = photos+drone+floor plan;
# Story adds reel; Showcase adds path tour; Full suite adds twilight.
_LADDER = {
    "photos_only": {PHOTOS},
    "essentials": {PHOTOS, DRONE, FLOORPLAN},
    "story": {PHOTOS, DRONE, FLOORPLAN, REEL},
    "showcase": {PHOTOS, DRONE, FLOORPLAN, REEL, TOUR},
    "full": {PHOTOS, DRONE, FLOORPLAN, REEL, TOUR, TWILIGHT},
    # Legacy (historical reads): standard = photo+drone+plan; premium+ adds tour.
    "standard": {PHOTOS, DRONE, FLOORPLAN},
    "premium": {PHOTOS, DRONE, FLOORPLAN, TOUR},
    "luxury": {PHOTOS, DRONE, FLOORPLAN, TOUR},
    "estate": {PHOTOS, DRONE, FLOORPLAN, TOUR},
}
_ALIASES = {
    "photosonly": "photos_only", "photos": "photos_only", "photo_only": "photos_only",
    "suite": "full", "full_suite": "full", "fullsuite": "full",
    "floor_plan_only": "floorplan_only", "floorplanonly": "floorplan_only",
}
_ADDON_COMPONENT = {
    "drone": DRONE, "aerial": DRONE, "drone_addon": DRONE,
    "reel": REEL, "video": REEL, "reel_addon": REEL, "cinematic_reel": REEL,
    "twilight": TWILIGHT, "twilight_addon": TWILIGHT, "dusk": TWILIGHT,
    "floorplan": FLOORPLAN, "floor_plan": FLOORPLAN, "floorplan_2d": FLOORPLAN,
    "floor_plan_only": FLOORPLAN, "2d_floor_plan": FLOORPLAN,
    "tour": TOUR, "virtual_tour": TOUR, "3d_tour": TOUR, "path_tour": TOUR,
    "visit": VISIT, "virtual_visit": VISIT, "virtualvisit": VISIT,
    "staging": STAGING, "virtual_staging": STAGING,
}
# Non-deliverable add-on keys (fees, pass-through).
_ADDON_IGNORE = {"travel", "travel_fee", "rush", "rush_fee", "hst", "tax", "discount"}

_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".lrv")


def gate_enabled() -> bool:
    raw = (os.environ.get(GATE_ENV) or "").strip().lower()
    return raw not in ("0", "false", "off", "no", "disabled")


def _key(raw: Any) -> str:
    return str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")


def _addon_keys(add_ons: Any) -> list[str]:
    data = add_ons
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = [data]
    if isinstance(data, dict):
        data = [data]
    out: list[str] = []
    for rec in data if isinstance(data, list) else []:
        if isinstance(rec, str):
            out.append(_key(rec))
        elif isinstance(rec, dict):
            if rec.get("included") is False or rec.get("enabled") is False:
                continue
            k = rec.get("key") or rec.get("id") or rec.get("sku") or rec.get("type")
            if k:
                out.append(_key(k))
    return out


def is_trade_job(job: Optional[dict]) -> bool:
    try:
        from .job_checklist import is_trade_job as _trade
        return bool(_trade(job or {}))
    except Exception:
        return False


def required_components(job: Optional[dict]) -> set[str]:
    """Purchased deliverable components for a media job.

    Unknown package ids (client-service sets, builder visits) return {"any"}:
    at least one approved deliverable must exist, plus explicit add-ons.
    """
    job = job or {}
    pid = _key(job.get("package_id"))
    pid = _ALIASES.get(pid, pid)
    job_type = _key(job.get("job_type"))
    req: set[str] = set()
    if pid == "floorplan_only" or job_type in ("floorplan", "floor_plan", "floor_plan_only"):
        req.add(FLOORPLAN)
    elif pid in _LADDER:
        req |= _LADDER[pid]
    for k in _addon_keys(job.get("add_ons")):
        if k in _ADDON_IGNORE:
            continue
        comp = _ADDON_COMPONENT.get(k)
        if comp:
            req.add(comp)
    # Capture entitlements catch tour / floor plan / visit sold via copy.
    try:
        from .capture_entitlements import job_capture_entitlements
        flags = job_capture_entitlements(job)
        if flags.get("visit"):
            req.add(VISIT)
        if flags.get("tour") and pid in _LADDER:
            req.add(TOUR)
    except Exception:
        pass
    if not req:
        req.add("any")
    return req


def _entry_component(entry: dict) -> Optional[str]:
    kind = _key(entry.get("kind"))
    if kind == "floorplan":
        return FLOORPLAN
    if kind == "splat":
        return TOUR
    if kind == "visit":
        return VISIT
    if kind == "drone":
        return DRONE
    if kind == "reel":
        # Only the editor's finished cut counts; raw clips / reel stills never do.
        return REEL if _key(entry.get("provider")) == PROVIDER_EDITOR_FINAL else None
    if kind == "twilight":
        return TWILIGHT
    if kind == "staging":
        return STAGING
    return PHOTOS


def present_components(delivery_urls: Iterable[Any] | None, *, visit_present: bool = False) -> set[str]:
    have: set[str] = set()
    for e in delivery_urls or []:
        if not isinstance(e, dict):
            continue
        comp = _entry_component(e)
        if comp:
            have.add(comp)
    if visit_present:
        have.add(VISIT)
    return have


def editor_approved(job: Optional[dict]) -> bool:
    return bool((job or {}).get("editor_approved_at"))


def evaluate(job: Optional[dict], delivery_urls: Iterable[Any] | None, *, visit_present: bool = False) -> dict:
    """Return {ready, required, present, missing, editor_approved, reasons}."""
    urls = list(delivery_urls or [])
    req = required_components(job)
    have = present_components(urls, visit_present=visit_present)
    if "any" in req:
        missing = [] if (have or urls) else ["any"]
        req_list = sorted(req - {"any"}) or ["any"]
        missing += sorted((req - {"any"}) - have)
    else:
        req_list = sorted(req)
        missing = sorted(req - have)
    approved = editor_approved(job)
    reasons = []
    if missing:
        reasons.append("missing: " + ", ".join(missing))
    if not approved:
        reasons.append("awaiting editor approval")
    return {
        "ready": not missing and approved,
        "required": req_list,
        "present": sorted(have),
        "missing": missing,
        "editor_approved": approved,
        "reasons": reasons,
    }


def is_source_clip(upload: Optional[dict]) -> bool:
    """Raw vendor video (reel/drone/video) not yet marked as the editor's final cut."""
    row = upload or {}
    if _key(row.get("provider")) == PROVIDER_EDITOR_FINAL:
        return False
    name = str(row.get("filename") or row.get("file_url") or "").lower().split("?", 1)[0]
    mime = str(row.get("mime_type") or "").lower()
    return name.endswith(_VIDEO_EXTS) or mime.startswith("video/")


def payout_release_allowed(job: Optional[dict]) -> tuple[bool, str]:
    """Vendor payout only after a complete, editor-approved delivery.

    No-show claims and trade jobs are handled by the caller / exempt here.
    """
    if not gate_enabled():
        return True, "gate_disabled"
    job = job or {}
    if is_trade_job(job):
        return True, "trade_job"
    if str(job.get("status") or "").lower() != "delivered":
        return False, "not_delivered"
    if not editor_approved(job):
        return False, "awaiting_editor_approval"
    gate = job.get("fulfillment_gate")
    if isinstance(gate, str):
        try:
            gate = json.loads(gate)
        except Exception:
            gate = None
    if isinstance(gate, dict) and gate.get("missing"):
        return False, "incomplete_delivery"
    return True, "ok"
