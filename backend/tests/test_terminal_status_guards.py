"""Closed jobs (cancelled / agent_no_show / qc_failed + finished) are never reopened.

L1: PUT /jobs/{id}/assign (unassign, auto) and POST /jobs/{id}/contractor-no-show.
L2: watch-action Accept on an already-assigned job still stamps vendor_accepted_at.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.routers import vendor_os as vos
from app.shared.job_scope import (
    KAM_MEMBER_ID,
    ORG_VENDOR_ID,
    NO_REDISPATCH_JOB_STATUSES,
    resolve_manual_assignment,
)

CLOSED = ["cancelled", "agent_no_show", "qc_failed"]

KAM = {
    "id": KAM_MEMBER_ID,
    "vendor_id": KAM_MEMBER_ID,
    "role": "Owner",
    "title": "Owner",
    "is_active": True,
    "onboarding_status": "approved",
}


def _job(status, assignee="ic-1"):
    return {"id": "job-1", "vendor_id": ORG_VENDOR_ID, "assignee_id": assignee, "status": status}


class _Res:
    def __init__(self, data=None):
        self.data = data


class FakeDb:
    """Records every update; jobs updates 'hit' only when the row's status matches."""

    def __init__(self, job, member=None):
        self.job = dict(job)
        self.member = member
        self.updates = []  # (table, fields, filters)

    def table(self, name):
        return _Chain(self, name)


class _Chain:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op = None
        self.fields = None
        self.filters = {}

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def update(self, fields):
        self.op, self.fields = "update", dict(fields)
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def is_(self, k, v):
        self.filters[f"is:{k}"] = v
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        db = self.db
        if self.op == "select":
            if self.name == "jobs":
                return _Res(dict(db.job))
            return _Res(dict(db.member) if db.member else None)
        db.updates.append((self.name, self.fields, dict(self.filters)))
        if self.name == "jobs":
            status = self.filters.get("status")
            if status is not None and status != db.job.get("status"):
                return _Res([])
            db.job.update(self.fields)
            return _Res([dict(db.job)])
        return _Res([{"id": "x"}])


# ---------------------------------------------------------------- L1: resolve_manual_assignment


@pytest.mark.parametrize("status", CLOSED)
def test_resolve_unassign_and_auto_blocked_on_closed_jobs(status):
    for member_id in ("unassign", "auto", ""):
        code, err, patch = resolve_manual_assignment(
            actor_id=KAM_MEMBER_ID, actor_member=KAM, job=_job(status),
            member_id=member_id or None, target_member=None,
        )
        assert (code, err, patch) == (409, "job_finished", {}), (status, member_id)


@pytest.mark.parametrize("status", sorted(NO_REDISPATCH_JOB_STATUSES))
def test_closed_job_without_assignee_cannot_be_flipped_to_assigned(status):
    target = {"id": "ic-2", "vendor_id": ORG_VENDOR_ID, "role": "Photographer IC",
              "is_active": True, "onboarding_status": "approved"}
    code, err, _ = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID, actor_member=KAM, job=_job(status, assignee=""),
        member_id="ic-2", target_member=target,
    )
    assert (code, err) == (409, "job_finished")


def test_open_job_unassign_and_auto_still_allowed():
    for status in ("assigned", "pending_assignment", "dispatch_failed", "in_progress"):
        code, err, patch = resolve_manual_assignment(
            actor_id=KAM_MEMBER_ID, actor_member=KAM, job=_job(status),
            member_id="unassign", target_member=None,
        )
        assert (code, err) == (200, "ok") and patch["status"] == "pending_assignment"
        code, err, _ = resolve_manual_assignment(
            actor_id=KAM_MEMBER_ID, actor_member=KAM, job=_job(status),
            member_id="auto", target_member=None,
        )
        assert (code, err) == (200, "auto")


# ---------------------------------------------------------------- L1: assign endpoint


@pytest.mark.parametrize("status", CLOSED)
@pytest.mark.parametrize("member_id", ["unassign", "auto"])
def test_assign_endpoint_never_resets_or_redispatches_closed_job(monkeypatch, status, member_id):
    db = FakeDb(_job(status))
    assign = AsyncMock(return_value="ic-9")
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=KAM))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(vos, "assign_vendor_for_job", assign)
    monkeypatch.setattr("app.services.commercial_jobs.refresh_company_policy", AsyncMock())

    with pytest.raises(HTTPException) as ei:
        asyncio.run(vos.assign_job_to_member(
            "job-1", vos.JobAssignmentUpdate(member_id=member_id),
            authorization="Bearer x", x_user_id=None,
        ))
    assert ei.value.status_code == 409
    assert db.updates == []
    assign.assert_not_awaited()
    assert db.job["status"] == status


# ---------------------------------------------------------------- L1: contractor no-show


def _patch_no_show(monkeypatch, db, redispatch=None):
    redispatch = redispatch or AsyncMock(return_value="ic-9")
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value={**KAM, "vendor_id": ORG_VENDOR_ID}))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(vos, "_safe_write_audit_event", AsyncMock())
    monkeypatch.setattr("app.services.vendor_selection.assign_vendor_for_job", redispatch)
    return redispatch


def _call_no_show():
    return asyncio.run(vos.report_contractor_no_show("job-1", authorization="Bearer x", x_user_id=None))


@pytest.mark.parametrize("status", CLOSED)
def test_contractor_no_show_blocked_on_closed_job_no_penalty(monkeypatch, status):
    db = FakeDb(_job(status), member={"total_assignments": 4, "cancel_rate": 0.25})
    redispatch = _patch_no_show(monkeypatch, db)
    with pytest.raises(HTTPException) as ei:
        _call_no_show()
    assert ei.value.status_code == 409
    assert db.updates == []  # no job reset, no vendor_members penalty
    redispatch.assert_not_awaited()
    assert db.job["status"] == status


def test_contractor_no_show_no_penalty_when_guarded_reset_changes_nothing(monkeypatch):
    """Row moved to 'cancelled' after the read: the status-guarded UPDATE hits 0 rows."""
    db = FakeDb(_job("assigned"), member={"total_assignments": 4, "cancel_rate": 0.25})
    redispatch = _patch_no_show(monkeypatch, db)
    real_select = _Chain.execute

    async def racing_execute(self):
        res = await real_select(self)
        if self.op == "select" and self.name == "jobs":
            self.db.job["status"] = "cancelled"  # cancelled right after the vendor read
        return res

    monkeypatch.setattr(_Chain, "execute", racing_execute)
    with pytest.raises(HTTPException) as ei:
        _call_no_show()
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "job_status_changed"
    assert [t for t, *_ in db.updates] == ["jobs"]  # the guarded attempt only
    assert not any(t == "vendor_members" for t, *_ in db.updates)
    redispatch.assert_not_awaited()
    assert db.job["status"] == "cancelled"


def test_contractor_no_show_penalty_only_after_successful_guarded_reset(monkeypatch):
    db = FakeDb(_job("assigned"), member={"total_assignments": 4, "cancel_rate": 0.25})
    redispatch = _patch_no_show(monkeypatch, db)
    out = _call_no_show()
    tables = [t for t, *_ in db.updates]
    assert tables == ["jobs", "vendor_members"]  # reset first, then penalty
    _, fields, filters = db.updates[0]
    assert filters["status"] == "assigned" and fields["status"] == "pending_assignment"
    _, penalty, _ = db.updates[1]
    assert penalty["total_assignments"] == 5 and penalty["cancel_rate"] == 0.4
    redispatch.assert_awaited_once()
    assert out["redispatched_to"] == "ic-9"


# ---------------------------------------------------------------- L2: watch-action accept


def _patch_watch(monkeypatch, db, job):
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value={"id": "photog-1", "vendor_id": ORG_VENDOR_ID}))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(vos, "send_dispatch_status_update_webhook", AsyncMock(return_value=None))


def _accept():
    return asyncio.run(vos.watch_job_action(
        "job-1", vos.WatchActionRequest(action="accept"), authorization="Bearer x", x_user_id=None,
    ))


def test_accept_on_auto_assigned_job_sets_vendor_accepted_at(monkeypatch):
    job = {**_job("assigned", assignee="photog-1"), "vendor_accepted_at": None}
    db = FakeDb(job)
    _patch_watch(monkeypatch, db, job)
    out = _accept()
    assert out["status"] == "no_change" and out["current_status"] == "assigned"
    assert len(db.updates) == 1
    table, fields, filters = db.updates[0]
    assert table == "jobs" and set(fields) == {"vendor_accepted_at"} and fields["vendor_accepted_at"]
    assert filters["is:vendor_accepted_at"] == "null"  # only when still null
    assert db.job["status"] == "assigned"  # status untouched


def test_accept_on_assigned_job_keeps_earlier_acceptance(monkeypatch):
    job = {**_job("assigned", assignee="photog-1"), "vendor_accepted_at": "2026-11-01T12:00:00+00:00"}
    db = FakeDb(job)
    _patch_watch(monkeypatch, db, job)
    out = _accept()
    assert out["status"] == "no_change"
    assert db.updates == []


def test_accept_from_pending_assignment_still_changes_status_and_stamps(monkeypatch):
    job = {**_job("pending_assignment", assignee="photog-1"), "vendor_accepted_at": None}
    db = FakeDb(job)
    _patch_watch(monkeypatch, db, job)
    out = _accept()
    assert out["status"] == "updated" and out["new_status"] == "assigned"
    assert db.job["status"] == "assigned"
    assert any(t == "jobs" and set(f) == {"vendor_accepted_at"} for t, f, _ in db.updates)
