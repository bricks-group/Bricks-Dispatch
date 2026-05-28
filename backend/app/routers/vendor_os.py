from datetime import datetime, timedelta, timezone
import httpx
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from ..services.vendor_selection import assign_vendor_for_job
from ..shared.audit import write_audit_event
from ..shared.auth import get_vendor_member, get_vendor_member_ids, is_vendor_admin, resolve_vendor_actor_id
from ..shared.database import get_db
from ..services.webhooks import send_dispatch_status_update_webhook
from ..shared.errors import api_error
from ..shared.pagination import normalize_pagination, paginated_response
from ..shared.schemas import (
    AvailabilitySettings,
    AvailabilityUpdate,
    JobAssignmentUpdate, JobStatusUpdate,
    JobCreate,
    VendorMember,
    VendorMemberCreate,
    VendorMemberUpdate,
    VendorSettings,
    VendorSettingsUpdate,
)


router = APIRouter(prefix="/api/vendor-os", tags=["vendor-os"])


@router.get("/overview")
async def get_vendor_os_overview(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    member_ids = await get_vendor_member_ids(vendor_id)
    
    res_jobs = await (await get_db()).table("jobs").select("*").eq("vendor_id", vendor_id).in_("assignee_id", member_ids).limit(500).execute()
    jobs = res_jobs.data
    
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    assigned_today = assigned_week = completed_week = scheduled_this_week = 0
    upcoming = []
    total_capacity = max(len(member_ids) * 4, 1)
    for job in jobs:
        try:
            scheduled = datetime.strptime(job.get("scheduled_date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if scheduled == today:
            assigned_today += 1
        if week_start <= scheduled <= week_end:
            assigned_week += 1
            scheduled_this_week += 1
            if job.get("status") == "completed":
                completed_week += 1
        if scheduled >= today and job.get("status") != "completed":
            upcoming.append(job)
    return {
        "jobs_assigned_today": assigned_today,
        "jobs_assigned_week": assigned_week,
        "jobs_completed_week": completed_week,
        "capacity_utilization_pct": round(min((scheduled_this_week / (total_capacity * 7)) * 100, 100), 1),
        "upcoming_jobs": sorted(upcoming, key=lambda j: (j.get("scheduled_date", ""), j.get("time_window_start", "")))[:5],
    }


@router.get("/team")
async def get_vendor_team(
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    sb = await get_db(); query = sb.table("vendor_members").select("*", count="exact").eq("vendor_id", vendor_id)
    if status:
        query = query.eq("status", status)
    if search:
        # Supabase doesn't have a direct OR for multiple columns with regex as easily as MongoDB in one line, 
        # but we can use `.or_` with filters.
        query = query.or_(f"name.ilike.%{search}%,email.ilike.%{search}%,role.ilike.%{search}%")
    
    page, page_size, skip = normalize_pagination(page, page_size)
    res = await query.order("created_at", desc=False).range(skip, skip + page_size - 1).execute()
    members = res.data
    total = res.count or 0
    
    for member in members:
        # Get assigned jobs count
        res_count = await (await get_db()).table("jobs").select("*", count="exact").eq("vendor_id", vendor_id).eq("assignee_id", member["id"]).neq("status", "completed").execute()
        member["assigned_jobs"] = res_count.count or 0
        
    return paginated_response(members, total, page, page_size)


@router.post("/team")
async def create_vendor_member(
    payload: VendorMemberCreate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    member = VendorMember(
        name=payload.name,
        email=payload.email.strip().lower(),
        phone=payload.phone,
        role=payload.role,
        vendor_id=actor_member.get("vendor_id", "default_vendor"),
    )
    member_dict = member.dict()
    await (await get_db()).table("vendor_members").insert(member_dict).execute()
    member_dict["assigned_jobs"] = 0
    await write_audit_event(actor_id, "vendor_member", member_dict["id"], "create", vendor_id=member_dict["vendor_id"], details={"email": member_dict["email"]})
    return member_dict


@router.put("/team/{member_id}")
async def update_vendor_member(
    member_id: str,
    payload: VendorMemberUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        api_error(400, "No update data", "no_update_data")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    res = await (await get_db()).table("vendor_members").update(update_data).eq("id", member_id).eq("vendor_id", vendor_id).execute()
    if not res.data:
        api_error(404, "Member not found", "member_not_found")
    
    member = res.data[0]
    res_count = await (await get_db()).table("jobs").select("*", count="exact").eq("vendor_id", vendor_id).eq("assignee_id", member_id).neq("status", "completed").execute()
    member["assigned_jobs"] = res_count.count or 0
    await write_audit_event(actor_id, "vendor_member", member_id, "update", vendor_id=vendor_id, details=update_data)
    return member


@router.delete("/team/{member_id}")
async def delete_vendor_member(
    member_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    if member_id == "default_user":
        api_error(400, "Cannot remove default member", "cannot_remove_default_member")
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    res = await (await get_db()).table("vendor_members").delete().eq("id", member_id).eq("vendor_id", vendor_id).execute()
    if not res.data:
        api_error(404, "Member not found", "member_not_found")
    
    await (await get_db()).table("jobs").update({"assignee_id": "default_user", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("assignee_id", member_id).eq("vendor_id", vendor_id).execute()
    await write_audit_event(actor_id, "vendor_member", member_id, "delete", vendor_id=vendor_id)
    return {"deleted": True}


@router.get("/jobs")
async def get_vendor_os_jobs(
    status: Optional[str] = None,
    search: Optional[str] = None,
    assignee_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    sb = await get_db(); query = sb.table("jobs").select("*", count="exact").eq("vendor_id", vendor_id)
    if status:
        query = query.eq("status", status)
    if assignee_id:
        query = query.eq("assignee_id", assignee_id)
    if search:
        query = query.or_(f"address.ilike.%{search}%,city.ilike.%{search}%")
        
    page, page_size, skip = normalize_pagination(page, page_size)
    res = await query.order("scheduled_date", desc=False).range(skip, skip + page_size - 1).execute()
    jobs = res.data
    total = res.count or 0
    
    res_members = await (await get_db()).table("vendor_members").select("id, name").eq("vendor_id", vendor_id).limit(500).execute()
    member_map = {m["id"]: m.get("name", "Unknown") for m in res_members.data}
    for job in jobs:
        job["assignee_name"] = member_map.get(job.get("assignee_id"), "Unassigned")
    return paginated_response(jobs, total, page, page_size)


@router.post("/jobs")
async def create_job(
    payload: JobCreate,
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    from ..shared.auth import resolve_actor_id
    raw_keys = os.environ.get("INTERNAL_API_KEYS")
    if not raw_keys:
        raise HTTPException(status_code=500, detail="INTERNAL_API_KEYS not configured")
    internal_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    if x_internal_api_key and x_internal_api_key in internal_keys:
        actor_id = "booking_system"
    else:
        actor_id = resolve_actor_id(x_user_id)

    job_id = payload.id or str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    job_row = {
        "id": job_id,
        "booking_id": payload.booking_id,
        "address": payload.address,
        "city": payload.city,
        "state": payload.state,
        "zip_code": payload.zip_code,
        "time_window_start": payload.time_window_start,
        "time_window_end": payload.time_window_end,
        "job_type": payload.job_type,
        "status": payload.status,
        "priority": payload.priority,
        "property_details": payload.property_details,
        "client_instructions": payload.client_instructions,
        "scheduled_date": payload.scheduled_date,
        "deliverables_count": payload.deliverables_count,
        "uploaded_count": payload.uploaded_count,
        "assignee_id": payload.assignee_id,
        "vendor_id": payload.vendor_id,
        "package_id": payload.package_id,
        "package_name": payload.package_name,
        "package_price": payload.package_price,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    try:
        result = await (await get_db()).table("jobs").insert(job_row).execute()
        return {"id": job_id, **job_row}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create job: {type(e).__name__}: {str(e)}")


@router.put("/jobs/{job_id}/assign")
async def assign_job_to_member(
    job_id: str,
    payload: JobAssignmentUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    res_job = await (await get_db()).table("jobs").select("assignee_id").eq("id", job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    existing_job = res_job.data
    if not existing_job:
        api_error(404, "Job not found", "job_not_found")

    if payload.member_id == "auto":
        new_assignee_id = await assign_vendor_for_job(job_id)
    else:
        res_member = await (await get_db()).table("vendor_members").select("*").eq("id", payload.member_id).eq("vendor_id", vendor_id).eq("is_active", True).maybe_single().execute()
        if not res_member.data:
            api_error(404, "Member not found", "member_not_found")
        new_assignee_id = payload.member_id
        await (await get_db()).table("jobs").update({"assignee_id": new_assignee_id, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", job_id).eq("vendor_id", vendor_id).execute()

    await write_audit_event(actor_id, "job", job_id, "assign", vendor_id=vendor_id, details={"from": existing_job.get("assignee_id"), "to": new_assignee_id, "method": "auto" if payload.member_id == "auto" else "manual"})
    res_final = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()

    # POST callback to Booking backend when job is assigned
    booking_callback_url = os.environ.get("BOOKING_CALLBACK_URL")
    if not booking_callback_url:
        logger.warning("BOOKING_CALLBACK_URL not set — skipping assignment callback")
        return res_final.data
    callback_payload = {
        "booking_id": res_final.data.get("booking_id"),
        "job_id": job_id,
        "status": res_final.data.get("status", "assigned"),
        "completed_at": res_final.data.get("completed_at"),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{booking_callback_url}/api/callbacks/job-status",
                json=callback_payload,
                headers={"Content-Type": "application/json"},
            )
    except Exception:
        pass  # Callback is fire-and-forget, don't block the response

    return res_final.data


@router.get("/availability")
async def get_vendor_availability(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    res = await (await get_db()).table("vendor_availability").select("*").eq("vendor_id", vendor_id).eq("member_id", actor_id).maybe_single().execute()
    record = res.data
    if not record:
        default = AvailabilitySettings(member_id=actor_id, vendor_id=vendor_id).dict()
        await (await get_db()).table("vendor_availability").insert(default).execute()
        return default
    return record


@router.put("/availability")
async def update_vendor_availability(
    payload: AvailabilityUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    if payload.jobs_per_day < 0:
        api_error(400, "jobs_per_day must be non-negative", "invalid_capacity")
    update_data = payload.dict()
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await (await get_db()).table("vendor_availability").upsert({
        "vendor_id": vendor_id, 
        "member_id": actor_id, 
        **update_data
    }).execute()
    
    await write_audit_event(actor_id, "availability", actor_id, "update", vendor_id=vendor_id, details=update_data)
    res = await (await get_db()).table("vendor_availability").select("*").eq("vendor_id", vendor_id).eq("member_id", actor_id).maybe_single().execute()
    return res.data


@router.get("/performance")
async def get_vendor_performance(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    res_members = await (await get_db()).table("vendor_members").select("id").eq("vendor_id", vendor_id).eq("is_active", True).limit(500).execute()
    member_ids = [m["id"] for m in res_members.data] or ["default_user"]
    
    res_jobs = await (await get_db()).table("jobs").select("*").eq("vendor_id", vendor_id).in_("assignee_id", member_ids).limit(500).execute()
    jobs = res_jobs.data
    
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_jobs = []
    completed = []
    for job in jobs:
        try:
            scheduled = datetime.strptime(job.get("scheduled_date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if week_start <= scheduled <= week_end:
            week_jobs.append(job)
            if job.get("status") == "completed":
                completed.append(job)
    on_time = 0
    for job in completed:
        try:
            deadline = datetime.strptime(f"{job.get('scheduled_date')} {job.get('time_window_end', '23:59')}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            updated_at = datetime.fromisoformat(job.get("updated_at"))
            if updated_at <= deadline:
                on_time += 1
        except Exception:
            continue
    return {
        "completion_rate": round((len(completed) / len(week_jobs) * 100), 1) if week_jobs else 0.0,
        "on_time_rate": round((on_time / len(completed) * 100), 1) if completed else 0.0,
        "job_volume_week": len(week_jobs),
    }


@router.get("/settings")
async def get_vendor_settings(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    res = await (await get_db()).table("vendor_settings").select("*").eq("vendor_id", vendor_id).maybe_single().execute()
    settings = res.data
    if not settings:
        default = VendorSettings(vendor_id=vendor_id).dict()
        await (await get_db()).table("vendor_settings").insert(default).execute()
        return default
    return settings


@router.put("/settings")
async def update_vendor_settings(
    payload: VendorSettingsUpdate,
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
        api_error(400, "No update data", "no_update_data")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await (await get_db()).table("vendor_settings").upsert({
        "vendor_id": vendor_id,
        **update_data
    }).execute()
    
    await write_audit_event(actor_id, "settings", vendor_id, "update", vendor_id=vendor_id, details=update_data)
    res = await (await get_db()).table("vendor_settings").select("*").eq("vendor_id", vendor_id).maybe_single().execute()
    return res.data


@router.get("/earnings")
async def get_vendor_os_earnings(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    from ..services.payout_service import TIER_PERCENTAGES, DEFAULT_TIER_PERCENTAGE
    res_members = await (await get_db()).table("vendor_members").select("id, tier").eq("vendor_id", vendor_id).eq("is_active", True).limit(500).execute()
    member_rate_map = {m["id"]: TIER_PERCENTAGES.get(m.get("tier"), DEFAULT_TIER_PERCENTAGE) for m in res_members.data}
    member_ids = list(member_rate_map.keys()) or ["__none__"]

    res_jobs = await (await get_db()).table("jobs").select("*").eq("vendor_id", vendor_id).in_("assignee_id", member_ids).order("scheduled_date", desc=True).limit(50).execute()
    jobs = res_jobs.data

    # Fetch real payout statuses from the payouts table
    job_ids = [j["id"] for j in jobs if j.get("id")]
    payout_status_map: dict[str, str] = {}
    if job_ids:
        res_payouts = await (await get_db()).table("payouts").select("job_id, status").in_("job_id", job_ids).execute()
        for p in (res_payouts.data or []):
            if p.get("job_id"):
                payout_status_map[p["job_id"]] = p.get("status", "pending")

    week_total = 0
    paid_total = 0
    pending_total = 0
    recent = []

    for j in jobs:
        raw_price = j.get("package_price") or 0
        rate = member_rate_map.get(j.get("assignee_id", ""), DEFAULT_TIER_PERCENTAGE)
        payout = round(raw_price * rate * 100) / 100
        sched = j.get("scheduled_date", "")
        is_this_week = False
        try:
            sched_dt = datetime.fromisoformat(sched).replace(tzinfo=timezone.utc) if sched else None
            is_this_week = bool(sched_dt and week_start <= sched_dt < week_end)
        except Exception:
            pass

        actual_payout_status = payout_status_map.get(j.get("id", ""), "pending")
        is_paid = actual_payout_status == "paid"
        if is_this_week:
            week_total += payout
            if is_paid:
                paid_total += payout
            else:
                pending_total += payout

        if len(recent) < 20:
            recent.append({
                "id": j.get("id"),
                "address": j.get("address", ""),
                "scheduled_date": sched,
                "status": j.get("status", "assigned"),
                "payout_amount": payout,
                "payout_status": actual_payout_status,
            })

    return {
        "week_total": week_total,
        "paid_amount": paid_total,
        "pending_amount": pending_total,
        "payout_day": week_end.strftime("%A"),
        "week_job_count": sum(1 for j in jobs if j.get("scheduled_date", "") >= week_start.date().isoformat()),
        "recent_jobs": recent,
    }


@router.get("/audit-events")
async def get_audit_events(
    entity_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    sb = await get_db(); query = sb.table("audit_events").select("*", count="exact").eq("vendor_id", vendor_id)
    if entity_type:
        query = query.eq("entity_type", entity_type)
        
    page, page_size, skip = normalize_pagination(page, page_size)
    res = await query.order("created_at", desc=True).range(skip, skip + page_size - 1).execute()
    items = res.data
    total = res.count or 0
    
    return paginated_response(items, total, page, page_size)

@router.put("/jobs/{job_id}/status")
async def update_vendor_job_status(
    job_id: str,
    payload: JobStatusUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    db = await get_db()
    res_job = await db.table("jobs").select("*").eq("id", job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    if not res_job.data:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = res_job.data
    old_status = job.get("status")
    new_status = payload.status
    booking_id = job.get("booking_id")
    
    now = datetime.now(timezone.utc).isoformat()
    await db.table("jobs").update({"status": new_status, "updated_at": now}).eq("id", job_id).execute()

    if new_status in ("completed", "delivered") and old_status not in ("completed", "delivered"):
        try:
            from ..services.payout_service import trigger_payout_for_job
            await trigger_payout_for_job(db, job_id)
        except Exception as e:
            logger.error(f"Payout trigger failed for job {job_id}: {e}")

        # Update on_time_rate for the assigned vendor member
        try:
            assignee_id = job.get("assignee_id")
            if assignee_id and assignee_id != "default_user":
                was_on_time = False
                time_end = job.get("time_window_end", "")
                sched_date = job.get("scheduled_date", "")
                if sched_date and time_end:
                    deadline = datetime.strptime(
                        f"{sched_date} {time_end}", "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=timezone.utc)
                    was_on_time = datetime.now(timezone.utc) <= deadline

                res_m = await db.table("vendor_members").select(
                    "total_assignments, on_time_rate"
                ).eq("id", assignee_id).maybe_single().execute()
                if res_m.data:
                    prev_total = res_m.data.get("total_assignments") or 0
                    prev_rate = res_m.data.get("on_time_rate") or 1.0
                    new_total = prev_total + 1
                    prev_on_time = round(prev_rate * prev_total)
                    new_on_time = prev_on_time + (1 if was_on_time else 0)
                    new_rate = round(new_on_time / new_total, 4)
                    await db.table("vendor_members").update({
                        "total_assignments": new_total,
                        "on_time_rate": new_rate,
                        "updated_at": now,
                    }).eq("id", assignee_id).execute()
        except Exception as e:
            logger.error(f"on_time_rate update failed for job {job_id}: {e}")

    # Client communications: notify agent on key status transitions
    if new_status == "in_progress" and old_status != "in_progress":
        try:
            from ..services.client_comms import notify_photographer_en_route
            await notify_photographer_en_route(job_id)
        except Exception as e:
            logger.error(f"En-route notification failed for job {job_id}: {e}")

    if booking_id:
        await send_dispatch_status_update_webhook(
            booking_id=booking_id,
            job_id=job_id,
            new_status=new_status,
            previous_status=old_status,
            actor_id=actor_id,
            actor_type="vendor_member"
        )

    return {"status": "updated", "job_id": job_id, "new_status": new_status}


# ---------------------------------------------------------------------------
# No-show policy (Snappr model):
# - Agent/client no-show → photographer gets $60 trip fee funded from Bricks
#   margin / cancellation policy revenue. No separate charge to the client.
# - Contractor no-show → cancel_rate hurt only, no cash penalty, job re-dispatched.
#
# Anti-abuse validation gates (all must pass before claim is recorded):
#   1. Job ownership: reporter must be the assigned photographer
#   2. Job state: must be active (not completed/cancelled/previously claimed)
#   3. Idempotency: duplicate claim for same job rejected
#   4. Time window: claim only valid T-15min → T+90min of scheduled shoot start
#   5. Booking status: booking must not have been cancelled before shoot start
#   6. Rate limit: 2nd+ claim in 30 days → held for admin review before payout
# ---------------------------------------------------------------------------
AGENT_NOSHOW_TRIP_FEE_CENTS = 6000   # $60 CAD
_NOSHOW_WINDOW_BEFORE_MIN = 15
_NOSHOW_WINDOW_AFTER_MIN = 90
_NOSHOW_RATE_LIMIT_DAYS = 30
_NOSHOW_RATE_LIMIT_THRESHOLD = 1     # 2nd+ claim in 30 days → hold for admin review


async def _validate_noshow_claim(
    db, job: dict, reporter_id: str
) -> dict:
    """
    Run all anti-abuse checks. Returns:
      {"ok": True, "hold_for_review": False}                  → record claim, paid on next payout
      {"ok": True, "hold_for_review": True, "reason": "..."}  → hold for admin review before payout
      {"ok": False, "reason": "..."}                          → reject outright (HTTP 422)
    """
    assignee_id = job.get("assignee_id")
    job_id = job.get("id")
    booking_id = job.get("booking_id")

    # Gate 1: must be the assigned photographer
    if assignee_id != reporter_id:
        return {"ok": False, "reason": "Only the assigned photographer can report a no-show"}

    # Gate 2: job must be in an active state (not already completed, cancelled, or previously claimed)
    current_status = job.get("status", "")
    if current_status in ("completed", "agent_no_show", "cancelled"):
        return {"ok": False, "reason": f"Cannot report no-show on a job with status '{current_status}'"}

    # Gate 3: idempotency — no existing claim for this job
    existing = await db.table("agent_noshow_trip_fees").select("id, status").eq(
        "job_id", job_id
    ).maybe_single().execute()
    if existing.data:
        return {"ok": False, "reason": "A no-show claim already exists for this job"}

    # Gate 4: time window — claim must be filed within the valid window
    shoot_start_str = job.get("time_window_start") or job.get("scheduled_at")
    if shoot_start_str:
        try:
            shoot_start = datetime.fromisoformat(shoot_start_str.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            earliest = shoot_start - timedelta(minutes=_NOSHOW_WINDOW_BEFORE_MIN)
            latest = shoot_start + timedelta(minutes=_NOSHOW_WINDOW_AFTER_MIN)
            if now_utc < earliest:
                return {
                    "ok": False,
                    "reason": f"Too early — no-show can be reported from {earliest.strftime('%H:%M UTC')}"
                }
            if now_utc > latest:
                return {
                    "ok": False,
                    "reason": (
                        f"Claim window expired — must be filed within "
                        f"{_NOSHOW_WINDOW_AFTER_MIN} minutes of scheduled shoot time"
                    )
                }
        except (ValueError, TypeError):
            pass  # If we can't parse the time, don't hard-block — log and continue

    # Gate 5: booking must not have been cancelled before shoot start
    if booking_id:
        try:
            res_b = await db.table("bookings").select("status, cancelled_at").eq(
                "id", booking_id
            ).maybe_single().execute()
            if res_b.data:
                bk_status = res_b.data.get("status", "")
                cancelled_at = res_b.data.get("cancelled_at")
                if bk_status == "cancelled" and cancelled_at:
                    # Only block if cancelled BEFORE the shoot window (not after)
                    try:
                        cancel_dt = datetime.fromisoformat(cancelled_at.replace("Z", "+00:00"))
                        shoot_dt = datetime.fromisoformat(
                            (shoot_start_str or "").replace("Z", "+00:00")
                        ) if shoot_start_str else None
                        if shoot_dt and cancel_dt < shoot_dt:
                            return {
                                "ok": False,
                                "reason": "Booking was cancelled before the shoot — no trip fee applies"
                            }
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

    # Gate 6: rate limit — count recent approved/pending claims for this photographer
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_NOSHOW_RATE_LIMIT_DAYS)).isoformat()
        res_recent = await db.table("agent_noshow_trip_fees").select("id").eq(
            "member_id", assignee_id
        ).in_("status", ["pending", "paid_out", "pending_review"]).gte(
            "created_at", cutoff
        ).execute()
        recent_count = len(res_recent.data or [])
        if recent_count >= _NOSHOW_RATE_LIMIT_THRESHOLD:
            return {
                "ok": True,
                "hold_for_review": True,
                "reason": (
                    f"Rate limit: {recent_count} claims in the last {_NOSHOW_RATE_LIMIT_DAYS} days "
                    f"— held for admin review before payout"
                ),
            }
    except Exception:
        pass

    return {"ok": True, "hold_for_review": False}


@router.post("/jobs/{job_id}/agent-no-show")
async def report_agent_no_show(
    job_id: str,
    notes: Optional[str] = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Photographer reports that the agent/client was not home.

    Trip fee ($60) is paid to the photographer from Bricks margin / cancellation
    policy revenue — no separate charge is issued to the client at this point.

    Validation gates (all must pass):
    - Must be the assigned photographer
    - Job must be in an active state
    - No duplicate claim for this job
    - Must be within T-15min → T+90min of scheduled shoot time
    - Booking must not have been cancelled before the shoot start
    - 2nd+ claim in 30 days → held for admin review before payout is released
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    db = await get_db()

    res_job = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        raise HTTPException(status_code=404, detail="Job not found")

    job = res_job.data
    vendor_id = job.get("vendor_id", "default_vendor")
    assignee_id = job.get("assignee_id")
    booking_id = job.get("booking_id")
    now = datetime.now(timezone.utc).isoformat()

    validation = await _validate_noshow_claim(db, job, actor_id)
    if not validation["ok"]:
        raise HTTPException(status_code=422, detail=validation["reason"])

    hold_for_review = validation.get("hold_for_review", False)
    review_reason = validation.get("reason", "")
    claim_status = "pending_review" if hold_for_review else "pending"

    await db.table("jobs").update({"status": "agent_no_show", "updated_at": now}).eq("id", job_id).execute()

    if assignee_id and assignee_id != "default_user":
        await db.table("agent_noshow_trip_fees").insert({
            "job_id": job_id,
            "member_id": assignee_id,
            "vendor_id": vendor_id,
            "amount_cents": AGENT_NOSHOW_TRIP_FEE_CENTS,
            "booking_id": booking_id,
            "notes": notes or "",
            "status": claim_status,
            "review_reason": review_reason or None,
            "created_at": now,
        }).execute()

    await write_audit_event(
        actor_id, "job", job_id, "agent_no_show", vendor_id=vendor_id,
        details={
            "assignee_id": assignee_id,
            "trip_fee_cents": AGENT_NOSHOW_TRIP_FEE_CENTS,
            "claim_status": claim_status,
            "hold_for_review": hold_for_review,
            "review_reason": review_reason,
        }
    )

    if hold_for_review:
        return {
            "job_id": job_id,
            "photographer_id": assignee_id,
            "trip_fee_cad": AGENT_NOSHOW_TRIP_FEE_CENTS / 100,
            "claim_status": "pending_review",
            "note": review_reason,
        }

    return {
        "job_id": job_id,
        "photographer_id": assignee_id,
        "trip_fee_cad": AGENT_NOSHOW_TRIP_FEE_CENTS / 100,
        "claim_status": "pending",
        "note": "Trip fee will be included in photographer's next payout.",
    }


@router.post("/jobs/{job_id}/contractor-no-show")
async def report_contractor_no_show(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Admin reports that the assigned photographer failed to show up.

    Effects:
    1. cancel_rate updated on contractor (hurts dispatch priority — no cash penalty)
    2. Job immediately re-dispatched
    3. Agent notified with new assignment confirmation
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions — admin only", "insufficient_permissions")

    vendor_id = actor_member.get("vendor_id", "default_vendor")
    db = await get_db()

    res_job = await db.table("jobs").select("*").eq("id", job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    if not res_job.data:
        raise HTTPException(status_code=404, detail="Job not found")

    job = res_job.data
    assignee_id = job.get("assignee_id")
    now = datetime.now(timezone.utc).isoformat()

    # Update cancel_rate — dispatch score penalty only (no cash deduction)
    if assignee_id and assignee_id != "default_user":
        try:
            res_m = await db.table("vendor_members").select(
                "total_assignments, cancel_rate"
            ).eq("id", assignee_id).maybe_single().execute()
            if res_m.data:
                total = (res_m.data.get("total_assignments") or 0) + 1
                prev_cancel = res_m.data.get("cancel_rate") or 0.0
                prev_cancels = round(prev_cancel * (total - 1))
                new_rate = round((prev_cancels + 1) / total, 4)
                await db.table("vendor_members").update({
                    "total_assignments": total,
                    "cancel_rate": new_rate,
                    "updated_at": now,
                }).eq("id", assignee_id).execute()
                logger.warning(f"Contractor {assignee_id} no-show: cancel_rate now {new_rate:.1%}")
        except Exception as e:
            logger.error(f"cancel_rate update failed for member {assignee_id}: {e}")

    await write_audit_event(
        actor_id, "job", job_id, "contractor_no_show", vendor_id=vendor_id,
        details={"assignee_id": assignee_id}
    )

    # Re-dispatch
    await db.table("jobs").update({
        "status": "pending",
        "assignee_id": "default_user",
        "updated_at": now,
    }).eq("id", job_id).execute()

    redispatch_member_id = None
    try:
        from ..services.vendor_selection import assign_vendor_for_job
        redispatch_member_id = await assign_vendor_for_job(job_id)
    except Exception as e:
        logger.error(f"Re-dispatch after contractor no-show failed for job {job_id}: {e}")

    try:
        from ..services.client_comms import notify_booking_confirmed
        if redispatch_member_id:
            await notify_booking_confirmed(job_id)
    except Exception as e:
        logger.error(f"Re-dispatch notification failed for job {job_id}: {e}")

    return {
        "job_id": job_id,
        "original_contractor": assignee_id,
        "redispatched_to": redispatch_member_id,
        "redispatch_status": "success" if redispatch_member_id else "failed_manual_required",
        "note": "Contractor cancel_rate updated (dispatch score impact); no cash penalty applied",
    }
