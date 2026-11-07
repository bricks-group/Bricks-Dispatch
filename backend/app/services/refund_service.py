"""Customer refund -> Dispatch job + vendor payout (Kam 2026-11-07, launch gaps).

Called by Bricks OS when Stripe sends charge.refunded for a booking.

Rule (runbook 2026-11-07 section 3, until Kam sets another):
  * Full refund, payout not yet transferred (none / held / pending / failed):
    the payout is cancelled and the job can never pay out (payout_status
    "refunded"). A job not yet delivered is cancelled.
  * Full refund after the transfer already went out: nothing is clawed back
    automatically (work done is paid). The job is flagged
    "refund_after_payout" for review. reverse_paid=True (an explicit ops
    choice, e.g. vendor no-show) reverses the transfer in full.
  * Partial refund (e.g. weather-affected part): recorded as a flag only;
    the payout is unchanged.
Never raises for one bad job; returns per-job actions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_NOT_CANCELLABLE = {"delivered", "completed", "cancelled", "canceled", "agent_no_show"}


def _rows(res: Any) -> list:
    data = getattr(res, "data", None)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


async def _jobs_for_booking(db, booking_id: str) -> list[dict]:
    try:
        res = await db.table("jobs").select("*").eq("booking_id", booking_id).execute()
        return _rows(res)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, _pg_connect, _rows_from_cursor
        if not _is_rest_quota_error(e):
            raise
        conn = _pg_connect()
        if conn is None:
            raise
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM public.jobs WHERE booking_id = %s", (booking_id,))
                return _rows_from_cursor(cur)
        finally:
            conn.close()


def _flags(job: dict, *new: str) -> list[str]:
    cur = job.get("ai_flags") or []
    if isinstance(cur, str):
        cur = [cur]
    out = [str(f) for f in cur]
    for f in new:
        if f and f not in out:
            out.append(f)
    return out


def _reverse_transfer(transfer_id: str, refund_id: str, payout_id: str) -> str:
    from .stripe_connect_service import stripe_lib
    rev = stripe_lib.Transfer.create_reversal(
        transfer_id,
        metadata={"reason": "customer_refund", "refund_id": refund_id or "", "payout_id": payout_id or ""},
        idempotency_key=f"reverse_{payout_id or transfer_id}",
    )
    return rev.get("id") if hasattr(rev, "get") else getattr(rev, "id", "")


async def apply_booking_refund(db, booking_id: str, *, full: bool, refund_id: str = "",
                               charge_id: str = "", amount_refunded: Optional[int] = None,
                               reverse_paid: bool = False) -> dict:
    from .payout_service import _db_select_payout_by_job, _db_update_payout, _db_update_job_fields

    jobs = await _jobs_for_booking(db, booking_id)
    now = datetime.now(timezone.utc).isoformat()
    tag = refund_id or charge_id or "refund"
    out: list[dict] = []
    for job in jobs:
        jid = str(job.get("id") or "")
        r: dict = {"job_id": jid}
        try:
            payout = await _db_select_payout_by_job(db, jid)
            transferred = bool(payout and (payout.get("stripe_transfer_id") or payout.get("status") == "paid"))
            job_fields: dict = {"updated_at": now}
            if not full:
                job_fields["ai_flags"] = _flags(job, f"partial_refund:{tag}")
                r["action"] = "partial_refund_recorded"
            elif transferred and reverse_paid:
                trr = _reverse_transfer(payout["stripe_transfer_id"], refund_id, payout["id"])
                await _db_update_payout(db, payout["id"], {"status": "reversed"})
                job_fields.update(payout_status="reversed", ai_flags=_flags(job, f"refunded:{tag}", f"transfer_reversed:{trr}"))
                r.update(action="transfer_reversed", reversal_id=trr, transfer_id=payout["stripe_transfer_id"])
            elif transferred:
                job_fields["ai_flags"] = _flags(job, f"refunded:{tag}", "refund_after_payout")
                r.update(action="paid_needs_review", transfer_id=payout.get("stripe_transfer_id"))
            else:
                if payout:
                    await _db_update_payout(db, payout["id"], {"status": "cancelled"})
                    r["payout_id"] = payout["id"]
                job_fields.update(payout_status="refunded", ai_flags=_flags(job, f"refunded:{tag}"))
                r["action"] = "payout_cancelled" if payout else "payout_blocked"
            if full and str(job.get("status") or "").lower() not in _NOT_CANCELLABLE:
                job_fields["status"] = "cancelled"
                r["job_cancelled"] = True
            await _db_update_job_fields(db, jid, job_fields)
        except Exception as e:  # noqa: BLE001 - report per job, keep going
            logger.error(f"refund apply failed for job {jid} booking {booking_id}: {type(e).__name__}: {e}")
            r.update(action="error", error=type(e).__name__)
        out.append(r)
    logger.info(f"Refund {tag} booking {booking_id} full={full}: {[(x['job_id'], x.get('action')) for x in out]}")
    return {"ok": True, "booking_id": booking_id, "full": full, "jobs": out,
            "amount_refunded": amount_refunded, "charge_id": charge_id or None}
