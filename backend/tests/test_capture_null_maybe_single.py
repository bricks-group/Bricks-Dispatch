"""Null maybe_single() must not 500 GET /api/capture/sessions.

postgrest maybe_single().execute() returns None when there is no row.
_actor_and_job used to read res.data and raise AttributeError, which the
global handler turns into HTTP 500 with no CORS (browser net::ERR_FAILED).
Null/missing data is no row: recover the job if Postgres has it, then the
route's existing empty session (sessionId null).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers import capture as cap
from app.shared import pg_fallback as pg


JOB_ID = "2a7afd47-9bce-4ee2-9dfd-fbca5f789499"
JOB = {
    "id": JOB_ID,
    "address": "10 Delivered St",
    "job_type": "photo",
    "sqft": 1200,
    "vendor_id": "v1",
    "assignee_id": "actor-1",
    "status": "delivered",
    "package_name": "Essentials",
    "add_ons": [],
    "property_details": {},
    "client_instructions": "",
}


class _Chain:
    def __init__(self, result):
        self._result = result

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return self._result


class _Db:
    def __init__(self, jobs_result):
        self._jobs_result = jobs_result

    def table(self, name):
        if name == "jobs":
            return _Chain(self._jobs_result)
        return _Chain(SimpleNamespace(data=[]))


def _wire(monkeypatch, jobs_result, pg_job):
    async def fake_get_db():
        return _Db(jobs_result)

    monkeypatch.setattr(cap, "get_db", fake_get_db)
    monkeypatch.setattr(pg, "pg_select_job_by_id", lambda jid: pg_job if jid == JOB_ID else None)
    monkeypatch.setattr(cap, "resolve_vendor_actor_id", AsyncMock(return_value="actor-1"))
    monkeypatch.setattr(
        cap,
        "get_vendor_member",
        AsyncMock(return_value={"id": "actor-1", "role": "Photographer", "vendor_id": "v1"}),
    )
    monkeypatch.setattr(cap, "job_visible_to", lambda *a, **k: True)


def _get(kind="floorplan"):
    return asyncio.run(
        cap.get_session(JOB_ID, kind=kind, authorization="Bearer t", x_user_id=None)
    )


def test_null_maybe_single_returns_empty_session(monkeypatch):
    """execute() is None — the AttributeError path. Must be the empty session."""
    _wire(monkeypatch, jobs_result=None, pg_job=JOB)
    out = _get()
    assert out["sessionId"] is None
    assert out["kind"] == "floorplan"
    assert out["address"] == "10 Delivered St"
    assert out["rooms"] == []
    assert out["pipeline"] == []
    assert out["stations"] == []


def test_null_maybe_single_data_returns_empty_session(monkeypatch):
    """Response object with data=None is the same no-row case, not a 500."""
    _wire(monkeypatch, jobs_result=SimpleNamespace(data=None), pg_job=JOB)
    out = _get()
    assert out["sessionId"] is None
    assert out["kind"] == "floorplan"
    assert out["address"] == "10 Delivered St"


def test_existing_job_row_still_empty_session_when_no_capture(monkeypatch):
    """Happy path: a real job row and no capture session stays 200-shaped."""
    _wire(monkeypatch, jobs_result=SimpleNamespace(data=dict(JOB)), pg_job=None)
    out = _get()
    assert out["sessionId"] is None
    assert out["kind"] == "floorplan"
    assert out["address"] == "10 Delivered St"
