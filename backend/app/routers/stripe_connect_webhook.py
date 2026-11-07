"""Stripe Connect account.updated → sync contractors.connect_status."""
from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import stripe as stripe_lib
from fastapi import APIRouter, Header, Request

from ..shared.database import get_db
from ..shared.errors import api_error
from ..services.stripe_connect_service import classify_connect_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stripe", tags=["stripe-connect"])


def _to_ns(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in value.items()})
    return value


@router.post("/connect-webhook")
async def stripe_connect_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    """Sync connect_status when Stripe sends account.updated (no payout required)."""
    secret = os.environ.get("STRIPE_CONNECT_WEBHOOK_SECRET") or os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        api_error(503, "Stripe Connect webhook is not configured", "stripe_webhook_not_configured")
    if not stripe_signature:
        api_error(400, "Missing Stripe-Signature", "missing_stripe_signature")

    payload = await request.body()
    try:
        event = stripe_lib.Webhook.construct_event(payload, stripe_signature, secret)
    except Exception as e:
        logger.warning("Connect webhook signature failed: %s", type(e).__name__)
        api_error(400, "Invalid Stripe signature", "invalid_stripe_signature")

    if event["type"] != "account.updated":
        return {"received": True, "ignored": event["type"]}

    account = event["data"]["object"]
    account_id = account.get("id") if isinstance(account, dict) else getattr(account, "id", None)
    if not account_id:
        return {"received": True, "ok": False, "detail": "no account id"}

    acct = _to_ns(account) if isinstance(account, dict) else account
    classified = classify_connect_account(acct)
    status = classified["connect_status"]

    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_select_contractor_by_stripe_account_id,
        pg_update_contractor,
    )

    db = await get_db()
    try:
        res = await db.table("contractors").select("id, member_id").eq(
            "stripe_account_id", account_id
        ).maybe_single().execute()
        data = getattr(res, "data", None) if res is not None else None
        row = data if isinstance(data, dict) else {}
    except Exception as e:
        if _is_rest_quota_error(e):
            row = pg_select_contractor_by_stripe_account_id(account_id) or {}
        else:
            raise
    if not row.get("id"):
        logger.info("account.updated for unknown stripe account %s", account_id)
        return {"received": True, "ok": False, "detail": "contractor not found"}

    try:
        await db.table("contractors").update({"connect_status": status}).eq(
            "id", row["id"]
        ).execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            if not pg_update_contractor(row["id"], {"connect_status": status}):
                logger.warning(
                    "connect webhook Fair-Use PG update failed contractor=%s",
                    row["id"],
                )
        else:
            raise
    if row.get("member_id") and status == "connected":
        # Payouts was the last onboarding step for some vendors.
        try:
            from ..services.vendor_onboarding import refresh_onboarding
            await refresh_onboarding(str(row["member_id"]))
        except Exception as e:
            logger.warning("onboarding refresh after connect webhook failed: %s", type(e).__name__)
    return {
        "received": True,
        "ok": True,
        "contractor_id": row["id"],
        "connect_status": status,
        "connected": classified["connected"],
    }
