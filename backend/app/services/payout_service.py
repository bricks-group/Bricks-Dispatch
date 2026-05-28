"""
Payout service for vendor payouts via Stripe Connect Express.

Vendor tiers (Bricks Dispatch — matches brxs.ca/vendors):
  Tier 1 (A): 40% of package price
  Tier 2 (B): 50% of package price
  Tier 3 (C): 60% of package price
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Tier percentage mapping: tier number -> percentage of package price
# Tier A=1, Tier B=2, Tier C=3 — matches the public-facing Dispatch pitch
TIER_PERCENTAGES = {
    1: 0.40,
    2: 0.50,
    3: 0.60,
}

DEFAULT_TIER_PERCENTAGE = 0.40  # fallback to Tier A


def calculate_payout_amount(package_price: int | None, vendor_tier: int | None) -> int:
    """
    Calculate payout amount in cents.
    package_price is stored as dollars (e.g. 449). Stripe expects cents.
    Tier 3 (C) on Standard ($449): int(449 * 100 * 0.60) = 26940 cents = $269.40.
    """
    if not package_price:
        return 0
    pct = TIER_PERCENTAGES.get(vendor_tier, DEFAULT_TIER_PERCENTAGE)
    return int(package_price * 100 * pct)


async def trigger_payout_for_job(db, job_id: str) -> dict:
    """
    Trigger a payout for a completed job.
    Looks up the job, calculates the payout amount, calls Stripe Connect,
    and records the payout in the payouts table.

    Returns a dict with payout details.
    """
    # Fetch the job
    res = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    if not res or not res.data:
        logger.error(f"Payout trigger failed: job {job_id} not found")
        return {"error": "job_not_found", "job_id": job_id}

    job = res.data
    vendor_id = job.get("vendor_id", "default_vendor")
    package_price = job.get("package_price")
    vendor_tier = job.get("vendor_tier")

    # Calculate payout amount
    payout_amount = calculate_payout_amount(package_price, vendor_tier)
    if payout_amount <= 0:
        logger.warning(
            f"Payout amount is 0 for job {job_id} "
            f"(package_price={package_price}, vendor_tier={vendor_tier})"
        )
        return {"error": "zero_payout_amount", "job_id": job_id}

    now = datetime.now(timezone.utc).isoformat()

    # Check if payout already exists for this job
    existing = await db.table("payouts").select("*").eq("job_id", job_id).maybe_single().execute()
    if existing.data:
        logger.info(f"Payout already exists for job {job_id}: {existing.data['id']}")
        return {
            "payout_id": existing.data["id"],
            "status": existing.data["status"],
            "amount": existing.data["amount"],
            "already_exists": True,
        }

    # Create pending payout record
    payout_record = {
        "job_id": job_id,
        "vendor_id": vendor_id,
        "amount": payout_amount,
        "status": "pending",
        "created_at": now,
    }
    payout_res = await db.table("payouts").insert(payout_record).execute()
    if not payout_res.data:
        logger.error(f"Failed to create payout record for job {job_id}")
        return {"error": "payout_record_creation_failed", "job_id": job_id}

    payout_id = payout_res.data[0]["id"]

    # Update job with payout_amount
    await db.table("jobs").update({"payout_amount": payout_amount}).eq("id", job_id).execute()

    # Add any pending trip fee claims (agent no-shows) to this payout.
    # These are already charged to the agent — photographer is owed the pass-through.
    assignee_id = job.get("assignee_id", "")
    pending_trip_fees_total = 0
    pending_trip_fee_ids = []
    if assignee_id and assignee_id != "default_user":
        res_fees = await db.table("agent_noshow_trip_fees").select("id, amount_cents").eq(
            "member_id", assignee_id
        ).eq("status", "pending").execute()
        for fee in (res_fees.data or []):
            pending_trip_fees_total += fee.get("amount_cents", 0)
            pending_trip_fee_ids.append(fee["id"])
        if pending_trip_fees_total:
            logger.info(
                f"Payout {payout_id}: adding ${pending_trip_fees_total/100:.2f} trip fee(s) "
                f"to photographer {assignee_id} payout (ids: {pending_trip_fee_ids})"
            )

    # Trip fees are added on top — agent was already charged, this is the pass-through to photographer
    net_payout = payout_amount + pending_trip_fees_total

    # Attempt Stripe transfer
    try:
        from ..services.stripe_connect_service import handle_payout_to_vendor

        stripe_transfer_id = await handle_payout_to_vendor(
            vendor_id=vendor_id,
            amount=net_payout,
            booking_id=job.get("booking_id", job_id),
        )

        # Mark payout as paid
        await db.table("payouts").update({
            "status": "paid",
            "stripe_transfer_id": stripe_transfer_id,
            "trip_fees_total": pending_trip_fees_total,
            "net_amount": net_payout,
        }).eq("id", payout_id).execute()

        # Mark trip fees as paid out
        if pending_trip_fee_ids:
            await db.table("agent_noshow_trip_fees").update({
                "status": "paid_out",
                "paid_from_payout_id": payout_id,
                "paid_at": datetime.now(timezone.utc).isoformat(),
            }).in_("id", pending_trip_fee_ids).execute()

        logger.info(
            f"Payout {payout_id} completed for job {job_id}: "
            f"base=${payout_amount/100:.2f} trip_fees=${pending_trip_fees_total/100:.2f} "
            f"total=${net_payout/100:.2f} to vendor {vendor_id} (transfer {stripe_transfer_id})"
        )

        return {
            "payout_id": payout_id,
            "status": "paid",
            "base_amount": payout_amount,
            "trip_fees_total": pending_trip_fees_total,
            "net_amount": net_payout,
            "stripe_transfer_id": stripe_transfer_id,
        }

    except Exception as e:
        logger.error(f"Payout {payout_id} failed for job {job_id}: {e}")

        # Mark payout as failed
        await db.table("payouts").update({
            "status": "failed",
        }).eq("id", payout_id).execute()

        return {
            "payout_id": payout_id,
            "status": "failed",
            "amount": payout_amount,
            "error": str(e),
        }
