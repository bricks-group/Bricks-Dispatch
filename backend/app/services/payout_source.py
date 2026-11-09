"""Where a job's money came from, checked before any vendor transfer.

Kam 2026-11-07 (launch gaps):
  * Stripe sandbox bookings land in the production DB. A sandbox (livemode=false)
    booking must never trigger a live transfer to a vendor.
  * A refunded booking must not pay out, and a payout should draw on the
    booking's own charge (Transfer.source_transaction) where we can find it.

booking_source() never raises. It only describes the booking; the decision is
payment_block() below (Kam 2026-11-09): no confirmed payment, no payout.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Job payout_status values written by the refund handler.
REFUNDED_PAYOUT_STATUSES = frozenset({"refunded", "cancelled_refund"})
REFUNDED_LEAD_STATUSES = frozenset({"refunded"})


def _key() -> str:
    try:
        from . import stripe_connect_service as scs
        return str(getattr(scs.stripe_lib, "api_key", "") or os.environ.get("STRIPE_SECRET_KEY", "") or "")
    except Exception:
        return os.environ.get("STRIPE_SECRET_KEY", "") or ""


def stripe_key_mode(key: Optional[str] = None) -> Optional[bool]:
    """True = live key, False = test key, None = unknown/unset."""
    k = (key if key is not None else _key()).strip()
    if k.startswith(("sk_live_", "rk_live_")):
        return True
    if k.startswith(("sk_test_", "rk_test_")):
        return False
    return None


def session_mode(session_id: Optional[str]) -> Optional[bool]:
    s = str(session_id or "")
    if s.startswith("cs_live_"):
        return True
    if s.startswith("cs_test_"):
        return False
    return None


def _rows(res: Any) -> list:
    data = getattr(res, "data", None)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _pg_one(sql: str, params: tuple) -> Optional[dict]:
    try:
        from ..shared.pg_fallback import _pg_connect, _row_from_cursor
    except Exception:
        return None
    conn = None
    try:
        conn = _pg_connect()
        if conn is None:
            return None
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return _row_from_cursor(cur)
    except Exception as e:
        logger.warning("payout_source pg lookup failed: %s", type(e).__name__)
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


async def _lookup(db, table: str, cols: str, booking_id: str) -> Optional[dict]:
    try:
        res = await db.table(table).select(cols).eq("id", booking_id).limit(1).execute()
        rows = _rows(res)
        return rows[0] if rows else None
    except Exception as e:
        try:
            from ..shared.pg_fallback import _is_rest_quota_error
            quota = _is_rest_quota_error(e)
        except Exception:
            quota = False
        if not quota:
            return None
        safe_cols = ", ".join(c.strip() for c in cols.split(",") if c.strip().replace("_", "").isalnum())
        return _pg_one(f"SELECT {safe_cols} FROM public.{table} WHERE id::text = %s LIMIT 1", (booking_id,))


def retrieve_session_charge(session_id: str) -> dict:
    """Live session -> {charge_id, refunded, amount, amount_refunded}. Never raises."""
    out: dict = {}
    if not session_id or stripe_key_mode() is None:
        return out
    try:
        from .stripe_connect_service import stripe_lib
        sess = stripe_lib.checkout.Session.retrieve(session_id, expand=["payment_intent.latest_charge"])
        pi = sess.get("payment_intent") if hasattr(sess, "get") else None
        charge = pi.get("latest_charge") if (pi is not None and hasattr(pi, "get")) else None
        if isinstance(charge, str):
            out["charge_id"] = charge
        elif charge is not None and hasattr(charge, "get"):
            out["charge_id"] = charge.get("id")
            amount = int(charge.get("amount") or 0)
            refunded_amt = int(charge.get("amount_refunded") or 0)
            out["amount"] = amount
            out["amount_refunded"] = refunded_amt
            out["refunded"] = bool(charge.get("refunded")) or (amount > 0 and refunded_amt >= amount)
            out["livemode"] = charge.get("livemode")
    except Exception as e:
        logger.info("payout_source: session %s charge lookup skipped (%s)", session_id[:14], type(e).__name__)
    return out


async def booking_source(db, job: Optional[dict], with_charge: bool = True) -> dict:
    """{livemode, session_id, refunded, charge_id, source} for a job's booking."""
    job = job or {}
    src: dict = {"livemode": None, "session_id": None, "refunded": False, "charge_id": None, "source": None}
    details = str(job.get("property_details") or "")
    if "cs_test_" in details:
        src.update(livemode=False, source="job.property_details")
    if str(job.get("payout_status") or "").lower() in REFUNDED_PAYOUT_STATUSES:
        src["refunded"] = True
    booking_id = str(job.get("booking_id") or "").strip()
    if booking_id:
        lead = await _lookup(db, "media_booking_leads", "stripe_session_id, status", booking_id)
        if lead:
            sid = lead.get("stripe_session_id")
            src["session_id"] = sid
            mode = session_mode(sid)
            if mode is not None and src["livemode"] is not False:
                src.update(livemode=mode, source="media_booking_leads")
            if str(lead.get("status") or "").lower() in REFUNDED_LEAD_STATUSES:
                src["refunded"] = True
        else:
            cb = await _lookup(db, "commercial_bookings", "stripe_session_id, livemode, status", booking_id)
            if cb:
                sid = cb.get("stripe_session_id")
                src["session_id"] = sid
                lm = cb.get("livemode")
                mode = lm if isinstance(lm, bool) else session_mode(sid)
                if mode is not None and src["livemode"] is not False:
                    src.update(livemode=mode, source="commercial_bookings")
                if str(cb.get("status") or "").lower() in REFUNDED_LEAD_STATUSES:
                    src["refunded"] = True
    if (with_charge and src["livemode"] is True and src["session_id"]
            and stripe_key_mode() is True and str(src["session_id"]).startswith("cs_live_")):
        ch = retrieve_session_charge(str(src["session_id"]))
        if ch.get("charge_id"):
            src["charge_id"] = ch["charge_id"]
        if ch.get("refunded"):
            src["refunded"] = True
    return src


def payout_block_reason(src: dict, key_mode: Optional[bool] = None) -> Optional[str]:
    """Why a transfer must not happen for this booking, or None."""
    km = stripe_key_mode() if key_mode is None else key_mode
    if src.get("livemode") is False and km is not False:
        # Sandbox booking + live (or unknown) key: never move real money.
        return "sandbox_booking"
    if src.get("refunded"):
        return "booking_refunded"
    return None


# ---------------------------------------------------------------- payment proof
# Kam 2026-11-09: no confirmed payment, no payout. A job whose booking is
# unknown, unpaid, refunded or a test order never attempts a Stripe transfer,
# whatever the key. The payment must also be in the same Stripe mode as the
# key: a live key needs a live payment, a test key a test payment.

TEST_BOOKING_PREFIXES = ("e2e-", "test-", "e2e_", "test_")
# media_booking_leads rows are only written once Checkout is paid (OS
# process_paid_checkout_session -> status "paid"); later statuses keep it paid.
LEAD_PAID = frozenset({"paid", "booked", "confirmed", "scheduled", "dispatched", "assigned",
                       "in_progress", "completed", "complete", "delivered"})
# commercial_bookings: "paid" (one-off) / "active" (plan) on a paid session.
CB_PAID = frozenset({"paid", "active", "dispatched", "completed", "complete", "delivered"})
PARTIAL_REFUND = frozenset({"partially_refunded", "partial_refund"})
# Retried by the ops sweep (status "failed"); every other reason is "blocked".
TRANSIENT_BLOCKS = frozenset({"booking_lookup_failed"})
# Kam can confirm an off-Stripe payment (cheque, invoice, brokerage credit) on
# the order; that only covers "we cannot see a payment", never test/refund.
MANUALLY_CONFIRMABLE = frozenset({"no_booking", "booking_not_found", "booking_unpaid",
                                  "payment_mode_unknown", "booking_lookup_failed"})

BLOCK_TEXT = {
    "test_booking": "This is a test order (its booking id starts with e2e- or test-). Test orders never pay out.",
    "sandbox_booking": "The booking was paid in Stripe test mode, so no real money came in.",
    "live_payment_test_key": "The booking was paid for real, but the server is on a Stripe test key.",
    "booking_refunded": "The client was refunded.",
    "booking_partially_refunded": "The client was partly refunded. Decide by hand what the vendor is owed.",
    "no_booking": "The order has no booking linked, so there is no payment on record.",
    "booking_not_found": "The linked booking was not found, so there is no payment on record.",
    "booking_unpaid": "The booking has no confirmed payment.",
    "payment_mode_unknown": "A payment is recorded, but it cannot be confirmed as a real (live) payment.",
    "stripe_key_unknown": "The server's Stripe key is not set, so no payment can be checked.",
    "booking_lookup_failed": "The booking could not be read just now. It is checked again on the next sweep.",
}


def is_test_booking(job: Optional[dict]) -> bool:
    return str((job or {}).get("booking_id") or "").strip().lower().startswith(TEST_BOOKING_PREFIXES)


def _looks_like_uuid(v: str) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", v or ""))


class BookingLookupFailed(Exception):
    pass


async def _lookup_strict(db, table: str, cols: str, booking_id: str) -> Optional[dict]:
    """Like _lookup, but a read error raises instead of reading as 'not found'."""
    try:
        res = await db.table(table).select(cols).eq("id", booking_id).limit(1).execute()
        rows = _rows(res)
        return rows[0] if rows else None
    except Exception as e:
        try:
            from ..shared.pg_fallback import _is_rest_quota_error
            quota = _is_rest_quota_error(e)
        except Exception:
            quota = False
        if not quota:
            raise BookingLookupFailed(f"{table}: {type(e).__name__}") from e
        safe_cols = ", ".join(c.strip() for c in cols.split(",") if c.strip().replace("_", "").isalnum())
        return _pg_one(f"SELECT {safe_cols} FROM public.{table} WHERE id::text = %s LIMIT 1", (booking_id,))


async def payment_proof(db, booking_id: str) -> dict:
    """{found, paid, livemode, refunded, partial, table}. Raises BookingLookupFailed."""
    out = {"found": False, "paid": False, "livemode": None, "refunded": False, "partial": False, "table": None}
    bid = str(booking_id or "").strip()
    cb_id = bid[3:] if bid.lower().startswith("cb_") else bid
    if _looks_like_uuid(bid):
        lead = await _lookup_strict(db, "media_booking_leads", "stripe_session_id, status", bid)
        if lead:
            st = str(lead.get("status") or "").lower()
            out.update(found=True, table="media_booking_leads", paid=st in LEAD_PAID,
                       livemode=session_mode(lead.get("stripe_session_id")),
                       refunded=st in REFUNDED_LEAD_STATUSES, partial=st in PARTIAL_REFUND)
            return out
    if _looks_like_uuid(cb_id):
        cb = await _lookup_strict(db, "commercial_bookings", "stripe_session_id, livemode, status", cb_id)
        if cb:
            st = str(cb.get("status") or "").lower()
            lm = cb.get("livemode")
            out.update(found=True, table="commercial_bookings", paid=st in CB_PAID,
                       livemode=lm if isinstance(lm, bool) else session_mode(cb.get("stripe_session_id")),
                       refunded=st in REFUNDED_LEAD_STATUSES, partial=st in PARTIAL_REFUND)
            return out
    if _looks_like_uuid(bid):
        bk = await _lookup_strict(db, "bookings", "status, stripe_session_id, stripe_payment_status, paid_at, "
                                  "refunded_at, refund_amount, total_cents", bid)
        if bk:
            pay = str(bk.get("stripe_payment_status") or "").lower()
            refunded = bool(bk.get("refunded_at"))
            partial = False
            try:
                amt, total = int(bk.get("refund_amount") or 0), int(bk.get("total_cents") or 0)
                partial = refunded and 0 < amt < total
            except (TypeError, ValueError):
                pass
            out.update(found=True, table="bookings", paid=pay == "paid",
                       livemode=session_mode(bk.get("stripe_session_id")),
                       refunded=refunded and not partial, partial=partial)
    return out


async def _manually_confirmed(job_id: str) -> bool:
    try:
        from . import delivery_ledger as dl
        return bool(await dl._rows_like(f"payment-confirmed:{job_id}"))
    except Exception:  # noqa: BLE001 - unsure means not confirmed
        return False


async def payment_block(db, job: Optional[dict], src: Optional[dict] = None,
                        key_mode: Optional[bool] = None) -> Optional[str]:
    """Why this job has no confirmed payment (so no transfer), or None. Never raises."""
    job = job or {}
    src = src or {}
    if is_test_booking(job):
        return "test_booking"
    km = stripe_key_mode() if key_mode is None else key_mode
    if km is None:
        return "stripe_key_unknown"
    if src.get("refunded"):
        return "booking_refunded"
    bid = str(job.get("booking_id") or "").strip()
    reason: Optional[str] = None
    if not bid:
        reason = "no_booking"
    else:
        try:
            proof = await payment_proof(db, bid)
        except BookingLookupFailed as e:
            logger.error("payout_source: booking lookup failed for job %s: %s", job.get("id"), e)
            proof, reason = None, "booking_lookup_failed"
        if proof is not None:
            if not proof["found"]:
                reason = "booking_not_found"
            elif proof["refunded"]:
                return "booking_refunded"
            elif proof["partial"]:
                return "booking_partially_refunded"
            elif not proof["paid"]:
                reason = "booking_unpaid"
            elif proof["livemode"] is None:
                reason = "payment_mode_unknown"
            elif proof["livemode"] is not km:
                return "sandbox_booking" if proof["livemode"] is False else "live_payment_test_key"
    if reason in MANUALLY_CONFIRMABLE and await _manually_confirmed(str(job.get("id") or "")):
        return None
    return reason
