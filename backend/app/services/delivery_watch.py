"""Delivery watch: the stuck-order sweep (runs inside the 15-minute ops sweep).

Kam, 2026-11-09: nothing in delivery may stick quietly. Each check alerts Kam
(raph@brxs.ca) once per order, through delivery_ledger.alert_kam (fail loud):

* delivery email failed or never went out  -> retried with backoff (15m, 1h,
  6h, 24h), then marked dead and Kam is told (delivery_ledger)
* files uploaded but the editor has not reviewed them for 6 hours
* the editor approved but the order is still not delivered after 1 hour
* delivered but the vendor payout has not gone through after 24 hours
* the daily ops check (health + backup) has not run for 30 hours

Test orders (booking ids starting e2e- or test-) are ignored, and nothing older
than 30 days is looked at, so historic test data stays quiet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import delivery_ledger as dl

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 30
EMAIL_RETRY_WINDOW_DAYS = 3
REVIEW_STALE_H = 6
APPROVED_STALE_H = 1
PAYOUT_STALE_H = 24
DAILY_STALE_H = 30
TEST_PREFIXES = ("e2e-", "test-", "e2e_", "test_")


def is_test_job(job: dict) -> bool:
    b = str((job or {}).get("booking_id") or "").lower()
    return b.startswith(TEST_PREFIXES)


def _ts(raw: Any) -> Optional[datetime]:
    return dl._parse(raw)


def _gate(job: dict) -> dict:
    from ..routers.post_production import _saved_gate
    return _saved_gate(job)


def _line(job: dict) -> str:
    return f"{job.get('package_name') or job.get('package_id') or 'Order'} at {job.get('address') or 'an unknown address'}"


async def _rows(db, table: str, cols: str, *, status_in: Optional[list] = None, col: str = "status") -> list[dict]:
    q = db.table(table).select(cols)
    if status_in:
        q = q.in_(col, status_in)
    return (await q.limit(500).execute()).data or []


async def _ops_seen(db, key: str) -> bool:
    try:
        from .ops_autopilot import seen
        return await seen(db, key)
    except Exception:  # noqa: BLE001
        return False


async def _retry_emails(db, now: datetime, jobs_by_id: dict, out: dict) -> None:
    rows = await _rows(db, "media_delivery", "job_id, delivery_urls, delivered_at, notification_status",
                       status_in=["failed", "sending", "pending"], col="notification_status")
    for md in rows:
        jid = str(md.get("job_id") or "")
        job = jobs_by_id.get(jid) or {}
        if not jid or is_test_job(job):
            continue
        delivered = _ts(md.get("delivered_at"))
        if not delivered or now - delivered > timedelta(days=LOOKBACK_DAYS):
            continue
        if now - delivered > timedelta(days=EMAIL_RETRY_WINDOW_DAYS):
            # Too late to email the client automatically; tell Kam once.
            if await dl.alert_kam(f"email-never:{jid}", jid, "A client was never emailed their files",
                                  [_line(job), f"Delivered {delivered.date().isoformat()}, but the delivery email "
                                   "never went out. The share page works. Send them the link yourself."]):
                out["email_alerted"].append(jid)
            continue
        if md.get("notification_status") == "pending" and now - delivered < timedelta(minutes=dl.SENDING_STALE_MIN):
            continue  # first send still in flight
        tries = await dl.email_attempts(jid)
        if not dl.email_retry_due(tries, now):
            continue
        from ..routers.post_production import _send_delivery_notification
        status = await _send_delivery_notification(jid, md.get("delivery_urls") or [])
        out["email_retried"].append({"job_id": jid, "status": status})


async def _stuck_orders(db, now: datetime, jobs: list[dict], out: dict) -> None:
    from .fulfillment_gate import editor_approved, is_trade_job
    for job in jobs:
        jid, st = str(job.get("id") or ""), str(job.get("status") or "")
        if not jid or is_test_job(job) or is_trade_job(job):
            continue
        gate = _gate(job)
        updated = _ts(job.get("updated_at")) or _ts(job.get("created_at"))
        if updated and now - updated > timedelta(days=LOOKBACK_DAYS):
            continue
        try:
            if st != "delivered" and editor_approved(job):
                at = _ts(job.get("editor_approved_at"))
                if at and now - at >= timedelta(hours=APPROVED_STALE_H):
                    why = "; ".join(gate.get("reasons") or []) or "no reason recorded"
                    if await dl.alert_kam(f"approved-stuck:{jid}", jid, "Approved, but not delivered",
                                          [_line(job), f"The editor approved it {int((now - at).total_seconds() // 3600)}h ago "
                                           f"and the client still has nothing. Held because: {why}."]):
                        out["stuck_alerted"].append(jid)
                continue
            if st in ("processing", "completed", "qc_failed") and not editor_approved(job):
                held = _ts(gate.get("held_at"))
                if held and now - held >= timedelta(hours=REVIEW_STALE_H):
                    if await dl.alert_kam(f"review-stale:{jid}", jid, "Files are waiting for editor review",
                                          [_line(job), f"Uploaded and waiting {int((now - held).total_seconds() // 3600)}h. "
                                           "Nothing is delivered or paid until someone approves them."]):
                        out["stuck_alerted"].append(jid)
                continue
            if st == "delivered" and str(job.get("payout_status") or "") != "paid":
                if await _ops_seen(db, f"payout_esc:{jid}"):
                    continue  # ops sweep already escalated this one
                pres = (await db.table("payouts").select("status").eq("job_id", jid).execute()).data or []
                if any((r.get("status") or "") in ("paid", "held") for r in pres):
                    continue  # paid, or a probation hold with its own review
                if updated and now - updated >= timedelta(hours=PAYOUT_STALE_H):
                    reason = await _last_payout_reason(jid)
                    if await dl.alert_kam(f"payout-stuck:{jid}", jid, "Delivered, but the vendor is not paid",
                                          [_line(job), f"Delivered over {PAYOUT_STALE_H}h ago; payout is "
                                           f"{job.get('payout_status') or 'not started'}.",
                                           f"Last reason: {reason}"]):
                        out["payout_alerted"].append(jid)
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the sweep
            logger.error("delivery watch job %s failed: %s", jid, type(exc).__name__)
            out["errors"].append({"job_id": jid, "error": type(exc).__name__})


async def _last_payout_reason(job_id: str) -> str:
    try:
        rows = [r for r in await dl.trail(job_id) if str(r.get("action") or "").startswith("delivery.payout")]
    except Exception:  # noqa: BLE001
        return "unknown (audit trail unreadable)"
    if not rows:
        return "no payout attempt recorded"
    d = rows[-1].get("details") or {}
    return str(d.get("reason") or d.get("error") or d.get("skipped") or d.get("status") or "unknown")[:200]


async def _daily_heartbeat(db, now: datetime, out: dict) -> None:
    rows = (await db.table("ops_autopilot_runs").select("created_at").eq("kind", "daily").order(
        "created_at", desc=True).limit(1).execute()).data or []
    last = _ts(rows[0].get("created_at")) if rows else None
    if last and now - last < timedelta(hours=DAILY_STALE_H):
        return
    if await dl.alert_kam(f"daily-stale:{now.date().isoformat()}", "ops", "The daily ops check has not run",
                          [f"Last run: {last.isoformat() if last else 'never'}.",
                           "That check covers site health and the nightly database backup."]):
        out["daily_alerted"] = True


async def run(db, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out: dict = {"email_retried": [], "email_alerted": [], "stuck_alerted": [], "payout_alerted": [], "errors": []}
    try:
        jobs = await _rows(db, "jobs", "*", status_in=["processing", "completed", "qc_failed", "delivered"])
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"step": "jobs", "error": type(exc).__name__})
        await dl.alert_kam(f"watch-down:{now.date().isoformat()}", "ops", "The delivery check could not read orders",
                           [f"Reason: {type(exc).__name__}. It tries again in 15 minutes."])
        return out
    by_id = {str(j.get("id")): j for j in jobs}
    for step, fn in (("emails", lambda: _retry_emails(db, now, by_id, out)),
                     ("stuck", lambda: _stuck_orders(db, now, jobs, out)),
                     ("daily", lambda: _daily_heartbeat(db, now, out))):
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001
            logger.error("delivery watch step %s failed: %s", step, type(exc).__name__)
            out["errors"].append({"step": step, "error": type(exc).__name__})
    return out
