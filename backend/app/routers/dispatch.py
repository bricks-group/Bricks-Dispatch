from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, Header

from ..services.notifications import create_notification
from ..shared.audit import write_audit_event
from ..shared.auth import ensure_assigned_to_actor, get_vendor_member, resolve_actor_id, resolve_vendor_actor_id
from ..shared.database import db
from ..shared.errors import api_error
from ..shared.schemas import ChecklistToggle, JobStatusUpdate, Profile, ProfileUpdate, PushTokenRegister, UploadRecord


router = APIRouter(prefix="/api", tags=["dispatch"])


@router.get("/")
async def root():
    return {"message": "Bricks Dispatch API"}


@router.post("/push-tokens")
async def register_push_token(reg: PushTokenRegister):
    existing = await db.push_tokens.find_one({"token": reg.token})
    if existing:
        await db.push_tokens.update_one({"token": reg.token}, {"$set": {"active": True, "platform": reg.platform, "updated_at": datetime.now(timezone.utc).isoformat()}})
    else:
        await db.push_tokens.insert_one({"token": reg.token, "platform": reg.platform, "active": True, "created_at": datetime.now(timezone.utc).isoformat()})
    return {"registered": True}


@router.get("/notifications")
async def get_notifications():
    return await db.notifications.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)


@router.put("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: str):
    await db.notifications.update_one({"id": notif_id}, {"$set": {"read": True}})
    return {"read": True}


@router.get("/notifications/unread-count")
async def unread_count():
    return {"count": await db.notifications.count_documents({"read": False})}


@router.get("/jobs")
async def get_jobs(status: Optional[str] = None, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    query = {"vendor_id": member.get("vendor_id", "default_vendor"), "assignee_id": actor_id}
    if status:
        query["status"] = status
    return await db.jobs.find(query, {"_id": 0}).sort("time_window_start", 1).to_list(100)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    job = await db.jobs.find_one({"id": job_id, "vendor_id": member.get("vendor_id", "default_vendor")}, {"_id": 0})
    if not job:
        api_error(404, "Job not found", "job_not_found")
    ensure_assigned_to_actor(job, actor_id)
    return job


@router.put("/jobs/{job_id}/status")
async def update_job_status(job_id: str, update: JobStatusUpdate, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    valid_transitions = {"assigned": ["in_progress"], "in_progress": ["completed"], "completed": []}
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        api_error(404, "Job not found", "job_not_found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    current_status = job["status"]
    if update.status not in valid_transitions.get(current_status, []):
        api_error(400, f"Cannot transition from {current_status} to {update.status}", "invalid_job_transition")
    if current_status == "in_progress" and update.status == "completed":
        checklist = job.get("checklist", [])
        incomplete_count = len([item for item in checklist if not item.get("completed", False)])
        missing_uploads = max(max(job.get("deliverables_count", 0), 0) - max(job.get("uploaded_count", 0), 0), 0)
        if incomplete_count > 0 or missing_uploads > 0:
            blockers = []
            if incomplete_count:
                blockers.append(f"{incomplete_count} checklist item(s) incomplete")
            if missing_uploads:
                blockers.append(f"{missing_uploads} deliverable upload(s) remaining")
            api_error(400, f"Cannot complete job: {', '.join(blockers)}", "job_completion_blocked")
    now = datetime.now(timezone.utc).isoformat()
    await db.jobs.update_one({"id": job_id}, {"$set": {"status": update.status, "updated_at": now}})
    updated = await db.jobs.find_one({"id": job_id}, {"_id": 0})

    # Sync status back to Booking's bookings collection
    if updated.get("booking_id"):
        booking_status_map = {"in_progress": "in_progress", "completed": "delivered"}
        booking_status = booking_status_map.get(update.status)
        if booking_status:
            await db.bookings.update_one(
                {"booking_id": updated["booking_id"]},
                {"$set": {"status": booking_status, "updated_at": now}},
            )

    await create_notification(
        title=f"Job {'Started' if update.status == 'in_progress' else 'Completed'}",
        body=f"{job['address']} — status changed to {update.status.replace('_', ' ')}",
        ntype="status_change",
        data={"job_id": job_id, "status": update.status},
    )
    await write_audit_event(actor_id, "job", job_id, "status_change", vendor_id=job.get("vendor_id", "default_vendor"), details={"from": current_status, "to": update.status})
    return updated


@router.put("/jobs/{job_id}/checklist/{item_id}")
async def toggle_checklist_item(job_id: str, item_id: str, toggle: ChecklistToggle, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        api_error(404, "Job not found", "job_not_found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") != "in_progress":
        api_error(400, "Checklist can only be updated while job is in progress", "checklist_locked")
    result = await db.jobs.update_one(
        {"id": job_id, "checklist.id": item_id},
        {"$set": {"checklist.$.completed": toggle.completed, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if result.matched_count == 0:
        api_error(404, "Checklist item not found", "checklist_item_not_found")
    await write_audit_event(actor_id, "job", job_id, "checklist_toggle", vendor_id=job.get("vendor_id", "default_vendor"), details={"item_id": item_id, "completed": toggle.completed})
    return await db.jobs.find_one({"id": job_id}, {"_id": 0})


@router.get("/schedule")
async def get_schedule(date: Optional[str] = None, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    query = {"vendor_id": member.get("vendor_id", "default_vendor"), "assignee_id": actor_id}
    if date:
        query["scheduled_date"] = date
    return await db.jobs.find(query, {"_id": 0}).sort("time_window_start", 1).to_list(100)


@router.get("/uploads")
async def get_uploads(job_id: Optional[str] = None, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    if job_id:
        job = await db.jobs.find_one({"id": job_id, "vendor_id": vendor_id}, {"_id": 0})
        if not job:
            api_error(404, "Job not found", "job_not_found")
        ensure_assigned_to_actor(job, actor_id)
        return await db.uploads.find({"job_id": job_id}, {"_id": 0}).sort("created_at", -1).to_list(200)
    jobs = await db.jobs.find({"vendor_id": vendor_id, "assignee_id": actor_id}, {"_id": 0, "id": 1}).to_list(500)
    job_ids = [j["id"] for j in jobs]
    if not job_ids:
        return []
    return await db.uploads.find({"job_id": {"$in": job_ids}}, {"_id": 0}).sort("created_at", -1).to_list(200)


@router.post("/uploads")
async def create_upload(job_id: str = Form(...), filename: str = Form(...), file_size: int = Form(0), mime_type: str = Form(""), file_data: str = Form(""), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        api_error(404, "Job not found", "job_not_found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") != "in_progress":
        api_error(400, "Uploads are only allowed while job is in progress", "upload_locked")
    upload = UploadRecord(job_id=job_id, filename=filename, file_size=file_size, mime_type=mime_type, thumbnail=file_data[:200] if file_data else "")
    upload_dict = upload.dict()
    await db.uploads.insert_one(upload_dict)
    await db.jobs.update_one({"id": job_id}, {"$inc": {"uploaded_count": 1}})
    upload_dict.pop("_id", None)
    await create_notification("Upload Complete", f"{filename} uploaded to {job['address']}", ntype="upload", data={"job_id": job_id, "filename": filename})
    await write_audit_event(actor_id, "upload", upload_dict["id"], "create", vendor_id=job.get("vendor_id", "default_vendor"), details={"job_id": job_id, "filename": filename})
    return upload_dict


@router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: str, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    upload = await db.uploads.find_one({"id": upload_id}, {"_id": 0})
    if not upload:
        api_error(404, "Upload not found", "upload_not_found")
    job = await db.jobs.find_one({"id": upload["job_id"]}, {"_id": 0})
    if not job:
        api_error(404, "Job not found", "job_not_found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") == "completed":
        api_error(400, "Cannot delete uploads from completed jobs", "upload_delete_blocked")
    if job.get("status") != "in_progress":
        api_error(400, "Uploads can only be deleted while job is in progress", "upload_locked")
    await db.uploads.delete_one({"id": upload_id})
    next_count = max(job.get("uploaded_count", 0) - 1, 0)
    await db.jobs.update_one({"id": upload["job_id"]}, {"$set": {"uploaded_count": next_count, "updated_at": datetime.now(timezone.utc).isoformat()}})
    await write_audit_event(actor_id, "upload", upload_id, "delete", vendor_id=job.get("vendor_id", "default_vendor"), details={"job_id": upload["job_id"]})
    return {"deleted": True}


@router.get("/profile")
async def get_profile():
    profile = await db.profile.find_one({"id": "default_user"}, {"_id": 0})
    if not profile:
        default = Profile().dict()
        await db.profile.insert_one(default)
        return default
    return profile


@router.put("/profile")
async def update_profile(update: ProfileUpdate):
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if not update_data:
        api_error(400, "No update data", "no_update_data")
    await db.profile.update_one({"id": "default_user"}, {"$set": update_data}, upsert=True)
    return await db.profile.find_one({"id": "default_user"}, {"_id": 0})

