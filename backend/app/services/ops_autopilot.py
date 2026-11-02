"""Ops autopilot: the recurring checks that used to need someone watching.

Kam, 2026-11-02: every recurring business process must run without the
assistant. Two entry points (see routers/ops_autopilot.py):

* sweep()  every 15 minutes from Supabase pg_cron:
    - a declined job goes back out to another vendor right away
    - a job left in pending_assignment for 30+ minutes is retried
    - an assigned vendor who has not checked in 30 minutes after the start
      is flagged to Kam with one-tap Reassign / Dismiss links
    - missing uploads: vendor reminder 6h after the start (external sends
      flag), Kam escalation at 24h
    - jobs stuck in processing or qc_failed are escalated to Kam
    - stuck editor uploads (last 3 days) go through the existing reconcile
      (Autoenhance timeout -> in-house fallback -> finalize -> delivery)
    - unpaid delivered jobs retry the vendor payout (3 tries, 6h apart),
      then escalate to Kam
    - day-before reminders to the client and vendor (external sends flag)
* daily()  once a day from the Vercel cron:
    - health: live URLs, sweep heartbeat, pg_cron failures, stale escalations
    - nightly logical backup to the private Supabase Storage bucket db-backups
    - one email to Kam ONLY when something is broken or needs a decision
      (probation reviews, open escalations), each with one-tap links

Every alert is sent once: a key in ops_autopilot_events marks it done.
Old rows are never touched: only jobs scheduled in the last 3 days (or
delivered in the last 14) are looked at, so historic test jobs stay quiet.
Vendor- and client-facing emails stay off until OPS_AUTOPILOT_EXTERNAL_SENDS=1.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import html as _html
import io
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

KAM_EMAIL_DEFAULT = "akashorter@gmail.com"
JOB_LOOKBACK_DAYS = 3
PAYOUT_LOOKBACK_DAYS = 14
NOSHOW_GRACE_MIN = 30
PENDING_RETRY_MIN = 30
UPLOAD_REMIND_H = 6
UPLOAD_ESCALATE_H = 24
STUCK_PROCESSING_H = 4
PAYOUT_MIN_AGE_H = 2
PAYOUT_RETRY_SPACING_H = 6
PAYOUT_MAX_TRIES = 3
SWEEP_STALE_MIN = 60
BACKUP_KEEP_DAYS = 14
BACKUP_BUCKET = "db-backups"
ACTION_TTL_S = 7 * 24 * 3600

HEALTH_URLS = [
    "https://www.brxs.ca/",
    "https://app.brxs.ca/",
    "https://go.brxs.ca/",
    "https://dispatch-api.brxs.ca/health",
    "https://api.brxs.ca/health",
    "https://booking-api.brxs.ca/health",
]


def kam_email() -> str:
    return (os.environ.get("OPS_ALERT_EMAIL") or os.environ.get("ADMIN_NOTIFICATION_EMAIL") or KAM_EMAIL_DEFAULT).strip()


def external_sends_enabled() -> bool:
    return (os.environ.get("OPS_AUTOPILOT_EXTERNAL_SENDS") or "").strip() == "1"


def api_base() -> str:
    return (os.environ.get("DISPATCH_PUBLIC_URL") or "https://dispatch-api.brxs.ca").rstrip("/")


# ---------------------------------------------------------------- time helpers

def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip().replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def job_start_utc(job: dict) -> Optional[datetime]:
    """Booked start of a job in UTC (scheduled_date + start time in the job's zone)."""
    if job.get("selected_slot_start"):
        dt = _parse_ts(job["selected_slot_start"])
        if dt:
            return dt.astimezone(timezone.utc)
    ymd = str(job.get("scheduled_date") or "")[:10]
    try:
        d = date.fromisoformat(ymd)
    except ValueError:
        return None
    hhmm = ""
    for key in ("confirmed_arrive_at", "time_window_start"):
        raw = str(job.get(key) or "")
        if len(raw) >= 5 and raw[2] == ":" and "T" not in raw:
            hhmm = raw[:5]
            break
    if not hhmm:
        rest = str(job.get("scheduled_date") or "")[11:16]
        hhmm = rest if len(rest) == 5 and rest[2] == ":" and rest != "00:00" else "12:00"
    try:
        from ..shared.job_timezone import resolve_job_iana
        tz = ZoneInfo(resolve_job_iana(job))
    except Exception:
        tz = ZoneInfo("America/Toronto")
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
    except ValueError:
        h, m = 12, 0
    return datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(timezone.utc)


def fmt_et(dt: Optional[datetime]) -> str:
    if not dt:
        return "unknown time"
    return dt.astimezone(ZoneInfo("America/Toronto")).strftime("%a %b %-d, %-I:%M %p ET")


# ---------------------------------------------------------------- one-tap links

def _action_key() -> bytes:
    raw = (os.environ.get("OPS_ACTION_SECRET") or os.environ.get("CRON_SECRET") or "").strip()
    if not raw:
        raise RuntimeError("OPS_ACTION_SECRET/CRON_SECRET not set")
    return hashlib.sha256(("ops-action:" + raw).encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_action(action: str, params: dict, ttl_s: int = ACTION_TTL_S, now: Optional[float] = None) -> str:
    payload = {"a": action, "p": params, "exp": int((now or time.time()) + ttl_s)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_action_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_action(token: str, now: Optional[float] = None) -> Optional[dict]:
    try:
        body, sig = (token or "").split(".", 1)
        want = _b64(hmac.new(_action_key(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want, sig):
            return None
        payload = json.loads(_unb64(body))
        if int(payload.get("exp", 0)) < (now or time.time()):
            return None
        return payload
    except Exception:
        return None


def action_url(action: str, params: dict) -> str:
    return f"{api_base()}/api/ops/act?t={sign_action(action, params)}"


ACTION_LABELS = {
    "reassign": "Reassign to another vendor",
    "dismiss": "Dismiss",
    "retry_payout": "Retry the payout now",
    "probation_approve": "Approve this probation job",
    "probation_fail": "Fail this probation job (pauses the vendor)",
    "resolve_escalation": "Mark resolved",
    "retry_processing": "Retry editing and delivery",
}


# ---------------------------------------------------------------- once markers

async def once(db, key: str, data: Optional[dict] = None) -> bool:
    """True the first time a key is claimed; False after that (or on error)."""
    try:
        res = await db.table("ops_autopilot_events").select("key").eq("key", key).limit(1).execute()
        if res.data:
            return False
        await db.table("ops_autopilot_events").insert({"key": key, "data": data or {}}).execute()
        return True
    except Exception as e:
        logger.warning("ops once(%s) failed: %s", key, e)
        return False


async def seen(db, key: str) -> bool:
    try:
        res = await db.table("ops_autopilot_events").select("key").eq("key", key).limit(1).execute()
        return bool(res.data)
    except Exception:
        return True  # unsure -> do not act twice


async def events_like(db, prefix: str) -> list[dict]:
    try:
        res = await db.table("ops_autopilot_events").select("key, created_at").like("key", prefix + "%").execute()
        return res.data or []
    except Exception:
        return []


# ---------------------------------------------------------------- emails

def _kam_html(title: str, lines: list[str], actions: list[tuple[str, str]]) -> str:
    body = "".join(f"<p style='margin:0 0 8px;color:#333;font-size:15px;line-height:1.45'>{_html.escape(l)}</p>" for l in lines)
    btns = "".join(
        f"<a href='{_html.escape(url)}' style='display:inline-block;margin:4px 8px 4px 0;padding:10px 14px;"
        f"background:{'#d2542b' if i == 0 else '#14110E'};color:#fff;border-radius:8px;text-decoration:none;"
        f"font-size:14px;font-weight:600'>{_html.escape(label)}</a>"
        for i, (label, url) in enumerate(actions)
    )
    return (
        "<!DOCTYPE html><html><body style='font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "background:#f4f1eb;padding:24px'><div style='max-width:520px;margin:0 auto;background:#fff;border-radius:12px;"
        "padding:24px'>"
        f"<h1 style='font-size:19px;margin:0 0 14px;color:#14110E'>{_html.escape(title)}</h1>{body}"
        f"<div style='margin-top:14px'>{btns}</div>"
        "<p style='margin-top:18px;font-size:12px;color:#888'>Sent by the Bricks ops autopilot. Each button opens a "
        "confirm page first; links expire in 7 days.</p></div></body></html>"
    )


async def send_kam(db, key: str, title: str, lines: list[str], actions: list[tuple] = (),
                   ntype: str = "ops_autopilot", data: Optional[dict] = None) -> bool:
    """Escalate to Kam once per key: in-app notification + email with one-tap links."""
    if key and not await once(db, key, {"title": title, **(data or {})}):
        return False
    links = [((x[2] if len(x) > 2 else ACTION_LABELS.get(x[0], x[0])), action_url(x[0], x[1])) for x in actions]
    try:
        from .notifications import create_notification
        await create_notification(
            title, "\n".join(lines), ntype, {**(data or {}), "actions": [x[0] for x in actions]},
            email_to=kam_email(), email_subject=f"[Bricks] {title}",
            email_html=_kam_html(title, lines, links),
        )
        return True
    except Exception as e:
        logger.error("ops escalation send failed (%s): %s", key, e)
        return False


async def send_external(to: str, subject: str, title: str, body: str) -> bool:
    if not to or not external_sends_enabled():
        return False
    from .notifications import _build_html_email, send_email_via_resend
    return await send_email_via_resend(to=to, subject=subject, html=_build_html_email(title, body))


async def _member(db, member_id: str) -> dict:
    if not member_id or member_id == "default_user":
        return {}
    try:
        res = await db.table("vendor_members").select("id, name, email, phone").eq("id", member_id).limit(1).execute()
        return (res.data or [{}])[0]
    except Exception:
        return {}


def _job_line(job: dict) -> str:
    return f"{job.get('package_name') or job.get('job_type') or 'Job'} at {job.get('address') or 'unknown address'}"


# ---------------------------------------------------------------- actions

async def reassign_job(db, job_id: str, reason: str) -> dict:
    from .vendor_selection import assign_vendor_for_job
    res = await db.table("jobs").select("id, status, assignee_id").eq("id", job_id).limit(1).execute()
    job = (res.data or [None])[0]
    if not job:
        return {"ok": False, "error": "job_not_found"}
    if job.get("status") in ("delivered", "cancelled", "completed", "processing", "in_progress"):
        return {"ok": False, "error": f"job is {job.get('status')}"}
    prev = job.get("assignee_id")
    now = datetime.now(timezone.utc).isoformat()
    await db.table("jobs").update({"status": "pending_assignment", "assignee_id": "default_user", "updated_at": now}).eq("id", job_id).execute()
    try:
        from ..shared.audit import write_audit_event  # type: ignore
    except Exception:
        write_audit_event = None
    if write_audit_event:
        try:
            await write_audit_event("ops_autopilot", "job", job_id, "auto_reassign", details={"from": prev, "reason": reason})
        except Exception:
            pass
    new_id = await assign_vendor_for_job(job_id, exclude_member_id=prev if prev and prev != "default_user" else None)
    return {"ok": bool(new_id), "job_id": job_id, "from": prev, "to": new_id}


async def run_action(db, payload: dict) -> dict:
    a, p = payload.get("a"), payload.get("p") or {}
    if a == "reassign":
        return await reassign_job(db, p["job_id"], "kam_one_tap")
    if a == "dismiss":
        return {"ok": True, "dismissed": p}
    if a == "retry_payout":
        from .payout_service import trigger_payout_for_job
        return {"ok": True, "result": await trigger_payout_for_job(db, p["job_id"])}
    if a in ("probation_approve", "probation_fail"):
        from .vendor_vetting import review_probation_job
        out = await review_probation_job(db, p["member_id"], p["job_id"],
                                         "approved" if a == "probation_approve" else "failed",
                                         reviewed_by="kam-one-tap")
        return {"ok": bool(out), "result": out}
    if a == "resolve_escalation":
        now = datetime.now(timezone.utc).isoformat()
        await db.table("ops_escalations").update({"status": "resolved", "resolved_at": now, "resolved_by": "kam-one-tap"}).eq("id", p["id"]).execute()
        return {"ok": True}
    if a == "retry_processing":
        from .post_production import process_job_assets
        await process_job_assets(p["job_id"])
        return {"ok": True}
    return {"ok": False, "error": "unknown_action"}


# ---------------------------------------------------------------- sweep

async def _recent_jobs(db, now: datetime) -> list[dict]:
    since = (now - timedelta(days=JOB_LOOKBACK_DAYS)).date().isoformat()
    res = await db.table("jobs").select("*").gte("scheduled_date", since).limit(500).execute()
    return res.data or []


async def sweep(db, now: Optional[datetime] = None, *, run_reconcile: bool = True, run_payouts: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    out: dict[str, list] = {k: [] for k in (
        "reassigned", "assign_retried", "noshow_flagged", "upload_reminded", "upload_escalated",
        "stuck_escalated", "payout_retried", "payout_escalated", "reminded", "errors")}
    jobs = await _recent_jobs(db, now)
    for job in jobs:
        jid, st = job.get("id"), job.get("status")
        start = job_start_utc(job)
        try:
            # 1. declined -> straight back out (excluding the decliner)
            if st == "declined":
                prev = job.get("assignee_id") or ""
                if start and start > now:
                    if await once(db, f"reassign:{jid}:{prev}"):
                        out["reassigned"].append(await reassign_job(db, jid, "vendor_declined"))
                else:
                    await send_kam(db, f"declined_late:{jid}", "A vendor declined a job that has already started",
                                   [_job_line(job), f"Booked for {fmt_et(start)}."],
                                   [("reassign", {"job_id": jid}), ("dismiss", {"job_id": jid})], data={"job_id": jid})
                continue
            # 2. stuck in pending_assignment
            if st == "pending_assignment" and start and start > now:
                upd = _parse_ts(job.get("updated_at")) or _parse_ts(job.get("created_at"))
                if upd and now - upd >= timedelta(minutes=PENDING_RETRY_MIN) and await once(db, f"assign_retry:{jid}"):
                    from .vendor_selection import assign_vendor_for_job
                    out["assign_retried"].append({"job_id": jid, "to": await assign_vendor_for_job(jid)})
                continue
            if not start:
                continue
            # 3. vendor no-show (no check-in, not started)
            if st == "assigned" and not job.get("checked_in_at") and now >= start + timedelta(minutes=NOSHOW_GRACE_MIN) \
                    and now <= start + timedelta(hours=12):
                m = await _member(db, job.get("assignee_id"))
                if await send_kam(db, f"noshow:{jid}", "Vendor has not checked in",
                                  [_job_line(job), f"Booked for {fmt_et(start)}. {m.get('name') or 'The vendor'} "
                                   f"({m.get('phone') or 'no phone'}) has not checked in {NOSHOW_GRACE_MIN} minutes after the start."],
                                  [("reassign", {"job_id": jid}), ("dismiss", {"job_id": jid})], data={"job_id": jid}):
                    out["noshow_flagged"].append(jid)
                continue
            # 4. uploads missing after the shoot
            uploaded, wanted = job.get("uploaded_count") or 0, max(job.get("deliverables_count") or 1, 1)
            shot = st in ("in_progress", "completed") or (st == "assigned" and job.get("checked_in_at"))
            if shot and uploaded < wanted:
                if now >= start + timedelta(hours=UPLOAD_ESCALATE_H):
                    if await send_kam(db, f"upload_esc:{jid}", "Uploads are late",
                                      [_job_line(job), f"Shot {fmt_et(start)}; {uploaded} of {wanted} files uploaded after {UPLOAD_ESCALATE_H}h."],
                                      [("reassign", {"job_id": jid}), ("dismiss", {"job_id": jid})], data={"job_id": jid}):
                        out["upload_escalated"].append(jid)
                elif now >= start + timedelta(hours=UPLOAD_REMIND_H) and external_sends_enabled():
                    m = await _member(db, job.get("assignee_id"))
                    if m.get("email") and await once(db, f"upload_remind:{jid}"):
                        await send_external(m["email"], "Reminder: upload your files", "Your upload is due",
                                            f"Hi {m.get('name') or ''},\nPlease upload the files for {_job_line(job)} in the Bricks app "
                                            f"(go.brxs.ca). The client is waiting on them.")
                        out["upload_reminded"].append(jid)
                continue
            # 5. stuck in editing / QC
            if st in ("processing", "qc_failed"):
                upd = _parse_ts(job.get("updated_at")) or start
                if st == "qc_failed" or (upd and now - upd >= timedelta(hours=STUCK_PROCESSING_H)):
                    if await send_kam(db, f"stuck:{jid}:{st}", "A job is held in editing or QC",
                                      [_job_line(job), f"Status {st} since {fmt_et(upd)}. The client has not got their files."],
                                      [("retry_processing", {"job_id": jid}), ("dismiss", {"job_id": jid})], data={"job_id": jid}):
                        out["stuck_escalated"].append(jid)
                continue
            # 6. day-before reminders (client + vendor)
            if st == "assigned" and external_sends_enabled() and timedelta(hours=18) <= start - now <= timedelta(hours=30):
                if await once(db, f"remind:{jid}"):
                    when = fmt_et(start) if resolve_tz_is_toronto(job) else start.astimezone(ZoneInfo(_iana(job))).strftime("%a %b %-d, %-I:%M %p")
                    m = await _member(db, job.get("assignee_id"))
                    if job.get("agent_email"):
                        await send_external(job["agent_email"], "Reminder: your Bricks visit is tomorrow", "See you tomorrow",
                                            f"{_job_line(job)} is booked for {when}. Reply to this email or write help@brxs.ca to change it.")
                    if m.get("email"):
                        await send_external(m["email"], "Reminder: you have a Bricks job tomorrow", "Job tomorrow",
                                            f"{_job_line(job)} at {when}. Open go.brxs.ca for the checklist and access notes.")
                    out["reminded"].append(jid)
        except Exception as e:
            logger.error("ops sweep job %s failed: %s", jid, e)
            out["errors"].append({"job_id": jid, "error": str(e)[:200]})

    if run_reconcile:
        try:
            out["reconcile"] = await _reconcile(now)
        except Exception as e:
            out["errors"].append({"step": "reconcile", "error": str(e)[:200]})
    if run_payouts:
        try:  # probation payout holds: release / block on the same 15-min cadence
            from .probation_hold import run_probation_sweep
            pr = await run_probation_sweep(db, now=now)
            out["probation"] = [r for r in pr.get("results", []) if r.get("action") != "wait"]
        except Exception as e:
            out["errors"].append({"step": "probation", "error": str(e)[:200]})
        try:
            await _payout_retries(db, now, out)
        except Exception as e:
            out["errors"].append({"step": "payouts", "error": str(e)[:200]})
    return out


def _iana(job: dict) -> str:
    try:
        from ..shared.job_timezone import resolve_job_iana
        return resolve_job_iana(job)
    except Exception:
        return "America/Toronto"


def resolve_tz_is_toronto(job: dict) -> bool:
    return _iana(job) == "America/Toronto"


async def _reconcile(now: datetime) -> dict:
    keys = [k.strip() for k in (os.environ.get("INTERNAL_API_KEYS") or "").split(",") if k.strip()]
    if not keys:
        return {"skipped": "no_internal_key"}
    from ..routers.post_production import reconcile_stuck_uploads
    since = (now - timedelta(days=JOB_LOOKBACK_DAYS)).date().isoformat()
    res = await reconcile_stuck_uploads(x_internal_api_key=keys[0], since=since)
    return {k: v for k, v in (res or {}).items() if isinstance(v, (int, str, bool))}


async def _payout_retries(db, now: datetime, out: dict) -> None:
    since = (now - timedelta(days=PAYOUT_LOOKBACK_DAYS)).date().isoformat()
    res = await db.table("jobs").select("id, status, payout_status, updated_at, address, package_name, assignee_id").eq(
        "status", "delivered").gte("updated_at", since).limit(200).execute()
    for job in res.data or []:
        jid = job["id"]
        if (job.get("payout_status") or "") == "paid":
            continue
        upd = _parse_ts(job.get("updated_at"))
        if not upd or now - upd < timedelta(hours=PAYOUT_MIN_AGE_H):
            continue
        pres = await db.table("payouts").select("status").eq("job_id", jid).execute()
        if any((r.get("status") or "") in ("paid", "held") for r in (pres.data or [])):
            continue  # "held" = probation hold; released by the probation sweep, not retried
        tries = await events_like(db, f"payout_retry:{jid}:")
        last = max((_parse_ts(t.get("created_at")) for t in tries), default=None)
        if len(tries) >= PAYOUT_MAX_TRIES:
            if await send_kam(db, f"payout_esc:{jid}", "A vendor payout keeps failing",
                              [_job_line(job), f"{PAYOUT_MAX_TRIES} automatic tries failed. Usually the vendor has not finished "
                               "Stripe payout setup, or the Stripe balance is short."],
                              [("retry_payout", {"job_id": jid}), ("dismiss", {"job_id": jid})], data={"job_id": jid}):
                out["payout_escalated"].append(jid)
            continue
        if last and now - last < timedelta(hours=PAYOUT_RETRY_SPACING_H):
            continue
        if not await once(db, f"payout_retry:{jid}:{len(tries) + 1}"):
            continue
        from .payout_service import trigger_payout_for_job
        r = await trigger_payout_for_job(db, jid)
        out["payout_retried"].append({"job_id": jid, "status": (r or {}).get("status") or (r or {}).get("error") or (r or {}).get("skipped")})


# ---------------------------------------------------------------- daily

async def _heartbeat(db, kind: str, ok: bool, summary: dict) -> None:
    try:
        await db.table("ops_autopilot_runs").insert({"kind": kind, "ok": ok, "summary": summary}).execute()
    except Exception as e:
        logger.warning("ops heartbeat insert failed: %s", e)


async def record_run(db, kind: str, ok: bool, summary: dict) -> None:
    await _heartbeat(db, kind, ok, json.loads(json.dumps(summary, default=str)))


async def check_urls(urls: list[str] = HEALTH_URLS) -> list[str]:
    bad = []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for u in urls:
            try:
                r = await client.get(u)
                if r.status_code >= 500 or r.status_code == 404:
                    bad.append(f"{u} returned {r.status_code}")
            except Exception as e:
                bad.append(f"{u} unreachable ({type(e).__name__})")
    return bad


def _pg_rows(sql: str, args: tuple = ()) -> Optional[list[tuple]]:
    from ..shared.pg_fallback import _pg_connect
    conn = _pg_connect()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def pg_cron_failures(hours: int = 26) -> Optional[list[str]]:
    rows = _pg_rows(
        "select j.jobname, d.status, left(coalesce(d.return_message,''),160), d.start_time "
        "from cron.job_run_details d join cron.job j on j.jobid=d.jobid "
        "where d.start_time > now() - make_interval(hours => %s) and d.status <> 'succeeded' "
        "order by d.start_time desc limit 20", (hours,))
    if rows is None:
        return None
    return [f"pg_cron {r[0]}: {r[1]} at {fmt_et(r[3])} ({r[2]})" for r in rows]


def sweep_secret_from_vault() -> Optional[str]:
    rows = _pg_rows("select decrypted_secret from vault.decrypted_secrets where name = 'ops_cron_secret' limit 1")
    return rows[0][0] if rows else None


async def health(db, now: datetime) -> list[str]:
    problems = await check_urls()
    try:
        res = await db.table("ops_autopilot_runs").select("created_at, ok").eq("kind", "sweep").order("created_at", desc=True).limit(1).execute()
        last = (res.data or [None])[0]
        ts = _parse_ts(last.get("created_at")) if last else None
        if not ts or now - ts > timedelta(minutes=SWEEP_STALE_MIN):
            problems.append(f"The 15-minute ops sweep has not run since {fmt_et(ts) if ts else 'ever'}.")
        elif last and not last.get("ok"):
            problems.append("The last ops sweep reported errors.")
    except Exception as e:
        problems.append(f"Could not read ops sweep runs ({type(e).__name__}).")
    try:
        fails = pg_cron_failures()
        if fails is None:
            problems.append("Could not connect to Postgres to read pg_cron runs.")
        else:
            problems.extend(fails)
    except Exception as e:
        problems.append(f"Could not read pg_cron runs ({type(e).__name__}).")
    return problems


async def pending_decisions(db, now: datetime) -> list[tuple[str, list[tuple[str, dict]]]]:
    items: list[tuple[str, list[tuple[str, dict]]]] = []
    try:
        res = await db.table("vendor_probation_reviews").select("member_id, job_id, created_at").eq("status", "pending").limit(20).execute()
        for r in res.data or []:
            m = await _member(db, r["member_id"])
            items.append((f"Probation review: {m.get('name') or r['member_id']}, job {r['job_id'][:8]}",
                          [("probation_approve", {"member_id": r["member_id"], "job_id": r["job_id"]}),
                           ("probation_fail", {"member_id": r["member_id"], "job_id": r["job_id"]})]))
    except Exception as e:
        logger.warning("probation digest failed: %s", e)
    try:
        cutoff = (now - timedelta(hours=24)).isoformat()
        res = await db.table("ops_escalations").select("id, job_id, escalation_type, created_at").eq("status", "open").gte(
            "created_at", (now - timedelta(days=30)).isoformat()).lte("created_at", cutoff).limit(20).execute()
        for r in res.data or []:
            acts = [("resolve_escalation", {"id": r["id"]})]
            if r.get("job_id"):
                acts.insert(0, ("reassign", {"job_id": r["job_id"]}))
            items.append((f"Open escalation: {r.get('escalation_type')} on job {str(r.get('job_id'))[:8]} since {fmt_et(_parse_ts(r['created_at']))}", acts))
    except Exception as e:
        logger.warning("escalation digest failed: %s", e)
    return items


def _storage_headers() -> tuple[str, dict]:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / service role key not set")
    return url, {"Authorization": f"Bearer {key}", "apikey": key}


BACKUP_SCHEMAS = ("public",)
BACKUP_EXTRA_TABLES = ("auth.users", "auth.identities")


def dump_database() -> tuple[bytes, dict]:
    """Logical backup: every public table (+ auth users) as JSON lines, gzipped."""
    from ..shared.pg_fallback import _pg_connect
    conn = _pg_connect()
    if conn is None:
        raise RuntimeError("no Postgres connection")
    counts: dict[str, int] = {}
    buf = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz, conn.cursor() as cur:
            cur.execute("select table_schema||'.'||table_name from information_schema.tables "
                        "where table_schema = any(%s) and table_type='BASE TABLE' order by 1", (list(BACKUP_SCHEMAS),))
            tables = [r[0] for r in cur.fetchall()] + list(BACKUP_EXTRA_TABLES)
            for t in tables:
                schema, name = t.split(".", 1)
                ident = f'"{schema}"."{name}"'
                try:
                    cur.execute(f"select row_to_json(x)::text from {ident} x")
                except Exception as e:
                    counts[t] = -1
                    logger.warning("backup skip %s: %s", t, e)
                    continue
                n = 0
                gz.write((json.dumps({"table": t}) + "\n").encode())
                while True:
                    rows = cur.fetchmany(2000)
                    if not rows:
                        break
                    for (row,) in rows:
                        gz.write(row.encode() + b"\n")
                        n += 1
                counts[t] = n
    finally:
        conn.close()
    return buf.getvalue(), counts


async def backup(now: datetime) -> dict:
    data, counts = dump_database()
    url, headers = _storage_headers()
    name = f"{now.strftime('%Y-%m-%d')}.jsonl.gz"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{url}/storage/v1/object/{BACKUP_BUCKET}/{name}", content=data,
                              headers={**headers, "Content-Type": "application/gzip", "x-upsert": "true"})
        if r.status_code >= 300:
            raise RuntimeError(f"backup upload {r.status_code}: {r.text[:200]}")
        # prune old copies
        pruned = []
        lr = await client.post(f"{url}/storage/v1/object/list/{BACKUP_BUCKET}", json={"prefix": "", "limit": 200},
                               headers=headers)
        if lr.status_code < 300:
            keep_from = (now - timedelta(days=BACKUP_KEEP_DAYS)).strftime("%Y-%m-%d")
            old = [o["name"] for o in lr.json() if o.get("name", "")[:10] < keep_from]
            if old:
                await client.request("DELETE", f"{url}/storage/v1/object/{BACKUP_BUCKET}", json={"prefixes": old}, headers=headers)
                pruned = old
    bad_tables = [t for t, n in counts.items() if n < 0]
    return {"object": f"{BACKUP_BUCKET}/{name}", "bytes": len(data), "tables": len(counts),
            "rows": sum(n for n in counts.values() if n > 0), "skipped_tables": bad_tables, "pruned": pruned}


async def daily(db, now: Optional[datetime] = None, *, do_backup: bool = True, notify: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    problems = await health(db, now)
    backup_res: dict = {}
    if do_backup:
        try:
            backup_res = await backup(now)
            if backup_res.get("skipped_tables"):
                problems.append(f"Backup skipped tables: {', '.join(backup_res['skipped_tables'])}")
        except Exception as e:
            problems.append(f"Nightly database backup failed: {str(e)[:200]}")
    decisions = await pending_decisions(db, now)
    sent = False
    if notify and (problems or decisions):
        lines: list[str] = []
        if problems:
            lines.append("Something is broken:")
            lines += [f"- {p}" for p in problems]
        actions: list[tuple] = []
        if decisions:
            lines.append("Waiting on you (buttons below, in this order):")
            short = {"probation_approve": "Approve", "probation_fail": "Fail", "reassign": "Reassign",
                     "resolve_escalation": "Resolved"}
            for i, (label, acts) in enumerate(decisions, 1):
                lines.append(f"{i}. {label}")
                actions += [(a, p, f"{i}: {short.get(a, ACTION_LABELS.get(a, a))}") for a, p in acts]
        title = "Bricks daily check: action needed" if problems else "Bricks: decisions waiting"
        sent = await send_kam(db, f"daily:{now.date().isoformat()}", title, lines, actions[:20],
                              data={"problems": len(problems), "decisions": len(decisions)})
    return {"problems": problems, "decisions": len(decisions), "backup": backup_res, "emailed": sent}
