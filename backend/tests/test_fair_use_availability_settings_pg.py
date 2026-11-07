"""Fair-Use: GET/PUT /api/vendor-os/availability via Postgres on PostgREST 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routers import vendor_os as vos
from app.shared import pg_fallback as pf
from app.shared.schemas import AvailabilityUpdate


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def _mock_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    return conn


def test_pg_select_vendor_availability_sql_and_json(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("vendor_id",),
        ("member_id",),
        ("available",),
        ("work_start",),
        ("work_end",),
        ("jobs_per_day",),
        ("blocked_windows",),
        ("updated_at",),
    ]
    cur.fetchall.return_value = [
        (
            "av-1",
            "default_vendor",
            "photog-1",
            True,
            "09:00",
            "18:00",
            3,
            '[{"start":"12:00","end":"13:00"}]',
            "2026-09-21T00:00:00+00:00",
        ),
    ]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    out = pf.pg_select_vendor_availability("photog-1", "default_vendor")
    assert out is not None
    assert out["member_id"] == "photog-1"
    assert out["work_start"] == "09:00"
    assert isinstance(out["blocked_windows"], list)
    assert out["blocked_windows"][0]["start"] == "12:00"
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "FROM public.vendor_availability" in sql
    assert "member_id = %s" in sql
    assert "vendor_id = %s" in sql
    assert params == ("photog-1", "default_vendor")


def test_pg_select_vendor_availability_missing(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [("id",), ("member_id",)]
    cur.fetchall.return_value = []
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))
    assert pf.pg_select_vendor_availability("nobody") is None
    assert pf.pg_select_vendor_availability("") is None


def test_pg_upsert_vendor_availability_on_conflict_jsonb(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("vendor_id",),
        ("member_id",),
        ("available",),
        ("work_start",),
        ("work_end",),
        ("jobs_per_day",),
        ("blocked_windows",),
        ("updated_at",),
    ]
    cur.fetchall.return_value = [
        (
            "av-upsert",
            "default_vendor",
            "photog-1",
            False,
            "10:00",
            "16:00",
            2,
            "[]",
            "2026-09-21T12:00:00+00:00",
        ),
    ]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    out = pf.pg_upsert_vendor_availability(
        {
            "vendor_id": "default_vendor",
            "member_id": "photog-1",
            "available": False,
            "work_start": "10:00",
            "work_end": "16:00",
            "jobs_per_day": 2,
            "blocked_windows": [],
            "evil_col": "dropme",
        }
    )
    assert out is not None
    assert out["available"] is False
    assert out["work_start"] == "10:00"
    assert isinstance(out["blocked_windows"], list)
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "INSERT INTO public.vendor_availability" in sql
    assert "ON CONFLICT (vendor_id, member_id) DO UPDATE SET" in sql
    assert "evil_col" not in sql
    assert "%s::jsonb" in sql
    assert "available = EXCLUDED.available" in sql
    assert "blocked_windows = EXCLUDED.blocked_windows" in sql
    assert "photog-1" in params
    assert "default_vendor" in params


def test_get_vendor_availability_falls_back_on_402(monkeypatch):
    actor = "photog-avail-1"
    pg_row = {
        "id": "av-1",
        "vendor_id": "default_vendor",
        "member_id": actor,
        "available": True,
        "work_start": "08:00",
        "work_end": "17:00",
        "jobs_per_day": 4,
        "blocked_windows": [],
        "updated_at": "2026-09-21T00:00:00+00:00",
    }

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_availability",
        lambda mid, vid=None: pg_row if mid == actor else None,
    )

    out = asyncio.get_event_loop().run_until_complete(
        vos.get_vendor_availability(authorization="Bearer x", x_user_id=None)
    )
    assert out["member_id"] == actor
    assert out["work_start"] == "08:00"


def test_get_vendor_availability_creates_default_via_pg(monkeypatch):
    actor = "photog-new"
    upsert_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_availability",
        lambda mid, vid=None: None,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_upsert_vendor_availability",
        lambda row: upsert_calls.append(dict(row)) or row,
    )

    out = asyncio.get_event_loop().run_until_complete(
        vos.get_vendor_availability(authorization="Bearer x", x_user_id=None)
    )
    assert out["member_id"] == actor
    assert out["available"] is True
    assert out["work_start"] == "08:00"
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["member_id"] == actor


def test_update_vendor_availability_falls_back_on_402(monkeypatch):
    actor = "photog-avail-2"
    upsert_calls = []

    class Chain:
        def upsert(self, *_a, **_k):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(vos, "write_audit_event", AsyncMock())
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_upsert_vendor_availability",
        lambda row: upsert_calls.append(dict(row))
        or {
            **row,
            "id": "av-put",
            "blocked_windows": row.get("blocked_windows") or [],
        },
    )

    payload = AvailabilityUpdate(
        available=False,
        work_start="11:00",
        work_end="15:00",
        jobs_per_day=1,
        blocked_windows=[],
    )
    out = asyncio.get_event_loop().run_until_complete(
        vos.update_vendor_availability(
            payload, authorization="Bearer x", x_user_id=None
        )
    )
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["member_id"] == actor
    assert upsert_calls[0]["work_start"] == "11:00"
    assert out["work_start"] == "11:00"
    assert out["available"] is False


def test_get_vendor_availability_reraises_non_quota(monkeypatch):
    actor = "photog-1"

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise RuntimeError("connection reset")

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))

    with pytest.raises(RuntimeError, match="connection reset"):
        asyncio.get_event_loop().run_until_complete(
            vos.get_vendor_availability(authorization="Bearer x", x_user_id=None)
        )


def test_update_vendor_availability_reraises_non_quota(monkeypatch):
    actor = "photog-1"

    class Chain:
        def upsert(self, *_a, **_k):
            return self

        async def execute(self):
            raise RuntimeError("boom")

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))

    payload = AvailabilityUpdate(
        available=True,
        work_start="08:00",
        work_end="17:00",
        jobs_per_day=4,
        blocked_windows=[],
    )
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.get_event_loop().run_until_complete(
            vos.update_vendor_availability(
                payload, authorization="Bearer x", x_user_id=None
            )
        )
