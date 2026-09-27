"""Honest Stripe Connect status for GO — shadows vendor_os hollow Connected.

Connected only after Stripe Account.retrieve proves details_submitted + payouts_enabled.
Mounted before vendor_os.router so this route wins.
"""
from fastapi import APIRouter, Header

from ..shared.auth import can_access_stripe_connect, get_vendor_member, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error

router = APIRouter(prefix="/api/vendor-os", tags=["vendor-os"])


@router.get("/stripe-connect/status")
async def get_stripe_connect_status(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Honest payout readiness: Connected only after Stripe Account.retrieve
    proves details_submitted + payouts_enabled. Mere stripe_account_id is pending."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    # Active Photographer/Manager/Owner/Admin may view org Connect status (not admin-only).
    if not can_access_stripe_connect(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    try:
        res = await (await get_db()).table("contractors").select(
            "id, stripe_account_id, connect_status"
        ).eq("vendor_id", vendor_id).maybe_single().execute()
        row = res.data or {}
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_contractor_by_vendor_id
        if _is_rest_quota_error(e):
            row = pg_select_contractor_by_vendor_id(vendor_id) or {}
        else:
            raise
    account_id = row.get("stripe_account_id") or ""
    if not account_id:
        return {
            "connected": False,
            "connect_status": "not_connected",
            "details_submitted": False,
            "payouts_enabled": False,
            "charges_enabled": False,
        }

    from ..services.stripe_connect_service import sync_contractor_connect_status

    synced = sync_contractor_connect_status(row["id"], account_id)
    return {
        "connected": bool(synced.get("connected")),
        "connect_status": synced.get("connect_status") or "pending",
        "details_submitted": bool(synced.get("details_submitted")),
        "payouts_enabled": bool(synced.get("payouts_enabled")),
        "charges_enabled": bool(synced.get("charges_enabled")),
    }
