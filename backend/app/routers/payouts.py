"""
Payout API router for Bricks GO.

Endpoints:
  POST /api/v1/payouts/trigger  — manually trigger payout for a job (internal only)
  GET  /api/v1/payouts          — list payouts (admin only)
  GET  /api/v1/payouts/summary  — payout summary for office dashboard (admin only)
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ..shared.database import get_db
from ..shared.auth import require_vendor_admin
from ..services.payout_service import trigger_payout_for_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/payouts", tags=["payouts"])


@router.post("/trigger", summary="Manually trigger payout for a job")
async def trigger_payout(
    job_id: str,
    x_internal_api_key: Optional[str] = Header(None),
):
    """
    Manually trigger a payout for a completed job.
    Requires X-Internal-Api-Key header (service-to-service only).
    """
    allowed = {k for k in os.environ.get("INTERNAL_API_KEYS", "").split(",") if k}
    if not allowed or not x_internal_api_key or x_internal_api_key not in allowed:
        raise HTTPException(status_code=403, detail="Invalid or missing internal API key")

    try:
        db = await get_db()
        result = await trigger_payout_for_job(db, job_id)

        if "error" in result and not result.get("already_exists"):
            raise HTTPException(status_code=400, detail=result)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Payout trigger failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail={"error": "payout_trigger_failed", "message": str(e)})


@router.get("/", summary="List payouts")
async def list_payouts(
    vendor_id: Optional[str] = Query(None, description="Filter by vendor ID"),
    status: Optional[str] = Query(None, description="Filter by status (pending/paid/failed)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
    x_internal_api_key: Optional[str] = Header(None),
):
    """
    List payouts, optionally filtered by vendor_id and/or status.
    Requires admin vendor member or internal API key.
    """
    await require_vendor_admin(authorization, x_user_id, x_internal_api_key)

    db = await get_db()
    query = db.table("payouts").select("*")

    if vendor_id:
        query = query.eq("vendor_id", vendor_id)
    if status:
        query = query.eq("status", status)

    result = await query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


@router.get("/summary", summary="Payout summary for office dashboard")
async def payout_summary(
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
    x_internal_api_key: Optional[str] = Header(None),
):
    """
    Aggregate payout summary: total pending, paid, and failed amounts + counts.
    Requires admin vendor member or internal API key.
    """
    await require_vendor_admin(authorization, x_user_id, x_internal_api_key)

    db = await get_db()

    result = await db.table("payouts").select("status, amount").execute()
    payouts = result.data or []

    summary = {
        "total_payouts": len(payouts),
        "pending_count": 0,
        "pending_amount": 0,
        "paid_count": 0,
        "paid_amount": 0,
        "failed_count": 0,
        "failed_amount": 0,
    }

    for p in payouts:
        status = p.get("status", "pending")
        amount = p.get("amount", 0)
        if status == "pending":
            summary["pending_count"] += 1
            summary["pending_amount"] += amount
        elif status == "paid":
            summary["paid_count"] += 1
            summary["paid_amount"] += amount
        elif status == "failed":
            summary["failed_count"] += 1
            summary["failed_amount"] += amount

    return summary
