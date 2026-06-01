"""
Payout service for vendor payouts via Stripe Connect Express.

Vendor tiers (Bricks Dispatch — matches brxs.ca/vendors):
  Tier 1 (C): 35% of package price — entry
  Tier 2 (B): 45% of package price — core
  Tier 3 (A): 55% of package price — elite

HST on contractor payouts (ETA principal model):
  Fees are EXCLUSIVE of HST. When a photographer is HST-registered (hst_number
  present), they supply photography services to Bricks and must charge HST on
  their fee. Bricks pays the HST component and records it as an ITC.
  T4A Box 048 reports the pre-HST fee only (not the HST component).
  Standard rate used for GTA photographers: Ontario HST 13%.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# HST rate applied when photographer has an HST number.
# GTA-based: Ontario 13%. Adjust per photographer province when province tracking
# is added. For now, 13% is the correct rate for the vast majority of Bricks contractors.
_PHOTOGRAPHER_HST_RATE = 0.13

# Tier percentage mapping: tier number -> percentage of package price
# 1=Tier C (entry,35%), 2=Tier B (core,45%), 3=Tier A (elite,55%)
TIER_PERCENTAGES = {
    1: 0.35,
    2: 0.45,
    3: 0.55,
}

DEFAULT_TIER_PERCENTAGE = 0.35  # fallback to Tier C (entry)


def calculate_payout_amount(package_price: int | None, vendor_tier: int | None) -> int:
    """
    Calculate payout amount in cents.
    package_price is stored as dollars (e.g. 299). Stripe expects cents.
    Tier A (55%) on Standard condo ($299): int(299 * 100 * 0.55) = 16445 cents = $164.45.
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

    # Look up photographer's HST registration status via assignee_id → vendor_members
    assignee_id = job.get("assignee_id", "")
    hst_paid_cents = 0
    contractor_hst_rate = 0.0
    contractor_id = assignee_id
    if assignee_id and assignee_id != "default_user":
        try:
            res_m = await db.table("vendor_members").select("id, hst_number").eq(
                "id", assignee_id
            ).maybe_single().execute()
            member = res_m.data or {}
            if member.get("hst_number"):
                # Photographer is HST-registered: Bricks owes HST on top of fee
                contractor_hst_rate = _PHOTOGRAPHER_HST_RATE
                hst_paid_cents = round(payout_amount * contractor_hst_rate)
                logger.info(
                    f"Photographer {assignee_id} is HST-registered (#{member['hst_number']}); "
                    f"adding ${hst_paid_cents/100:.2f} HST to payout {job_id}"
                )
        except Exception as e:
            logger.warning(f"HST lookup failed for assignee {assignee_id}: {e}")

    now = datetime.now(timezone.utc).isoformat()

    # Check if payout already exists for this job
    existing = await db.table("payouts").select("*").eq("job_id", job_id).maybe_single().execute()
    if existing.data:
        existing_payout_id = existing.data["id"]
        existing_status = existing.data.get("status", "pending")
        logger.info(f"Payout already exists for job {job_id}: {existing_payout_id} (status={existing_status})")

        # Attach any pending trip fees filed after the original payout ran.
        # If the payout is already paid, promote them directly (Stripe already cleared).
        if assignee_id and assignee_id != "default_user" and existing_status == "paid":
            try:
                res_late = await db.table("agent_noshow_trip_fees").select("id, amount_cents").eq(
                    "member_id", assignee_id
                ).eq("status", "pending").execute()
                late_ids = [f["id"] for f in (res_late.data or [])]
                if late_ids:
                    await db.table("agent_noshow_trip_fees").update({
                        "status": "paid_out",
                        "paid_from_payout_id": existing_payout_id,
                        "paid_at": datetime.now(timezone.utc).isoformat(),
                    }).in_("id", late_ids).execute()
                    logger.info(f"Late trip fees {late_ids} attached to existing payout {existing_payout_id}")
            except Exception as e:
                logger.warning(f"Late trip fee attachment failed for job {job_id}: {e}")

        return {
            "payout_id": existing_payout_id,
            "status": existing_status,
            "amount": existing.data["amount"],
            "already_exists": True,
        }

    # Create pending payout record
    payout_record = {
        "job_id": job_id,
        "vendor_id": vendor_id,
        "contractor_id": contractor_id,
        "amount": payout_amount,
        "hst_paid_cents": hst_paid_cents,
        "contractor_hst_rate": contractor_hst_rate,
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
    # Trip fees also attract HST if photographer is registered (T4A Box 048 includes them).
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

    # HST on trip fees (if photographer is registered)
    if contractor_hst_rate and pending_trip_fees_total:
        trip_fee_hst = round(pending_trip_fees_total * contractor_hst_rate)
        hst_paid_cents += trip_fee_hst

    # net_payout = base fee + trip fees + HST (if registered)
    net_payout = payout_amount + pending_trip_fees_total + hst_paid_cents

    # Reserve trip fees before touching Stripe to prevent double-payout on retry.
    # If Stripe succeeds we promote to paid_out; if it fails we release the reservation.
    if pending_trip_fee_ids:
        await db.table("agent_noshow_trip_fees").update({
            "status": "reserved",
        }).in_("id", pending_trip_fee_ids).execute()

    # Attempt Stripe transfer — separate try/except so post-Stripe DB failures
    # don't roll back a transfer that already cleared.
    from ..services.stripe_connect_service import handle_payout_to_vendor
    try:
        stripe_transfer_id = await handle_payout_to_vendor(
            vendor_id=vendor_id,
            amount=net_payout,
            booking_id=job.get("booking_id", job_id),
            payout_id=payout_id,
        )
    except Exception as e:
        logger.error(f"Stripe transfer failed for payout {payout_id} job {job_id}: {e}")
        # Stripe never charged — safe to release trip fees and mark failed
        if pending_trip_fee_ids:
            await db.table("agent_noshow_trip_fees").update({
                "status": "pending",
            }).in_("id", pending_trip_fee_ids).execute()
        await db.table("payouts").update({"status": "failed"}).eq("id", payout_id).execute()
        return {
            "payout_id": payout_id,
            "status": "failed",
            "amount": payout_amount,
            "error": str(e),
        }

    # Stripe succeeded — post-Stripe DB updates; fail open (never rollback a completed transfer)
    try:
        await db.table("payouts").update({
            "status": "paid",
            "stripe_transfer_id": stripe_transfer_id,
            "trip_fees_total": pending_trip_fees_total,
            "hst_paid_cents": hst_paid_cents,
            "net_amount": net_payout,
            "paid_at": now,
        }).eq("id", payout_id).execute()

        if pending_trip_fee_ids:
            await db.table("agent_noshow_trip_fees").update({
                "status": "paid_out",
                "paid_from_payout_id": payout_id,
                "paid_at": datetime.now(timezone.utc).isoformat(),
            }).in_("id", pending_trip_fee_ids).execute()
    except Exception as e:
        # Transfer cleared — log for manual reconciliation; do NOT revert payout status
        logger.error(
            f"Post-Stripe DB update failed for payout {payout_id} job {job_id}: {e} — "
            f"Stripe transfer {stripe_transfer_id} succeeded; manual reconciliation required"
        )

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
