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

        is_paid = j.get("status") == "completed"
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
                "payout_status": "paid" if is_paid else "pending",
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
