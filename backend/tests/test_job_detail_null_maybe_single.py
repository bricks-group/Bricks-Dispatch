"""Null maybe_single() must not 500 GET /api/vendor-os/jobs/{id}.

postgrest maybe_single().execute() returns None when there is no row.
Job detail used to read .data and raise AttributeError (HTTP 500, no CORS).
A null job row is an empty REST body: recover from Postgres, then the
existing visibility gate. A null assignee member is "Unassigned", not a crash.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers import vendor_os as vos
from app.shared import pg_fallback as pg


JOB_ID = "2a7afd47-9bce-4ee2-9dfd-fbca5f789499"
JOB = {
    "id": JOB_ID,
    "address": "10 Delivered St",
    "assignee_id": "actor-1",
    "vendor_id": "v1",
    "status": "assigned",
}


class _Chain:
    def __init__(self, result):
        self._result = result

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return self._result


class _Db:
    def __init__(self, jobs_result, members_result=None):
        self._jobs_result = jobs_result
        self._members_result = members_result

    def table(self, name):
        if name == "jobs":
            return _Chain(self._jobs_result)
        if name == "vendor_members":
            return _Chain(self._members_result)
        raise AssertionError(name)


def test_load_visible_job_null_maybe_single_uses_pg(monkeypatch):
    monkeypatch.setattr(pg, "pg_select_job_by_id", lambda jid: dict(JOB) if jid == JOB_ID else None)
    monkeypatch.setattr(vos, "job_visible_to", lambda *a, **k: True)

    out = asyncio.run(
        vos._load_visible_job(
            _Db(None),
            JOB_ID,
            "actor-1",
            {"id": "actor-1", "role": "Photographer", "vendor_id": "v1"},
            admin_all=False,
        )
    )
    assert out["id"] == JOB_ID
    assert out["address"] == "10 Delivered St"


def test_get_vendor_os_job_null_assignee_member_is_unassigned(monkeypatch):
    async def fake_get_db():
        return _Db(SimpleNamespace(data=dict(JOB)), None)

    monkeypatch.setattr(vos, "get_db", fake_get_db)
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="actor-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "actor-1", "name": "Pat", "role": "Photographer", "vendor_id": "v1"}),
    )
    monkeypatch.setattr(vos, "job_visible_to", lambda *a, **k: True)
    monkeypatch.setattr(vos, "is_vendor_admin", lambda *_a, **_k: False)

    out = asyncio.run(
        vos.get_vendor_os_job(JOB_ID, authorization="Bearer t", x_user_id=None)
    )
    assert out["id"] == JOB_ID
    assert out["assignee_name"] == "Unassigned"
