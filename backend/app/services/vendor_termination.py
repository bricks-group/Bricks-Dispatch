"""Vendor termination on a no-show or a late cancel (Kam, 2026-11-02, decisions 10:18 ET).

Rules (all server-side in Dispatch, no bot):
  * Hand-back: a vendor may hand back an accepted job free up to 48 h before the
    window start (VENDOR_FREE_HANDBACK_H); Dispatch reassigns it. Inside 48 h a vendor
    cancel terminates them automatically.
  * Exceptions: when the vendor picks medical emergency, bereavement, accident or
    breakdown, severe weather or unsafe drone conditions, unsafe site or no access
    caused by the client, or a Bricks app or GPS outage, the termination is paused:
    the vendor is held off auto-assign and Kam gets one email with one-tap
    "Terminate" / "Excuse" (vendor_termination_reviews). A cancel that arrives with no
    reason (an app build without the picker) is also held for Kam, never auto-terminated.
  * No substitutes: the vendor can't send someone else (guide + emails).
  * No-show: no GPS check-in by start + grace (default 30 min, so minute 31+), or a
    check-in after that. A late check-in still counts even if the job is completed;
    the vendor keeps that job, it is paid, and they are terminated once it's done.
  * Appeal: the termination notice carries a link valid for 7 days; an appeal reaches
    Kam with one-tap "Reinstate". Kam also keeps the "Undo termination" link.
Never triggered by a Bricks or client cancellation (status "cancelled" is not a vendor
action) and never for the owner account.

On a termination: vendor_members.is_active=false, onboarding_status="terminated",
terminated_at/reason, paused (so no auto-assign); a vendor_terminations ledger row
(email, phone, ID fingerprints) blocks reapplying; open and future jobs are
reassigned through Dispatch; the client hears about it only when the visit moves;
Kam gets one email with "Undo termination"; the vendor gets a short neutral notice
from help@brxs.ca with the appeal link. Completed and delivered jobs still pay out.
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
REVIEW_PREFIX = "Termination review: "
DEFAULT_FREE_HANDBACK_H = 48
APPEAL_DAYS = 7
CANCEL_REASONS = {
    "medical": "Medical emergency",
    "bereavement": "Bereavement",
    "accident": "Accident or breakdown on the way",
    "weather": "Severe weather or unsafe drone conditions",
    "client_site": "Unsafe site or no access caused by the client",
    "app_outage": "Bricks app or GPS outage",
    "other": "Something else",
}
EXCEPTION_REASONS = tuple(k for k in CANCEL_REASONS if k != "other")
# Enforcement follows IC amendment A1 ("IC-A1 v1.0", legal/final/ic-amendment-A1-v1.0.md):
# it binds a vendor from the time they accept it in the app, otherwise from
# 2026-11-16 00:00 ET (14 days' notice), and covers jobs accepted on or after that.
# Anything outside it is flagged for Kam, never auto-terminated.


def _ver(v: Any) -> str:
    return str(v or "").strip()


def binding_at(member: dict) -> Optional[datetime]:
    from ..routers.onboarding import _IC_AMENDMENT_NOTICE_BINDS, _IC_AMENDMENT_VERSION
    from .ops_autopilot import _parse_ts
    notice = _parse_ts(_IC_AMENDMENT_NOTICE_BINDS)
    if _ver(member.get("ic_amendment_version")) == _IC_AMENDMENT_VERSION:
        acc = _parse_ts(member.get("ic_amendment_accepted_at"))
        if acc:
            return min(acc, notice) if notice else acc
    return notice


def fmt_binds(member: dict) -> str:
    from .ops_autopilot import fmt_et
    b = binding_at(member)
    accepted = _ver(member.get("ic_amendment_version")) and member.get("ic_amendment_accepted_at")
    return (fmt_et(b) if b else "unknown") + (" when they accepted it" if accepted else ", not yet accepted in the app")


def job_accepted_at(job: dict) -> Optional[datetime]:
    from .ops_autopilot import _parse_ts
    acc = _parse_ts(job.get("vendor_accepted_at"))
    if acc:
        return acc
    asg = _parse_ts(job.get("assigned_at"))
    return asg + timedelta(minutes=accept_window_min()) if asg else None


def radius_set() -> bool:
    from ..shared import noshow_policy
    return (noshow_policy.CHECKIN_RADIUS_M or 0) > 0


def clause_in_force(member: dict, job: Optional[dict], trigger: str = "cancelled_after_accept") -> bool:
    """True when amendment A1 binds the vendor and they accepted this job on or after that.
    No-shows also need a check-in radius set."""
    if trigger == "no_show" and (not radius_set() or (job or {}).get("lat") is None
                                 or (job or {}).get("lng") is None):
        return False  # no geofence for this job: Kam decides
    binds, acc = binding_at(member), job_accepted_at(job or {})
    return bool(binds and acc and acc >= binds)


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
    """No check-in by start + grace, while the job still sits assigned and nothing was uploaded."""
    if job.get("status") != "assigned" or not _real_vendor(job.get("assignee_id")):
        return False
    if job.get("checked_in_at") or (job.get("uploaded_count") or 0) > 0:
        return False
    start = explicit_start(job)
    if not start or start < RULE_START:
        return False
    return start + timedelta(minutes=grace_min()) <= now <= start + timedelta(hours=12)


LATE_DONE_STATUSES = ("completed", "processing", "delivered", "qc_failed")


def late_check_in_min(job: dict) -> Optional[int]:
    """Minutes past start when the check-in came after start + grace, else None."""
    from .ops_autopilot import _parse_ts
    if not _real_vendor(job.get("assignee_id")) or job.get("status") in ("cancelled", "declined", "agent_no_show"):
        return None
    at, start = _parse_ts(job.get("checked_in_at")), explicit_start(job)
    if not at or not start or start < RULE_START:
        return None
    late = (at - start).total_seconds() / 60
    return int(late) if late > grace_min() else None


def is_late_check_in_due(job: dict, now: datetime) -> bool:
    """A late check-in terminates once the vendor has finished that job (so it's
    completed and paid), or 12 h after start if it never finishes."""
    if late_check_in_min(job) is None:
        return False
    start = explicit_start(job)
    return job.get("status") in LATE_DONE_STATUSES or now >= start + timedelta(hours=12)


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


def free_handback_h() -> int:
    try:
        return max(0, int(os.environ.get("VENDOR_FREE_HANDBACK_H") or DEFAULT_FREE_HANDBACK_H))
    except ValueError:
        return DEFAULT_FREE_HANDBACK_H


def normalize_reason(reason: Any) -> Optional[str]:
    r = str(reason or "").strip().lower()
    return r if r in CANCEL_REASONS else None


def drop_decision(job: dict, now: datetime, reason: Optional[str]) -> str:
    """free_handback | not_accepted | review_unknown_accept | review_exception |
    review_no_reason | terminate."""
    from .ops_autopilot import job_start_utc
    acc = acceptance(job, now)
    if acc == "no":
        return "not_accepted"
    start = explicit_start(job) or job_start_utc(job)
    if start and start - now >= timedelta(hours=free_handback_h()):
        return "free_handback"
    if acc == "unknown":
        return "review_unknown_accept"
    r = normalize_reason(reason)
    if r in EXCEPTION_REASONS:
        return "review_exception"
    if r is None:
        return "review_no_reason"
    return "terminate"


async def on_vendor_drop(db, job: dict, member_id: Optional[str], old_status: Optional[str],
                         new_status: Optional[str], now: Optional[datetime] = None,
                         reason: Optional[str] = None, note: Optional[str] = None) -> Optional[dict]:
    """Hook for vendor decline/un-accept. The caller still reassigns the job. Never raises."""
    try:
        if not is_cancel_after_accept(old_status, new_status) or not _real_vendor(member_id):
            return None
        now = now or datetime.now(timezone.utc)
        decision = drop_decision(job, now, reason)
        if decision == "terminate":
            return await terminate(db, member_id, job.get("id"), "cancelled_after_accept", now=now,
                                   detail=f"Reason given: {CANCEL_REASONS['other'].lower()}"
                                   + (f" ({str(note)[:200]})" if note else ""))
        if decision.startswith("review_"):
            return await hold_for_review(db, member_id, job, decision, normalize_reason(reason), note, now)
        return {"skipped": decision}
    except Exception as e:
        logger.error("on_vendor_drop failed: %s", e)
        return None


async def outage_evidence(db, job: dict, now: datetime) -> dict:
    """What our own records say about the app and GPS around this job: how many other
    vendors checked in within 3 h either side, and how many jobs in that span have no
    check-in. Lots of misses and no check-ins points to an outage."""
    from .ops_autopilot import job_start_utc, _parse_ts
    start = explicit_start(job) or job_start_utc(job) or now
    lo, hi = start - timedelta(hours=3), start + timedelta(hours=3)
    try:
        day = start.date()
        res = await db.table("jobs").select("id, assignee_id, status, checked_in_at, selected_slot_start, "
                                            "scheduled_date, time_window_start, confirmed_arrive_at")             .in_("scheduled_date", [(day - timedelta(days=1)).isoformat(), day.isoformat(), (day + timedelta(days=1)).isoformat()])             .limit(500).execute()
        rows = [r for r in (res.data or []) if str(r.get("id")) != str(job.get("id"))]
    except Exception as e:
        return {"error": type(e).__name__}
    ok = sum(1 for r in rows if (t := _parse_ts(r.get("checked_in_at"))) and lo <= t <= hi)
    missed = sum(1 for r in rows if not r.get("checked_in_at") and r.get("status") in ("assigned",)
                 and (s := job_start_utc(r)) and lo <= s <= min(hi, now))
    return {"other_check_ins": ok, "jobs_missing_check_in": missed,
            "suggests_outage": ok == 0 and missed >= 2}


async def hold_for_review(db, member_id: str, job: dict, decision: str, reason: Optional[str],
                          note: Optional[str], now: datetime, trigger: str = "cancelled_after_accept") -> dict:
    """Pause the termination: keep the vendor off auto-assign and ask Kam to Terminate or Excuse."""
    from . import ops_autopilot as ops
    jid = job.get("id")
    if not await ops.once(db, f"term_review:{member_id}:{jid}", {"decision": decision}):
        return {"skipped": "already_held"}
    m = await _one(db, "vendor_members", id=member_id)
    if not m or m.get("terminated_at"):
        return {"skipped": "missing_or_terminated"}
    evidence = await outage_evidence(db, job, now) if reason == "app_outage" else {}
    row = {"member_id": member_id, "job_id": jid, "kind": "cancel_exception", "decision": decision,
           "reason_code": reason, "note": (str(note)[:1000] if note else None), "evidence": evidence,
           "status": "pending"}
    review_id = None
    try:
        ins = await db.table("vendor_termination_reviews").insert(row).execute()
        review_id = ((ins.data or [{}])[0]).get("id")
    except Exception as e:
        logger.error("termination review insert failed: %s", type(e).__name__)
    iso = now.isoformat()
    pause = decision != "review_clause_not_accepted" and clause_in_force(m, job, trigger)
    if pause and not m.get("paused_at"):
        await db.table("vendor_members").update({"paused_at": iso, "paused_reason": REVIEW_PREFIX + "late cancel",
                                                 "updated_at": iso}).eq("id", member_id).execute()
    name = m.get("name") or "A vendor"
    why = {"review_exception": f"Reason given: {CANCEL_REASONS.get(reason or '', reason)}.",
           "review_no_reason": "No reason was given (their app didn't ask for one).",
           "review_unknown_accept": "The job was assigned before acceptance was recorded.",
           "review_no_geofence": "The property has no map location, so the 100 m check-in can't be verified.",
           "review_clause_not_accepted": "Amendment IC-A1 v1.0 didn't bind them yet when they accepted this job "
                                         f"(binds {fmt_binds(m)}), so the termination rule doesn't apply automatically.",
           }[decision]
    what = (f"cancelled {ops._job_line(job)} inside {free_handback_h()} hours of the start"
            if trigger == "cancelled_after_accept" else
            f"was a no-show on {ops._job_line(job)}" + (f" ({note})" if note else ""))
    lines = [f"{name} ({m.get('email') or 'no email'}) {what}.", why]
    if note and trigger == "cancelled_after_accept":
        lines.append(f"Their note: {str(note)[:300]}")
    if evidence and "error" not in evidence:
        lines.append(f"Our records: {evidence['other_check_ins']} other check-ins worked within 3 hours of this job; "
                     f"{evidence['jobs_missing_check_in']} jobs in that span have no check-in."
                     + (" That looks like an outage." if evidence.get("suggests_outage") else " No sign of an outage."))
    lines.append("They're off auto-assign until you decide." if pause else "They're still active until you decide.")
    acts = [("terminate_vendor", {"member_id": member_id, "job_id": jid, "review_id": review_id, "trigger": trigger}, "Terminate"),
            ("excuse_vendor", {"member_id": member_id, "review_id": review_id}, "Excuse")]
    if trigger == "no_show" and job.get("status") == "assigned":
        acts.insert(1, ("reassign", {"job_id": jid}, "Reassign the job"))
    title = "Late cancel to review" if trigger == "cancelled_after_accept" else "No-show to review"
    sent = await ops.send_kam(db, f"term_review_alert:{member_id}:{jid}", f"{title}: {name}", lines, acts,
                              data={"member_id": member_id, "job_id": jid, "review_id": review_id})
    # Vendor-facing, not the termination notice itself: behind OPS_AUTOPILOT_EXTERNAL_SENDS.
    if m.get("email") and trigger == "cancelled_after_accept" and decision != "review_clause_not_accepted":
        await ops.send_external(m["email"], "We got your cancellation", "We got your cancellation",
                                f"Hi {_first_name(m) or 'there'},\n\nWe've reassigned the job and Bricks will review the "
                                "reason you gave. You won't get new jobs until then. We'll email you either way.\n\nBricks")
    return {"held": True, "decision": decision, "review_id": review_id, "kam_alerted": sent}


async def _set_review(db, review_id: Optional[str], status: str, by: str) -> None:
    if not review_id:
        return
    try:
        await db.table("vendor_termination_reviews").update({
            "status": status, "decided_at": datetime.now(timezone.utc).isoformat(), "decided_by": by,
        }).eq("id", review_id).execute()
    except Exception as e:
        logger.warning("review update failed: %s", type(e).__name__)


async def excuse(db, member_id: str, review_id: Optional[str], by: str = "kam-one-tap") -> dict:
    """Kam excused the late cancel: lift the review pause. No termination."""
    m = await _one(db, "vendor_members", id=member_id)
    if not m:
        return {"ok": False, "error": "not_found"}
    if str(m.get("paused_reason") or "").startswith(REVIEW_PREFIX):
        await db.table("vendor_members").update({"paused_at": None, "paused_reason": None,
                                                 "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", member_id).execute()
    await _set_review(db, review_id, "excused", by)
    return {"ok": True, "member_id": member_id, "excused": True}


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


def appeal_url(member_id: str, now: Optional[datetime] = None) -> str:
    from .ops_autopilot import api_base, sign_action
    ts = (now or datetime.now(timezone.utc)).timestamp()
    tok = sign_action("vendor_appeal", {"member_id": member_id}, ttl_s=APPEAL_DAYS * 86400, now=ts)
    return f"{api_base()}/api/ops/appeal?t={tok}"


def vendor_notice(member: dict, link: str = "", trigger: str = "no_show") -> tuple[str, str]:
    hi = f"Hi {_first_name(member)}," if _first_name(member) else "Hi,"
    appeal = (f"If you think this was a mistake, or an emergency or one of the other exceptions applied, appeal within "
              f"{APPEAL_DAYS} days by emailing help@brxs.ca" + (f" or here: {link}" if link else "") + ".\n\n")
    why = (f"you cancelled a booked job within {free_handback_h()} hours of its start"
           if trigger == "cancelled_after_accept" else
           f"a booked job wasn't checked in within {grace_min()} minutes of its start")
    body = (f"{hi}\n\nYour Bricks vendor account has been closed because {why}. "
            "Under amendment A1 of your contractor agreement (IC-A1 v1.0), that ends the agreement.\n\n"
            "Payment for jobs you completed and delivered will still be sent as usual.\n\n"
            f"{appeal}Bricks")
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
async def terminate(db, member_id: str, job_id: Optional[str], trigger: str, now: Optional[datetime] = None,
                    detail: Optional[str] = None, keep_job: bool = False, force: bool = False) -> dict:
    """Idempotent: a vendor already terminated is left as is. Never raises.
    keep_job: the trigger job stays with the vendor (late check-in: they did the work).
    force: Kam's own one-tap decision; skips the agreement-version gate."""
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
        if not force:
            trig = await _one(db, "jobs", id=job_id) if job_id else None
            if not clause_in_force(m, trig, trigger):
                no_fence = trigger == "no_show" and trig is not None and clause_in_force(
                    m, {**trig, "lat": 0, "lng": 0}, trigger)
                held = await hold_for_review(db, member_id, trig or {"id": job_id},
                                             "review_no_geofence" if no_fence else "review_clause_not_accepted",
                                             None, detail, now, trigger=trigger)
                return {**out, "skipped": "clause_not_accepted", **held}
        if not await ops.once(db, f"terminate:{member_id}:{job_id or ''}", {"trigger": trigger}):
            return {**out, "skipped": "already_handled"}
        iso = now.isoformat()
        reason = TRIGGER_TEXT[trigger] + (f" ({detail})" if detail else "")
        prior = {k: m.get(k) for k in ("is_active", "onboarding_status", "paused_at", "paused_reason")}
        if str(prior.get("paused_reason") or "").startswith(REVIEW_PREFIX):
            prior["paused_at"] = prior["paused_reason"] = None  # the review pause ends with the decision
        try:
            dres = await db.table("vendor_documents").select("doc_type, validation_result").eq("member_id", member_id).execute()
            ids = doc_id_hashes(dres.data or [])
        except Exception:
            ids = []
        await db.table("vendor_members").update({
            "is_active": False, "onboarding_status": "terminated", "terminated_at": iso,
            "terminated_reason": reason, "paused_at": iso,
            "paused_reason": PREFIX + reason, "updated_at": iso,
        }).eq("id", member_id).execute()
        # Open + future jobs go back out through Dispatch (excluding this vendor).
        jobs = []
        try:
            jr = await db.table("jobs").select("*").eq("assignee_id", member_id).in_("status", list(OPEN_STATUSES)).limit(200).execute()
            jobs = jr.data or []
        except Exception as e:
            logger.error("termination job load failed: %s", type(e).__name__)
        if keep_job:
            jobs = [j for j in jobs if str(j.get("id")) != str(job_id)]
        elif job_id and not any(str(j.get("id")) == str(job_id) for j in jobs):
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
        try:
            link = appeal_url(member_id, now)
        except Exception:
            link = ""
        subj, body = vendor_notice(m, link, trigger)
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
    try:
        await db.table("vendor_termination_reviews").update({"status": "reinstated", "decided_at": now, "decided_by": by}) \
            .eq("member_id", member_id).eq("kind", "appeal").eq("status", "pending").execute()
    except Exception:
        pass
    return {"ok": True, "member_id": member_id, "restored": patch, "undone": len(rows)}


# ------------------------------------------------------------------ appeal

async def appeal_open(db, member_id: str, now: Optional[datetime] = None) -> Optional[dict]:
    """The vendor's live termination if it's still inside the appeal window."""
    from .ops_autopilot import _parse_ts
    now = now or datetime.now(timezone.utc)
    m = await _one(db, "vendor_members", id=member_id)
    if not m or not m.get("terminated_at"):
        return None
    at = _parse_ts(m.get("terminated_at"))
    if not at or now > at + timedelta(days=APPEAL_DAYS):
        return None
    return m


async def submit_appeal(db, member_id: str, message: str, now: Optional[datetime] = None) -> dict:
    """Send the appeal to Kam with one-tap Reinstate. One appeal per termination."""
    from . import ops_autopilot as ops
    now = now or datetime.now(timezone.utc)
    m = await appeal_open(db, member_id, now)
    if not m:
        return {"ok": False, "error": "closed"}
    msg = (message or "").strip()[:2000]
    if len(msg) < 5:
        return {"ok": False, "error": "empty"}
    if not await ops.once(db, f"appeal:{member_id}:{m.get('terminated_at')}", {"len": len(msg)}):
        return {"ok": False, "error": "already_sent"}
    try:
        await db.table("vendor_termination_reviews").insert({
            "member_id": member_id, "kind": "appeal", "decision": "appeal", "note": msg, "status": "pending"}).execute()
    except Exception as e:
        logger.error("appeal insert failed: %s", type(e).__name__)
    name = m.get("name") or "A vendor"
    sent = await ops.send_kam(
        db, f"appeal_alert:{member_id}:{m.get('terminated_at')}", f"Termination appeal: {name}",
        [f"{name} ({m.get('email') or 'no email'}) is appealing. Closed {ops.fmt_et(ops._parse_ts(m.get('terminated_at')))} "
         f"because they {m.get('terminated_reason') or 'broke a vendor rule'}.", f"Their appeal: {msg}"],
        [("undo_termination", {"member_id": member_id}, "Reinstate"), ("dismiss", {"member_id": member_id}, "Keep closed")],
        data={"member_id": member_id})
    # Vendor-facing acknowledgement (not the termination notice): behind OPS_AUTOPILOT_EXTERNAL_SENDS.
    if m.get("email"):
        await ops.send_external(m["email"], "We got your appeal", "We got your appeal",
                                f"Hi {_first_name(m) or 'there'},\n\nBricks will review your appeal and email you.\n\nBricks")
    return {"ok": True, "kam_alerted": sent}
