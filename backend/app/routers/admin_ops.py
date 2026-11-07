import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Header, HTTPException

from ..shared.auth import is_vendor_admin, get_vendor_member, resolve_vendor_actor_id, require_vendor_admin
from ..shared.database import get_db


def _maybe_single_row(res) -> dict:
    """postgrest maybe_single().execute() is None when there is no row."""
    data = getattr(res, "data", None) if res is not None else None
    return data if isinstance(data, dict) else {}

from ..shared.errors import api_error
from ..shared.pagination import normalize_pagination, paginated_response
from ..shared.schemas import VendorMemberUpdate, VendorLead
from ..shared.portfolio import assemble_portfolio_samples
from ..services.webhooks import send_dispatch_status_update_webhook
from ..services.vendor_selection import assign_vendor_for_job
from ..services.notifications import create_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin-ops"])
ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "")

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
    
    applicants = res.data or []
    total = res.count or 0

    # Attach portfolio samples by catalog media type for Hiring review.
    member_ids = [a["id"] for a in applicants if a.get("id")]
    docs_by_member: dict[str, list] = {mid: [] for mid in member_ids}
    if member_ids:
        try:
            docs_res = await (await get_db()).table("vendor_documents").select(
                "id, member_id, doc_type, file_url, status, created_at"
            ).in_("member_id", member_ids).execute()
            for d in (docs_res.data or []):
                mid = d.get("member_id")
                if mid in docs_by_member and str(d.get("doc_type") or "").startswith("portfolio_"):
                    docs_by_member[mid].append(d)
        except Exception:
            pass
    for a in applicants:
        a["portfolio_samples"] = assemble_portfolio_samples(a, docs_by_member.get(a.get("id"), []))

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
        # Preserve Owner; otherwise set role from vendor_type (Wave A).
        existing = await (await get_db()).table("vendor_members").select(
            "vendor_type,role,title,application_profile"
        ).eq("id", member_id).maybe_single().execute()
        row = _maybe_single_row(existing)
        from ..services.vendor_profile import (
            is_platform_owner,
            member_vendor_type,
            normalize_vendor_type,
            role_for_vendor_type,
            storage_vendor_type,
        )
        if is_platform_owner({**row, "id": member_id}):
            update_data["role"] = "Owner"
        else:
            # Trade comes from application_profile.trade when the column can't
            # hold it yet (inspector / measurer / handyman).
            vtype = normalize_vendor_type(update_data.get("vendor_type") or member_vendor_type(row or {}))
            update_data["vendor_type"] = storage_vendor_type(vtype)
            update_data["role"] = role_for_vendor_type(vtype)
    
    res = await (await get_db()).table("vendor_members").update(update_data).eq("id", member_id).eq("vendor_id", vendor_id).execute()
    
    if not res.data:
        api_error(404, "Applicant not found", "applicant_not_found")

    approved_row = res.data[0]
    if update_data.get("onboarding_status") == "approved":
        try:
            email = (approved_row.get("email") or "").strip()
            if email:
                from ..services.notifications import create_notification, welcome_approved_email_html
                from ..services.vendor_calendars import calendar_success_redirect_base, VENDOR_CALENDAR_WELCOME
                availability_url = calendar_success_redirect_base()
                await create_notification(
                    title="Welcome to Bricks GO",
                    body=(
                        f"{VENDOR_CALENDAR_WELCOME} "
                        "Open Availability and connect Google, Outlook, Apple, or an ICS feed."
                    ),
                    ntype="onboarding_approved",
                    data={
                        "member_id": member_id,
                        "type": "onboarding_approved",
                        "route": "/settings",
                        "url": availability_url,
                    },
                    member_id=member_id,
                    email_to=email,
                    email_subject="You’re approved — connect your calendar",
                    email_html=welcome_approved_email_html(availability_url),
                )
        except Exception:
            pass
    return approved_row

async def _require_applicant_in_own_vendor(db, member_id: str, vendor_id: str):
    """IDOR guard (fixed 2026-08-15): list_applicants/update_applicant_status both
    scope by vendor_id, but this pair of document endpoints only matched on
    member_id/doc_id -- a vendor admin could view or verify ANY vendor's
    applicant documents by guessing/observing a member_id, not just their own
    vendor's. Fail closed if the applicant isn't in the caller's vendor org."""
    res = await db.table("vendor_members").select("vendor_id").eq("id", member_id).maybe_single().execute()
    row = _maybe_single_row(res)
    if not row or row.get("vendor_id") != vendor_id:
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
    liability_cad: Optional[int] = None,
    aviation_covered: Optional[bool] = None,
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

    doc = res.data[0]
    # WSIB: optional record only (expiry copied for history, never a gate).
    if status == "verified" and doc.get("doc_type") == "wsib" and doc.get("expiry_date"):
        await db.table("vendor_members").update({
            "wsib_expires_on": doc["expiry_date"],
        }).eq("id", member_id).execute()
    # COI: listing jobs still ignore it, but a verified COI is what the
    # commercial job gate and the Tier A rule read (Kam, 2026-10-17). The
    # admin confirms the liability limit and drone/aviation coverage; the
    # vendor's declared values are only a default.
    if doc.get("doc_type") == "coi":
        if status == "verified":
            declared = ((doc.get("validation_result") or {}).get("declared") or {}) if isinstance(doc.get("validation_result"), dict) else {}
            limit = liability_cad if liability_cad is not None else declared.get("liability_cad")
            aviation = aviation_covered if aviation_covered is not None else bool(declared.get("aviation_covered"))
            patch = {
                "coi_verified": True,
                "coi_expires_on": doc.get("expiry_date") or declared.get("expires_on"),
                "coi_liability_cad": int(limit) if limit else None,
                "coi_aviation_covered": bool(aviation),
                "coi_verified_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.table("vendor_members").update(patch).eq("id", member_id).execute()
        elif status in ("rejected", "flagged"):
            await db.table("vendor_members").update({"coi_verified": False}).eq("id", member_id).execute()

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
            vendor_name = _maybe_single_row(res_m).get("name", "")

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
    status: Optional[str] = "open",
    page: int = 1,
    page_size: int = 50,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    List no-show claims. Defaults to "open": every claim Bricks still has to review,
    i.e. held claims (pending_review) and clean claims that were recorded but not yet
    reviewed (pending, reviewed_at unset). Pass a status to filter on it exactly, or
    status=all to see every claim regardless of state.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()
    page, page_size, skip = normalize_pagination(page, page_size)

    query = db.table("agent_noshow_trip_fees").select("*", count="exact")
    if not status or status == "open":
        query = query.in_("status", ["pending_review", "pending"]).is_("reviewed_at", "null")
    elif status != "all":
        query = query.eq("status", status)

    res = await query.order("created_at", ascending=False).range(skip, skip + page_size - 1).execute()
    rows = [await _with_noshow_evidence(r) for r in (res.data or [])]
    return paginated_response(rows, res.count or 0, page, page_size)


async def _with_noshow_evidence(claim: dict) -> dict:
    """Add what the reviewer needs: a short signed photo URL and check-in facts."""
    from ..services.storage import signed_url_in_bucket
    from ..shared import noshow_policy

    out = dict(claim or {})
    path = out.get("photo_path")
    out["photo_url"] = (
        await signed_url_in_bucket(noshow_policy.EVIDENCE_BUCKET, path, 3600) if path else None
    )
    dist = out.get("check_in_distance_m")
    out["checkin_radius_m"] = noshow_policy.CHECKIN_RADIUS_M
    out["check_in_distance_label"] = (
        f"{int(round(float(dist)))} m from the property" if dist is not None
        else ("Distance unknown (job has no map location)" if out.get("checked_in_at") else "No check-in")
    )
    return out


@router.post("/noshow-claims/{claim_id}/review")
async def review_noshow_claim(
    claim_id: str,
    action: str,
    admin_notes: Optional[str] = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Approve or reject a no-show claim that no admin has reviewed yet: a flagged
    claim (pending_review) or an auto-recorded one (pending, reviewed_at unset).
    approve → status='pending', then the vendor is paid half the job's base payout
              (HST on top as usual) and the claim becomes 'paid_out'
    reject  → status='rejected', claim voided, nothing is paid
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
    claim = _maybe_single_row(res_claim)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    prev_status = claim.get("status")
    unreviewed_auto = prev_status == "pending" and not claim.get("reviewed_at")
    if prev_status != "pending_review" and not unreviewed_auto:
        raise HTTPException(
            status_code=409,
            detail=f"Claim is not pending review (current status: {prev_status})"
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

    if action == "approve" and booking_id and job_id and prev_status == "pending_review":
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
        # Claim voided — notify Booking to reverse the agent retention penalty.
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

    payout = None
    if action == "approve" and job_id:
        # Approved no-show: pay the vendor half the job's base payout (Kam, 2026-10-30).
        from ..services.payout_service import trigger_payout_for_job
        try:
            payout = await trigger_payout_for_job(db, job_id, noshow_claim_id=claim_id)
        except Exception as e:
            logger.error(f"No-show payout failed for job {job_id} (claim {claim_id}): {e}")
            payout = {"error": str(e), "job_id": job_id}

    return {
        "claim_id": claim_id,
        "status": new_status,
        "reviewed_by": actor_id,
        "payout": payout,
        "note": "No-show confirmed." if action == "approve" else "Claim voided.",
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
    - fee_without_retention: no-show claim exists but booking not marked as retained
    - retention_without_fee: booking shows no-show but no claim record (photographer did not file)
    - booking_missing: claim references a booking that no longer exists

    dry_run=true (default): report mismatches, write nothing.
    dry_run=false: write reconciliation_events rows for each mismatch.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()

    # All active (non-rejected) no-show claims
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
            bk_row = _maybe_single_row(res_bk)

            if not bk_row:
                mismatches.append({
                    "type": "booking_missing",
                    "fee_id": fee["id"],
                    "booking_id": booking_id,
                    "note": "Booking record not found",
                })
                continue

            bk_status = bk_row.get("status", "")
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
                    "note": "No-show claim exists but booking not marked as retained — Booking webhook may need retry",
                })
        except Exception as e:
            mismatches.append({
                "type": "error",
                "fee_id": fee["id"],
                "booking_id": booking_id,
                "note": str(e),
            })

    # Reverse check: retained bookings with no no-show claim
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
                    "note": "Booking retained but no photographer no-show claim — photographer may not have filed",
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
# Vendor compliance expiry scan
#
# Informational only. COI and WSIB proof are not required to apply, be
# approved, or be dispatched. This scan reports stored expiry dates so ops
# can see them. It does not suspend a vendor, reject an application, or
# hold a job.
# ---------------------------------------------------------------------------

@router.get("/compliance-expiry-scan")
async def compliance_expiry_scan(
    days_threshold: int = 14,
    notify: bool = False,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Report stored COI/WSIB expiry dates. Does not change vendor or job status.

    notify=false (default): report only, sends nothing.
    notify=true: sends one digest admin notification (Resend + in-app) if any
    vendor is flagged. Does not email vendors directly -- ops decides how to
    follow up.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    db = await get_db()

    res_docs = await db.table("vendor_documents").select(
        "id, member_id, doc_type, expiry_date, status"
    ).in_("doc_type", ["coi", "wsib"]).not_.is_("expiry_date", "null").execute()
    docs = res_docs.data or []

    res_members = await db.table("vendor_members").select(
        "id, name, email, is_active"
    ).in_("id", list({d["member_id"] for d in docs})).execute() if docs else None
    members_by_id = {m["id"]: m for m in (res_members.data or [])} if res_members else {}

    today = datetime.now(timezone.utc).date()
    flagged = []
    for doc in docs:
        member = members_by_id.get(doc["member_id"])
        if not member or not member.get("is_active"):
            continue
        try:
            expiry = datetime.fromisoformat(doc["expiry_date"]).date()
        except (ValueError, TypeError):
            continue
        days_left = (expiry - today).days
        if days_left < 0:
            severity = "expired"
        elif days_left <= days_threshold:
            severity = "expiring_soon"
        else:
            continue
        flagged.append({
            "member_id": member["id"],
            "vendor_name": member.get("name"),
            "vendor_email": member.get("email"),
            "doc_type": doc["doc_type"],
            "expiry_date": doc["expiry_date"],
            "days_left": days_left,
            "severity": severity,
        })

    flagged.sort(key=lambda f: f["days_left"])

    if notify and flagged:
        lines = [
            f"{f['vendor_name']} — {f['doc_type'].upper()} {f['severity']} "
            f"({f['expiry_date']}, {f['days_left']}d)"
            for f in flagged
        ]
        await create_notification(
            title=f"{len(flagged)} vendor(s) with lapsed/expiring compliance docs",
            body="\n".join(lines),
            ntype="compliance_expiry_scan",
            data={"flagged_count": len(flagged)},
            email_to=ADMIN_NOTIFICATION_EMAIL,
            email_subject=f"Compliance expiry scan: {len(flagged)} vendor(s) need attention",
        )

    return {
        "days_threshold": days_threshold,
        "checked_documents": len(docs),
        "flagged_count": len(flagged),
        "flagged": flagged,
        "notified": bool(notify and flagged),
    }


# ---------------------------------------------------------------------------
# Dispatch failure + Jev-hold manual queue
# ---------------------------------------------------------------------------

_DISPATCH_QUEUE_STATUSES = ("dispatch_failed", "held_for_review")
_JEV_HOLD_NOTE_MARKER = "Held for a person"
_DISPATCH_QUEUE_SELECT = (
    "id, booking_id, address, city, job_type, package_name, min_tier_required, "
    "scheduled_date, time_window_start, time_window_end, created_at, updated_at, "
    "status, dispatch_explanation, assignee_id"
)


def _is_jev_hold_row(job: dict) -> bool:
    """True when the job is (or was left as) a Jev human-hold."""
    status = str((job or {}).get("status") or "").strip()
    if status == "held_for_review":
        return True
    if status == "pending_assignment":
        note = str((job or {}).get("dispatch_explanation") or "")
        return _JEV_HOLD_NOTE_MARKER in note
    return False


def _dispatch_queue_kind(job: dict) -> str:
    status = str((job or {}).get("status") or "").strip()
    if status == "dispatch_failed":
        return "dispatch_failed"
    if _is_jev_hold_row(job):
        return "held_for_review"
    return status or "unknown"


def _sort_queue_jobs(jobs: list) -> list:
    def _key(j: dict):
        return str(j.get("updated_at") or j.get("created_at") or ""), str(j.get("id") or "")

    return sorted(jobs, key=_key, reverse=True)


@router.get("/dispatch-failures")
async def list_dispatch_failures(
    limit: int = 50,
    offset: int = 0,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Admin recovery queue: dispatch_failed + Jev held_for_review (and legacy
    pending_assignment rows that still carry the hold note).

    Newest first. Retry failed assign via POST .../reassign; release a Jev hold
    via POST .../release (clears hold + runs auto-assign).

    Fair-Use: when Canada PostgREST returns 402, list jobs + escalations via Postgres
    so Owner can still recover failed auto-assign / holds while Storage quota is locked.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_select_dispatch_failed_jobs,
        pg_select_ops_escalations_for_jobs,
    )

    db = await get_db()
    jobs: list = []
    try:
        # Pull failed + held + pending_assignment candidates (admin queue is small),
        # then keep true queue rows in Python — covers legacy Jev holds that still
        # sit on pending_assignment with the hold note and empty assignee.
        res_jobs = await db.table("jobs").select(
            _DISPATCH_QUEUE_SELECT
        ).in_(
            "status",
            ["dispatch_failed", "held_for_review", "pending_assignment"],
        ).order("updated_at", desc=True).range(0, 199).execute()
        candidates = list(res_jobs.data or [])
        kept = []
        for row in candidates:
            status = str(row.get("status") or "").strip()
            if status in _DISPATCH_QUEUE_STATUSES:
                kept.append(row)
            elif _is_jev_hold_row(row) and not str(row.get("assignee_id") or "").strip():
                kept.append(row)
        jobs = _sort_queue_jobs(kept)[offset: offset + limit]
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        jobs = pg_select_dispatch_failed_jobs(limit=limit, offset=offset)

    if not jobs:
        return {"total": 0, "items": []}

    job_ids = [j["id"] for j in jobs if j.get("id")]
    escalations: list = []
    try:
        res_esc = await db.table("ops_escalations").select(
            "job_id, attempt_count, error_message, status, created_at"
        ).in_("job_id", job_ids).execute()
        escalations = res_esc.data or []
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        escalations = pg_select_ops_escalations_for_jobs(job_ids)

    escalation_by_job = {e["job_id"]: e for e in escalations if e.get("job_id")}

    items = []
    for j in jobs:
        esc = escalation_by_job.get(j["id"])
        items.append({
            **j,
            "queue_kind": _dispatch_queue_kind(j),
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
    Resets status to 'pending_assignment' before running assign_vendor_for_job.
    If assignment still fails, the job returns to dispatch_failed and a new
    ops_escalation record is written.

    Fair-Use: job load/reset and open-escalation close fall back to Postgres on
    PostgREST 402 so Owner can recover failed auto-assign under Fair Use.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_mark_ops_escalations_manual_retry,
        pg_select_job_by_id,
        pg_update_job,
    )

    db = await get_db()
    job_row = None
    try:
        res = await db.table("jobs").select("id, status").eq("id", job_id).maybe_single().execute()
        job_row = _maybe_single_row(res) or None
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        job_row = pg_select_job_by_id(job_id)

    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found")

    if job_row.get("status") != "dispatch_failed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job is in status '{job_row.get('status')}' — only dispatch_failed "
                "jobs can be manually reassigned (use /release for Jev holds)"
            ),
        )

    # Reset to pending_assignment so assign_vendor_for_job can proceed
    reset_fields = {"status": "pending_assignment", "assignee_id": ""}
    try:
        await db.table("jobs").update(reset_fields).eq("id", job_id).execute()
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, reset_fields):
            raise HTTPException(status_code=500, detail="Could not reset job for reassign (Fair-Use PG)")

    # Close the previous escalation record
    try:
        await db.table("ops_escalations").update({"status": "manual_retry"}).eq("job_id", job_id).eq("status", "open").execute()
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        pg_mark_ops_escalations_manual_retry(job_id)

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


@router.post("/dispatch-failures/{job_id}/release")
async def release_held_dispatch_job(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Release a Jev-held booking (held_for_review, or legacy pending_assignment
    with the hold note) back to auto-assign.

    Clears the hold stamp, resets to pending_assignment, then runs
    assign_vendor_for_job. Does not touch dispatch_failed rows — use /reassign.
    Fair-Use: job load/reset fall back to Postgres on PostgREST 402.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")

    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_select_job_by_id,
        pg_update_job,
    )

    db = await get_db()
    job_row = None
    try:
        res = await db.table("jobs").select(
            "id, status, dispatch_explanation, assignee_id"
        ).eq("id", job_id).maybe_single().execute()
        job_row = _maybe_single_row(res) or None
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        job_row = pg_select_job_by_id(job_id)

    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _is_jev_hold_row(job_row):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job is in status '{job_row.get('status')}' — only Jev-held jobs "
                "(held_for_review or pending_assignment with hold note) can be released "
                "(use /reassign for dispatch_failed)"
            ),
        )

    now = datetime.now(timezone.utc).isoformat()
    reset_fields = {
        "status": "pending_assignment",
        "assignee_id": "",
        "dispatch_explanation": "Released from Jev hold for auto-assign.",
        "updated_at": now,
    }
    try:
        await db.table("jobs").update(reset_fields).eq("id", job_id).execute()
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, reset_fields):
            raise HTTPException(status_code=500, detail="Could not release held job (Fair-Use PG)")

    assigned_member_id = await assign_vendor_for_job(job_id)

    if assigned_member_id:
        return {
            "job_id": job_id,
            "released": True,
            "assigned": True,
            "assigned_member_id": assigned_member_id,
        }

    return {
        "job_id": job_id,
        "released": True,
        "assigned": False,
        "detail": (
            "Hold released but no eligible vendor found. "
            "Job is now dispatch_failed — use /reassign after fixing supply."
        ),
    }


@router.post("/seed-owner-profile")
async def seed_owner_profile(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Explicitly (re-)seed the platform Owner's vendor_members profile:
    top-tier, GTA, Advanced RPAS, and normalizes jobs/vendor rows that
    predate that seeding. Idempotent. Used to run on every /health/ready
    poll -- moved here 2026-08-30 because a readiness probe must not
    perform writes on every hit from an uptime monitor or load balancer."""
    await require_vendor_admin(authorization, x_user_id, x_internal_api_key)
    from ..services.vendor_profile import ensure_owner_profile
    return await ensure_owner_profile(await get_db())
