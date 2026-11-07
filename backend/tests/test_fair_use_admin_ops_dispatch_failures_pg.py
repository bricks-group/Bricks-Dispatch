"""Fair-Use: admin dispatch-failures list + reassign via Postgres on PostgREST 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.routers import admin_ops as ao
from app.shared import pg_fallback as pf
from app.shared.job_scope import KAM_MEMBER_ID


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_pg_select_dispatch_failed_jobs_sql(monkeypatch):
    cur = MagicMock()
    cur.fetchall.return_value = [
        (
            "job-f1", "bk-1", "1 Main", "Toronto", None, "Essentials", None,
            None, None, None, None, None, "dispatch_failed", None, "",
        ),
    ]
    cur.description = [
        ("id",), ("booking_id",), ("address",), ("city",), ("job_type",),
        ("package_name",), ("min_tier_required",), ("scheduled_date",),
        ("time_window_start",), ("time_window_end",), ("created_at",),
        ("updated_at",), ("status",), ("dispatch_explanation",), ("assignee_id",),
    ]
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    rows = pf.pg_select_dispatch_failed_jobs(limit=10, offset=2)
    assert len(rows) == 1
    assert rows[0]["id"] == "job-f1"
    assert rows[0]["status"] == "dispatch_failed"
    sql = cur.execute.call_args[0][0]
    assert "dispatch_failed" in sql
    assert "held_for_review" in sql
    assert "Held for a person" in cur.execute.call_args[0][1][0]
    assert cur.execute.call_args[0][1][1:] == (10, 2)


def test_pg_select_ops_escalations_for_jobs(monkeypatch):
    cur = MagicMock()
    cur.fetchall.return_value = [
        ("job-f1", 2, "no vendor", "open", "2026-09-21T12:00:00+00:00"),
    ]
    cur.description = [
        ("job_id",), ("attempt_count",), ("error_message",), ("status",), ("created_at",),
    ]
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    rows = pf.pg_select_ops_escalations_for_jobs(["job-f1", "job-f2"])
    assert rows[0]["job_id"] == "job-f1"
    assert rows[0]["status"] == "open"
    assert cur.execute.call_args[0][1][0] == ["job-f1", "job-f2"]


def test_pg_mark_ops_escalations_manual_retry(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    n = pf.pg_mark_ops_escalations_manual_retry("job-f1")
    assert n == 1
    sql = cur.execute.call_args[0][0]
    assert "ops_escalations" in sql
    assert cur.execute.call_args[0][1] == ("manual_retry", "job-f1", "open")


def test_list_dispatch_failures_pg_on_402(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def range(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    jobs = [
        {
            "id": "job-fail",
            "booking_id": "bk-1",
            "address": "9 Queen",
            "city": "Mississauga",
            "status": "dispatch_failed",
        }
    ]
    esc = [
        {
            "job_id": "job-fail",
            "attempt_count": 3,
            "error_message": "no vendor",
            "status": "open",
            "created_at": "2026-09-21T12:00:00+00:00",
        }
    ]

    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(
        ao,
        "get_vendor_member",
        AsyncMock(return_value={"id": KAM_MEMBER_ID, "role": "owner", "title": "Owner"}),
    )
    monkeypatch.setattr(ao, "is_vendor_admin", lambda _m: True)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_dispatch_failed_jobs",
        lambda limit=50, offset=0: jobs,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_ops_escalations_for_jobs",
        lambda ids: esc if "job-fail" in ids else [],
    )

    out = asyncio.run(ao.list_dispatch_failures(limit=20, offset=0))
    assert out["total"] == 1
    assert out["items"][0]["id"] == "job-fail"
    assert out["items"][0]["escalation"]["attempt_count"] == 3


def test_manual_reassign_pg_on_402(monkeypatch):
    """Owner reassign: REST job select/update + escalation update 402 → PG."""
    job = {"id": "job-retry", "status": "dispatch_failed"}
    pg_job_calls = []
    pg_update_calls = []
    pg_esc_calls = []
    assign_calls = []

    class Chain:
        def __init__(self):
            self._op = None

        def select(self, *_a, **_k):
            self._op = "select"
            return self

        def update(self, *_a, **_k):
            self._op = "update"
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(
        ao,
        "get_vendor_member",
        AsyncMock(return_value={"id": KAM_MEMBER_ID, "role": "owner", "title": "Owner"}),
    )
    monkeypatch.setattr(ao, "is_vendor_admin", lambda _m: True)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: pg_job_calls.append(jid) or (job if jid == "job-retry" else None),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_update_calls.append((jid, fields)) or True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_mark_ops_escalations_manual_retry",
        lambda jid: pg_esc_calls.append(jid) or 1,
    )

    async def _assign(jid):
        assign_calls.append(jid)
        return KAM_MEMBER_ID

    monkeypatch.setattr(ao, "assign_vendor_for_job", _assign)

    out = asyncio.run(ao.manual_reassign_dispatch_failure("job-retry"))
    assert out["assigned"] is True
    assert out["assigned_member_id"] == KAM_MEMBER_ID
    assert pg_job_calls == ["job-retry"]
    assert pg_update_calls and pg_update_calls[0][0] == "job-retry"
    assert pg_update_calls[0][1]["status"] == "pending_assignment"
    assert pg_update_calls[0][1]["assignee_id"] == ""
    assert pg_esc_calls == ["job-retry"]
    assert assign_calls == ["job-retry"]


def test_manual_reassign_409_when_not_failed(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            m = MagicMock()
            m.data = {"id": "job-ok", "status": "assigned"}
            return m

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(
        ao,
        "get_vendor_member",
        AsyncMock(return_value={"id": KAM_MEMBER_ID, "role": "owner"}),
    )
    monkeypatch.setattr(ao, "is_vendor_admin", lambda _m: True)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=Db()))

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(ao.manual_reassign_dispatch_failure("job-ok"))
    assert ei.value.status_code == 409


def test_list_dispatch_failures_includes_held_for_review(monkeypatch):
    """REST path: held_for_review (+ legacy hold note) land next to dispatch_failed."""
    rows = [
        {
            "id": "job-hold",
            "booking_id": "bk-h",
            "address": "12 King",
            "city": "Toronto",
            "status": "held_for_review",
            "dispatch_explanation": "Held for a person before auto-assign (Jev yes-probability 0.91).",
            "assignee_id": "",
            "updated_at": "2026-10-05T14:00:00+00:00",
        },
        {
            "id": "job-fail",
            "booking_id": "bk-f",
            "address": "9 Queen",
            "city": "Mississauga",
            "status": "dispatch_failed",
            "dispatch_explanation": None,
            "assignee_id": "",
            "updated_at": "2026-10-05T13:00:00+00:00",
        },
        {
            "id": "job-plain",
            "status": "pending_assignment",
            "dispatch_explanation": None,
            "assignee_id": "",
            "updated_at": "2026-10-05T12:00:00+00:00",
        },
        {
            "id": "job-legacy-hold",
            "status": "pending_assignment",
            "dispatch_explanation": "Held for a person before auto-assign (Jev yes-probability 0.88).",
            "assignee_id": "",
            "updated_at": "2026-10-05T12:30:00+00:00",
        },
    ]
    state = {"calls": 0}

    class Chain:
        def select(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def range(self, *_a, **_k):
            return self

        async def execute(self):
            state["calls"] += 1
            m = MagicMock()
            m.data = rows if state["calls"] == 1 else []
            return m

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(
        ao,
        "get_vendor_member",
        AsyncMock(return_value={"id": KAM_MEMBER_ID, "role": "owner", "title": "Owner"}),
    )
    monkeypatch.setattr(ao, "is_vendor_admin", lambda _m: True)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=Db()))

    out = asyncio.run(ao.list_dispatch_failures(limit=20, offset=0))
    ids = [i["id"] for i in out["items"]]
    assert ids[0] == "job-hold"
    assert "job-fail" in ids
    assert "job-legacy-hold" in ids
    assert "job-plain" not in ids
    by_id = {i["id"]: i for i in out["items"]}
    assert by_id["job-hold"]["queue_kind"] == "held_for_review"
    assert by_id["job-fail"]["queue_kind"] == "dispatch_failed"
    assert by_id["job-legacy-hold"]["queue_kind"] == "held_for_review"


def test_release_held_job_runs_assign(monkeypatch):
    job = {
        "id": "job-hold",
        "status": "held_for_review",
        "dispatch_explanation": "Held for a person before auto-assign (Jev yes-probability 0.91).",
        "assignee_id": "",
    }
    updates = []
    assign_calls = []

    class Chain:
        def __init__(self):
            self._op = None

        def select(self, *_a, **_k):
            self._op = "select"
            return self

        def update(self, payload):
            self._op = "update"
            updates.append(payload)
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            m = MagicMock()
            m.data = dict(job) if self._op == "select" else (updates[-1:] if updates else [])
            return m

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(
        ao,
        "get_vendor_member",
        AsyncMock(return_value={"id": KAM_MEMBER_ID, "role": "owner"}),
    )
    monkeypatch.setattr(ao, "is_vendor_admin", lambda _m: True)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=Db()))

    async def _assign(jid):
        assign_calls.append(jid)
        return KAM_MEMBER_ID

    monkeypatch.setattr(ao, "assign_vendor_for_job", _assign)

    out = asyncio.run(ao.release_held_dispatch_job("job-hold"))
    assert out["released"] is True
    assert out["assigned"] is True
    assert out["assigned_member_id"] == KAM_MEMBER_ID
    assert assign_calls == ["job-hold"]
    assert updates and updates[0]["status"] == "pending_assignment"
    assert updates[0]["assignee_id"] == ""
    assert "Released from Jev hold" in updates[0]["dispatch_explanation"]


def test_release_409_on_dispatch_failed(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            m = MagicMock()
            m.data = {"id": "job-fail", "status": "dispatch_failed", "dispatch_explanation": None}
            return m

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(
        ao,
        "get_vendor_member",
        AsyncMock(return_value={"id": KAM_MEMBER_ID, "role": "owner"}),
    )
    monkeypatch.setattr(ao, "is_vendor_admin", lambda _m: True)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=Db()))

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(ao.release_held_dispatch_job("job-fail"))
    assert ei.value.status_code == 409


def test_release_legacy_pending_hold_note(monkeypatch):
    """Pre-held_for_review rows (pending_assignment + note) still release."""
    job = {
        "id": "job-legacy",
        "status": "pending_assignment",
        "dispatch_explanation": "Held for a person before auto-assign (Jev yes-probability 0.85).",
        "assignee_id": "",
    }
    updates = []

    class Chain:
        def __init__(self):
            self._op = None

        def select(self, *_a, **_k):
            self._op = "select"
            return self

        def update(self, payload):
            self._op = "update"
            updates.append(payload)
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            m = MagicMock()
            m.data = dict(job) if self._op == "select" else updates[-1:]
            return m

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(
        ao,
        "get_vendor_member",
        AsyncMock(return_value={"id": KAM_MEMBER_ID, "role": "owner"}),
    )
    monkeypatch.setattr(ao, "is_vendor_admin", lambda _m: True)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(ao, "assign_vendor_for_job", AsyncMock(return_value=None))

    out = asyncio.run(ao.release_held_dispatch_job("job-legacy"))
    assert out["released"] is True
    assert out["assigned"] is False
    assert updates and updates[0]["status"] == "pending_assignment"
