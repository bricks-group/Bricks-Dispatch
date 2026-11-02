"""Probation payout hold: a vendor's first N jobs are paid only once the
client rating lands or the auto-release window passes with no complaint.

Runs entirely server-side, with no bot or box involved:
- trigger_payout_for_job() calls hold_for_payout() and writes a payout with
  status "held" instead of paying.
- The daily Vercel cron (/api/onboarding/probation-sweep) and the OS rating
  webhook (same path with job_id, internal key) call run_probation_sweep().
- A bad rating (<= bad_rating_max), a QC fail or an open dispute blocks the
  payout, pauses the vendor (not for disputes) and texts and emails the owner
  with signed one-tap links (/api/onboarding/probation/action).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as _html
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import trade_specs as ts
from ..shared.vendor_identity import is_owner_member

logger = logging.getLogger(__name__)

PROBATION_PREFIX = "Probation: "
DEFAULT_RELEASE_HOURS = 72
ACTION_TTL_SECONDS = 14 * 24 * 3600
ACTIONS = {
    "release": "Release payout and resume vendor",
    "keep": "Keep vendor paused and payout blocked",
}
ADMIN_EMAIL = "raphael@brxs.ca"
OPEN_DISPUTE_CLOSED = {"resolved", "closed", "rejected", "dismissed", "withdrawn", "cancelled", "canceled"}


def release_hours() -> int:
    try:
        return int(os.environ.get("PROBATION_RELEASE_HOURS") or ts.registry().get("probation_release_hours") or DEFAULT_RELEASE_HOURS)
    except (TypeError, ValueError):
        return DEFAULT_RELEASE_HOURS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Hold at payout time
# ---------------------------------------------------------------------------
def _eligible_member(member_id: str) -> bool:
    return bool(member_id) and member_id not in ("default_user", "default_vendor") and not is_owner_member({"id": member_id})


async def hold_for_payout(db, job: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Return the probation row when this job's payout must be held. Creates the
    row (one of the vendor's first N jobs) if completion didn't. Soft-fails open:
    on a lookup error the payout proceeds, as it did before probation existed."""
    member_id = str(job.get("assignee_id") or "")
    job_id = str(job.get("id") or "")
    if not job_id or not _eligible_member(member_id):
        return None
    now = now or _now()
    try:
        res = await db.table("vendor_probation_reviews").select("*").eq("member_id", member_id).execute()
        rows = res.data or []
        row = next((r for r in rows if str(r.get("job_id")) == job_id), None)
        if row is None:
            if len(rows) >= ts.probation_jobs():
                return None
            row = new_row(job_id, member_id, now)
            await db.table("vendor_probation_reviews").insert(row).execute()
            return row
        if row.get("payout_hold") in ("held", "blocked"):
            return row
        return None
    except Exception as e:
        logger.warning("probation hold lookup soft-fail job=%s: %s", job_id, type(e).__name__)
        return None


def new_row(job_id: str, member_id: str, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    return {
        "job_id": job_id, "member_id": member_id, "status": "pending",
        "payout_hold": "held", "delivered_at": now.isoformat(),
        "release_after": (now + timedelta(hours=release_hours())).isoformat(),
        "hold_reason": "probation",
    }


# ---------------------------------------------------------------------------
# Decide
# ---------------------------------------------------------------------------
def qc_failed(uploads: list[dict]) -> bool:
    """Half or more of the job's reviewed stills were rejected by QC."""
    reviewed = [u for u in uploads or [] if u.get("qc_status") in ("passed", "flagged", "rejected")
                and str(u.get("provider") or "").lower() != "bracket_sibling"]
    if not reviewed:
        return False
    rejected = sum(1 for u in reviewed if u.get("qc_status") == "rejected")
    return rejected * 2 >= len(reviewed)


def decide(row: dict, rating: Optional[int], qc_fail: bool, open_dispute: bool,
           now: Optional[datetime] = None) -> tuple[str, str]:
    """(action, reason). action: release | block | wait | dispute."""
    now = now or _now()
    bad = ts.bad_rating_max()
    if rating is not None and int(rating) <= bad:
        return "block", f"client rating {int(rating)}/5"
    if qc_fail or row.get("qc_result") == "failed":
        return "block", "QC failed"
    if open_dispute:
        return "dispute", "open client complaint"
    if rating is not None:
        return "release", f"client rating {int(rating)}/5"
    due = _parse_ts(row.get("release_after"))
    if due and now >= due:
        return "release", f"auto-release after {release_hours()}h, no rating or complaint"
    return "wait", "waiting for client rating"


async def _rating(db, job_id: str, member_id: str, row: dict) -> Optional[int]:
    if row.get("client_rating"):
        return int(row["client_rating"])
    try:
        res = await db.table("vendor_reviews").select("rating, created_at").eq("job_id", job_id).eq("vendor_id", member_id).execute()
        vals = [int(r["rating"]) for r in (res.data or []) if r.get("rating")]
        return min(vals) if vals else None
    except Exception as e:
        logger.warning("probation rating lookup failed job=%s: %s", job_id, type(e).__name__)
        return None


async def _job(db, job_id: str) -> dict:
    res = await db.table("jobs").select("id, assignee_id, address, booking_id, status").eq("id", job_id).execute()
    return (res.data or [{}])[0]


async def _uploads(db, job_id: str) -> list[dict]:
    try:
        res = await db.table("uploads").select("id, qc_status, provider").eq("job_id", job_id).execute()
        return res.data or []
    except Exception:
        return []


async def _open_dispute(db, booking_id) -> bool:
    if not booking_id:
        return False
    try:
        res = await db.table("booking_disputes").select("id, status").eq("booking_id", str(booking_id)).execute()
        return any(str(r.get("status") or "open").lower() not in OPEN_DISPUTE_CLOSED for r in (res.data or []))
    except Exception:
        return False


async def _pause(db, member_id: str, reason: str) -> bool:
    rm = await db.table("vendor_members").select("id, paused_at").eq("id", member_id).execute()
    m = (rm.data or [{}])[0]
    if not m or m.get("paused_at") or is_owner_member(m):
        return False
    now = _now().isoformat()
    await db.table("vendor_members").update({"paused_at": now, "paused_reason": f"{PROBATION_PREFIX}{reason}", "updated_at": now}).eq("id", member_id).execute()
    return True


async def _release(db, row: dict, reason: str, reviewed_by: str = "auto") -> dict:
    from .payout_service import trigger_payout_for_job

    now = _now().isoformat()
    patch = {"payout_hold": "released", "payout_released_at": now, "hold_reason": reason,
             "status": "approved", "reviewed_by": reviewed_by, "reviewed_at": now}
    await _update(db, row, patch)
    try:
        payout = await trigger_payout_for_job(db, str(row["job_id"]), release=True)
    except Exception as e:  # payout failure is retried by the normal payout retry paths
        logger.error("probation release payout failed job=%s: %s", row.get("job_id"), type(e).__name__)
        payout = {"error": type(e).__name__}
    return {"payout": {k: payout.get(k) for k in ("payout_id", "status", "error") if isinstance(payout, dict)}}


async def _update(db, row: dict, patch: dict) -> None:
    await db.table("vendor_probation_reviews").update(patch).eq("member_id", row["member_id"]).eq("job_id", row["job_id"]).execute()
    row.update(patch)


async def evaluate(db, row: dict, now: Optional[datetime] = None, notify: bool = True) -> dict:
    """Apply the decision for one held row. Idempotent."""
    job_id, member_id = str(row["job_id"]), str(row["member_id"])
    job = await _job(db, job_id)
    rating = await _rating(db, job_id, member_id, row)
    action, reason = decide(row, rating, qc_failed(await _uploads(db, job_id)),
                            await _open_dispute(db, job.get("booking_id")), now)
    out = {"job_id": job_id, "member_id": member_id, "action": action, "reason": reason}
    if rating is not None and row.get("client_rating") != rating:
        await _update(db, row, {"client_rating": rating})
    if action == "release":
        out.update(await _release(db, row, reason))
    elif action == "block":
        if row.get("payout_hold") != "blocked":
            await _update(db, row, {"payout_hold": "blocked", "status": "failed", "hold_reason": reason,
                                    "qc_result": "failed" if reason == "QC failed" else row.get("qc_result"),
                                    "reviewed_by": "auto", "reviewed_at": _now().isoformat()})
        out["paused"] = await _pause(db, member_id, reason)
        if notify and not row.get("alerted_at"):
            out["alerted"] = await alert_owner(db, row, job, reason, paused=True)
    elif action == "dispute":
        if row.get("hold_reason") != reason:
            await _update(db, row, {"hold_reason": reason})
        if notify and not row.get("alerted_at"):
            out["alerted"] = await alert_owner(db, row, job, reason, paused=False)
    return out


async def run_probation_sweep(db, job_id: Optional[str] = None, now: Optional[datetime] = None,
                              notify: bool = True) -> dict:
    q = db.table("vendor_probation_reviews").select("*").eq("payout_hold", "held")
    if job_id:
        q = q.eq("job_id", str(job_id))
    res = await q.execute()
    results = []
    for row in res.data or []:
        try:
            results.append(await evaluate(db, row, now=now, notify=notify))
        except Exception as e:
            logger.error("probation evaluate failed job=%s: %s", row.get("job_id"), type(e).__name__)
            results.append({"job_id": row.get("job_id"), "error": type(e).__name__})
    return {"ok": True, "checked": len(results), "results": results}


# ---------------------------------------------------------------------------
# One-tap owner actions (HMAC-signed, expiring)
# ---------------------------------------------------------------------------
def _secret() -> bytes:
    raw = (os.environ.get("PROBATION_ACTION_SECRET") or os.environ.get("CRON_SECRET")
           or os.environ.get("SUPABASE_JWT_SECRET") or "")
    if not raw:
        raise RuntimeError("probation action secret not configured")
    return hashlib.sha256(b"bricks-probation-action|" + raw.encode()).digest()


def sign_action(member_id: str, job_id: str, action: str, exp: Optional[int] = None) -> str:
    exp = exp or int(time.time()) + ACTION_TTL_SECONDS
    payload = f"{member_id}|{job_id}|{action}|{exp}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode().rstrip("=")


def verify_action(token: str) -> Optional[dict]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        member_id, job_id, action, exp, sig = raw.split("|")
    except Exception:
        return None
    want = hmac.new(_secret(), f"{member_id}|{job_id}|{action}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, sig) or action not in ACTIONS or int(exp) < time.time():
        return None
    return {"member_id": member_id, "job_id": job_id, "action": action}


def action_url(member_id: str, job_id: str, action: str) -> str:
    base = (os.environ.get("DISPATCH_PUBLIC_URL") or "https://dispatch-api.brxs.ca").rstrip("/")
    return f"{base}/api/onboarding/probation/action?t={sign_action(member_id, job_id, action)}"


async def apply_action(db, member_id: str, job_id: str, action: str) -> dict:
    found = await db.table("vendor_probation_reviews").select("*").eq("member_id", member_id).eq("job_id", job_id).execute()
    row = (found.data or [None])[0]
    if not row:
        return {"ok": False, "error": "not_found"}
    now = _now().isoformat()
    if action == "keep":
        await _update(db, row, {"payout_hold": "blocked", "reviewed_by": "owner", "reviewed_at": now})
        return {"ok": True, "action": "keep"}
    out = {"ok": True, "action": "release"}
    rm = await db.table("vendor_members").select("id, paused_reason").eq("id", member_id).execute()
    m = (rm.data or [{}])[0]
    if str(m.get("paused_reason") or "").startswith(PROBATION_PREFIX):
        await db.table("vendor_members").update({"paused_at": None, "paused_reason": None, "updated_at": now}).eq("id", member_id).execute()
        out["resumed"] = True
    if row.get("payout_hold") == "released":
        out["already_released"] = True
        return out
    out.update(await _release(db, row, "owner released", reviewed_by="owner"))
    return out


async def _member_name(db, member_id: str) -> tuple:
    try:
        rm = await db.table("vendor_members").select("name, vendor_type").eq("id", member_id).execute()
        m = (rm.data or [{}])[0]
        return (m.get("name") or "A vendor").strip(), (m.get("vendor_type") or "").strip()
    except Exception:
        return "A vendor", ""


async def alert_owner(db, row: dict, job: dict, reason: str, paused: bool) -> bool:
    """SMS the owner phone + email the admin inbox. Never raises."""
    member_id, job_id = str(row["member_id"]), str(row["job_id"])
    try:
        name, trade = await _member_name(db, member_id)
        place = (job.get("address") or "").strip() or "a job"
        release = action_url(member_id, job_id, "release")
        keep = action_url(member_id, job_id, "keep")
        head = (f"{name} is paused and their payout is blocked ({reason}) on {place}." if paused
                else f"{name}'s payout is on hold ({reason}) on {place}.")
        sent = False
        try:
            from .qc_hold_notify import _owner_phone
            from ..notifications import send_dispatch_sms

            phone = await _owner_phone(db)
            if phone and send_dispatch_sms(phone, f"Bricks probation: {head} One tap to release: {release}"):
                sent = True
        except Exception as e:
            logger.error("probation SMS failed job=%s: %s", job_id, type(e).__name__)
        try:
            from .notifications import send_email_via_resend

            subject = f"Probation hold: {name}" + (f" ({trade})" if trade else "")
            body = (f"<p>{_html.escape(head)}</p><p>This is one of their first {int(ts.probation_jobs())} jobs "
                    f"(job id {_html.escape(job_id)}).</p>"
                    f'<p><a href="{_html.escape(release)}" style="display:inline-block;padding:10px 16px;'
                    f'background:#111;color:#fff;border-radius:6px;text-decoration:none">{ACTIONS["release"]}</a></p>'
                    f'<p><a href="{_html.escape(keep)}">{ACTIONS["keep"]}</a></p>'
                    "<p>Links open a confirm page and expire in 14 days.</p>")
            ok = await send_email_via_resend(to=ADMIN_EMAIL, subject=subject, html=body)
            sent = sent or bool(ok)
        except Exception as e:
            logger.error("probation email failed job=%s: %s", job_id, type(e).__name__)
        if sent:
            await _update(db, row, {"alerted_at": _now().isoformat()})
        return sent
    except Exception as e:
        logger.error("probation alert failed job=%s: %s", job_id, type(e).__name__)
        return False
