"""Honest Stripe Connect status for GO — shadows vendor_os hollow Connected.

Connected only after Stripe Account.retrieve proves details_submitted + payouts_enabled.
Mounted before vendor_os.router so this route wins.
"""
from fastapi import APIRouter, Header

from ..shared.auth import can_access_stripe_connect, get_vendor_member, resolve_vendor_actor_id
from ..shared.errors import api_error

router = APIRouter(prefix="/api/vendor-os", tags=["vendor-os"])


def payout_account_required(member: dict | None) -> bool:
    """True for anyone Dispatch pays per job by Stripe transfer: ICs (own Express
    account) and the owner (company row, full package). False for salaried staff."""
    if not member:
        return False
    from ..shared.vendor_identity import is_owner_member
    from ..services.commercial_jobs import is_employee
    if is_owner_member(member):
        return True
    return not is_employee(member)


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

    # Per photographer: each vendor member has their own Express account; the
    # company row (member_id NULL) is the owner's.
    from ..services.stripe_connect_service import connect_member_key, load_contractor_row
    member_key = connect_member_key(actor_member)
    # payout_required: this person is paid per job through Stripe (ICs and the
    # owner); salaried in-house staff are not. GO shows a "Connect payouts"
    # banner when this is true and connected is false — that is how vendors
    # grandfathered past the onboarding checklist (no payouts step) get asked.
    payout_required = payout_account_required(actor_member)
    row = load_contractor_row(vendor_id, member_key) or {}
    account_id = row.get("stripe_account_id") or ""
    if not account_id:
        return {
            "payout_required": payout_required,
            "connected": False,
            "connect_status": "not_connected",
            "details_submitted": False,
            "payouts_enabled": False,
            "charges_enabled": False,
        }

    from ..services.stripe_connect_service import sync_contractor_connect_status

    synced = sync_contractor_connect_status(row["id"], account_id)
    if member_key and synced.get("connected"):
        try:
            from ..services.vendor_onboarding import refresh_onboarding
            await refresh_onboarding(member_key)
        except Exception:
            pass
    return {
        "payout_required": payout_required,
        "connected": bool(synced.get("connected")),
        "connect_status": synced.get("connect_status") or "pending",
        "details_submitted": bool(synced.get("details_submitted")),
        "payouts_enabled": bool(synced.get("payouts_enabled")),
        "charges_enabled": bool(synced.get("charges_enabled")),
    }
