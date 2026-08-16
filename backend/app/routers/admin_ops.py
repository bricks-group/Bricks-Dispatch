import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Header, HTTPException

from ..shared.auth import is_vendor_admin, get_vendor_member, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.pagination import normalize_pagination, paginated_response
from ..shared.schemas import VendorMemberUpdate, VendorLead
from ..services.webhooks import send_dispatch_status_update_webhook
from ..services.vendor_selection import assign_vendor_for_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin-ops"])

@router.post("/leads")
async def create_lead(
    payload: VendorLead,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    lead_dict = payload.dict()
    await (await get_db()).table("vendor_leads").insert(lead_dict).execute()
    return lead_dict

@router.get("/leads")
async def list_leads(
    status: Optional[str] = None,
    city: Optional[str] = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    sb = await get_db(); query = sb.table("vendor_leads").select("*")
    if status: query = query.eq("status", status)
    if city: query = query.eq("city", city)
    
    res = await query.order("created_at", ascending=False).limit(100).execute()
    return res.data

@router.get("/applicants")
async def list_applicants(
    status: Optional[str] = "pending",
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions for vetting", "insufficient_permissions")
        
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    sb = await get_db(); query = sb.table("vendor_members").select("*", count="exact").eq("vendor_id", vendor_id).eq("onboarding_status", status)
    
    page, page_size, skip = normalize_pagination(page, page_size)
    res = await query.order("created_at", ascending=False).range(skip, skip + page_size - 1).execute()
    
    applicants = res.data
    total = res.count or 0
    
    return paginated_response(applicants, total, page, page_size)

@router.patch("/applicants/{member_id}")
async def update_applicant_status(
    member_id: str,
    payload: VendorMemberUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        api_error(400, "No update data provided", "no_update_data")
        
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    if update_data.get("onboarding_status") == "approved":
        update_data["is_active"] = True
        update_data["role"] = "Photographer"
    
    res = await (await get_db()).table("vendor_members").update(update_data).eq("id", member_id).eq("vendor_id", vendor_id).execute()
    
    if not res.data:
        api_error(404, "Applicant not found", "applicant_not_found")
        
    return res.data[0]

async def _require_applicant_in_own_vendor(db, member_id: str, vendor_id: str):
    """IDOR guard (fixed 2026-08-15): list_applicants/update_applicant_status both
    scope by vendor_id, but this pair of document endpoints only matched on
    member_id/doc_id -- a vendor admin could view or verify ANY vendor's
    applicant documents by guessing/observing a member_id, not just their own
    vendor's. Fail closed if the applicant isn't in the caller's vendor org."""
    res = await db.table("vendor_members").select("vendor_id").eq("id", member_id).maybe_single().execute()
    if not res.data or res.data.get("vendor_id") != vendor_id:
        api_error(404, "Applicant not found", "applicant_not_found")

@router.get("/applicants/{member_id}/documents")
async def get_applicant_documents(
    member_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)

    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()
    await _require_applicant_in_own_vendor(db, member_id, actor_member.get("vendor_id", "default_vendor"))

    res = await db.table("vendor_documents").select("*").eq("member_id", member_id).limit(50).execute()
    return res.data

@router.put("/applicants/{member_id}/documents/{doc_id}/verify")
async def verify_document(
    member_id: str,
    doc_id: str,
    status: str = "verified",
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)

    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()
    await _require_applicant_in_own_vendor(db, member_id, actor_member.get("vendor_id", "default_vendor"))

    res = await db.table("vendor_documents").update({
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", doc_id).eq("member_id", member_id).execute()

    if not res.data:
        api_error(404, "Document not found", "document_not_found")

    return {"id": doc_id, "status": status}


@router.post("/validate-rpas-batch")
async def validate_rpas_batch(
    dry_run: bool = True,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()

    res_docs = await db.table("vendor_documents").select(
        "id, member_id, file_url, status"
    ).eq("doc_type", "rpas_certificate").eq("status", "pending").limit(50).execute()
    docs = res_docs.data or []

    if dry_run:
        return {
            "dry_run": True,
            "pending_count": len(docs),
            "members": [{"member_id": d["member_id"], "doc_id": d["id"]} for d in docs],
        }

    from ..services.rpas_validator import validate_rpas_certificate as _validate
    from ..services.storage import get_signed_download_url

    results = []
    for doc in docs:
        member_id = doc["member_id"]
        try:
            res_m = await db.table("vendor_members").select("name").eq("id", member_id).maybe_single().execute()
            vendor_name = (res_m.data or {}).get("name", "")

            image_url = await get_signed_download_url(doc["file_url"])
            if not image_url:
                results.append({"member_id": member_id, "outcome": "failed", "error": "no_signed_url"})
                continue

            validation = await _validate(image_url=image_url, vendor_name=vendor_name)

            doc_status = "verified" if validation["outcome"] == "verified" else (
                "flagged" if validation["outcome"] == "suspicious" else "pending"
            )
            await db.table("vendor_documents").update({
                "status": doc_status,
                "validation_result": validation,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", doc["id"]).execute()

            if validation["outcome"] in ("verified", "extracted"):
                await db.table("vendor_members").update({
                    "drone_certified": True,
                    "rpas_certificate_number": validation.get("certificate_number"),
                    "rpas_certificate_level": validation.get("level"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", member_id).execute()

            results.append({
                "member_id": member_id,
                "doc_id": doc["id"],
                "outcome": validation["outcome"],
                "level": validation.get("level"),
                "issues": validation.get("issues", []),
                "drone_certified": validation["outcome"] in ("verified", "extracted"),
            })
        except Exception as e:
            results.append({"member_id": member_id, "outcome": "error", "error": str(e)})

    verified = sum(1 for r in results if r.get("drone_certified"))
    return {
        "dry_run": False,
        "processed": len(results),
        "verified": verified,
        "flagged": sum(1 for r in results if r.get("outcome") == "suspicious"),
        "failed": sum(1 for r in results if r.get("outcome") in ("failed", "error")),
        "results": results,
    }


# ---------------------------------------------------------------------------
# No-show claim review queue
# ---------------------------------------------------------------------------

@router.get("/noshow-claims")
async def list_noshow_claims(
    status: Optional[str] = "pending_review",
    page: int = 1,
    page_size: int = 50,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    List no-show trip fee claims. Defaults to pending_review (admin work queue).
    Pass status=all to see every claim regardless of state.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()
    page, page_size, skip = normalize_pagination(page, page_size)

    query = db.table("agent_noshow_trip_fees").select("*", count="exact")
    if status and status != "all":
        query = query.eq("status", status)

    res = await query.order("created_at", ascending=False).range(skip, skip + page_size - 1).execute()
    return paginated_response(res.data or [], res.count or 0, page, page_size)


@router.post("/noshow-claims/{claim_id}/review")
async def review_noshow_claim(
    claim_id: str,
    action: str,
    admin_notes: Optional[str] = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Approve or reject a flagged no-show claim.
    approve → status='pending' (paid on next payout cycle)
    reject  → status='rejected', claim voided
    """
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    res_claim = await db.table("agent_noshow_trip_fees").select("*").eq(
        "id", claim_id
    ).maybe_single().execute()
    if not res_claim.data:
        raise HTTPException(status_code=404, detail="Claim not found")

    claim = res_claim.data
    if claim.get("status") != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Claim is not pending review (current status: {claim.get('status')})"
        )

    new_status = "rejected" if action == "reject" else "pending"

    await db.table("agent_noshow_trip_fees").update({
        "status": new_status,
        "reviewed_by": actor_id,
        "reviewed_at": now,
        "admin_notes": admin_notes or "",
    }).eq("id", claim_id).execute()

    logger.info(f"No-show claim {claim_id} {action}d by {actor_id}")

    job_id = claim.get("job_id")
    booking_id = claim.get("booking_id")

    if action == "approve" and booking_id and job_id:
        # Booking webhook was deferred at claim time for pending_review holds.
        # Fire it now that admin has confirmed the no-show is valid.
        # previous_status="pending_review" signals this is the deferred notification
        # confirming a held claim — Booking uses it to apply the retention penalty.
        try:
            await send_dispatch_status_update_webhook(
                booking_id=booking_id,
                job_id=job_id,
                new_status="agent_no_show",
                previous_status="pending_review",
                actor_id=actor_id,
                actor_type="admin",
            )
        except Exception as e:
            logger.warning(f"Booking no-show webhook failed for {booking_id} (claim {claim_id}): {e}")

    elif action == "reject" and booking_id and job_id:
        # Trip fee voided — notify Booking to reverse the agent retention penalty.
        # Job stays in agent_no_show; only the financial consequence is reversed.
        try:
            await send_dispatch_status_update_webhook(
                booking_id=booking_id,
                job_id=job_id,
                new_status="no_show_reversed",
                previous_status="agent_no_show",
                actor_id=actor_id,
                actor_type="admin",
            )
        except Exception as e:
            logger.warning(f"Booking reversal webhook failed for {booking_id} (claim {claim_id}): {e}")

    return {
        "claim_id": claim_id,
        "status": new_status,
        "reviewed_by": actor_id,
        "note": "Photographer will receive $60 on next payout cycle." if action == "approve" else "Claim voided.",
    }


# ---------------------------------------------------------------------------
# No-show reconciliation
# ---------------------------------------------------------------------------

@router.get("/reconcile-noshow")
async def reconcile_noshow_claims(
    dry_run: bool = True,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Match agent_noshow_trip_fees against bookings.status to surface discrepancies.

    Detects:
    - fee_without_retention: trip fee exists but booking not marked as retained
    - retention_without_fee: booking shows no-show but no trip fee record (photographer did not file)
    - booking_missing: trip fee references a booking that no longer exists

    dry_run=true (default): report mismatches, write nothing.
    dry_run=false: write reconciliation_events rows for each mismatch.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()

    # All active (non-rejected) trip fee claims
    res_fees = await db.table("agent_noshow_trip_fees").select(
        "id, job_id, booking_id, member_id, status, amount_cents, created_at"
    ).in_("status", ["pending", "paid_out", "pending_review"]).execute()
    fees = res_fees.data or []

    mismatches = []

    for fee in fees:
        booking_id = fee.get("booking_id")
        if not booking_id:
            continue
        try:
            res_bk = await db.table("bookings").select(
                "id, status"
            ).eq("id", booking_id).maybe_single().execute()

            if not res_bk.data:
                mismatches.append({
                    "type": "booking_missing",
                    "fee_id": fee["id"],
                    "booking_id": booking_id,
                    "note": "Booking record not found",
                })
                continue

            bk_status = res_bk.data.get("status", "")
            # pending_review fees are held for admin approval; the Booking retention webhook
            # fires only after approval — these are expected not to show retained yet.
            if fee.get("status") == "pending_review":
                continue
            if bk_status not in ("retained", "agent_no_show", "no_show"):
                mismatches.append({
                    "type": "fee_without_retention",
                    "fee_id": fee["id"],
                    "booking_id": booking_id,
                    "booking_status": bk_status,
                    "fee_status": fee["status"],
                    "amount_cents": fee["amount_cents"],
                    "note": "Trip fee exists but booking not marked as retained — Booking webhook may need retry",
                })
        except Exception as e:
            mismatches.append({
                "type": "error",
                "fee_id": fee["id"],
                "booking_id": booking_id,
                "note": str(e),
            })

    # Reverse check: retained bookings with no trip fee
    try:
        res_bk_noshow = await db.table("bookings").select(
            "id, status"
        ).in_("status", ["retained", "agent_no_show", "no_show"]).limit(200).execute()
        fee_booking_ids = {f.get("booking_id") for f in fees}
        for bk in (res_bk_noshow.data or []):
            if bk["id"] not in fee_booking_ids:
                mismatches.append({
                    "type": "retention_without_fee",
                    "booking_id": bk["id"],
                    "booking_status": bk["status"],
                    "note": "Booking retained but no photographer trip fee — photographer may not have filed",
                })
    except Exception as e:
        logger.warning(f"Reverse reconciliation check failed: {e}")

    if not dry_run and mismatches:
        now = datetime.now(timezone.utc).isoformat()
        for m in mismatches:
            try:
                await db.table("reconciliation_events").insert({
                    "event_type": m["type"],
                    "booking_id": m.get("booking_id"),
                    "fee_id": m.get("fee_id"),
                    "note": m.get("note", ""),
                    "created_at": now,
                    "resolved": False,
                }).execute()
            except Exception:
                pass

    return {
        "dry_run": dry_run,
        "checked_fees": len(fees),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Dispatch failure manual queue
# ---------------------------------------------------------------------------

@router.get("/dispatch-failures")
async def list_dispatch_failures(
    limit: int = 50,
    offset: int = 0,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    List jobs stuck in dispatch_failed status, joined with their ops_escalation record.
    Returns newest first. Use POST /dispatch-failures/{job_id}/reassign to retry.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()
    res_jobs = await db.table("jobs").select(
        "id, booking_id, address, city, job_type, package_name, min_tier_required, "
        "scheduled_date, time_window_start, time_window_end, created_at, updated_at"
    ).eq("status", "dispatch_failed").order("updated_at", desc=True).range(offset, offset + limit - 1).execute()

    jobs = res_jobs.data or []
    if not jobs:
        return {"total": 0, "items": []}

    job_ids = [j["id"] for j in jobs]
    res_esc = await db.table("ops_escalations").select(
        "job_id, attempt_count, error_message, status, created_at"
    ).in_("job_id", job_ids).execute()

    escalation_by_job = {e["job_id"]: e for e in (res_esc.data or [])}

    items = []
    for j in jobs:
        esc = escalation_by_job.get(j["id"])
        items.append({
            **j,
            "escalation": esc,
        })

    return {"total": len(items), "items": items}


@router.post("/dispatch-failures/{job_id}/reassign")
async def manual_reassign_dispatch_failure(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Manually retry vendor assignment for a dispatch_failed job.
    Resets status to 'pending' before running assign_vendor_for_job.
    If assignment still fails, the job returns to dispatch_failed and a new
    ops_escalation record is written.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()
    res = await db.table("jobs").select("id, status").eq("id", job_id).maybe_single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    if res.data["status"] != "dispatch_failed":
        raise HTTPException(
            status_code=409,
            detail=f"Job is in status '{res.data['status']}' — only dispatch_failed jobs can be manually reassigned",
        )

    # Reset to pending so assign_vendor_for_job can proceed
    await db.table("jobs").update({"status": "pending"}).eq("id", job_id).execute()

    # Close the previous escalation record
    await db.table("ops_escalations").update({"status": "manual_retry"}).eq("job_id", job_id).eq("status", "open").execute()

    assigned_member_id = await assign_vendor_for_job(job_id)

    if assigned_member_id:
        return {
            "job_id": job_id,
            "assigned": True,
            "assigned_member_id": assigned_member_id,
        }

    return {
        "job_id": job_id,
        "assigned": False,
        "detail": "No eligible vendor found. Job returned to dispatch_failed. A new escalation has been written.",
    }
