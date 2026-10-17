"""
Payout service for vendor payouts via Stripe Connect Express.

Vendor tiers (Bricks GO — matches brxs.ca/vendors):
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


def calculate_payout_amount(rack_price: int | None, vendor_tier: int | None) -> int:
    """
    Calculate payout amount in cents from tier_pct * rack_price.
    rack_price is the undiscounted rate, stored as dollars (e.g. 299). Stripe expects cents.
    Tier A (55%) on Essentials-band condo rack ($299): int(299 * 100 * 0.55) = 16445 cents = $164.45.

    Must always be called with rack_price, never a customer-charged/discounted price --
    a member discount or brokerage credit consumption must never shrink a vendor's payout.

    2026-08-03: superseded as the primary payout calculation by the fixed rate card
    (get_fixed_payout_amount / vendor_payout_rates) -- this remains the fallback used
    when a job predates that migration or has no package_id/sqft recorded. Left
    unchanged (signature, behaviour, existing tests) since it's still load-bearing.
    """
    if not rack_price:
        return 0
    pct = TIER_PERCENTAGES.get(vendor_tier, DEFAULT_TIER_PERCENTAGE)
    return int(rack_price * 100 * pct)


# www client footprint bands (match brxs.ca): Small ≤1,200 / Mid 1,201–2,500 /
# Large 2,501–4,000 / XL 4,001+. Ceilings are inclusive upper bounds of each band.
_SQFT_TIER_BOUNDARIES = (1200, "small"), (2500, "mid"), (4000, "large")

# Canonical www package_ids for vendor_payout_rates (5 packages × 4 bands × 3 tiers).
_WWW_PACKAGE_IDS = frozenset({"photos_only", "essentials", "story", "showcase", "full"})

# Normalize alternate spellings onto the five www cells. Each resolves to its OWN
# package_id — not shared standard/premium/luxury. Legacy package_ids
# (standard/premium/luxury/estate) are intentionally absent here so they pass
# through unchanged for historical job reads only; nothing new aliases TO estate.
_PACKAGE_RATE_ALIASES = {
    "photos_only": "photos_only",
    "photosonly": "photos_only",
    "photos": "photos_only",
    "essentials": "essentials",
    "story": "story",
    "showcase": "showcase",
    "suite": "full",
    "full": "full",
    "full_suite": "full",
}

# Explicit legacy map: old shared cells remain readable for jobs booked before
# the www reprice. Not used as alias *targets* for new www catalog ids.
_LEGACY_PACKAGE_IDS = frozenset({"standard", "premium", "luxury", "estate"})

# Soft-retired for NEW job intake only. Historical jobs keep these package_ids
# so vendor_payout_rates legacy rows still resolve. Nothing maps www IDs onto these.
_RETIRED_FOR_NEW_JOBS = _LEGACY_PACKAGE_IDS


def normalize_package_id_key(package_id: str | None) -> str | None:
    if not package_id:
        return None
    return package_id.strip().lower().replace("-", "_").replace(" ", "_")


def is_retired_for_new_jobs(package_id: str | None) -> bool:
    key = normalize_package_id_key(package_id)
    return bool(key and key in _RETIRED_FOR_NEW_JOBS)


def _rate_card_package_id(package_id: str | None) -> str | None:
    key = normalize_package_id_key(package_id)
    if not key:
        return None
    return _PACKAGE_RATE_ALIASES.get(key, key)


def _sqft_tier(sqft: int | None) -> str:
    """Client footprint band for vendor_payout_rates: Small≤1200 / Mid≤2500 / Large≤4000 / XL."""
    if sqft:
        for ceiling, tier in _SQFT_TIER_BOUNDARIES:
            if sqft <= ceiling:
                return tier
    return "xl" if sqft else "small"


_payout_rate_cache: dict = {}
_payout_rate_cache_ts: float = 0.0
_PAYOUT_RATE_CACHE_TTL = 300  # 5 min, same convention as Companion's sqft-price cache


async def get_fixed_payout_amount(db, package_id: str | None, sqft: int | None, vendor_tier: int | None) -> int | None:
    """
    Look up the flat vendor_payout_rates cell for (package, sqft band, tier).
    Returns None (caller should fall back to calculate_payout_amount) if no
    package_id is known or no row matches -- keeps this additive, never a hard
    dependency for jobs that predate the rate card.

    De-indexes payout from rack_price entirely, so a customer's volume discount
    can never compress what a vendor is paid -- previously both discount and
    payout read the same rack_price, and a large discount stacked with a high
    vendor tier could compress Bricks' own margin toward (or below) zero on
    that booking. Seeded from exactly what calculate_payout_amount already
    computed for today's live prices -- day one, no vendor's pay changes.
    """
    global _payout_rate_cache, _payout_rate_cache_ts
    package_id = _rate_card_package_id(package_id)
    if not package_id:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if not _payout_rate_cache or now - _payout_rate_cache_ts >= _PAYOUT_RATE_CACHE_TTL:
        rows = []
        try:
            res = await db.table("vendor_payout_rates").select(
                "package_id,sqft_tier,vendor_tier,amount_cents"
            ).execute()
            rows = res.data or []
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_payout_rates
            if _is_rest_quota_error(e):
                rows = pg_select_vendor_payout_rates() or []
                logger.warning("vendor_payout_rates Fair-Use PG fallback n=%s", len(rows))
            else:
                raise
        if rows:
            _payout_rate_cache = {
                (r["package_id"], r["sqft_tier"], r["vendor_tier"]): r["amount_cents"] for r in rows
            }
            _payout_rate_cache_ts = now
    tier = vendor_tier if vendor_tier in TIER_PERCENTAGES else 1
    return _payout_rate_cache.get((package_id, _sqft_tier(sqft), tier))


async def _db_select_job(db, job_id: str) -> dict | None:
    from ..shared.database import maybe_single_row
    from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
    try:
        res = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        return maybe_single_row(res) or None
    except Exception as e:
        if _is_rest_quota_error(e):
            return pg_select_job_by_id(job_id)
        raise


async def _db_select_vendor_member(db, assignee_id: str) -> dict:
    from ..shared.database import maybe_single_row
    from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member
    try:
        res_m = await db.table("vendor_members").select(
            "id, tier, hst_number, role, title, payout_mode, email"
        ).eq("id", assignee_id).maybe_single().execute()
        return maybe_single_row(res_m)
    except Exception as e:
        if _is_rest_quota_error(e):
            return pg_select_vendor_member(actor_id=assignee_id) or {}
        raise


async def _db_select_payout_by_job(db, job_id: str) -> dict | None:
    from ..shared.database import maybe_single_row
    from ..shared.pg_fallback import _is_rest_quota_error, pg_select_payout_by_job_id
    try:
        existing = await db.table("payouts").select("*").eq("job_id", job_id).maybe_single().execute()
        return maybe_single_row(existing) or None
    except Exception as e:
        if _is_rest_quota_error(e):
            return pg_select_payout_by_job_id(job_id)
        raise


async def _db_update_payout(db, payout_id: str, fields: dict) -> None:
    from ..shared.pg_fallback import _is_rest_quota_error, pg_update_payout
    try:
        await db.table("payouts").update(fields).eq("id", payout_id).execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            if not pg_update_payout(payout_id, fields):
                raise RuntimeError("payouts Fair-Use PG update failed") from e
            return
        raise


async def _db_insert_payout(db, payout_record: dict) -> dict | None:
    from ..shared.pg_fallback import _is_rest_quota_error, pg_insert_payout
    try:
        payout_res = await db.table("payouts").insert(payout_record).execute()
        if payout_res.data:
            return payout_res.data[0]
        return None
    except Exception as e:
        if _is_rest_quota_error(e):
            return pg_insert_payout(payout_record)
        raise


async def _db_update_job_payout_amount(db, job_id: str, payout_amount: int) -> None:
    from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
    try:
        await db.table("jobs").update({"payout_amount": payout_amount}).eq("id", job_id).execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            if not pg_update_job(job_id, {"payout_amount": payout_amount}):
                raise RuntimeError("jobs.payout_amount Fair-Use PG update failed") from e
            return
        raise


async def _db_select_trip_fees(
    db, assignee_id: str, *, status: str = "pending", created_at_lte: str | None = None, cols: str = "id, amount_cents"
) -> list:
    from ..shared.pg_fallback import _is_rest_quota_error, pg_select_trip_fees
    try:
        q = db.table("agent_noshow_trip_fees").select(cols).eq(
            "member_id", assignee_id
        ).eq("status", status)
        if created_at_lte:
            q = q.lte("created_at", created_at_lte)
        if status == "pending" and cols == "id":
            q = q.limit(1)
        res = await q.execute()
        return res.data or []
    except Exception as e:
        if _is_rest_quota_error(e):
            return pg_select_trip_fees(
                assignee_id, status=status, created_at_lte=created_at_lte, limit=1 if cols == "id" else 200
            )
        raise


async def _db_update_trip_fees_by_ids(db, fee_ids: list, fields: dict) -> None:
    from ..shared.pg_fallback import _is_rest_quota_error, pg_update_trip_fees_by_ids
    if not fee_ids:
        return
    try:
        await db.table("agent_noshow_trip_fees").update(fields).in_("id", fee_ids).execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            if not pg_update_trip_fees_by_ids(fee_ids, fields):
                raise RuntimeError("trip_fees Fair-Use PG update failed") from e
            return
        raise


async def _db_update_trip_fees_for_member(
    db, assignee_id: str, *, status_from: str, fields: dict, created_at_lte: str | None = None
) -> None:
    from ..shared.pg_fallback import _is_rest_quota_error, pg_update_trip_fees_for_member
    try:
        q = db.table("agent_noshow_trip_fees").update(fields).eq(
            "member_id", assignee_id
        ).eq("status", status_from)
        if created_at_lte:
            q = q.lte("created_at", created_at_lte)
        await q.execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            if not pg_update_trip_fees_for_member(
                assignee_id, status_from=status_from, fields=fields, created_at_lte=created_at_lte
            ):
                raise RuntimeError("trip_fees member Fair-Use PG update failed") from e
            return
        raise


async def trigger_payout_for_job(db, job_id: str) -> dict:
    """
    Trigger a payout for a completed job.
    Looks up the job, calculates the payout amount, calls Stripe Connect,
    and records the payout in the payouts table.

    Returns a dict with payout details.
    Fair-Use: every PostgREST path falls back to Postgres on 402.
    """
    # Fetch the job
    job = await _db_select_job(db, job_id)
    if not job:
        logger.error(f"Payout trigger failed: job {job_id} not found")
        return {"error": "job_not_found", "job_id": job_id}

    vendor_id = job.get("vendor_id", "default_vendor")
    # Always pay off rack_price, never package_price -- package_price may reflect a
    # member discount or brokerage credit consumption once that billing logic ships,
    # and a customer-side discount must never shrink what the vendor is paid.
    # Falls back to package_price only for rows created before rack_price existed.
    rack_price = job.get("rack_price") if job.get("rack_price") is not None else job.get("package_price")
    vendor_tier = job.get("vendor_tier")
    assignee_id = job.get("assignee_id", "")

    # Look up the assignee's current tier and HST status in a single query.
    # vendor_members.tier is the authoritative source — jobs.vendor_tier can be stale
    # after reassignment (e.g. contractor no-show → new photographer of a different tier).
    _member_hst_number = None
    contractor_id = assignee_id
    member = {}
    if assignee_id and assignee_id != "default_user":
        try:
            member = await _db_select_vendor_member(db, assignee_id)
            if member.get("tier"):
                vendor_tier = member["tier"]
            _member_hst_number = member.get("hst_number")
        except Exception as e:
            logger.warning(f"Member lookup failed for assignee {assignee_id}: {e}")

    # Owner: full package/rack, $0 deduction, no IC HST add-on.
    # ICs: fixed vendor_payout_rates card (never % of rack).
    from ..shared.vendor_identity import is_owner_member as _is_owner_member
    from .vendor_profile import owner_payout_cents
    from .commercial_jobs import commercial_payout_cents, is_employee
    owner_job = _is_owner_member(member)
    if (not owner_job) and member and is_employee(member):
        # In-house staff are salaried outside Dispatch: no per-job payout row.
        logger.info(f"Job {job_id}: assignee {assignee_id} is an employee; no per-job payout")
        return {"skipped": "employee_salaried", "job_id": job_id, "amount": 0}
    if owner_job:
        payout_amount = owner_payout_cents(job)
        logger.info(f"Owner payout for job {job_id}: full package {payout_amount} cents, $0 deduction")
    elif commercial_payout_cents(job) is not None:
        # Commercial (builder/developer) visit: fixed sourced table, Tier A only.
        payout_amount = int(commercial_payout_cents(job))
        logger.info(f"Commercial payout for job {job_id}: {payout_amount} cents")
    else:
        payout_amount = await get_fixed_payout_amount(db, job.get("package_id"), job.get("sqft"), vendor_tier)
        if payout_amount is None:
            payout_amount = calculate_payout_amount(rack_price, vendor_tier)
        from .vendor_payout_rate_card import addon_payout_cents
        payout_amount = int(payout_amount or 0) + addon_payout_cents(
            job.get("add_ons"), job.get("sqft"), vendor_tier
        )
    if payout_amount <= 0:
        # Don't bail if there are pending trip fees — a photographer who only earned a
        # no-show trip fee on a zero-price job still needs to be paid out.
        if assignee_id and assignee_id != "default_user":
            _fee_check = await _db_select_trip_fees(
                db, assignee_id, status="pending", cols="id"
            )
            if not _fee_check:
                logger.warning(
                    f"Payout amount is 0 for job {job_id} and no pending trip fees "
                    f"(rack_price={rack_price}, vendor_tier={vendor_tier})"
                )
                return {"error": "zero_payout_amount", "job_id": job_id}
            logger.info(
                f"Job {job_id}: base payout is 0 but photographer {assignee_id} has pending "
                f"trip fees — continuing to pay them out"
            )
        else:
            logger.warning(
                f"Payout amount is 0 for job {job_id} "
                f"(rack_price={rack_price}, vendor_tier={vendor_tier})"
            )
            return {"error": "zero_payout_amount", "job_id": job_id}

    # HST on contractor fee (ETA principal model — paid on top of base fee)
    hst_paid_cents = 0
    contractor_hst_rate = 0.0
    if (not owner_job) and _member_hst_number and assignee_id and assignee_id != "default_user":
        contractor_hst_rate = _PHOTOGRAPHER_HST_RATE
        hst_paid_cents = round(payout_amount * contractor_hst_rate)
        logger.info(
            f"Photographer {assignee_id} is HST-registered (#{_member_hst_number}); "
            f"adding ${hst_paid_cents/100:.2f} HST to payout {job_id}"
        )

    now = datetime.now(timezone.utc).isoformat()

    # Check if payout already exists for this job
    existing_data = await _db_select_payout_by_job(db, job_id)
    is_retry = False
    reuse_payout_id = None
    retry_created_at = ""  # set below when reusing an existing payout record
    if existing_data:
        existing_payout_id = existing_data["id"]
        existing_status = existing_data.get("status", "pending")
        existing_transfer_id = existing_data.get("stripe_transfer_id")
        logger.info(f"Payout already exists for job {job_id}: {existing_payout_id} (status={existing_status})")

        if existing_status == "pending" and existing_transfer_id:
            # Stripe transfer succeeded but the post-Stripe DB update was lost.
            # Complete reconciliation now: promote to paid and finalize any reserved trip fees.
            logger.info(
                f"Reconciling orphaned payout {existing_payout_id} for job {job_id}: "
                f"transfer {existing_transfer_id} already succeeded"
            )
            payout_created_at = existing_data.get("created_at", "")
            try:
                await _db_update_payout(db, existing_payout_id, {
                    "status": "paid",
                    "paid_at": datetime.now(timezone.utc).isoformat(),
                })
                # Promote only trip fees that were reserved at or before this payout's created_at;
                # fees added after payout creation belong to the next payout cycle.
                if assignee_id and assignee_id != "default_user":
                    await _db_update_trip_fees_for_member(
                        db,
                        assignee_id,
                        status_from="reserved",
                        fields={
                            "status": "paid_out",
                            "paid_via_payout_id": existing_payout_id,
                            "paid_at": datetime.now(timezone.utc).isoformat(),
                        },
                        created_at_lte=payout_created_at or None,
                    )
            except Exception as e:
                logger.error(f"Reconciliation update failed for payout {existing_payout_id}: {e}")
            return {
                "payout_id": existing_payout_id,
                "status": "paid",
                "base_amount": existing_data.get("amount"),
                "already_exists": True,
                "reconciled": True,
            }

        # A pending record with no Stripe transfer ID means the process crashed before the
        # Stripe call was made — treat it as retriable, same as "failed".
        is_retriable = existing_status == "failed" or (
            existing_status == "pending" and not existing_transfer_id
        )
        if not is_retriable:
            # Already paid — idempotent return, but reconcile any reserved trip fees
            # that survived a prior post-Stripe DB failure (stuck in "reserved" forever
            # because the normal L278-283 update threw and the payout is now closed).
            if existing_status == "paid" and assignee_id and assignee_id != "default_user":
                payout_created_at = existing_data.get("created_at", "")
                try:
                    await _db_update_trip_fees_for_member(
                        db,
                        assignee_id,
                        status_from="reserved",
                        fields={
                            "status": "paid_out",
                            "paid_via_payout_id": existing_payout_id,
                            "paid_at": datetime.now(timezone.utc).isoformat(),
                        },
                        created_at_lte=payout_created_at or None,
                    )
                except Exception as e:
                    logger.error(f"Trip-fee reconciliation failed for paid payout {existing_payout_id}: {e}")
            return {
                "payout_id": existing_payout_id,
                "status": existing_status,
                "base_amount": existing_data.get("amount"),
                "already_exists": True,
            }
        # Failed or stale-pending payout — reuse the existing record's ID so the Stripe
        # idempotency key (`payout_{id}`) is unchanged. If the original transfer actually
        # succeeded but the DB status-update was lost, Stripe returns the existing transfer
        # instead of creating a duplicate payment.
        logger.info(f"Retrying payout for job {job_id} (status={existing_status}, reusing record {existing_payout_id})")
        is_retry = True
        reuse_payout_id = existing_payout_id
        retry_created_at = existing_data.get("created_at", "")

    # Fetch pending trip fees BEFORE creating the payout record so we can compute the
    # complete hst_paid_cents (base fee HST + trip fee HST) in the initial insert.
    # This avoids an inconsistent state where the payout record has stale HST if the
    # post-Stripe DB update fails.
    pending_trip_fees_total = 0
    pending_trip_fee_ids = []
    if assignee_id and assignee_id != "default_user":
        fees = await _db_select_trip_fees(
            db,
            assignee_id,
            status="pending",
            created_at_lte=retry_created_at if (is_retry and retry_created_at) else None,
            cols="id, amount_cents",
        )
        for fee in fees:
            pending_trip_fees_total += fee.get("amount_cents", 0)
            pending_trip_fee_ids.append(fee["id"])

    # HST on trip fees (if photographer is registered); add to base-fee HST
    if contractor_hst_rate and pending_trip_fees_total:
        trip_fee_hst = round(pending_trip_fees_total * contractor_hst_rate)
        hst_paid_cents += trip_fee_hst

    # net_payout = base fee + trip fees + total HST (if registered)
    net_payout = payout_amount + pending_trip_fees_total + hst_paid_cents

    if pending_trip_fees_total:
        logger.info(
            f"Payout for job {job_id}: adding ${pending_trip_fees_total/100:.2f} trip fee(s) "
            f"to photographer {assignee_id} (ids: {pending_trip_fee_ids})"
        )

    # Create or reset the payout record
    if is_retry:
        # Update existing failed record in-place — same payout_id preserves Stripe idempotency key
        await _db_update_payout(db, reuse_payout_id, {
            "amount": payout_amount,
            "hst_paid_cents": hst_paid_cents,
            "contractor_hst_rate": contractor_hst_rate,
            "contractor_id": contractor_id or "",
            "status": "pending",
        })
        payout_id = reuse_payout_id
    else:
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
        inserted = await _db_insert_payout(db, payout_record)
        if not inserted:
            logger.error(f"Failed to create payout record for job {job_id}")
            return {"error": "payout_record_creation_failed", "job_id": job_id}
        payout_id = inserted["id"]

    # Update job with payout_amount
    await _db_update_job_payout_amount(db, job_id, payout_amount)

    # Reserve trip fees before touching Stripe to prevent double-payout on retry.
    # If Stripe succeeds we promote to paid_out; if it fails we release the reservation.
    if pending_trip_fee_ids:
        await _db_update_trip_fees_by_ids(db, pending_trip_fee_ids, {"status": "reserved"})

    # Attempt Stripe transfer — separate try/except so post-Stripe DB failures
    # don't roll back a transfer that already cleared.
    from .stripe_connect_service import handle_payout_to_vendor
    try:
        stripe_transfer_id = await handle_payout_to_vendor(
            vendor_id=vendor_id,
            contractor_id=contractor_id or "",
            amount=net_payout,
            booking_id=job.get("booking_id", job_id),
            payout_id=payout_id,
        )
    except Exception as e:
        logger.error(f"Stripe transfer failed for payout {payout_id} job {job_id}: {e}")
        # Stripe never charged — safe to release trip fees and mark failed
        if pending_trip_fee_ids:
            await _db_update_trip_fees_by_ids(db, pending_trip_fee_ids, {"status": "pending"})
        await _db_update_payout(db, payout_id, {"status": "failed"})
        return {
            "payout_id": payout_id,
            "status": "failed",
            "amount": payout_amount,
            "net_amount": net_payout,
            "error": str(e),
        }

    # Stripe succeeded — post-Stripe DB updates; fail open (never rollback a completed transfer)
    try:
        await _db_update_payout(db, payout_id, {
            "status": "paid",
            "stripe_transfer_id": stripe_transfer_id,
            "trip_fees_total": pending_trip_fees_total,
            "hst_paid_cents": hst_paid_cents,
            "net_amount": net_payout,
            "paid_at": now,
        })

        if pending_trip_fee_ids:
            await _db_update_trip_fees_by_ids(db, pending_trip_fee_ids, {
                "status": "paid_out",
                "paid_via_payout_id": payout_id,
                "paid_at": datetime.now(timezone.utc).isoformat(),
            })
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
