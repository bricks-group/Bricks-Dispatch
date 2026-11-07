"""Where a job's money came from, checked before any vendor transfer.

Kam 2026-11-07 (launch gaps):
  * Stripe sandbox bookings land in the production DB. A sandbox (livemode=false)
    booking must never trigger a live transfer to a vendor.
  * A refunded booking must not pay out, and a payout should draw on the
    booking's own charge (Transfer.source_transaction) where we can find it.

booking_source() never raises. Unknown (no booking / lookup failed) means
"not proven sandbox": legacy and manual jobs keep paying exactly as before.
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
