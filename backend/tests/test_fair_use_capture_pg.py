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


def test_pg_update_job_updated_at(monkeypatch):
    from app.shared import pg_fallback as pf

    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_update_job("job-1", {"updated_at": "2026-09-20T08:00:00+00:00"})
    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.jobs" in sql
    assert "updated_at" in sql
    assert cur.execute.call_args[0][1][-1] == "job-1"


def test_pg_update_job_checklist_jsonb(monkeypatch):
    from app.shared import pg_fallback as pf

    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    checklist = [{"label": "Floor plan capture submitted", "completed": True}]
    ok = pf.pg_update_job(
        "job-2",
        {"checklist": checklist, "updated_at": "2026-09-20T08:00:00+00:00"},
    )
    assert ok is True
    sql, params = cur.execute.call_args[0]
    assert "checklist = %s::jsonb" in sql
    assert '"Floor plan capture submitted"' in params[0]
    assert params[-1] == "job-2"


def test_pg_update_job_rejects_unknown_fields(monkeypatch):
    from app.shared import pg_fallback as pf

    calls = []

    def boom():
        calls.append(1)
        return None

    monkeypatch.setattr(pf, "_pg_connect", boom)
    # Only unknown fields → nothing to write → False without connecting
    assert pf.pg_update_job("job-x", {"secret_col": "nope"}) is False
    assert calls == []


def test_note_floorplan_pg_on_rest_402(monkeypatch):
    """_note_floorplan_on_job falls back to pg_update_job on Fair-Use 402."""
    job = {"id": "job-fp", "checklist": []}
    now = "2026-09-20T08:00:00+00:00"
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
            assert name == "jobs"
            return Chain()

    def fake_pg(job_id, fields):
        pg_calls.append((job_id, fields))
        return True

    monkeypatch.setattr("app.shared.pg_fallback.pg_update_job", fake_pg)

    asyncio.run(cap._note_floorplan_on_job(Db(), job, "job-fp", "sess-1", 4, now))
    assert len(pg_calls) == 1
    assert pg_calls[0][0] == "job-fp"
    assert pg_calls[0][1]["updated_at"] == now
    labels = [c.get("label") for c in pg_calls[0][1]["checklist"]]
    assert "Floor plan capture submitted" in labels
    assert job["checklist"][-1]["label"] == "Floor plan capture submitted"


def test_notify_deliver_updated_at_pg_on_402(monkeypatch):
    """First-of-kind deliver bumps jobs.updated_at via PG when REST is 402."""
    job = {"id": "job-dl", "address": "9 King", "booking_id": "bk-1"}
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

    monkeypatch.setattr(cap, "_first_delivered_of_kind", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_calls.append((jid, fields)) or True,
    )

    async def fake_notify(*_a, **_k):
        return None

    monkeypatch.setattr(
        "app.services.client_comms.notify_capture_delivered", fake_notify
    )

    asyncio.run(
        cap._notify_capture_session_delivered(Db(), job, "sess-9", "splat", "path/out")
    )
    assert pg_calls and pg_calls[0][0] == "job-dl"
    assert "updated_at" in pg_calls[0][1]


def test_pg_upsert_media_file_insert(monkeypatch):
    cur = MagicMock()
    cur.fetchone.return_value = None  # no existing row
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pc, "_pg_connect", lambda: conn)

    ok = pc.pg_upsert_media_file(
        booking_id="bk-1",
        storage_path="visits/job/floorplan-s1.png",
        file_url="https://signed.example/plan.png",
        file_name="floorplan-s1.png",
    )
    assert ok is True
    assert cur.execute.call_count == 2
    select_sql = cur.execute.call_args_list[0][0][0]
    insert_sql = cur.execute.call_args_list[1][0][0]
    assert "SELECT id FROM public.media_files" in select_sql
    assert "INSERT INTO public.media_files" in insert_sql
    insert_params = cur.execute.call_args_list[1][0][1]
    assert insert_params[0] == "bk-1"
    assert insert_params[1] == "floor_plan"
    assert insert_params[3] == "https://signed.example/plan.png"
    assert insert_params[4] == "visits/job/floorplan-s1.png"


def test_pg_upsert_media_file_update_existing(monkeypatch):
    cur = MagicMock()
    cur.fetchone.return_value = ("media-row-9",)
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pc, "_pg_connect", lambda: conn)

    ok = pc.pg_upsert_media_file(
        booking_id="bk-2",
        storage_path="visits/job/floorplan-s2.png",
        file_url="https://signed.example/updated.png",
        file_name="floorplan-s2.png",
        label="Floor plan",
    )
    assert ok is True
    assert cur.execute.call_count == 2
    update_sql = cur.execute.call_args_list[1][0][0]
    assert "UPDATE public.media_files" in update_sql
    update_params = cur.execute.call_args_list[1][0][1]
    assert update_params[0] == "https://signed.example/updated.png"
    assert update_params[1] == "floorplan-s2.png"
    assert update_params[2] == "Floor plan"
    assert update_params[3] == "media-row-9"


def test_pg_upsert_media_file_requires_booking_and_path(monkeypatch):
    calls = []

    def boom():
        calls.append(1)
        return None

    monkeypatch.setattr(pc, "_pg_connect", boom)
    assert pc.pg_upsert_media_file("", "path/a.png", "url", "a.png") is False
    assert pc.pg_upsert_media_file("bk", "", "url", "a.png") is False
    assert calls == []


def test_attach_floorplan_media_rest_402_uses_pg(monkeypatch):
    """_attach_floorplan_media falls back to pg_upsert_media_file on Fair-Use 402."""
    from app.services import capture_worker as cw

    pg_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def update(self, *_a, **_k):
            return self

        def insert(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "media_files"
            return Chain()

    async def fake_signed(path, expiry=0):
        return f"https://signed.example/{path}"

    def fake_pg(**kwargs):
        pg_calls.append(kwargs)
        return True

    monkeypatch.setattr(cw, "get_signed_download_url", fake_signed)
    monkeypatch.setattr("app.shared.pg_capture.pg_upsert_media_file", fake_pg)

    job = {"id": "job-1", "booking_id": "bk-fp"}
    asyncio.run(
        cw._attach_floorplan_media(Db(), job, "visits/j/floorplan-sess.png", "sess")
    )
    assert len(pg_calls) == 1
    assert pg_calls[0]["booking_id"] == "bk-fp"
    assert pg_calls[0]["storage_path"] == "visits/j/floorplan-sess.png"
    assert pg_calls[0]["file_name"] == "floorplan-sess.png"
    assert pg_calls[0]["file_type"] == "floor_plan"
    assert "signed.example" in pg_calls[0]["file_url"]


def test_attach_floorplan_media_no_booking_noop(monkeypatch):
    from app.services import capture_worker as cw

    called = []

    class Db:
        def table(self, *_a, **_k):
            called.append("table")
            raise AssertionError("should not touch REST without booking_id")

    monkeypatch.setattr(
        "app.shared.pg_capture.pg_upsert_media_file",
        lambda **_k: called.append("pg") or True,
    )
    asyncio.run(cw._attach_floorplan_media(Db(), {"id": "j"}, "path.png", "sess"))
    assert called == []
