"""Internal payment events from Bricks OS (service-to-service only)."""
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from ..shared.auth import verify_internal_api_key
from ..shared.database import get_db
from ..services.refund_service import apply_booking_refund

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/payments", tags=["payments-internal"])


@router.post("/refunded")
async def booking_refunded(
    request: Request,
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """A booking's charge was refunded: cancel/hold its vendor payout (refund_service)."""
    if not verify_internal_api_key(x_internal_api_key):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected object")
    booking_id = str(body.get("booking_id") or "").strip()
    if not booking_id:
        raise HTTPException(status_code=400, detail="booking_id required")
    db = await get_db()
    amt = body.get("amount_refunded")
    return await apply_booking_refund(
        db, booking_id,
        full=bool(body.get("full")),
        refund_id=str(body.get("refund_id") or ""),
        charge_id=str(body.get("charge_id") or ""),
        amount_refunded=int(amt) if isinstance(amt, (int, float)) else None,
        reverse_paid=bool(body.get("reverse_paid")),
    )
