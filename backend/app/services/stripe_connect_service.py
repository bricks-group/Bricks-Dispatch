"""
Stripe Connect service for vendor payouts in Bricks-Dispatch.

Wraps the Booking backend's stripe_connect_service for use in the Dispatch service.
In production, this should call the Booking backend's API directly.
For now, we import and re-export the functions we need.
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


async def handle_payout_to_vendor(vendor_id: str, amount: int, booking_id: str) -> str:
    """
    Transfer funds from the brokerage to the vendor's connected Stripe account.
    Amount should be in cents.
    Returns the Stripe transfer ID.
    """
    # Look up vendor's Stripe account
    response = supabase.table("vendors").select("stripe_account_id").eq("vendor_id", vendor_id).single().execute()
    vendor = response.data

    if not vendor or not vendor.get("stripe_account_id"):
        # Fallback: try looking up by id instead of vendor_id
        response = supabase.table("vendors").select("stripe_account_id").eq("id", vendor_id).single().execute()
        vendor = response.data

    if not vendor or not vendor.get("stripe_account_id"):
        raise ValueError(f"Stripe account not found for vendor: {vendor_id}")

    stripe_account_id = vendor["stripe_account_id"]

    # Create Stripe transfer to connected account
    transfer = stripe_lib.Transfer.create(
        amount=amount,
        currency="cad",
        destination=stripe_account_id,
        metadata={"booking_id": booking_id, "vendor_id": vendor_id},
    )

    logger.info(f"Transfer {transfer.id} created: ${amount/100:.2f} to vendor {vendor_id} ({stripe_account_id})")
    return transfer.id


async def create_connect_account_and_link(vendor_id: str, refresh_url: str, return_url: str) -> str:
    """
    Creates a Stripe Connect Express account for a vendor and generates an account link.
    """
    # Check if a Stripe account already exists
    response = supabase.table("vendors").select("stripe_account_id").eq("vendor_id", vendor_id).single().execute()
    vendor = response.data

    stripe_account_id = None
    if vendor and vendor.get("stripe_account_id"):
        stripe_account_id = vendor["stripe_account_id"]
    else:
        account = stripe_lib.Account.create(
            type="express",
            country="CA",
            capabilities={"transfers": {"requested": True}},
        )
        stripe_account_id = account.id
        supabase.table("vendors").update({"stripe_account_id": stripe_account_id}).eq("vendor_id", vendor_id).execute()

    # Generate account link for onboarding
    account_link = stripe_lib.AccountLink.create(
        account=stripe_account_id,
        refresh_url=refresh_url,
        return_url=return_url,
        type="account_onboarding",
        collect_options={"fields": "currently_due"},
    )
    return account_link.url
