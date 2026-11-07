"""Null maybe_single() must not 500 GET/PUT /api/vendor-os/profile.

postgrest maybe_single().execute() returns None when there is no row. A new GO
photographer has no vendor_availability row, so build_profile's availability
flag read .data on None and raised AttributeError (CORS-less HTTP 500) on every
GO profile load (account tab, job detail, layout boot).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers import vendor_profile as vp

ACTOR = "11111111-2222-3333-4444-555555555555"
MEMBER = {
    "id": ACTOR, "name": "New GO", "role": "Photographer",
    "vendor_id": "default_vendor", "status": "available", "email": "go@example.com",
}


class _Chain:
    def __init__(self, db, name):
        self._db = db
        self._name = name
        self._single = False
        self._op = "select"

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def update(self, patch):
        self._op = "update"
        self._db.updates.append(patch)
        return self

    def upsert(self, row):
        self._op = "upsert"
        return self

    async def execute(self):
        if self._single:
            return None  # postgrest: no row
        if self._op == "update":
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])


class _Db:
    def __init__(self):
        self.updates: list[dict] = []

    def table(self, name):
        return _Chain(self, name)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _patch(monkeypatch, db):
    async def fake_get_db():
        return db

    monkeypatch.setattr(vp, "get_db", fake_get_db)
    monkeypatch.setattr(vp, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(vp, "get_vendor_member", AsyncMock(return_value=dict(MEMBER)))


def test_maybe_single_row_none_is_empty():
    assert vp._maybe_single_row(None) == {}
    assert vp._maybe_single_row(SimpleNamespace(data=None)) == {}
    assert vp._maybe_single_row(SimpleNamespace(data={"available": False})) == {"available": False}


def test_get_profile_without_availability_row(monkeypatch):
    _patch(monkeypatch, _Db())
    out = _run(vp.get_profile_impl("Bearer t", None))
    assert out["availability"] is True  # falls back to member status


def test_put_profile_null_reread_keeps_member(monkeypatch):
    db = _Db()
    _patch(monkeypatch, db)
    out = _run(vp.update_profile_impl(vp.ProfileUpdateBody(name="Renamed"), "Bearer t", None))
    assert out["availability"] is True
    assert db.updates and db.updates[-1]["name"] == "Renamed"
