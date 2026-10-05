"""
Stripe Connect service for vendor payouts in Bricks GO.

Stripe accounts live on public.contractors (canonical vendor identity, uuid PK),
linked to public.vendors/vendor_members via contractors.vendor_id (text FK).

Honesty: Connected means Stripe-verified payout-ready — never mere stripe_account_id
presence after Account.create (that stays pending until details_submitted + payouts_enabled).
"""

import logging
import os
import stripe as stripe_lib
from supabase import create_client, Client
from app.shared.database import maybe_single_row

logger = logging.getLogger(__name__)

stripe_lib.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def classify_connect_account(account) -> dict:
    """Map a Stripe Account object to honest connect fields.

    Connected / ready only when onboarding is submitted and payouts are enabled.
    """
    if not account:
        return {
            "connected": False,
            "connect_status": "not_connected",
            "details_submitted": False,
            "payouts_enabled": False,
            "charges_enabled": False,
        }
    details_submitted = bool(getattr(account, "details_submitted", False))
    payouts_enabled = bool(getattr(account, "payouts_enabled", False))
    charges_enabled = bool(getattr(account, "charges_enabled", False))
    disabled = None
    requirements = getattr(account, "requirements", None)
    if requirements is not None:
        disabled = getattr(requirements, "disabled_reason", None) or None

    if disabled:
        status = "restricted"
        connected = False
    elif details_submitted and payouts_enabled:
        status = "connected"
        connected = True
    elif getattr(account, "id", None):
        status = "pending"
        connected = False
    else:
        status = "not_connected"
        connected = False

    return {
        "connected": connected,
        "connect_status": status,
        "details_submitted": details_submitted,
        "payouts_enabled": payouts_enabled,
        "charges_enabled": charges_enabled,
        "disabled_reason": disabled,
    }


def sync_contractor_connect_status(contractor_id: str, stripe_account_id: str) -> dict:
    """Retrieve Stripe account, persist connect_status, return honest status payload."""
    if not stripe_account_id:
        return {
            "connected": False,
            "connect_status": "not_connected",
            "details_submitted": False,
            "payouts_enabled": False,
            "charges_enabled": False,
        }
    if not stripe_lib.api_key:
        # Fail closed for Connected — keep pending if we have an id but cannot verify.
        return {
            "connected": False,
            "connect_status": "pending",
            "details_submitted": False,
            "payouts_enabled": False,
            "charges_enabled": False,
            "detail": "Stripe is not configured",
        }
    try:
        account = stripe_lib.Account.retrieve(stripe_account_id)
    except Exception as e:
        logger.warning("Stripe Account.retrieve failed for %s: %s", stripe_account_id, type(e).__name__)
        return {
            "connected": False,
            "connect_status": "pending",
            "details_submitted": False,
            "payouts_enabled": False,
            "charges_enabled": False,
            "detail": "Could not verify Stripe account",
        }

    payload = classify_connect_account(account)
    try:
        supabase.table("contractors").update({
            "connect_status": payload["connect_status"],
        }).eq("id", contractor_id).execute()
    except Exception as e:
        from app.shared.pg_fallback import _is_rest_quota_error, pg_update_contractor
        if _is_rest_quota_error(e):
            if not pg_update_contractor(contractor_id, {"connect_status": payload["connect_status"]}):
                logger.warning(
                    "Could not persist connect_status (Fair-Use PG) for contractor %s",
                    contractor_id,
                )
        else:
            logger.warning("Could not persist connect_status for contractor %s: %s", contractor_id, type(e).__name__)
    return payload


def assert_payout_ready(stripe_account_id: str) -> None:
    """Raise ValueError unless the Express account can receive payouts."""
    if not stripe_account_id:
        raise ValueError("Stripe account not connected")
    if not stripe_lib.api_key:
        raise ValueError("Stripe is not configured")
    account = stripe_lib.Account.retrieve(stripe_account_id)
    payload = classify_connect_account(account)
    if not payload["connected"]:
        raise ValueError(
            f"Stripe account is not payout-ready (status={payload['connect_status']})"
        )


async def handle_payout_to_vendor(vendor_id: str, amount: int, booking_id: str, payout_id: str = "", contractor_id: str = "") -> str:
    """
    Transfer funds to the vendor's connected Stripe account.
    Amount must be in cents. Returns the Stripe transfer ID.
    payout_id is used as the Stripe idempotency key — scoped per payout so a retry
    with the same payout_id and amount is safe, but a new payout (different id) gets
    a fresh key even for the same booking.
    contractor_id should be the assignee's vendor_members.id (photographer-level).
    When provided it is used to look up the Stripe account directly, avoiding the
    multi-row ambiguity of querying by org-level vendor_id.
    """
    def _load_contractor(resolved_vendor_id: str):
        from app.shared.pg_fallback import (
            _is_rest_quota_error,
            pg_select_contractor_by_vendor_id,
        )
        try:
            response = supabase.table("contractors").select(
                "id, stripe_account_id, connect_status"
            ).eq("vendor_id", resolved_vendor_id).maybe_single().execute()
            return maybe_single_row(response) or None
        except Exception as e:
            if _is_rest_quota_error(e):
                return pg_select_contractor_by_vendor_id(resolved_vendor_id)
            raise

    if contractor_id:
        # contractor_id is vendor_members.id (photographer-level); contractors are linked via
        # vendor_id FK — not a shared PK — so resolve the vendor_id from vendor_members first.
        from app.shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member
        try:
            member_res = supabase.table("vendor_members").select("vendor_id").eq(
                "id", contractor_id
            ).maybe_single().execute()
            member = maybe_single_row(member_res)
        except Exception as e:
            if _is_rest_quota_error(e):
                member = pg_select_vendor_member(actor_id=contractor_id) or {}
            else:
                raise
        resolved_vendor_id = member.get("vendor_id") or vendor_id
        contractor = _load_contractor(resolved_vendor_id)
    else:
        contractor = _load_contractor(vendor_id)

    if not contractor or not contractor.get("stripe_account_id"):
        raise ValueError(f"Stripe account not found for vendor: {vendor_id} contractor: {contractor_id}")

    stripe_account_id = contractor["stripe_account_id"]
    # Fail closed: verify Stripe readiness (and refresh connect_status) before transfer.
    synced = sync_contractor_connect_status(contractor["id"], stripe_account_id)
    if not synced.get("connected"):
        raise ValueError(
            f"Stripe account is not payout-ready for vendor {vendor_id} "
            f"(status={synced.get('connect_status')})"
        )

    idempotency_key = f"payout_{payout_id}" if payout_id else f"payout_{booking_id}"

    transfer = stripe_lib.Transfer.create(
        amount=amount,
        currency="cad",
        destination=stripe_account_id,
        metadata={"booking_id": booking_id, "payout_id": payout_id, "vendor_id": vendor_id},
        idempotency_key=idempotency_key,
    )

    logger.info(f"Transfer {transfer.id} created: ${amount/100:.2f} to vendor {vendor_id} ({stripe_account_id})")
    return transfer.id


async def create_connect_account_and_link(
    vendor_id: str, refresh_url: str, return_url: str, full_name: str = "", email: str = ""
) -> str:
    """
    Creates a Stripe Connect Express account for a vendor and generates an account link.
    Stripe accounts are stored on contractors (canonical vendor identity).

    After Account.create, connect_status stays pending until Stripe reports payout-ready.
    """
    from app.shared.pg_fallback import (
        _is_rest_quota_error,
        pg_insert_contractor,
        pg_select_contractor_by_vendor_id,
        pg_update_contractor,
    )

    try:
        response = supabase.table("contractors").select("id, stripe_account_id").eq(
            "vendor_id", vendor_id
        ).maybe_single().execute()
        contractor = maybe_single_row(response) or None
    except Exception as e:
        if _is_rest_quota_error(e):
            contractor = pg_select_contractor_by_vendor_id(vendor_id)
        else:
            raise

    if not contractor:
        payload = {
            "vendor_id": vendor_id,
            "full_name": full_name or vendor_id,
            "email": email or f"{vendor_id}@bricks.internal",
        }
        try:
            insert_res = supabase.table("contractors").insert(payload).execute()
            if not insert_res.data:
                raise ValueError(f"Could not create contractor record for vendor: {vendor_id}")
            contractor = insert_res.data[0]
        except Exception as e:
            if _is_rest_quota_error(e):
                contractor = pg_insert_contractor(payload)
                if not contractor:
                    raise ValueError(
                        f"Could not create contractor record for vendor: {vendor_id}"
                    ) from e
            else:
                raise

    stripe_account_id = contractor.get("stripe_account_id")
    if not stripe_account_id:
        account = stripe_lib.Account.create(
            type="express",
            country="CA",
            capabilities={"transfers": {"requested": True}},
            email=email or None,
        )
        stripe_account_id = account.id
        update_fields = {
            "stripe_account_id": stripe_account_id,
            "connect_status": "pending",
        }
        try:
            supabase.table("contractors").update(update_fields).eq(
                "id", contractor["id"]
            ).execute()
        except Exception as e:
            if _is_rest_quota_error(e):
                if not pg_update_contractor(contractor["id"], update_fields):
                    raise RuntimeError(
                        "Could not persist Stripe account id (Fair-Use PG failed)"
                    ) from e
            else:
                raise

    account_link = stripe_lib.AccountLink.create(
        account=stripe_account_id,
        refresh_url=refresh_url,
        return_url=return_url,
        type="account_onboarding",
        collect_options={"fields": "currently_due"},
    )
    return account_link.url
