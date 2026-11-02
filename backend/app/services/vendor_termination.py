"""Vendor termination on a no-show or a cancel after acceptance (Kam, 2026-11-02).

"If a vendor no-shows or cancels, we terminate them."

Triggers (all server-side, no bot):
  * cancelled_after_accept: the assigned vendor declines / un-accepts a job that was
    already assigned to them (vendor_os status + watch-action endpoints).
  * no_show: no GPS check-in by the booked start + grace (default 30 min,
    NOSHOW_TERMINATE_GRACE_MIN or trade_specs "noshow_terminate_grace_min"), found by
    the ops autopilot's 15-minute pg_cron sweep.
Never triggered by a Bricks or client cancellation (status "cancelled" is not a
vendor action) and never for the owner account.

On a trigger: vendor_members.is_active=false, onboarding_status="terminated",
terminated_at/reason, paused (so no auto-assign); a vendor_terminations ledger row
(email, phone, ID fingerprints) blocks reapplying; open and future jobs are
reassigned through Dispatch; the client hears about it only when the visit moves
(no-show, or no replacement found); Kam gets one email with an "Undo termination"
one-tap link (ops /api/ops/act); the vendor gets a short neutral notice from
help@brxs.ca. Completed and delivered jobs are still paid (payouts don't check this).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

PREFIX = "Terminated: "
DEFAULT_GRACE_MIN = 30
# Jobs that started before the rule shipped never trigger a no-show termination.
RULE_START = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)  # 10:00 AM ET
OPEN_STATUSES = ("assigned", "pending_assignment", "declined")
TRIGGER_TEXT = {
    "cancelled_after_accept": "cancelled a job after accepting it",
    "no_show": "didn't check in for a booked job",
}
FROM_HELP = "Bricks <help@brxs.ca>"
TERMINATED_APPLY_MESSAGE = "We can't accept a new application from you. Questions: help@brxs.ca."


def grace_min() -> int:
    try:
        raw = os.environ.get("NOSHOW_TERMINATE_GRACE_MIN")
        if raw:
            return max(0, int(raw))
        from . import trade_specs as ts
        v = ts.registry().get("noshow_terminate_grace_min")
        return max(0, int(v)) if v is not None else DEFAULT_GRACE_MIN
    except Exception:
        return DEFAULT_GRACE_MIN


def _real_vendor(member_id: Any) -> bool:
    return bool(member_id) and str(member_id) not in ("default_user", "unassigned", "none", "null")


def explicit_start(job: dict) -> Optional[datetime]:
    """Booked start only when the job carries a real time (no 12:00 fallback)."""
    from .ops_autopilot import job_start_utc
    has_time = bool(job.get("selected_slot_start")) or any(
        len(str(job.get(k) or "")) >= 5 and str(job.get(k))[2] == ":" for k in ("confirmed_arrive_at", "time_window_start"))
    return job_start_utc(job) if has_time else None


def is_no_show(job: dict, now: datetime) -> bool:
    if job.get("status") != "assigned" or not _real_vendor(job.get("assignee_id")):
        return False
    if job.get("checked_in_at") or (job.get("uploaded_count") or 0) > 0:
        return False
    start = explicit_start(job)
    if not start or start < RULE_START:
        return False
    return start + timedelta(minutes=grace_min()) <= now <= start + timedelta(hours=12)


def is_cancel_after_accept(old_status: Optional[str], new_status: Optional[str]) -> bool:
    """The job was already the vendor's (assigned) and they dropped it."""
    return new_status == "declined" and old_status == "assigned"


def accept_window_min() -> int:
    try:
        return max(0, int(os.environ.get("AUTO_ASSIGN_ACCEPT_WINDOW_MIN") or 60))
    except ValueError:
        return 60


def acceptance(job: dict, now: datetime) -> str:
    """"yes" when the vendor tapped Accept, or was auto-assigned and kept the job past the
    accept window (default 60 min); "no" when they drop an auto-assignment inside the
    window; "unknown" for jobs assigned before acceptance was recorded."""
    from .ops_autopilot import _parse_ts
    if job.get("vendor_accepted_at"):
        return "yes"
    at = _parse_ts(job.get("assigned_at"))
    if not at:
        return "unknown"
    return "yes" if now - at >= timedelta(minutes=accept_window_min()) else "no"


async def on_vendor_drop(db, job: dict, member_id: Optional[str], old_status: Optional[str],
                         new_status: Optional[str], now: Optional[datetime] = None) -> Optional[dict]:
    """Hook for vendor decline/un-accept. Never raises."""
    try:
        if not is_cancel_after_accept(old_status, new_status) or not _real_vendor(member_id):
            return None
        now = now or datetime.now(timezone.utc)
        acc = acceptance(job, now)
        if acc == "yes":
            return await terminate(db, member_id, job.get("id"), "cancelled_after_accept", now=now)
        if acc == "unknown":
            from . import ops_autopilot as ops
            m = await _one(db, "vendor_members", id=member_id)
            await ops.send_kam(
                db, f"drop_unknown:{job.get('id')}:{member_id}", "A vendor dropped an assigned job",
                [ops._job_line(job), f"{m.get('name') or 'The vendor'} dropped it, but the job was assigned before "
                 "acceptance was recorded, so they weren't terminated automatically. It's being reassigned."],
                [("terminate_vendor", {"member_id": member_id, "job_id": job.get("id")}), ("dismiss", {"job_id": job.get("id")})],
                data={"job_id": job.get("id"), "member_id": member_id})
        return {"skipped": f"acceptance_{acc}"}
    except Exception as e:
        logger.error("on_vendor_drop failed: %s", e)
        return None


# ------------------------------------------------------------------ identity
def norm_email(e: Any) -> str:
    return str(e or "").strip().lower()


def norm_phone(p: Any) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else d


def id_fingerprint(name: Any, last4: Any) -> Optional[str]:
    n = " ".join(sorted(re.findall(r"[a-z]+", str(name or "").lower())))
    l4 = re.sub(r"[^A-Za-z0-9]", "", str(last4 or "")).upper()[-4:]
    if not n or len(l4) < 3:
        return None
    return hashlib.sha256(f"bricks-id|{n}|{l4}".encode()).hexdigest()


def doc_id_hashes(docs: list[dict]) -> list[str]:
    out = []
    for d in docs or []:
        if d.get("doc_type") != "id_verification":
            continue
        vr = d.get("validation_result") if isinstance(d.get("validation_result"), dict) else {}
        cc = vr.get("content_check") if isinstance(vr.get("content_check"), dict) else {}
        for h in (cc.get("sha256"), cc.get("id_fingerprint")):
            if h and h not in out:
                out.append(h)
    return out


async def blocked_identity(db, email: Any = None, phone: Any = None, id_hashes: list = ()) -> Optional[dict]:
    """Active (not undone) termination matching this email, phone or ID. None when clear or on error."""
    try:
        res = await db.table("vendor_terminations").select("id, member_id, email, phone, id_hashes, undone_at") \
            .is_("undone_at", "null").limit(1000).execute()
    except Exception as e:
        logger.warning("termination lookup failed: %s", type(e).__name__)
        return None
    e, p, ids = norm_email(email), norm_phone(phone), set(h for h in (id_hashes or []) if h)
    for r in res.data or []:
        if r.get("undone_at"):
            continue
        if (e and norm_email(r.get("email")) == e) or (p and len(p) >= 10 and norm_phone(r.get("phone")) == p) \
                or (ids and ids & set(r.get("id_hashes") or [])):
            return r
    return None


# ------------------------------------------------------------------ helpers
async def _one(db, table: str, **eq) -> dict:
    q = db.table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    res = await q.limit(1).execute()
    return (res.data or [{}])[0]


def _first_name(m: dict) -> str:
    return (str(m.get("name") or "").split() or [""])[0]


async def _send(to: str, subject: str, title: str, body: str) -> bool:
    if not to:
        return False
    try:
        from .notifications import _build_html_email, send_email_via_resend
        return await send_email_via_resend(to=to, subject=subject, html=_build_html_email(title, body),
                                           text=body, from_addr=FROM_HELP)
    except Exception as e:
        logger.error("termination email failed: %s", type(e).__name__)
        return False


def vendor_notice(member: dict) -> tuple[str, str]:
    hi = f"Hi {_first_name(member)}," if _first_name(member) else "Hi,"
    body = (f"{hi}\n\nYour Bricks vendor account has been closed because a booked job was cancelled after you "
            "accepted it, or wasn't checked in on time. Under the Bricks vendor rules, that ends the account.\n\n"
            "Payment for jobs you completed and delivered will still be sent as usual.\n\n"
            "If you think this is a mistake, reply to this email.\n\nBricks")
    return "Your Bricks vendor account has been closed", body


async def _notify_client(db, job: dict, reassigned_ok: bool, trigger: str) -> bool:
    """Only when the visit actually moves: a no-show, or no replacement was found."""
    from .ops_autopilot import once, _job_line
    if reassigned_ok and trigger != "no_show":
        return False
    to = job.get("agent_email") or job.get("client_email")
    if not to or not await once(db, f"term_client:{job.get('id')}"):
        return False
    if reassigned_ok:
        title, body = ("A different pro is on the way",
                       f"Your pro couldn't make it to {_job_line(job)}. We've sent another Bricks pro and will confirm "
                       "their arrival time shortly. Sorry for the wait. Reply to this email if that time doesn't work.")
    else:
        title, body = ("We're rescheduling your visit",
                       f"Your pro can't make it to {_job_line(job)}. We're finding another Bricks pro and will confirm "
                       "a new time with you shortly. Reply to this email if you have questions.")
    return await _send(to, f"Update on your Bricks visit", title, body)


# ------------------------------------------------------------------ terminate / undo
async def terminate(db, member_id: str, job_id: Optional[str], trigger: str, now: Optional[datetime] = None) -> dict:
    """Idempotent: a vendor already terminated is left as is. Never raises."""
    from . import ops_autopilot as ops
    from ..shared.vendor_identity import is_owner_member
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {"member_id": member_id, "job_id": job_id, "trigger": trigger}
    try:
        if trigger not in TRIGGER_TEXT or not _real_vendor(member_id):
            return {**out, "skipped": "not_applicable"}
        m = await _one(db, "vendor_members", id=member_id)
        if not m or is_owner_member(m):
            return {**out, "skipped": "owner_or_missing"}
        if m.get("terminated_at") or str(m.get("onboarding_status") or "") == "terminated":
            return {**out, "skipped": "already_terminated"}
        if not await ops.once(db, f"terminate:{member_id}:{job_id or ''}", {"trigger": trigger}):
            return {**out, "skipped": "already_handled"}
        iso = now.isoformat()
        reason = TRIGGER_TEXT[trigger]
        prior = {k: m.get(k) for k in ("is_active", "onboarding_status", "paused_at", "paused_reason")}
        try:
            dres = await db.table("vendor_documents").select("doc_type, validation_result").eq("member_id", member_id).execute()
            ids = doc_id_hashes(dres.data or [])
        except Exception:
            ids = []
        await db.table("vendor_members").update({
            "is_active": False, "onboarding_status": "terminated", "terminated_at": iso,
            "terminated_reason": reason, "paused_at": m.get("paused_at") or iso,
            "paused_reason": PREFIX + reason, "updated_at": iso,
        }).eq("id", member_id).execute()
        # Open + future jobs go back out through Dispatch (excluding this vendor).
        jobs = []
        try:
            jr = await db.table("jobs").select("*").eq("assignee_id", member_id).in_("status", list(OPEN_STATUSES)).limit(200).execute()
            jobs = jr.data or []
        except Exception as e:
            logger.error("termination job load failed: %s", type(e).__name__)
        if job_id and not any(str(j.get("id")) == str(job_id) for j in jobs):
            trig_job = await _one(db, "jobs", id=job_id)
            if trig_job and trig_job.get("status") in OPEN_STATUSES:
                jobs.append(trig_job)
        moved = []
        for j in jobs:
            jid = j.get("id")
            start = ops.job_start_utc(j)
            if str(jid) != str(job_id) and start and start < now - timedelta(hours=12):
                continue  # stale history, not an open booking
            res = {"job_id": jid, "ok": False}
            if await ops.once(db, f"reassign:{jid}:{member_id}"):
                try:
                    res = await ops.reassign_job(db, jid, f"vendor_terminated_{trigger}")
                except Exception as e:
                    res = {"job_id": jid, "ok": False, "error": type(e).__name__}
            res["client_notified"] = await _notify_client(db, j, bool(res.get("ok")), trigger if str(jid) == str(job_id) else "future")
            moved.append(res)
        out["reassigned"] = moved
        await db.table("vendor_terminations").insert({
            "member_id": member_id, "job_id": job_id, "trigger": trigger, "reason": reason,
            "email": norm_email(m.get("email")), "phone": norm_phone(m.get("phone")), "id_hashes": ids,
            "prior": prior, "reassigned": moved,
        }).execute()
        name = m.get("name") or "A vendor"
        lines = [f"{name} ({m.get('email') or 'no email'}) {reason}.",
                 f"Job {job_id or '-'}; trigger {trigger.replace('_', ' ')} at {ops.fmt_et(now)}.",
                 f"Reassigned {sum(1 for r in moved if r.get('ok'))} of {len(moved)} open jobs"
                 + (f"; {sum(1 for r in moved if not r.get('ok'))} still need a vendor." if any(not r.get('ok') for r in moved) else "."),
                 "They're off auto-assign and can't reapply with the same email, phone or ID. "
                 "Completed and delivered jobs still pay out."]
        out["kam_alerted"] = await ops.send_kam(
            db, f"terminated:{member_id}:{job_id or ''}", f"Vendor terminated: {name}", lines,
            [("undo_termination", {"member_id": member_id}, "Undo termination"), ("dismiss", {"member_id": member_id})],
            data={"member_id": member_id, "job_id": job_id})
        subj, body = vendor_notice(m)
        out["vendor_notified"] = await _send(m.get("email") or "", subj, "Your account has been closed", body) \
            if await ops.once(db, f"term_vendor:{member_id}") else False
        try:
            from ..shared.audit import write_audit_event  # type: ignore
            await write_audit_event("vendor_termination", "vendor_member", member_id, "terminated",
                                    details={"trigger": trigger, "job_id": job_id})
        except Exception:
            pass
        out["terminated"] = True
        logger.info("vendor terminated member=%s trigger=%s job=%s", str(member_id)[:8], trigger, job_id)
        return out
    except Exception as e:
        logger.error("terminate failed member=%s: %s", str(member_id)[:8], e)
        return {**out, "error": type(e).__name__}


async def undo(db, member_id: str, by: str = "kam-one-tap") -> dict:
    """Restore the vendor's prior state. Jobs already reassigned stay with the new vendor."""
    res = await db.table("vendor_terminations").select("*").eq("member_id", member_id).is_("undone_at", "null") \
        .order("created_at", desc=True).limit(5).execute()
    rows = [r for r in (res.data or []) if not r.get("undone_at")]
    m = await _one(db, "vendor_members", id=member_id)
    if not m:
        return {"ok": False, "error": "not_found"}
    now = datetime.now(timezone.utc).isoformat()
    prior = (rows[0].get("prior") if rows else None) or {}
    patch = {"terminated_at": None, "terminated_reason": None, "updated_at": now,
             "is_active": prior.get("is_active", True) if prior else True,
             "onboarding_status": prior.get("onboarding_status") or "approved"}
    if str(m.get("paused_reason") or "").startswith(PREFIX):
        patch["paused_at"], patch["paused_reason"] = prior.get("paused_at"), prior.get("paused_reason")
    await db.table("vendor_members").update(patch).eq("id", member_id).execute()
    for r in rows:
        await db.table("vendor_terminations").update({"undone_at": now, "undone_by": by}).eq("id", r["id"]).execute()
    return {"ok": True, "member_id": member_id, "restored": patch, "undone": len(rows)}
