import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks
from ..shared.schemas import JobIntakeRequest, Job, JobStatusUpdate
from ..shared.database import get_db
from ..services.vendor_selection import assign_vendor_for_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])

@router.get("/", summary="Dispatch service root")
async def dispatch_root():
    return {"message": "Welcome to the Dispatch Service"}

@router.post("/jobs", summary="Create a new dispatch job")
async def create_dispatch_job(payload: JobIntakeRequest, background_tasks: BackgroundTasks):
    """
    Intake endpoint for Booking backend. Creates a job and triggers AI assignment.
    """
    db = await get_db()
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    # Map intake request to Job schema
    job_data = payload.dict()
    job_data["id"] = job_id
    job_data["status"] = "pending_assignment"
    job_data["created_at"] = now
    job_data["updated_at"] = now
    
    # Ensure checklist items have IDs
    for item in job_data.get("checklist", []):
        if "id" not in item:
            item["id"] = str(uuid.uuid4())

    try:
        res = await db.table("jobs").insert(job_data).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Failed to persist job")
        
        # Trigger AI assignment in background
        background_tasks.add_task(assign_vendor_for_job, job_id)
        
        return {
            "job_id": job_id,
            "status": "pending_assignment",
            "created_at": now,
            "checklist": job_data["checklist"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.put("/jobs/{job_id}/status", summary="Update job status and sync to Booking")
async def update_job_status(job_id: str, payload: JobStatusUpdate, authorization: str = Header(None), x_user_id: str = Header(None)):
    """
    Update job status and trigger webhook back to Booking service.
    """
    from ..shared.auth import resolve_vendor_actor_id, get_vendor_member
    from ..services.webhooks import send_dispatch_status_update_webhook
    
    db = await get_db()
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    # Fetch job to get current status and booking_id
    res_job = await db.table("jobs").select("*").eq("id", job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    if not res_job.data:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = res_job.data
    old_status = job.get("status")
    new_status = payload.status
    booking_id = job.get("booking_id")
    
    now = datetime.now(timezone.utc).isoformat()
    await db.table("jobs").update({
        "status": new_status,
        "updated_at": now
    }).eq("id", job_id).execute()

    # Auto-trigger payout when job is completed or delivered
    if new_status in ("completed", "delivered") and old_status not in ("completed", "delivered"):
        logger.info(f"Job {job_id} moved to {new_status}, triggering payout")
        try:
            from ..services.payout_service import trigger_payout_for_job
            payout_result = await trigger_payout_for_job(db, job_id)
            logger.info(f"Payout result for job {job_id}: {payout_result}")
        except Exception as e:
            logger.error(f"Payout trigger failed for job {job_id}: {e}")

    # Trigger sync to Booking if booking_id exists
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
