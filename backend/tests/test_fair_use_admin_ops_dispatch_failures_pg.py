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
        ("job-f1", "bk-1", "1 Main", "Toronto", None, "Essentials", None, None, None, None, None, None, "dispatch_failed"),
    ]
    cur.description = [
        ("id",), ("booking_id",), ("address",), ("city",), ("job_type",),
        ("package_name",), ("min_tier_required",), ("scheduled_date",),
        ("time_window_start",), ("time_window_end",), ("created_at",),
        ("updated_at",), ("status",),
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
    assert "dispatch_failed" in sql or cur.execute.call_args[0][1][0] == "dispatch_failed"
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
