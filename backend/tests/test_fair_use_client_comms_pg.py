"""Fair-Use: client_comms job/photographer loads via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from app.services import client_comms as cc


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_load_job_pg_on_rest_402(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "jobs"
            return Chain()

    row = {
        "id": "job-comms",
        "agent_email": "agent@example.com",
        "agent_name": "Alex",
        "address": "1 Main St",
        "assignee_id": "photog-1",
        "package_name": "Essentials",
    }
    pg_calls = []

    async def _get_db():
        return Db()

    monkeypatch.setattr(
        "app.shared.database.get_db",
        _get_db,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: pg_calls.append(jid) or (row if jid == "job-comms" else None),
    )

    out = asyncio.run(cc._load_job("job-comms", "id, agent_email, assignee_id"))
    assert out["id"] == "job-comms"
    assert out["agent_email"] == "agent@example.com"
    assert pg_calls == ["job-comms"]


def test_load_job_pg_on_empty_rest(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            m = MagicMock()
            m.data = None
            return m

    class Db:
        def table(self, name):
            return Chain()

    async def _get_db():
        return Db()

    monkeypatch.setattr("app.shared.database.get_db", _get_db)
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: {"id": jid, "agent_email": "a@b.c"} if jid == "job-empty" else None,
    )

    out = asyncio.run(cc._load_job("job-empty", "id, agent_email"))
    assert out["id"] == "job-empty"
    assert out["agent_email"] == "a@b.c"


def test_resolve_photographer_name_pg_on_rest_402(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "vendor_members"
            return Chain()

    async def _get_db():
        return Db()

    pg_calls = []

    monkeypatch.setattr("app.shared.database.get_db", _get_db)
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_member",
        lambda actor_id="", email="": pg_calls.append(actor_id)
        or ({"id": actor_id, "name": "Cam Photog"} if actor_id == "photog-1" else None),
    )

    name = asyncio.run(cc._resolve_photographer_name("photog-1", "fallback name"))
    assert name == "Cam Photog"
    assert pg_calls == ["photog-1"]


def test_resolve_photographer_name_pg_on_empty_rest(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            m = MagicMock()
            m.data = None
            return m

    class Db:
        def table(self, name):
            return Chain()

    async def _get_db():
        return Db()

    monkeypatch.setattr("app.shared.database.get_db", _get_db)
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_member",
        lambda actor_id="", email="": {"id": actor_id, "name": "Dana"},
    )

    name = asyncio.run(cc._resolve_photographer_name("photog-2", "fallback"))
    assert name == "Dana"


def test_resolve_photographer_name_missing_assignee():
    name = asyncio.run(cc._resolve_photographer_name(None, "your Bricks photographer"))
    assert name == "your Bricks photographer"
