"""Fair-Use: GET /api/jobs lists via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.routers import jobs as jobs_mod
from app.shared import pg_fallback as pf


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_list_jobs_pg_on_rest_402(monkeypatch):
    actor = "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580"
    aliases = [actor]
    pg_jobs = [
        {
            "id": "job-1",
            "assignee_id": actor,
            "status": "assigned",
            "scheduled_date": "2026-09-21",
        }
    ]

    class Chain:
        def select(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(
        jobs_mod,
        "resolve_vendor_actor_id",
        AsyncMock(return_value=actor),
    )
    monkeypatch.setattr(
        jobs_mod,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "role": "Owner"}),
    )
    monkeypatch.setattr(
        jobs_mod,
        "photographer_assignee_ids",
        lambda _a, _m: aliases,
    )
    monkeypatch.setattr(jobs_mod, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        pf,
        "pg_select_jobs_for_assignees",
        lambda ids, status=None, limit=50, offset=0: (pg_jobs, 1),
    )

    out = asyncio.get_event_loop().run_until_complete(
        jobs_mod.list_jobs(status=None, authorization="Bearer x", x_user_id=None)
    )
    assert out == pg_jobs
    assert out[0]["id"] == "job-1"


def test_list_jobs_reraises_non_quota(monkeypatch):
    actor = "photog-1"

    class Chain:
        def select(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        async def execute(self):
            raise RuntimeError("connection reset")

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(
        jobs_mod, "resolve_vendor_actor_id", AsyncMock(return_value=actor)
    )
    monkeypatch.setattr(
        jobs_mod, "get_vendor_member", AsyncMock(return_value={"id": actor})
    )
    monkeypatch.setattr(
        jobs_mod, "photographer_assignee_ids", lambda _a, _m: [actor]
    )
    monkeypatch.setattr(jobs_mod, "get_db", AsyncMock(return_value=db))

    try:
        asyncio.get_event_loop().run_until_complete(
            jobs_mod.list_jobs(status=None, authorization="Bearer x", x_user_id=None)
        )
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "connection reset" in str(e)


def test_pg_select_jobs_limit_allows_500(monkeypatch):
    cur = MagicMock()
    cur.fetchone.return_value = [1]
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    # rows
    cur.description = [("id",), ("assignee_id",)]
    cur.fetchall.return_value = [("job-x", "a1")]
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    jobs, total = pf.pg_select_jobs_for_assignees(["a1"], limit=500, offset=0)
    assert total == 1
    # last two params are LIMIT and OFFSET
    params = cur.execute.call_args_list[-1][0][1]
    assert params[-2] == 500
    assert params[-1] == 0
