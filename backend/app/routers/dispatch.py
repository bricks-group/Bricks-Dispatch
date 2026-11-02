import logging
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException
from ..shared.schemas import JobIntakeRequest, Job, JobStatusUpdate
from ..shared.database import get_db
from ..services.jev_hold import jev_hold_decision
from ..services.vendor_selection import assign_vendor_for_job
from ..services.notifications import (
    create_notification,
    paid_booking_admin_subject,
    short_booking_ref,
    _paid_booking_admin_email_html,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])

ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "akashorter@gmail.com")


async def _note_jev_hold(db, job_id: str, yes_probability: float | None) -> None:
    """Stamp held_for_review + note. A write failure must not assign it.

    Admin GET /api/admin/dispatch-failures lists held_for_review next to
    dispatch_failed so Jev holds are visible without digging email. Release via
    POST /api/admin/dispatch-failures/{job_id}/release.
    """
    probability = f"{yes_probability:.2f}" if isinstance(yes_probability, float) else "n/a"
    try:
        await db.table("jobs").update({
            "status": "held_for_review",
            "dispatch_explanation": (
                "Held for a person before auto-assign "
                f"(Jev yes-probability {probability})."
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).execute()
    except Exception:
        logger.exception("Jev hold note failed for job %s", job_id)

@router.get("/", summary="Dispatch service root")
async def dispatch_root():
    return {"message": "Welcome to the Dispatch Service"}

@router.post("/jobs", summary="Create a new dispatch job")
async def create_dispatch_job(
    payload: JobIntakeRequest,
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
):
    """
    Intake endpoint for Booking backend. Creates a job and assigns a vendor before return.
    """
    internal_keys = [k.strip() for k in (os.environ.get("INTERNAL_API_KEYS") or "").split(",") if k.strip()]
    if not internal_keys or x_internal_api_key not in internal_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing internal API key")

    # Soft-retire legacy Companion SKUs for NEW jobs only (estate/standard/premium/luxury).
    # Historical rows keep those package_ids for vendor_payout_rates reads.
    from ..services.payout_service import is_retired_for_new_jobs
    _pkg = getattr(payload, "package_id", None)
    if is_retired_for_new_jobs(_pkg):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Package '{_pkg}' is retired for new jobs. "
                "Use photos_only / essentials / story / showcase / full (or suite). "
                "Legacy package_ids remain readable for historical payouts only."
            ),
        )

    # Every job traces back to exactly one caller: Booking (booking_id) or
    # Bricks-OS's developer/REIT progress-capture flow (dev_capture_visit_id).
    # Without this gate a malformed payload would insert a job neither side
    # can ever look up or report status back against.
    if not payload.booking_id and not payload.dev_capture_visit_id:
        raise HTTPException(status_code=400, detail="Either booking_id or dev_capture_visit_id is required")

    db = await get_db()
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    # Map intake request to Job schema
    job_data = payload.dict(exclude_none=True)
    job_data["id"] = job_id
    job_data["status"] = "pending_assignment"
    job_data["created_at"] = now
    job_data["updated_at"] = now
    # rack_price must always be the undiscounted rate vendor payout is computed off --
    # default to package_price if Booking didn't send it explicitly (should always send it
    # going forward, this is a defensive fallback, not the primary path).
    if "rack_price" not in job_data:
        job_data["rack_price"] = job_data.get("package_price")
    # Assign-first checkout stamps assignee_id in Stripe metadata → OS/edge intake.
    # Honor it so paid travel fee matches the photographer who was quoted.
    preassigned = (job_data.get("assignee_id") or "").strip()
    if preassigned in ("", "default_user"):
        preassigned = ""
        job_data["assignee_id"] = ""
    else:
        job_data["assignee_id"] = preassigned
        job_data["status"] = "assigned"
    # Commercial jobs: a pre-stamped assignee must still clear the Tier A +
    # insurance + RPAS gate, otherwise fall back to auto-assign.
    if preassigned:
        from ..services.commercial_jobs import is_commercial_job, refresh_company_policy
        if is_commercial_job(job_data):
            await refresh_company_policy(db)
            try:
                _pm = await db.table("vendor_members").select("*").eq("id", preassigned).limit(1).execute()
                _prow = (_pm.data or [None])[0]
            except Exception:
                _prow = None
            from ..services.vendor_profile import member_eligible_for_job
            if not _prow or not member_eligible_for_job(_prow, job_data):
                logger.info("Commercial job %s: preassigned %s fails commercial gate; auto-assigning", job_id, preassigned)
                preassigned = ""
                job_data["assignee_id"] = ""
                job_data["status"] = "pending_assignment"
    if not job_data.get("vendor_id") or job_data.get("vendor_id") == job_data.get("assignee_id"):
        job_data["vendor_id"] = "default_vendor"
    # Booked clocks are property-local HH:MM — never raw ISO UTC.
    from ..services.notifications import _clock_hhmm
    from ..shared.job_timezone import stamp_job_timezone
    job_data = stamp_job_timezone(job_data)
    for _k in ("time_window_start", "time_window_end"):
        _norm = _clock_hhmm(job_data.get(_k), job_data)
        if _norm:
            job_data[_k] = _norm
    
    # One-visit shoot checklist (Arrive → modes → Submit). No return visits.
    from ..services.visit_checklist import ensure_visit_checklist
    from ..services.shoot_duration import estimate_shoot_duration_minutes, selected_slot_iso_pair
    job_data["checklist"] = ensure_visit_checklist(job_data)
    if job_data.get("estimated_duration_min") is None:
        job_data["estimated_duration_min"] = estimate_shoot_duration_minutes(job_data)
    # Persist one-visit UTC slot for FreeBusy / conflict / travel (migration 0008).
    if not job_data.get("selected_slot_start") or not job_data.get("selected_slot_end"):
        pair = selected_slot_iso_pair(job_data, job_data.get("estimated_duration_min"))
        if pair:
            job_data["selected_slot_start"], job_data["selected_slot_end"] = pair

    try:
        # jobs.timezone is not a live column yet (DDL lives in Bricks OS).
        # Keep IANA on job_data for the admin when-line; persist province via state.
        insert_data = {k: v for k, v in job_data.items() if k != "timezone"}
        # Soft-drop columns that may not exist yet (duration / slot clocks).
        from ..services.vendor_profile import _missing_column
        res = None
        last_err = None
        for _ in range(6):
            try:
                res = await db.table("jobs").insert(insert_data).execute()
                break
            except Exception as e:
                last_err = e
                miss = _missing_column(str(e), list(insert_data.keys()))
                if not miss:
                    raise
                insert_data.pop(miss, None)
        if res is None or not res.data:
            if last_err:
                raise last_err
            raise HTTPException(status_code=500, detail="Failed to persist job")

        # Assign synchronously before responding. Dispatch API runs on Vercel
        # serverless — FastAPI BackgroundTasks often never run after the
        # response, which left paid /book jobs stuck in pending_assignment
        # with empty assignee_id so they never landed on GO.
        # Assign-first: when checkout already allocated a photographer, keep them
        # (travel fee was billed from their base) and still fire confirm/assign mail.
        #
        # Booking intake only: ask Jev whether to hold for a person before
        # auto-assign. Progress-capture visits, manual assign, and admin
        # reassign do not go through this gate. A Jev failure must still assign.
        assignee_id = None
        status = "pending_assignment"
        held = False
        hold_probability = None
        if preassigned:
            try:
                from ..services.vendor_selection import _update_job_assignee
                await _update_job_assignee(job_id, preassigned, job_doc=job_data)
                assignee_id = preassigned
                status = "assigned"
            except Exception:
                logger.exception("Sync vendor assign failed for job %s", job_id)
        else:
            if job_data.get("booking_id"):
                try:
                    decision = await jev_hold_decision(job_data)
                    held = decision.hold
                    hold_probability = decision.yes_probability
                except Exception:
                    logger.exception("Jev hold gate failed open for job %s", job_id)
                    held = False
            if held:
                # Note or notify failure must not assign.
                status = "held_for_review"
                await _note_jev_hold(db, job_id, hold_probability)
                try:
                    from ..services.vendor_selection import notify_owner_jev_hold
                    await notify_owner_jev_hold(
                        db,
                        {**job_data, "id": job_id},
                        hold_probability,
                    )
                except Exception:
                    logger.exception("Jev hold notify failed for job %s", job_id)
            else:
                try:
                    assignee_id = await assign_vendor_for_job(job_id)
                    status = "assigned" if assignee_id else "dispatch_failed"
                except Exception:
                    logger.exception("Sync vendor assign failed for job %s", job_id)
        # Await notify — Vercel serverless drops FastAPI BackgroundTasks after response
        # (same class of bug as pending_assignment with empty assignee_id).
        try:
            from ..services.notifications import format_when_line, format_package_line
            when = format_when_line(job_data)
            address = (job_data.get("address") or "").strip() or "n/a"
            pkg_name = format_package_line(
                job_data.get("package_name"),
                job_data.get("package_id"),
            )
            pkg_price = job_data.get("package_price")
            package = f"{pkg_name} (${pkg_price})" if pkg_price is not None else pkg_name
            assignee = "held for a person" if held else (assignee_id or "unassigned")
            booking_ref = short_booking_ref(job_data.get("booking_id"))
            await create_notification(
                title="New paid booking",
                body=(
                    f"New job from Booking.\n"
                    f"Address: {address}\n"
                    f"Package: {package}\n"
                    f"When: {when}\n"
                    f"Assignee: {assignee}\n"
                    f"Booking ref: {booking_ref}"
                ),
                ntype="new_booking",
                data={"job_id": job_id, "assignee_id": assignee_id or ""},
                email_to=ADMIN_NOTIFICATION_EMAIL,
                email_subject=paid_booking_admin_subject(job_data.get("address")),
                email_html=_paid_booking_admin_email_html(
                    address=address,
                    package=package,
                    when=when,
                    assignee=assignee,
                    booking_ref=booking_ref,
                ),
            )
        except Exception:
            logger.exception("Admin new-booking notify failed for job %s", job_id)

        # First live (cs_live_) paid booking ever → one-time prompt to Kam to
        # start the Autoenhance plan. Test-mode bookings never count.
        try:
            from ..services.editor_ledger import maybe_prompt_autoenhance_plan
            await maybe_prompt_autoenhance_plan({**job_data, "id": job_id})
        except Exception:
            logger.exception("Autoenhance plan prompt failed for job %s", job_id)

        return {
            "job_id": job_id,
            "status": status,
            "assignee_id": assignee_id or "",
            "created_at": now,
            "checklist": job_data.get("checklist") or [],
        }
    except Exception as e:
        # Fair-Use / PostgREST 402: insert via Postgres already assigned to Owner
        # so paid /book jobs still land on GO. Needs POSTGRES_URL on this project.
        from ..shared.pg_fallback import _is_rest_quota_error, pg_insert_job_assigned_to_owner
        from ..shared.job_scope import KAM_MEMBER_ID as _KAM

        if _is_rest_quota_error(e):
            # Prefer checkout/intake assignee when present; else Owner (Kam).
            if preassigned:
                job_data["assignee_id"] = preassigned
                job_data["status"] = "assigned"
            pg_id = pg_insert_job_assigned_to_owner(job_data)
            fallback_assignee = (job_data.get("assignee_id") or "").strip() or _KAM
            if pg_id:
                logger.warning(
                    "create_dispatch_job used pg fallback id=%s assignee=%s",
                    pg_id,
                    fallback_assignee,
                )
                try:
                    from ..services.notifications import format_when_line, format_package_line
                    when = format_when_line(job_data)
                    address = (job_data.get("address") or "").strip() or "n/a"
                    pkg_name = format_package_line(
                        job_data.get("package_name"),
                        job_data.get("package_id"),
                    )
                    pkg_price = job_data.get("package_price")
                    package = f"{pkg_name} (${pkg_price})" if pkg_price is not None else pkg_name
                    booking_ref = short_booking_ref(job_data.get("booking_id"))
                    await create_notification(
                        title="New paid booking",
                        body=(
                            "New job from Booking (pg fallback).\n"
                            f"Address: {address}\n"
                            f"Package: {package}\n"
                            f"When: {when}\n"
                            f"Assignee: {fallback_assignee}\n"
                            f"Booking ref: {booking_ref}"
                        ),
                        ntype="new_booking",
                        data={"job_id": pg_id, "assignee_id": fallback_assignee},
                        email_to=ADMIN_NOTIFICATION_EMAIL,
                        email_subject=paid_booking_admin_subject(job_data.get("address")),
                        email_html=_paid_booking_admin_email_html(
                            address=address,
                            package=package,
                            when=when,
                            assignee=fallback_assignee,
                            booking_ref=booking_ref,
                        ),
                    )
                except Exception:
                    logger.exception("Admin notify after pg fallback failed for %s", pg_id)
                try:
                    from ..services.editor_ledger import maybe_prompt_autoenhance_plan
                    await maybe_prompt_autoenhance_plan({**job_data, "id": pg_id})
                except Exception:
                    logger.exception("Autoenhance plan prompt failed for job %s", pg_id)
                # Best-effort client/vendor notify for preassigned Fair-Use path
                if preassigned:
                    try:
                        from ..services.client_comms import notify_booking_confirmed
                        await notify_booking_confirmed(pg_id)
                    except Exception:
                        logger.exception("Booking confirmed notify after pg fallback failed for %s", pg_id)
                    try:
                        from ..services.notifications import notify_assignment
                        await notify_assignment({**job_data, "id": pg_id}, preassigned)
                    except Exception:
                        logger.exception("Assignment notify after pg fallback failed for %s", pg_id)
                return {
                    "job_id": pg_id,
                    "status": "assigned",
                    "assignee_id": fallback_assignee,
                    "created_at": now,
                    "checklist": job_data.get("checklist") or [],
                    "note": "pg_fallback_assigned_owner",
                }
            logger.error(
                "create_dispatch_job Fair-Use + pg fallback failed (POSTGRES_URL set?): %s",
                str(e)[:200],
            )
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.put("/jobs/{job_id}/status", summary="Update job status and sync to Booking")
async def update_job_status(
    job_id: str,
    payload: JobStatusUpdate,
    authorization: str = Header(None),
    x_user_id: str = Header(None),
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
):
    """
    Update job status and trigger webhook back to Booking service.

    FIXED 2026-08-16: this endpoint never accepted X-Internal-Api-Key at all,
    so resolve_vendor_actor_id() always fell through to its X-User-Id branch --
    which the 2026-08-15 impersonation fix correctly hardened to also require
    a valid internal key. Since that key was never being passed through here,
    every service-to-service call (Booking notifying Dispatch of a
    cancellation, server.py's booking_cancel) has 401'd since that fix landed.
    Added a proper internal-service branch (same pattern as
    require_vendor_admin) instead of depending on X-User-Id: default_user,
    which is dev seed data, not a guaranteed row in production.
    """
    from ..shared.auth import resolve_vendor_actor_id, get_vendor_member, verify_internal_api_key
    from ..services.webhooks import send_dispatch_status_update_webhook

    db = await get_db()

    if x_internal_api_key is not None:
        if not verify_internal_api_key(x_internal_api_key):
            raise HTTPException(status_code=403, detail="Invalid internal API key")
        actor_id = "internal_service"
        job_query = db.table("jobs").select("*").eq("id", job_id)
    else:
        actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
        actor_member = await get_vendor_member(actor_id)
        vendor_id = actor_member.get("vendor_id", "default_vendor")
        job_query = db.table("jobs").select("*").eq("id", job_id).eq("vendor_id", vendor_id)

    # Fetch job to get current status and booking_id
    try:
        res_job = await job_query.maybe_single().execute()
        job = res_job.data if res_job else None
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        job = pg_select_job_by_id(job_id)
        if job and x_internal_api_key is None:
            # Mirror vendor_id filter used on the REST query for non-internal callers.
            if str(job.get("vendor_id") or "") != str(vendor_id):
                job = None
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    old_status = job.get("status")
    new_status = payload.status
    booking_id = job.get("booking_id")
    dev_capture_visit_id = job.get("dev_capture_visit_id")

    # Delivered is final for the shoot flow: a second "complete" (double tap,
    # retry, Booking echo) must not drag the job back to completed and re-run
    # post-production (E2E BUG 7). Cancel / no-show transitions still pass.
    if old_status == "delivered" and new_status in ("completed", "delivered", "processing"):
        return {"status": "unchanged", "job_id": job_id, "new_status": old_status}

    # Vendors can't complete a trade job here either until its checklist is
    # ticked with proof (same gate as /api/vendor-os/jobs/{id}/status).
    if (
        x_internal_api_key is None
        and new_status in ("completed", "delivered")
        and old_status not in ("completed", "delivered")
    ):
        from ..services.job_checklist import completion_block_message
        from ..shared.errors import api_error
        blocked = completion_block_message(job)
        if blocked:
            api_error(400, blocked, "checklist_incomplete")

    now = datetime.now(timezone.utc).isoformat()
    status_fields = {"status": new_status, "updated_at": now}
    try:
        await db.table("jobs").update(status_fields).eq("id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, status_fields):
            raise HTTPException(
                status_code=503,
                detail="Job status update unavailable (Fair Use); try again shortly",
            )

    # Auto-trigger payout when job is completed or delivered
    if new_status in ("completed", "delivered") and old_status not in ("completed", "delivered"):
        # Probation: hold one of the vendor's first N jobs for admin review (soft-fail).
        try:
            from ..services.vendor_vetting import record_probation_job
            await record_probation_job(db, job)
        except Exception as e:
            logger.warning(f"Probation record failed for job {job_id}: {e}")
        logger.info(f"Job {job_id} moved to {new_status}, triggering payout")
        try:
            from ..services.payout_service import trigger_payout_for_job
            payout_result = await trigger_payout_for_job(db, job_id)
            logger.info(f"Payout result for job {job_id}: {payout_result}")
        except Exception as e:
            logger.error(f"Payout trigger failed for job {job_id}: {e}")

    # Trigger sync to whichever service originated the job — exactly one of
    # these is ever set (create_dispatch_job requires it).
    if booking_id:
        await send_dispatch_status_update_webhook(
            booking_id=booking_id,
            job_id=job_id,
            new_status=new_status,
            previous_status=old_status,
            actor_id=actor_id,
            actor_type="vendor_member"
        )
    elif dev_capture_visit_id:
        from ..services.webhooks import send_dev_capture_status_update_webhook
        await send_dev_capture_status_update_webhook(
            dev_capture_visit_id=dev_capture_visit_id,
            job_id=job_id,
            new_status=new_status,
        )

    return {"status": "updated", "job_id": job_id, "new_status": new_status}
