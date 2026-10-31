"""Trade job checklist: saved ticks and proof, and the completion gate.

Media jobs keep their read-only shoot checklist. Non-media trade jobs
(cleaner, stager, de-staging, handyman, inspector, measurer) save each tick
and its proof on jobs.checklist, and the vendor can't mark the job complete
until every item is ticked with its proof attached. Kam, 2026-10-31.

Proof lives on the checklist item itself (jobs.checklist is jsonb):
  photos     [{id, path, content_type, uploaded_at, taken_at}]
  inventory  [{item, room, note}]
  report     {path, filename, content_type, uploaded_at}
  note, none_found, completed, completed_at
Files sit in the private PROOF_BUCKET (service role only).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .visit_checklist import (
    TRADE_CHECKLIST_TITLE,
    _as_dict,
    seed_visit_checklist,
    swap_untouched_checklist,
    trade_checklist_key,
)

PROOF_BUCKET: str = os.environ.get("CHECKLIST_PROOF_BUCKET", "job-checklist-proof")
# Vercel caps a request body at 4.5 MB.
MAX_UPLOAD_BYTES: int = 4 * 1024 * 1024
MAX_PHOTOS_PER_ITEM = 40
MAX_INVENTORY_ROWS = 200
MAX_TEXT = 500
MAX_NOTE = 2000

PROOF_TICK = "tick"
PROOF_PHOTO = "photo"
PROOF_INVENTORY = "inventory"
PROOF_REPORT = "report"
PROOF_FINDINGS = "findings"

EDITABLE_STATUSES = {"assigned", "in_progress"}
REPORT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}

_NUM_RE = re.compile(r"^\s*\d+\.\s*")


def _plain_label(label: Any) -> str:
    return _NUM_RE.sub("", str(label or "")).strip()


def proof_kind(label: Any) -> str:
    """What an item needs before it can be ticked, read from its label.

    Derived (not stored) so lists saved before this change still work.
    """
    text = _plain_label(label).lower()
    if "report" in text and "upload" in text:
        return PROOF_REPORT
    if "inventory" in text:
        return PROOF_INVENTORY
    if text.startswith("note "):
        return PROOF_FINDINGS
    if "photo" in text:
        return PROOF_PHOTO
    return PROOF_TICK


def findings_need_photo(label: Any) -> bool:
    return "photo" in _plain_label(label).lower()


def is_trade_job(job: Any) -> bool:
    try:
        return bool(trade_checklist_key(job))
    except Exception:
        return False


def _stable_id(job_id: str, n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bricks-checklist:{job_id}:{n}"))


def effective_checklist(job: Any) -> list[dict]:
    """The trade checklist as saved, or the one Bricks would seed, with stable ids.

    Same choice stamp_capture makes on reads (seed when empty, swap an untouched
    generic listing list), but ids come from the job id so the list a vendor
    sees before anything is saved has the ids their first tick will use.
    """
    j = _as_dict(job)
    job_id = str(j.get("id") or "").strip()
    existing = j.get("checklist") or []
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except ValueError:
            existing = []
    if not isinstance(existing, list):
        existing = []
    j = {**j, "checklist": existing}
    items: list
    if not existing:
        items = seed_visit_checklist(j)
        if job_id:
            for n, item in enumerate(items, 1):
                item["id"] = _stable_id(job_id, n)
    else:
        swapped = swap_untouched_checklist(j)
        items = swapped if swapped is not None else existing
    out: list[dict] = []
    for n, item in enumerate(items, 1):
        if isinstance(item, dict):
            row = dict(item)
        else:
            row = {"label": str(item), "completed": False}
        if not row.get("id"):
            row["id"] = _stable_id(job_id, n) if job_id else str(uuid.uuid4())
        row["completed"] = bool(row.get("completed"))
        row["proof"] = proof_kind(row.get("label"))
        out.append(row)
    return out


def _inventory_ok(rows: Any) -> bool:
    return any(
        isinstance(r, dict) and str(r.get("item") or "").strip() and str(r.get("room") or "").strip()
        for r in (rows or [])
    )


def proof_missing(item: dict) -> Optional[str]:
    """What proof the item still needs, in plain words, or None when it has it."""
    kind = proof_kind(item.get("label"))
    if kind == PROOF_PHOTO:
        return None if item.get("photos") else "add at least one photo"
    if kind == PROOF_INVENTORY:
        return None if _inventory_ok(item.get("inventory")) else "add each piece with its item and room"
    if kind == PROOF_REPORT:
        report = item.get("report")
        return None if isinstance(report, dict) and report.get("path") else "upload the report"
    if kind == PROOF_FINDINGS:
        if item.get("none_found"):
            return None
        need_photo = findings_need_photo(item.get("label"))
        has_note = bool(str(item.get("note") or "").strip())
        has_photo = bool(item.get("photos"))
        if has_note and (has_photo or not need_photo):
            return None
        if need_photo:
            return "write what you found and add a photo, or mark that there was nothing"
        return "write what you found, or mark that there was nothing"
    return None


def item_missing(item: dict) -> Optional[str]:
    need = proof_missing(item)
    if need:
        return need
    if not item.get("completed"):
        return "tick it"
    return None


def missing_items(items: list[dict]) -> list[str]:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        need = item_missing(item)
        if need:
            out.append(f"{item.get('label') or 'Checklist item'} ({need})")
    return out


def completion_block_message(job: Any) -> Optional[str]:
    """Plain copy for a trade job that can't be completed yet, else None.

    Media jobs always return None: their completion rules don't change.
    """
    if not is_trade_job(job):
        return None
    items = effective_checklist(job)
    if not items:
        return None
    missing = missing_items(items)
    if not missing:
        return None
    return "Finish the job checklist before you mark this job complete. Still needed: " + "; ".join(missing) + "."


def find_item(items: list[dict], item_id: str) -> Optional[dict]:
    for item in items:
        if str(item.get("id")) == str(item_id):
            return item
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def settle_tick(item: dict) -> None:
    """Untick an item whose proof was removed."""
    if item.get("completed") and proof_missing(item):
        item["completed"] = False
        item.pop("completed_at", None)


def apply_patch(item: dict, patch: dict, actor_id: str) -> Optional[str]:
    """Apply a vendor edit to one item. Returns an error message, or None.

    Proof fields first, then the tick, so one request can add a note and tick.
    """
    kind = proof_kind(item.get("label"))
    if "note" in patch and patch["note"] is not None:
        item["note"] = str(patch["note"])[:MAX_NOTE].strip()
    if "none_found" in patch and patch["none_found"] is not None:
        if kind != PROOF_FINDINGS:
            return "This item doesn't take a nothing-found answer."
        item["none_found"] = bool(patch["none_found"])
    if "inventory" in patch and patch["inventory"] is not None:
        if kind != PROOF_INVENTORY:
            return "This item doesn't take an inventory."
        rows = []
        for raw in list(patch["inventory"])[:MAX_INVENTORY_ROWS]:
            r = _as_dict(raw)
            entry = {
                "item": str(r.get("item") or "").strip()[:MAX_TEXT],
                "room": str(r.get("room") or "").strip()[:MAX_TEXT],
            }
            note = str(r.get("note") or "").strip()[:MAX_TEXT]
            if note:
                entry["note"] = note
            if entry["item"] or entry["room"] or note:
                rows.append(entry)
        bad = [r for r in rows if not (r["item"] and r["room"])]
        if bad:
            return "Each inventory line needs both the item and the room."
        item["inventory"] = rows
    if patch.get("completed") is True:
        need = proof_missing(item)
        if need:
            return f"Before you tick this, {need}."
        if not item.get("completed"):
            item["completed"] = True
            item["completed_at"] = now_iso()
            item["completed_by"] = actor_id
    elif patch.get("completed") is False:
        item["completed"] = False
        item.pop("completed_at", None)
        item.pop("completed_by", None)
    settle_tick(item)
    return None


def public_items(items: list[dict], signed: Optional[dict] = None) -> list[dict]:
    """Items for the vendor apps: storage paths swapped for short signed URLs."""
    signed = signed or {}
    out = []
    for item in items:
        row = {k: v for k, v in item.items() if k not in ("photos", "report")}
        row["proof"] = proof_kind(item.get("label"))
        row["photos"] = [
            {
                "id": p.get("id"),
                "uploaded_at": p.get("uploaded_at"),
                "url": signed.get(p.get("path")),
            }
            for p in (item.get("photos") or [])
            if isinstance(p, dict)
        ]
        report = item.get("report")
        row["report"] = (
            {
                "filename": report.get("filename"),
                "content_type": report.get("content_type"),
                "uploaded_at": report.get("uploaded_at"),
                "url": signed.get(report.get("path")),
            }
            if isinstance(report, dict) and report.get("path")
            else None
        )
        row["missing"] = item_missing(item)
        if row["proof"] == PROOF_FINDINGS:
            row["photo_required"] = findings_need_photo(item.get("label"))
        out.append(row)
    return out


def storage_paths(items: list[dict]) -> list[str]:
    paths = []
    for item in items:
        for p in item.get("photos") or []:
            if isinstance(p, dict) and p.get("path"):
                paths.append(p["path"])
        report = item.get("report")
        if isinstance(report, dict) and report.get("path"):
            paths.append(report["path"])
    return paths


def view(job: Any, items: list[dict], signed: Optional[dict] = None) -> dict:
    j = _as_dict(job)
    pub = public_items(items, signed)
    missing = missing_items(items)
    return {
        "job_id": j.get("id"),
        "title": TRADE_CHECKLIST_TITLE,
        "editable": str(j.get("status") or "") in EDITABLE_STATUSES,
        "items": pub,
        "complete": not missing,
        "missing": missing,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }


_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "application/pdf": "pdf",
}


def proof_path(job_id: str, item_id: str, content_type: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ext = _EXT.get((content_type or "").lower(), "jpg")
    return f"{job_id}/{item_id}/{stamp}_{uuid.uuid4().hex[:8]}.{ext}"
