"""Fair-Use: GO watch-action Accept writes via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.routers import vendor_os as vos
from app.shared import pg_fallback as pf


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_pg_update_vendor_member_fields_writes_decline_rate(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_update_vendor_member_fields(
        "member-1",
        {"decline_rate": 0.25, "updated_at": "2026-09-21T12:00:00+00:00"},
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.vendor_members" in sql
    assert "decline_rate" in sql
    assert "updated_at" in sql
    assert cur.execute.call_args[0][1][-1] == "member-1"
    assert 0.25 in cur.execute.call_args[0][1]


def test_watch_job_action_accept_pg_on_rest_402(monkeypatch):
    """watch Accept updates status via pg_update_job when REST raises 402."""
    job = {
        "id": "job-wa",
        "assignee_id": "photog-1",
        "status": "pending_assignment",
        "booking_id": "bk-1",
        "vendor_id": "default_vendor",
    }
    pg_calls = []

    class Chain:
        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields, **_k: pg_calls.append((jid, fields)) or True,
    )
    monkeypatch.setattr(
        "app.services.webhooks.send_dispatch_status_update_webhook",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(vos, "send_dispatch_status_update_webhook", AsyncMock(return_value=None))

    out = asyncio.run(
        vos.watch_job_action(
            "job-wa",
            vos.WatchActionRequest(action="accept"),
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert pg_calls and pg_calls[0][0] == "job-wa"
    assert pg_calls[0][1]["status"] == "assigned"
    assert "updated_at" in pg_calls[0][1]
    assert out["status"] == "updated"
    assert out["action"] == "accept"
    assert out["new_status"] == "assigned"


def test_watch_job_action_load_uses_load_visible_job(monkeypatch):
    """watch-action loads the job through _load_visible_job (Fair-Use PG path)."""
    job = {
        "id": "job-load",
        "assignee_id": "photog-1",
        "status": "pending_assignment",
        "booking_id": None,
    }
    load_calls = []

    class Chain:
        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            return MagicMock()

    class Db:
        def table(self, name):
            return Chain()

    async def _load(db, job_id, actor_id, actor_member, *, admin_all=False):
        load_calls.append((job_id, actor_id, admin_all))
        return job

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(vos, "_load_visible_job", _load)
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda *_a, **_k: True,
    )

    out = asyncio.run(
        vos.watch_job_action(
            "job-load",
            vos.WatchActionRequest(action="accept"),
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert load_calls == [("job-load", "photog-1", False)]
    assert out["new_status"] == "assigned"


def test_watch_job_action_decline_rate_pg_on_402(monkeypatch):
    """Decline metrics fall back to pg_select_jobs + pg_update_vendor_member_fields."""
    job = {
        "id": "job-dec",
        "assignee_id": "photog-1",
        "status": "assigned",
        "booking_id": None,
    }
    member_pg = []
    jobs_pg = []

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

        def gte(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields, **_k: True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_jobs_for_assignees",
        lambda ids, status=None, limit=50, offset=0: (
            jobs_pg.append(ids)
            or (
                [
                    {
                        "id": "job-dec",
                        "status": "declined",
                        "created_at": "2026-09-20T00:00:00+00:00",
                    },
                    {
                        "id": "job-old",
                        "status": "completed",
                        "created_at": "2026-09-10T00:00:00+00:00",
                    },
                ],
                2,
            )
        ),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_vendor_member_fields",
        lambda mid, fields: member_pg.append((mid, fields)) or True,
    )

    out = asyncio.run(
        vos.watch_job_action(
            "job-dec",
            vos.WatchActionRequest(action="decline"),
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert out["new_status"] == "declined"
    assert jobs_pg == [["photog-1"]]
    assert member_pg and member_pg[0][0] == "photog-1"
    assert "decline_rate" in member_pg[0][1]
    assert member_pg[0][1]["decline_rate"] == 0.5
