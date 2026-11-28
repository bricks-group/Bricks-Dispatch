"""Closed jobs (cancelled / agent_no_show / qc_failed + finished) are never reopened.

L1: PUT /jobs/{id}/assign (unassign, auto, specific-member) and POST /jobs/{id}/contractor-no-show.
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


TARGET = {
    "id": "ic-2",
    "vendor_id": ORG_VENDOR_ID,
    "role": "Photographer IC",
    "title": "Photographer IC",
    "email": "ic2@brxs.ca",
    "is_active": True,
    "onboarding_status": "approved",
}

ALL_CLOSED = sorted(NO_REDISPATCH_JOB_STATUSES)
ASSIGN_VERBS = ("unassign", "auto", "ic-2")


# ---------------------------------------------------------------- Must: specific-member assign on a closed job


@pytest.mark.parametrize("status", ALL_CLOSED)
@pytest.mark.parametrize("member_id", ASSIGN_VERBS)
def test_resolve_blocks_every_verb_on_closed_job_that_already_has_an_assignee(status, member_id):
    code, err, patch = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID, actor_member=KAM, job=_job(status, assignee="ic-1"),
        member_id=member_id, target_member=TARGET,
    )
    assert (code, err, patch) == (409, "job_finished", {}), (status, member_id)


def _patch_assign(monkeypatch, db, job=None):
    notify = AsyncMock()
    confirmed = AsyncMock()
    callback = MagicMock()
    auto_assign = AsyncMock(return_value="ic-9")
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=KAM))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(vos, "assign_vendor_for_job", auto_assign)
    monkeypatch.setattr(vos, "_safe_write_audit_event", AsyncMock())
    monkeypatch.setattr("app.services.commercial_jobs.refresh_company_policy", AsyncMock())
    monkeypatch.setattr("app.services.notifications.notify_assignment", notify)
    monkeypatch.setattr("app.services.client_comms.notify_booking_confirmed", confirmed)
    monkeypatch.setenv("BOOKING_CALLBACK_URL", "https://book.example")

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            callback(*a, **k)
            return MagicMock()

    # conftest stubs httpx as a MagicMock (no real AsyncClient attr)
    monkeypatch.setattr(vos, "httpx", type("Httpx", (), {"AsyncClient": staticmethod(lambda *a, **k: _Resp())}))
    return notify, confirmed, callback, auto_assign


@pytest.mark.parametrize("status", ALL_CLOSED)
@pytest.mark.parametrize("member_id", ASSIGN_VERBS)
def test_assign_endpoint_closed_job_with_assignee_never_writes_or_notifies(
    monkeypatch, status, member_id,
):
    """A photographer must not be sent to a cancelled / finished booking, and the
    client must not get a confirmation. Covers unassign, auto, and a named member.
    """
    db = FakeDb(_job(status, assignee="ic-1"), member=TARGET)
    notify, confirmed, callback, auto_assign = _patch_assign(monkeypatch, db)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(vos.assign_job_to_member(
            "job-1", vos.JobAssignmentUpdate(member_id=member_id),
            authorization="Bearer x", x_user_id=None,
        ))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "job_finished"
    assert db.updates == []
    assert db.job["assignee_id"] == "ic-1"
    assert db.job["status"] == status
    notify.assert_not_awaited()
    confirmed.assert_not_awaited()
    callback.assert_not_called()
    auto_assign.assert_not_awaited()


def test_open_job_specific_member_assign_still_writes_and_notifies(monkeypatch):
    """Guard: the closed-job block must not reject a live assigned job."""
    db = FakeDb(_job("assigned", assignee="ic-1"), member=TARGET)
    notify, confirmed, callback, auto_assign = _patch_assign(monkeypatch, db)
    out = asyncio.run(vos.assign_job_to_member(
        "job-1", vos.JobAssignmentUpdate(member_id="ic-2"),
        authorization="Bearer x", x_user_id=None,
    ))
    assert any(t == "jobs" and f.get("assignee_id") == "ic-2" for t, f, _ in db.updates)
    notify.assert_awaited()
    confirmed.assert_awaited()
    auto_assign.assert_not_awaited()
    assert out["assignee_id"] == "ic-2"
    assert db.job["status"] == "assigned"


# ---------------------------------------------------------------- L2 follow-ups: 403 and Fair-Use PG stamp


def test_accept_by_non_assignee_returns_403_and_writes_nothing(monkeypatch):
    job = {**_job("assigned", assignee="photog-1"), "vendor_accepted_at": None}
    db = FakeDb(job)
    _patch_watch(monkeypatch, db, job)
    # Actor is still photog-1 from _patch_watch; the job is assigned to someone else.
    job["assignee_id"] = "photog-OTHER"
    with pytest.raises(HTTPException) as ei:
        _accept()
    assert ei.value.status_code == 403
    assert db.updates == []
    assert job.get("vendor_accepted_at") is None
    assert db.job["status"] == "assigned"


def test_pg_update_job_stamps_vendor_accepted_at_only_when_null(monkeypatch):
    from app.shared import pg_fallback as pf

    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_update_job(
        "job-1",
        {"vendor_accepted_at": "2026-11-28T10:00:00+00:00"},
        only_if_null="vendor_accepted_at",
    )
    assert ok is True
    sql, params = cur.execute.call_args[0]
    assert "UPDATE public.jobs" in sql
    assert "vendor_accepted_at = %s" in sql
    assert "vendor_accepted_at IS NULL" in sql
    assert "2026-11-28T10:00:00+00:00" in params
    assert params[-1] == "job-1"


def test_pg_update_job_rejects_unknown_only_if_null_column(monkeypatch):
    from app.shared import pg_fallback as pf

    calls = []
    monkeypatch.setattr(pf, "_pg_connect", lambda: calls.append(1) or None)
    assert pf.pg_update_job(
        "job-1",
        {"vendor_accepted_at": "2026-11-28T10:00:00+00:00"},
        only_if_null="not_a_real_column",
    ) is False
    assert calls == []


def test_record_vendor_accepted_falls_back_to_pg_on_rest_402(monkeypatch):
    class _QuotaErr(Exception):
        def __str__(self):
            return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"

    class Chain:
        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def is_(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    pg_calls = []
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields, **k: pg_calls.append((jid, fields, k)) or True,
    )
    job = {**_job("assigned", assignee="photog-1"), "vendor_accepted_at": None}
    asyncio.run(vos._record_vendor_accepted(Db(), job, "2026-11-28T10:00:00+00:00"))
    assert pg_calls == [
        ("job-1", {"vendor_accepted_at": "2026-11-28T10:00:00+00:00"}, {"only_if_null": "vendor_accepted_at"})
    ]
