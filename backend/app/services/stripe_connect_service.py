"""
Stripe Connect service for vendor payouts in Bricks-Dispatch.

Stripe accounts live on public.contractors (canonical vendor identity, uuid PK),
linked to public.vendors/vendor_members via contractors.vendor_id (text FK).
"""

import logging
import os
import stripe as stripe_lib
from supabase import create_client, Client

logger = logging.getLogger(__name__)

stripe_lib.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def handle_payout_to_vendor(vendor_id: str, amount: int, booking_id: str, payout_id: str = "") -> str:
    """
    Transfer funds to the vendor's connected Stripe account.
    Amount must be in cents. Returns the Stripe transfer ID.
    payout_id is used as the Stripe idempotency key — scoped per payout so a retry
    with the same payout_id and amount is safe, but a new payout (different id) gets
    a fresh key even for the same booking.
    """
    response = supabase.table("contractors").select("id, stripe_account_id").eq("vendor_id", vendor_id).maybe_single().execute()
    contractor = response.data

    if not contractor or not contractor.get("stripe_account_id"):
        raise ValueError(f"Stripe account not found for vendor: {vendor_id}")

    stripe_account_id = contractor["stripe_account_id"]
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


async def create_connect_account_and_link(vendor_id: str, refresh_url: str, return_url: str) -> str:
    """
    Creates a Stripe Connect Express account for a vendor and generates an account link.
    Stripe accounts are stored on contractors (canonical vendor identity).
    """
    response = supabase.table("contractors").select("id, stripe_account_id").eq("vendor_id", vendor_id).maybe_single().execute()
    contractor = response.data

    if not contractor:
        raise ValueError(f"No contractor record found for vendor: {vendor_id}")

    stripe_account_id = contractor.get("stripe_account_id")
    if not stripe_account_id:
        account = stripe_lib.Account.create(
            type="express",
            country="CA",
            capabilities={"transfers": {"requested": True}},
        )
        stripe_account_id = account.id
        supabase.table("contractors").update({"stripe_account_id": stripe_account_id}).eq("id", contractor["id"]).execute()

    account_link = stripe_lib.AccountLink.create(
        account=stripe_account_id,
        refresh_url=refresh_url,
        return_url=return_url,
        type="account_onboarding",
        collect_options={"fields": "currently_due"},
    )
    return account_link.url
