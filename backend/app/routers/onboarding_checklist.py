"""GO onboarding checklist API (Kam, 2026-10-31).

  GET  /api/vendor-os/onboarding               signed-in vendor's checklist
  POST /api/vendor-os/onboarding/guide         "I've read the guide"
  POST /api/vendor-os/onboarding/availability  "My availability is right"
  GET  /api/admin/vendor-onboarding              owner view: every approved vendor's progress

Completion (onboarding_completed_at + "You're live" email) lives in
services/vendor_onboarding.refresh_onboarding.
"""
from __future__ import annotations

from fastapi import APIRouter, Header

from ..services import vendor_onboarding as vo
from ..shared.auth import get_vendor_member, is_vendor_admin, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error

router = APIRouter(prefix="/api/vendor-os", tags=["vendor-onboarding"])
admin_router = APIRouter(prefix="/api/admin", tags=["vendor-onboarding"])


async def _member(authorization, x_user_id) -> dict:
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    if not member or not member.get("id"):
        api_error(403, "Vendor member not found", "vendor_member_not_found")
    return member


def _require_onboarding_access(member: dict) -> None:
    if (member.get("onboarding_status") or "") != "approved" or not member.get("is_active"):
        api_error(403, "Your account isn't active yet.", "not_approved")


def _sync_stripe_if_pending(member: dict) -> None:
    """A vendor back from Stripe: re-check readiness before showing the step."""
    if not vo.payouts_required(member):
        return
    try:
        from ..services.stripe_connect_service import load_contractor_row, sync_contractor_connect_status
        row = load_contractor_row(member.get("vendor_id") or "default_vendor", str(member["id"])) or {}
        if row.get("stripe_account_id") and (row.get("connect_status") or "") != "connected":
            sync_contractor_connect_status(row["id"], row["stripe_account_id"])
    except Exception:
        pass


@router.get("/onboarding")
async def get_onboarding(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    member = await _member(authorization, x_user_id)
    _require_onboarding_access(member)
    if not member.get("first_signed_in_at"):
        await vo.stamp_step(member["id"], "first_signed_in_at")
    if not member.get("onboarding_completed_at"):
        _sync_stripe_if_pending(member)
    return await vo.refresh_onboarding(member["id"])


async def _ack(column: str, authorization, x_user_id) -> dict:
    member = await _member(authorization, x_user_id)
    _require_onboarding_access(member)
    await vo.stamp_step(member["id"], column)
    return await vo.refresh_onboarding(member["id"])


@router.post("/onboarding/guide")
async def ack_guide(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await _ack("guide_acknowledged_at", authorization, x_user_id)


@router.post("/onboarding/availability")
async def confirm_availability(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await _ack("availability_confirmed_at", authorization, x_user_id)


@admin_router.get("/vendor-onboarding")
async def admin_onboarding_progress(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Owner view: approved vendors and where each is in onboarding."""
    actor = await _member(authorization, x_user_id)
    if not is_vendor_admin(actor):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    db = await get_db()
    res = await db.table("vendor_members").select("*").eq(
        "vendor_id", actor.get("vendor_id") or "default_vendor"
    ).eq("onboarding_status", "approved").order("created_at", desc=True).limit(500).execute()
    rows = res.data or []
    ids = [str(r["id"]) for r in rows if r.get("id")]
    connect: dict = {}
    if ids:
        try:
            cres = await db.table("contractors").select("member_id,connect_status").in_("member_id", ids).execute()
            connect = {c.get("member_id"): c for c in (cres.data or [])}
        except Exception:
            connect = {}
    out = []
    for r in rows:
        state = vo.checklist(r, connect.get(str(r["id"])))
        out.append({
            "id": r["id"], "name": r.get("name") or "", "home_city": r.get("home_city") or "",
            "is_active": bool(r.get("is_active")), **state,
        })
    return {"vendors": out}
