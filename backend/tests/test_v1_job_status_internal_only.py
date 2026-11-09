"""Hotfix 2026-11-09 (Sentinel cx-roles round 2, HIGH).

PUT /api/v1/dispatch/jobs/{id}/status is for internal services only (Companion
sends X-Internal-Api-Key when a booking is cancelled). A contractor's sign-in
can no longer use it to start, accept, complete or cancel any job. Contractors
keep using /api/vendor-os/jobs/{id}/status, which checks the assignee and the
allowed statuses, and completion there still runs the delivery-and-payout path.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import dispatch as dispatch_router
from app.routers import vendor_os as vos
from app.shared.schemas import JobStatusUpdate
from tests.test_trade_checklist_gate import _Db, _run

JOB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OWES_TERMS = {"id": "member-owes", "vendor_id": "default_vendor", "role": "photographer", "status": "active",
              "ic_agreement_accepted_at": None, "ic_agreement_version": None}
CLEARED = {"id": "member-ok", "vendor_id": "default_vendor", "role": "photographer", "status": "active",
           "ic_agreement_accepted_at": "2026-10-01T00:00:00+00:00", "ic_agreement_version": "IC-A1"}


def _job(**o):
    j = {"id": JOB, "assignee_id": "someone-else", "vendor_id": "default_vendor", "booking_id": "bk-1",
         "status": "assigned", "job_type": "photo", "package_name": "Essentials", "checklist": [],
         "deliverables_count": 0, "uploaded_count": 0}
    j.update(o)
    return j


@pytest.fixture
def v1(monkeypatch):
    def install(job, member, key_ok=True):
        db = _Db(job)
        monkeypatch.setattr(dispatch_router, "get_db", AsyncMock(return_value=db))
        import app.shared.auth as auth
        monkeypatch.setattr(auth, "resolve_vendor_actor_id", AsyncMock(return_value=member["id"]))
        monkeypatch.setattr(auth, "get_vendor_member", AsyncMock(return_value=member))
        monkeypatch.setattr(auth, "verify_internal_api_key", lambda k: bool(key_ok) and k == "internal-ok")
        import app.services.webhooks as wh
        hook = AsyncMock(return_value=None)
        monkeypatch.setattr(wh, "send_dispatch_status_update_webhook", hook, raising=False)
        import app.services.payout_service as ps
        paid = AsyncMock(return_value={"ok": True})
        monkeypatch.setattr(ps, "trigger_payout_for_job", paid)
        import app.services.vendor_vetting as vv
        monkeypatch.setattr(vv, "record_probation_job", AsyncMock(return_value=None))
        return db, paid, hook
    return install


def _refused(e, db, paid, hook, status):
    assert e.value.status_code == 403
    assert db.updates == [] and db.tables["jobs"][0]["status"] == status
    assert paid.await_count == 0 and hook.await_count == 0


@pytest.mark.parametrize("target", ["completed", "delivered", "cancelled", "declined", "agent_no_show"])
def test_non_assignee_contractor_cannot_complete_or_cancel(v1, target):
    db, paid, hook = v1(_job(status="in_progress", assignee_id="other-member"), CLEARED)
    with pytest.raises(HTTPException) as e:
        _run(dispatch_router.update_job_status(JOB, JobStatusUpdate(status=target), "Bearer t", None, None))
    _refused(e, db, paid, hook, "in_progress")


@pytest.mark.parametrize("target", ["completed", "cancelled"])
def test_even_the_assignee_cannot_use_the_internal_route(v1, target):
    db, paid, hook = v1(_job(status="in_progress", assignee_id=CLEARED["id"]), CLEARED)
    with pytest.raises(HTTPException) as e:
        _run(dispatch_router.update_job_status(JOB, JobStatusUpdate(status=target), "Bearer t", None, None))
    _refused(e, db, paid, hook, "in_progress")


@pytest.mark.parametrize("old,target", [("pending_assignment", "assigned"), ("assigned", "in_progress")])
def test_contractor_owing_terms_cannot_accept_or_start(v1, old, target):
    db, paid, hook = v1(_job(status=old, assignee_id=OWES_TERMS["id"]), OWES_TERMS)
    with pytest.raises(HTTPException) as e:
        _run(dispatch_router.update_job_status(JOB, JobStatusUpdate(status=target), "Bearer t", "member-owes", None))
    _refused(e, db, paid, hook, old)


def test_wrong_or_empty_internal_key_refused(v1):
    for key in ("", "not-the-key"):
        db, paid, hook = v1(_job(status="in_progress"), CLEARED)
        with pytest.raises(HTTPException) as e:
            _run(dispatch_router.update_job_status(JOB, JobStatusUpdate(status="completed"), None, None, key))
        _refused(e, db, paid, hook, "in_progress")


def test_internal_service_completion_still_pays_out_once_and_syncs(v1):
    db, paid, hook = v1(_job(status="in_progress"), CLEARED)
    out = _run(dispatch_router.update_job_status(JOB, JobStatusUpdate(status="completed"), None, None, "internal-ok"))
    assert out["new_status"] == "completed"
    assert db.tables["jobs"][0]["status"] == "completed"
    assert paid.await_count == 1 and hook.await_count == 1


def test_internal_service_cancellation_still_works(v1):
    db, paid, hook = v1(_job(status="assigned"), CLEARED)
    _run(dispatch_router.update_job_status(JOB, JobStatusUpdate(status="cancelled"), None, None, "internal-ok"))
    assert db.tables["jobs"][0]["status"] == "cancelled" and paid.await_count == 0 and hook.await_count == 1


# The contractor route: real assignee check, completion runs post-production
# (which finalizes delivery and triggers the payout).
def _install_vos(monkeypatch, member, job):
    db = _Db(job)
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=member["id"]))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=member))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    import app.services.post_production as pp
    post = AsyncMock(return_value=None)
    monkeypatch.setattr(pp, "process_job_assets", post)
    import app.services.vendor_vetting as vv
    monkeypatch.setattr(vv, "record_probation_job", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.bracket_require.validate_hdr_uploads_for_complete", lambda rows: None)
    return db, post


def test_vendor_route_assignee_completion_runs_delivery_and_payout_path(monkeypatch):
    job = _job(status="in_progress", assignee_id=CLEARED["id"], booking_id=None)
    db, post = _install_vos(monkeypatch, CLEARED, job)
    _run(vos.update_vendor_job_status(JOB, JobStatusUpdate(status="completed"), authorization="Bearer x", x_user_id=None))
    assert db.tables["jobs"][0]["status"] == "completed" and post.await_count == 1


@pytest.mark.parametrize("target", ["completed", "cancelled"])
def test_vendor_route_non_assignee_refused(monkeypatch, target):
    job = _job(status="in_progress", assignee_id="other-member", booking_id=None)
    db, post = _install_vos(monkeypatch, CLEARED, job)
    with pytest.raises(HTTPException) as e:
        _run(vos.update_vendor_job_status(JOB, JobStatusUpdate(status=target), authorization="Bearer x", x_user_id=None))
    assert e.value.status_code in (400, 403) and db.updates == [] and post.await_count == 0
