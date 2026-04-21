from datetime import datetime, timedelta, timezone
import uuid
from typing import Optional

from fastapi import APIRouter, Header

from ..services.vendor_selection import assign_vendor_for_job
from ..shared.audit import write_audit_event
from ..shared.auth import get_vendor_member, get_vendor_member_ids, is_vendor_admin, resolve_vendor_actor_id
from ..shared.database import db
from ..shared.errors import api_error
from ..shared.pagination import normalize_pagination, paginated_response
from ..shared.schemas import (
    AvailabilitySettings,
    AvailabilityUpdate,
    JobAssignmentUpdate,
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
    jobs = await db.jobs.find({"vendor_id": vendor_id, "assignee_id": {"$in": member_ids}}, {"_id": 0}).to_list(500)
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
    query = {"vendor_id": actor_member.get("vendor_id", "default_vendor")}
    if status:
        query["status"] = status
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"role": {"$regex": search, "$options": "i"}},
        ]
    total = await db.vendor_members.count_documents(query)
    page, page_size, skip = normalize_pagination(page, page_size)
    members = await db.vendor_members.find(query, {"_id": 0}).sort("created_at", 1).skip(skip).limit(page_size).to_list(page_size)
    for member in members:
        member["assigned_jobs"] = await db.jobs.count_documents({"vendor_id": query["vendor_id"], "assignee_id": member["id"], "status": {"$ne": "completed"}})
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
    await db.vendor_members.insert_one(member_dict)
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
    result = await db.vendor_members.update_one({"id": member_id, "vendor_id": actor_member.get("vendor_id", "default_vendor")}, {"$set": update_data})
    if result.matched_count == 0:
        api_error(404, "Member not found", "member_not_found")
    member = await db.vendor_members.find_one({"id": member_id}, {"_id": 0})
    member["assigned_jobs"] = await db.jobs.count_documents({"vendor_id": actor_member.get("vendor_id", "default_vendor"), "assignee_id": member_id, "status": {"$ne": "completed"}})
    await write_audit_event(actor_id, "vendor_member", member_id, "update", vendor_id=actor_member.get("vendor_id", "default_vendor"), details=update_data)
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
    result = await db.vendor_members.delete_one({"id": member_id, "vendor_id": vendor_id})
    if result.deleted_count == 0:
        api_error(404, "Member not found", "member_not_found")
    await db.jobs.update_many({"assignee_id": member_id, "vendor_id": vendor_id}, {"$set": {"assignee_id": "default_user", "updated_at": datetime.now(timezone.utc).isoformat()}})
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
    query = {"vendor_id": vendor_id}
    if status:
        query["status"] = status
    if assignee_id:
        query["assignee_id"] = assignee_id
    if search:
        query["$or"] = [
            {"address": {"$regex": search, "$options": "i"}},
            {"city": {"$regex": search, "$options": "i"}},
        ]
    total = await db.jobs.count_documents(query)
    page, page_size, skip = normalize_pagination(page, page_size)
    jobs = await db.jobs.find(query, {"_id": 0}).sort("scheduled_date", 1).skip(skip).limit(page_size).to_list(page_size)
    members = await db.vendor_members.find({"vendor_id": vendor_id}, {"_id": 0, "id": 1, "name": 1}).to_list(500)
    member_map = {m["id"]: m.get("name", "Unknown") for m in members}
    for job in jobs:
        job["assignee_name"] = member_map.get(job.get("assignee_id"), "Unassigned")
    return paginated_response(jobs, total, page, page_size)


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

    existing_job = await db.jobs.find_one({"id": job_id, "vendor_id": vendor_id}, {"_id": 0, "assignee_id": 1})
    if not existing_job:
        api_error(404, "Job not found", "job_not_found")

    if payload.member_id == "auto":
        new_assignee_id = await assign_vendor_for_job(job_id)
    else:
        member = await db.vendor_members.find_one({"id": payload.member_id, "vendor_id": vendor_id, "is_active": True}, {"_id": 0})
        if not member:
            api_error(404, "Member not found", "member_not_found")
        new_assignee_id = payload.member_id
        await db.jobs.update_one({"id": job_id, "vendor_id": vendor_id}, {"$set": {"assignee_id": new_assignee_id, "updated_at": datetime.now(timezone.utc).isoformat()}})

    await write_audit_event(actor_id, "job", job_id, "assign", vendor_id=vendor_id, details={"from": existing_job.get("assignee_id"), "to": new_assignee_id, "method": "auto" if payload.member_id == "auto" else "manual"})
    return await db.jobs.find_one({"id": job_id}, {"_id": 0})


@router.get("/availability")
async def get_vendor_availability(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    record = await db.vendor_availability.find_one({"vendor_id": vendor_id, "member_id": actor_id}, {"_id": 0})
    if not record:
        default = AvailabilitySettings(member_id=actor_id, vendor_id=vendor_id).dict()
        await db.vendor_availability.insert_one(default)
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
    await db.vendor_availability.update_one(
        {"vendor_id": vendor_id, "member_id": actor_id},
        {"$set": update_data, "$setOnInsert": {"id": str(uuid.uuid4()), "vendor_id": vendor_id, "member_id": actor_id}},
        upsert=True,
    )
    await write_audit_event(actor_id, "availability", actor_id, "update", vendor_id=vendor_id, details=update_data)
    return await db.vendor_availability.find_one({"vendor_id": vendor_id, "member_id": actor_id}, {"_id": 0})


@router.get("/performance")
async def get_vendor_performance(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    members = await db.vendor_members.find({"vendor_id": vendor_id, "is_active": True}, {"_id": 0, "id": 1}).to_list(500)
    member_ids = [m["id"] for m in members] or ["default_user"]
    jobs = await db.jobs.find({"vendor_id": vendor_id, "assignee_id": {"$in": member_ids}}, {"_id": 0}).to_list(500)
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
    settings = await db.vendor_settings.find_one({"vendor_id": vendor_id}, {"_id": 0})
    if not settings:
        default = VendorSettings(vendor_id=vendor_id).dict()
        await db.vendor_settings.insert_one(default)
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
    await db.vendor_settings.update_one(
        {"vendor_id": vendor_id},
        {"$set": update_data, "$setOnInsert": {"id": "default_vendor_settings", "vendor_id": vendor_id}},
        upsert=True,
    )
    await write_audit_event(actor_id, "settings", vendor_id, "update", vendor_id=vendor_id, details=update_data)
    return await db.vendor_settings.find_one({"vendor_id": vendor_id}, {"_id": 0})


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
    query = {"vendor_id": vendor_id}
    if entity_type:
        query["entity_type"] = entity_type
    total = await db.audit_events.count_documents(query)
    page, page_size, skip = normalize_pagination(page, page_size)
    items = await db.audit_events.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(page_size).to_list(page_size)
    return paginated_response(items, total, page, page_size)

