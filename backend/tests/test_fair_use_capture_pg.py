"""Fair-Use: capture session lifecycle via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routers import capture as cap
from app.shared import pg_capture as pc
from app.shared.pg_fallback import _is_rest_quota_error


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_missing_table_does_not_swallow_fair_use():
    assert not cap._missing_table(_QuotaErr())
    assert not cap._missing_table(Exception("restricted: fair_use storage_size_quota"))
    assert _is_rest_quota_error(_QuotaErr())
    # Still true for real missing tables
    assert cap._missing_table(Exception('relation "capture_sessions" does not exist'))


def test_pg_select_session_in_progress(monkeypatch):
    rows = [{"id": "sess-1", "job_id": "job-1", "status": "in_progress", "kind": "floorplan"}]
    cur = MagicMock()
    cur.description = [("id",), ("job_id",), ("status",), ("kind",)]
    cur.fetchone.return_value = ("sess-1", "job-1", "in_progress", "floorplan")
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pc, "_pg_connect", lambda: conn)

    out = pc.pg_select_session_in_progress("job-1", "floorplan")
    assert out["id"] == "sess-1"
    assert out["status"] == "in_progress"
    cur.execute.assert_called()
    sql = cur.execute.call_args[0][0]
    assert "in_progress" in sql
    assert "capture_sessions" in sql


def test_pg_create_session_with_defaults(monkeypatch):
    session_row = (
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "job-9",
        "in_progress",
        "splat",
        0,
    )
    # description for RETURNING *
    desc = [("id",), ("job_id",), ("status",), ("kind",), ("frame_count",)]

    class FakeCur:
        def __init__(self):
            self.calls = []
            self.description = desc

        def execute(self, sql, params=None):
            self.calls.append((sql, params))

        def fetchone(self):
            # Only the session INSERT returns a row
            if any("capture_sessions" in c[0] for c in self.calls[-1:]):
                return session_row
            return session_row

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    cur = FakeCur()
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pc, "_pg_connect", lambda: conn)

    pipelines = [
        ("Captured", "0 stations", "active"),
        ("Building tour", "Stitch", "todo"),
        ("QC", "review", "todo"),
        ("Delivered", "listing", "todo"),
    ]
    out = pc.pg_create_session_with_defaults("job-9", "splat", pipelines)
    assert out is not None
    assert out["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert out["job_id"] == "job-9"
    # session + 4 pipeline + 1 room
    assert len(cur.calls) == 6
    assert any("capture_rooms" in c[0] for c in cur.calls)
    assert sum(1 for c in cur.calls if "capture_pipeline" in c[0]) == 4


def test_ensure_session_pg_create_on_rest_402(monkeypatch):
    """ensure_session falls back to PG create when REST insert raises Fair-Use."""
    job = {
        "id": "job-fu",
        "address": "1 Test St",
        "assignee_id": "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580",
        "vendor_id": "default_vendor",
        "package_name": "Photo + Floor Plan",
        "add_ons": [],
        "property_details": {},
        "client_instructions": "",
    }
    created = {
        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "job_id": "job-fu",
        "status": "in_progress",
        "kind": "floorplan",
        "frame_count": 0,
    }

    async def fake_actor(*_a, **_k):
        return {"id": job["assignee_id"], "role": "Photographer"}, job

    class BoomTable:
        def insert(self, *_a, **_k):
            raise _QuotaErr()

        def select(self, *_a, **_k):
            raise _QuotaErr()

    class BoomDb:
        def table(self, name):
            return BoomTable()

    async def fake_get_db():
        return BoomDb()

    monkeypatch.setattr(cap, "_actor_and_job", fake_actor)
    monkeypatch.setattr(cap, "get_db", fake_get_db)
    monkeypatch.setattr(cap, "job_capture_entitlements", lambda _j: {
        "splat": False, "floorplan": True, "visit": False,
    })
    monkeypatch.setattr(cap, "kind_purchased", lambda _j, k: k == "floorplan")

    # _in_progress_session will hit BoomTable.select → QuotaErr → pg_select
    monkeypatch.setattr(cap, "_in_progress_session", AsyncMock(side_effect=[None, created]))

    create_calls = []

    def fake_create(job_id, kind, pipelines):
        create_calls.append((job_id, kind, list(pipelines)))
        return created

    monkeypatch.setattr(
        "app.shared.pg_capture.pg_create_session_with_defaults", fake_create
    )

    async def fake_bundle(db, job_id, session, job, preferred_room_id=None):
        return {
            "sessionId": session["id"],
            "kind": session.get("kind"),
            "frameCount": 0,
            "rooms": [{"name": "Room 1", "id": "room-1"}],
            "pipeline": [],
        }

    monkeypatch.setattr(cap, "_load_session_bundle", fake_bundle)

    # Drive ensure_session: REST create path raises inside try → PG create
    # We need the inner insert to raise. Patch _in_progress first call None,
    # then make db.table().insert raise.
    class InsertBoom:
        def insert(self, *_a, **_k):
            m = MagicMock()
            m.execute = AsyncMock(side_effect=_QuotaErr())
            return m

        def select(self, *_a, **_k):
            m = MagicMock()
            m.eq = MagicMock(return_value=m)
            m.order = MagicMock(return_value=m)
            m.limit = MagicMock(return_value=m)
            m.execute = AsyncMock(side_effect=_QuotaErr())
            return m

    class Db2:
        def table(self, name):
            return InsertBoom()

    async def get_db2():
        return Db2()

    monkeypatch.setattr(cap, "get_db", get_db2)
    # First in_progress: None (no existing); after create, return created
    calls = {"n": 0}

    async def in_prog(db, job_id, kind):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return created

    monkeypatch.setattr(cap, "_in_progress_session", in_prog)

    out = asyncio.run(
        cap.ensure_session(
            payload={"job_id": "job-fu", "kind": "floorplan"},
            authorization="Bearer x",
            x_user_id=job["assignee_id"],
        )
    )
    assert create_calls and create_calls[0][0] == "job-fu"
    assert create_calls[0][1] == "floorplan"
    assert out["sessionId"] == created["id"]


def test_in_progress_lookup_via_pg(monkeypatch):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(
        "app.shared.pg_capture.pg_select_session_in_progress",
        lambda job_id, kind: {
            "id": "pg-sess",
            "job_id": job_id,
            "kind": kind,
            "status": "in_progress",
        },
    )
    out = asyncio.run(cap._in_progress_session(Db(), "job-z", "splat"))
    assert out["id"] == "pg-sess"
