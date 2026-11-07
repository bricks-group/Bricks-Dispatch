"""Null maybe_single() must not 500 GET/PUT /api/vendor-os/availability.

postgrest maybe_single().execute() returns None when there is no row. A new
GO photographer has no vendor_availability row yet, so the GET read .data on
None and raised AttributeError (CORS-less HTTP 500) before the default-create
branch could run. The PUT re-read had the same crash after a successful save.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers import vendor_os as vos
from app.shared import pg_fallback as pg
from app.shared.schemas import AvailabilityUpdate


ACTOR = "11111111-2222-3333-4444-555555555555"
MEMBER = {"id": ACTOR, "name": "New GO", "role": "Photographer", "vendor_id": "default_vendor"}


class _Chain:
    def __init__(self, db, result):
        self._db = db
        self._result = result

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def insert(self, row):
        self._db.inserted.append(row)
        self._result = SimpleNamespace(data=[row])
        return self

    def upsert(self, row):
        self._db.upserted.append(row)
        self._result = SimpleNamespace(data=[row])
        return self

    async def execute(self):
        return self._result


class _Db:
    def __init__(self, read_result):
        self._read_result = read_result
        self.inserted: list[dict] = []
        self.upserted: list[dict] = []

    def table(self, name):
        assert name == "vendor_availability", name
        return _Chain(self, self._read_result)


def _run(coro):
    # Keep a current loop set: sibling Fair-Use tests use get_event_loop().
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _patch(monkeypatch, db, pg_row=None):
    async def fake_get_db():
        return db

    monkeypatch.setattr(vos, "get_db", fake_get_db)
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=dict(MEMBER)))
    monkeypatch.setattr(vos, "write_audit_event", AsyncMock(return_value=None))
    monkeypatch.setattr(pg, "pg_select_vendor_availability", lambda *a, **k: pg_row)
    monkeypatch.setattr(pg, "pg_upsert_vendor_availability", lambda row: row)


def test_get_availability_null_maybe_single_creates_default(monkeypatch):
    db = _Db(None)
    _patch(monkeypatch, db)

    out = _run(vos.get_vendor_availability(authorization="Bearer t", x_user_id=None))

    assert out["member_id"] == ACTOR
    assert out["vendor_id"] == "default_vendor"
    assert out["available"] is True
    assert len(db.inserted) == 1 and db.inserted[0]["member_id"] == ACTOR


def test_get_availability_null_rest_prefers_existing_pg_row(monkeypatch):
    pg_row = {"member_id": ACTOR, "vendor_id": "default_vendor", "work_start": "07:00", "available": True}
    db = _Db(None)
    _patch(monkeypatch, db, pg_row=pg_row)

    out = _run(vos.get_vendor_availability(authorization="Bearer t", x_user_id=None))

    assert out["work_start"] == "07:00"
    assert db.inserted == []


def test_put_availability_null_reread_returns_saved_row(monkeypatch):
    db = _Db(None)
    _patch(monkeypatch, db)
    payload = AvailabilityUpdate(
        available=True, work_start="09:00", work_end="18:00", jobs_per_day=3, blocked_windows=[]
    )

    out = _run(
        vos.update_vendor_availability(payload, authorization="Bearer t", x_user_id=None)
    )

    assert out["member_id"] == ACTOR
    assert out["work_start"] == "09:00"
    assert out["jobs_per_day"] == 3
    assert len(db.upserted) == 1
