import base64
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, Header

from ..services.notifications import create_notification
from ..services.storage import get_signed_download_url, upload_file
from ..services.vendor_selection import assign_vendor_for_job
from ..shared.audit import write_audit_event
from ..shared.auth import ensure_assigned_to_actor, get_vendor_member, resolve_actor_id, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.schemas import ChecklistToggle, JobStatusUpdate, Profile, ProfileUpdate, PushTokenRegister, UploadRecord


router = APIRouter(prefix="/api", tags=["dispatch"])


@router.get("/")
async def root():
    return {"message": "Bricks Dispatch API"}


@router.post("/push-tokens")
async def register_push_token(reg: PushTokenRegister):
    res = await (await get_db()).table("push_tokens").select("*").eq("token", reg.token).maybe_single().execute()
    existing = res.data
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        await (await get_db()).table("push_tokens").update({"active": True, "platform": reg.platform, "updated_at": now}).eq("token", reg.token).execute()
    else:
        await (await get_db()).table("push_tokens").insert({"token": reg.token, "platform": reg.platform, "active": True, "created_at": now, "updated_at": now}).execute()
    return {"registered": True}


@router.post("/jobs/{job_id}/auto-assign")
async def auto_assign_job(job_id: str):
    try:
        member_id = await assign_vendor_for_job(job_id)
        res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        return res.data
    except ValueError as e:
        api_error(404, str(e), "job_not_found")
    except Exception as e:
        api_error(500, f"Auto-assignment failed: {str(e)}", "auto_assign_failed")


@router.get("/notifications")
async def get_notifications():
    res = await (await get_db()).table("notifications").select("*").order("created_at", ascending=False).limit(50).execute()
    return res.data


@router.put("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: str):
    await (await get_db()).table("notifications").update({"read": True}).eq("id", notif_id).execute()
    return {"read": True}


@router.get("/notifications/unread-count")
async def unread_count():
    res = await (await get_db()).table("notifications").select("*", count="exact").eq("read", False).execute()
    return {"count": res.count}


@router.get("/jobs")
async def get_jobs(status: Optional[str] = None, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    sb = await get_db(); query = sb.table("jobs").select("*").eq("vendor_id", member.get("vendor_id", "default_vendor")).eq("assignee_id", actor_id)
    if status:
        query = query.eq("status", status)
    res = await query.order("time_window_start", ascending=True).limit(100).execute()
    return res.data


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    res = await (await get_db()).table("jobs").select("*").eq("id", job_id).eq("vendor_id", member.get("vendor_id", "default_vendor")).maybe_single().execute()
    job = res.data
    if not job:
        api_error(404, "Job not found", "job_not_found")
    ensure_assigned_to_actor(job, actor_id)
    return job


@router.put("/jobs/{job_id}/status")
async def update_job_status(job_id: str, update: JobStatusUpdate, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    valid_transitions = {"pending_assignment": ["assigned"], "assigned": ["in_progress", "pending_assignment"], "in_progress": ["completed"], "completed": ["delivered"], "delivered": []}
    res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job = res.data
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
    await (await get_db()).table("jobs").update({"status": update.status, "updated_at": now}).eq("id", job_id).execute()
    res_updated = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    updated = res_updated.data

    if updated.get("booking_id"):
        booking_status_map = {
            "assigned":    "scheduled",
            "in_progress": "in_progress",
            "completed":   "completed",
            "delivered":   "delivered",
        }
        booking_status = booking_status_map.get(update.status)
        if booking_status:
            set_fields: dict = {"status": booking_status, "updated_at": now}
            if update.status == "completed":
                set_fields["completed_at"] = now
            if update.status == "delivered":
                set_fields["delivered_at"] = now
            await (await get_db()).table("bookings").update(set_fields).eq("booking_id", updated["booking_id"]).execute()

    _notif_title_map = {"assigned": "Job Assigned", "in_progress": "Shoot Started", "completed": "Shoot Complete", "delivered": "Media Delivered"}
    await create_notification(
        title=_notif_title_map.get(update.status, "Job Updated"),
        body=f"{job['address']} \u2014 status changed to {update.status.replace('_', ' ')}",
        ntype="status_change",
        data={"job_id": job_id, "status": update.status},
    )
    await write_audit_event(actor_id, "job", job_id, "status_change", vendor_id=job.get("vendor_id", "default_vendor"), details={"from": current_status, "to": update.status})
    return updated


@router.put("/jobs/{job_id}/checklist/{item_id}")
async def toggle_checklist_item(job_id: str, item_id: str, toggle: ChecklistToggle, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job = res.data
    if not job:
        api_error(404, "Job not found", "job_not_found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") != "in_progress":
        api_error(400, "Checklist can only be updated while job is in progress", "checklist_locked")
    
    checklist = job.get("checklist", [])
    found = False
    for item in checklist:
        if item.get("id") == item_id:
            item["completed"] = toggle.completed
            found = True
            break
    
    if not found:
        api_error(404, "Checklist item not found", "checklist_item_not_found")
    
    await (await get_db()).table("jobs").update({"checklist": checklist, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", job_id).execute()
    await write_audit_event(actor_id, "job", job_id, "checklist_toggle", vendor_id=job.get("vendor_id", "default_vendor"), details={"item_id": item_id, "completed": toggle.completed})
    
    res_final = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    return res_final.data


@router.get("/schedule")
async def get_schedule(date: Optional[str] = None, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    sb = await get_db(); query = sb.table("jobs").select("*").eq("vendor_id", member.get("vendor_id", "default_vendor")).eq("assignee_id", actor_id)
    if date:
        query = query.eq("scheduled_date", date)
    res = await query.order("time_window_start", ascending=True).limit(100).execute()
    return res.data


@router.get("/uploads")
async def get_uploads(job_id: Optional[str] = None, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    if job_id:
        res_job = await (await get_db()).table("jobs").select("*").eq("id", job_id).eq("vendor_id", vendor_id).maybe_single().execute()
        job = res_job.data
        if not job:
            api_error(404, "Job not found", "job_not_found")
        ensure_assigned_to_actor(job, actor_id)
        res_uploads = await (await get_db()).table("uploads").select("*").eq("job_id", job_id).order("created_at", ascending=False).limit(200).execute()
        return res_uploads.data
    
    res_jobs = await (await get_db()).table("jobs").select("id").eq("vendor_id", vendor_id).eq("assignee_id", actor_id).limit(500).execute()
    job_ids = [j["id"] for j in res_jobs.data]
    if not job_ids:
        return []
    res_uploads = await (await get_db()).table("uploads").select("*").contains("job_id", job_ids).order("created_at", ascending=False).limit(200).execute()
    # Wait, Supabase `.contains` is for arrays. For IN, we use `.in_`
    res_uploads = await (await get_db()).table("uploads").select("*").in_("job_id", job_ids).order("created_at", ascending=False).limit(200).execute()
    return res_uploads.data


@router.post("/uploads")
async def create_upload(job_id: str = Form(...), filename: str = Form(...), file_size: int = Form(0), mime_type: str = Form(""), file_data: str = Form(""), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    res_job = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job = res_job.data
    if not job:
        api_error(404, "Job not found", "job_not_found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") != "in_progress":
        api_error(400, "Uploads are only allowed while job is in progress", "upload_locked")

    upload = UploadRecord(job_id=job_id, filename=filename, file_size=file_size, mime_type=mime_type)
    if file_data:
        try:
            raw_bytes = base64.b64decode(file_data)
        except Exception:
            raw_bytes = file_data.encode()
        file_key, thumb_key, thumb_b64 = await upload_file(
            raw_bytes, job_id, upload.id, filename, mime_type
        )
        upload.thumbnail = thumb_b64
        upload.file_url = file_key
        upload.thumbnail_url = thumb_key
    
    upload_dict = upload.dict()
    await (await get_db()).table("uploads").insert(upload_dict).execute()
    
    # Increment uploaded_count
    new_count = job.get("uploaded_count", 0) + 1
    await (await get_db()).table("jobs").update({"uploaded_count": new_count}).eq("id", job_id).execute()
    
    await create_notification("Upload Complete", f"{filename} uploaded to {job['address']}", ntype="upload", data={"job_id": job_id, "filename": filename})
    await write_audit_event(actor_id, "upload", upload_dict["id"], "create", vendor_id=job.get("vendor_id", "default_vendor"), details={"job_id": job_id, "filename": filename})
    return upload_dict


@router.get("/uploads/{upload_id}/download")
async def get_upload_download_url(upload_id: str, authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    res_upload = await (await get_db()).table("uploads").select("*").eq("id", upload_id).maybe_single().execute()
    upload = res_upload.data
    if not upload:
        api_error(404, "Upload not found", "upload_not_found")
    res_job = await (await get_db()).table("jobs").select("*").eq("id", upload["job_id"]).maybe_single().execute()
    job = res_job.data
    if not job:
        api_error(404, "Job not found", "job_not_found")
    
    res_member = await (await get_db()).table("vendor_members").select("*").eq("id", actor_id).maybe_single().execute()
    member = res_member.data
    if member:
        ensure_assigned_to_actor(job, actor_id)
    
    s3_key = upload.get("file_url")
    signed_url = get_signed_download_url(s3_key) if s3_key else None
    return {
        "upload_id": upload_id,
        "filename": upload.get("filename"),
        "signed_url": signed_url,
        "expires_in": 3600,
        "available": signed_url is not None,
    }


@router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: str, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    res_upload = await (await get_db()).table("uploads").select("*").eq("id", upload_id).maybe_single().execute()
    upload = res_upload.data
    if not upload:
        api_error(404, "Upload not found", "upload_not_found")
    res_job = await (await get_db()).table("jobs").select("*").eq("id", upload["job_id"]).maybe_single().execute()
    job = res_job.data
    if not job:
        api_error(404, "Job not found", "job_not_found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") == "completed":
        api_error(400, "Cannot delete uploads from completed jobs", "upload_delete_blocked")
    if job.get("status") != "in_progress":
        api_error(400, "Uploads can only be deleted while job is in progress", "upload_locked")
    
    await (await get_db()).table("uploads").delete().eq("id", upload_id).execute()
    next_count = max(job.get("uploaded_count", 0) - 1, 0)
    await (await get_db()).table("jobs").update({"uploaded_count": next_count, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", upload["job_id"]).execute()
    await write_audit_event(actor_id, "upload", upload_id, "delete", vendor_id=job.get("vendor_id", "default_vendor"), details={"job_id": upload["job_id"]})
    return {"deleted": True}


@router.get("/profile")
async def get_profile():
    res = await (await get_db()).table("profile").select("*").eq("id", "default_user").maybe_single().execute()
    profile = res.data
    if not profile:
        default = Profile().dict()
        await (await get_db()).table("profile").insert(default).execute()
        return default
    return profile


@router.put("/profile")
async def update_profile(update: ProfileUpdate):
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if not update_data:
        api_error(400, "No update data", "no_update_data")
    await (await get_db()).table("profile").upsert({"id": "default_user", **update_data}).execute()
    res = await (await get_db()).table("profile").select("*").eq("id", "default_user").maybe_single().execute()
    return res.data


VENDOR_PAYOUT_RATE = 0.62

@router.get("/payouts")
async def get_payouts(authorization: str | None = Header(default=None, alias="Authorization"), x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    from datetime import datetime, timezone, timedelta
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")

    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    res_jobs = await (await get_db()).table("jobs").select("*").eq("assignee_id", actor_id).eq("vendor_id", vendor_id).order("scheduled_date", ascending=False).limit(50).execute()
    jobs = res_jobs.data

    recent = []
    week_total = 0
    paid_total = 0
    pending_total = 0

    for j in jobs:
        raw_price = j.get("package_price") or 0
        payout = round(raw_price * VENDOR_PAYOUT_RATE)
        sched = j.get("scheduled_date", "")
        is_this_week = False
        try:
            sched_dt = datetime.fromisoformat(sched).replace(tzinfo=timezone.utc) if sched else None
            is_this_week = sched_dt and week_start <= sched_dt < week_end
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
                "payout_status": "Paid" if is_paid else "Pending",
            })

    payout_day = (week_end).strftime("%A")
    week_job_count = sum(1 for j in jobs if j.get("scheduled_date", "") >= week_start.date().isoformat())
    return {
        "week_total": week_total,
        "paid_amount": paid_total,
        "pending_amount": pending_total,
        "payout_day": payout_day,
        "week_job_count": week_job_count,
        "recent_jobs": recent,
    }
