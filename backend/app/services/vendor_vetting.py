"""Vendor vetting after approval (Kam, 2026-11-01).

- Expiry sweep (daily cron): an approved vendor whose required credential has
  expired is paused (paused_at + paused_reason "Expired: ..."). When every
  required credential is current again, a pause this sweep set is lifted.
  Pauses set by probation or an admin are never lifted here.
- Probation: the first N completed jobs (trade_specs.json probation_jobs) get
  a vendor_probation_reviews row for admin review. A failed review, or a
  client rating at or under bad_rating_max, pauses the vendor.

Never emails or texts anyone. Paused vendors are not assignable
(shared/vendor_identity.is_vendor_paused).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from . import trade_specs as ts
from ..shared.vendor_identity import is_owner_member

logger = logging.getLogger(__name__)

EXPIRED_PREFIX = "Expired: "
PROBATION_PREFIX = "Probation: "
PROBATION_STATUSES = ("approved", "failed")


def expiry_decision(member: dict, docs: list[dict], today: Optional[date] = None) -> tuple[Optional[str], Optional[str]]:
    """("pause", reason) | ("resume", None) | (None, None)."""
    if not member or is_owner_member(member):
        return None, None
    expired = ts.expired_required(member, docs, today)
    reason = str(member.get("paused_reason") or "")
    if expired:
        if member.get("paused_at"):
            return None, None
        labels = ", ".join(e.get("label") or e["kind"] for e in expired)
        return "pause", f"{EXPIRED_PREFIX}{labels}"
    if member.get("paused_at") and reason.startswith(EXPIRED_PREFIX):
        return "resume", None
    return None, None


def probation_pause_reason(status: str, client_rating: Optional[int]) -> Optional[str]:
    if status == "failed":
        return f"{PROBATION_PREFIX}job failed review"
    if client_rating is not None and int(client_rating) <= ts.bad_rating_max():
        return f"{PROBATION_PREFIX}client rating {int(client_rating)}/5"
    return None


async def run_expiry_sweep(db, dry_run: bool = False, today: Optional[date] = None) -> dict:
    res = await db.table("vendor_members").select("*").eq("onboarding_status", "approved").eq("is_active", True).execute()
    members = [m for m in (res.data or []) if not is_owner_member(m)]
    ids = [str(m["id"]) for m in members]
    docs_by: dict[str, list[dict]] = {}
    if ids:
        rd = await db.table("vendor_documents").select("member_id, doc_type, status, expiry_date, validation_result").in_("member_id", ids).execute()
        for d in rd.data or []:
            docs_by.setdefault(str(d.get("member_id")), []).append(d)
    now = datetime.now(timezone.utc).isoformat()
    paused, resumed = [], []
    for m in members:
        action, reason = expiry_decision(m, docs_by.get(str(m["id"]), []), today)
        if action == "pause":
            paused.append({"member_id": m["id"], "reason": reason})
            if not dry_run:
                await db.table("vendor_members").update({"paused_at": now, "paused_reason": reason, "updated_at": now}).eq("id", m["id"]).execute()
        elif action == "resume":
            resumed.append({"member_id": m["id"]})
            if not dry_run:
                await db.table("vendor_members").update({"paused_at": None, "paused_reason": None, "updated_at": now}).eq("id", m["id"]).execute()
    return {"ok": True, "dry_run": dry_run, "checked": len(members), "paused": paused, "resumed": resumed}


async def record_probation_job(db, job: dict) -> Optional[dict]:
    """On job completion: hold one of the vendor's first N jobs for review. Soft-fails."""
    try:
        member_id = str(job.get("assignee_id") or "")
        job_id = str(job.get("id") or "")
        if not member_id or not job_id or member_id == "default_user" or is_owner_member({"id": member_id}):
            return None
        res = await db.table("vendor_probation_reviews").select("job_id").eq("member_id", member_id).execute()
        rows = res.data or []
        if any(str(r.get("job_id")) == job_id for r in rows) or len(rows) >= ts.probation_jobs():
            return None
        from .probation_hold import new_row

        row = new_row(job_id, member_id)  # payout held until rating / auto-release
        await db.table("vendor_probation_reviews").insert(row).execute()
        return row
    except Exception as e:  # never block a status change
        logger.warning("probation record soft-fail job=%s: %s", job.get("id"), e)
        return None


async def review_probation_job(db, member_id: str, job_id: str, status: str, client_rating: Optional[int] = None,
                               qc_result: Optional[str] = None, notes: Optional[str] = None,
                               reviewed_by: Optional[str] = None) -> Optional[dict]:
    if status not in PROBATION_STATUSES:
        raise ValueError("status must be approved or failed")
    if client_rating is not None and not 1 <= int(client_rating) <= 5:
        raise ValueError("client_rating must be 1-5")
    found = await db.table("vendor_probation_reviews").select("*").eq("member_id", member_id).eq("job_id", job_id).execute()
    if not (found.data or []):
        return None
    now = datetime.now(timezone.utc).isoformat()
    patch = {"status": status, "client_rating": client_rating, "qc_result": qc_result, "notes": notes,
             "reviewed_by": reviewed_by, "reviewed_at": now}
    await db.table("vendor_probation_reviews").update(patch).eq("member_id", member_id).eq("job_id", job_id).execute()
    reason = probation_pause_reason(status, client_rating)
    paused = False
    if reason:
        rm = await db.table("vendor_members").select("id, paused_at").eq("id", member_id).execute()
        m = (rm.data or [{}])[0]
        if m and not m.get("paused_at") and not is_owner_member(m):
            await db.table("vendor_members").update({"paused_at": now, "paused_reason": reason, "updated_at": now}).eq("id", member_id).execute()
            paused = True
    payout = None
    row = {**found.data[0], **patch}
    if found.data[0].get("payout_hold") in ("held", "blocked"):
        from . import probation_hold as ph

        if status == "approved" and not reason:
            payout = (await ph._release(db, row, "owner approved", reviewed_by=reviewed_by or "owner")).get("payout")
        elif reason:
            await ph._update(db, row, {"payout_hold": "blocked", "hold_reason": reason})
    return {**row, "paused": paused, "pause_reason": reason, "payout": payout}


async def probation_reviews(db, member_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    q = db.table("vendor_probation_reviews").select("*")
    if member_id:
        q = q.eq("member_id", member_id)
    if status:
        q = q.eq("status", status)
    res = await q.order("created_at", desc=True).limit(200).execute()
    return res.data or []
