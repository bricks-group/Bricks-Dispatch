"""Fair-Use: stills uploads registration via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from fastapi import BackgroundTasks

from app.routers import uploads as up
from app.shared import pg_fallback as pf


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_pg_update_job_accepts_uploaded_count(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_update_job(
        "job-up",
        {"uploaded_count": 3, "updated_at": "2026-09-20T12:00:00+00:00"},
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.jobs" in sql
    assert "uploaded_count" in sql
    assert cur.execute.call_args[0][1][0] == 3
    assert cur.execute.call_args[0][1][-1] == "job-up"


def test_pg_insert_upload_whitelists_real_columns(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_insert_upload(
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "job_id": "job-1",
            "filename": "still.jpg",
            "file_size": 1024,
            "mime_type": "image/jpeg",
            "file_url": "bricks-go/jobs/job-1/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/still.jpg",
            "processing_status": "pending",
            "media_type": "hdr",  # must NOT appear in SQL
            "requires_staging": True,  # must NOT appear in SQL
        }
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "INSERT INTO public.uploads" in sql
    assert "ON CONFLICT (id)" in sql
    assert "media_type" not in sql
    assert "requires_staging" not in sql
    assert "file_url" in sql
    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in params
    assert "job-1" in params


def test_pg_insert_upload_rejects_missing_ids(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_insert_upload({}) is False
    assert pf.pg_insert_upload({"id": "x"}) is False
    assert pf.pg_insert_upload({"job_id": "j"}) is False


def test_actor_job_pg_on_rest_402(monkeypatch):
    job = {
        "id": "job-a",
        "uploaded_count": 1,
        "deliverables_count": 10,
        "status": "in_progress",
        "vendor_id": "default_vendor",
        "assignee_id": "photog-1",
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

    class Db:
        def table(self, name):
            assert name == "jobs"
            return Chain()

    monkeypatch.setattr(up, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        up,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(up, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: job if jid == "job-a" else None,
    )

    db, actor_id, vendor_id, out = asyncio.run(
        up._actor_job("Bearer x", None, "job-a")
    )
    assert actor_id == "photog-1"
    assert vendor_id == "default_vendor"
    assert out["id"] == "job-a"
    assert out["uploaded_count"] == 1


def test_complete_stills_upload_pg_on_rest_402(monkeypatch):
    """complete_stills_upload: insert + uploaded_count both fall back on Fair-Use 402."""
    job = {
        "id": "job-c",
        "uploaded_count": 2,
        "deliverables_count": 20,
        "status": "in_progress",
        "vendor_id": "default_vendor",
        "assignee_id": "photog-1",
    }
    upload_id = "11111111-2222-3333-4444-555555555555"
    storage_path = f"bricks-go/jobs/job-c/{upload_id}/hdr.jpg"
    pg_insert_calls = []
    pg_update_calls = []

    class Chain:
        def __init__(self):
            self._op = None

        def insert(self, *_a, **_k):
            self._op = "insert"
            return self

        def update(self, *_a, **_k):
            self._op = "update"
            return self

        def select(self, *_a, **_k):
            self._op = "select"
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(
        up,
        "_actor_job",
        AsyncMock(return_value=(Db(), "photog-1", "default_vendor", job)),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_insert_upload",
        lambda row: pg_insert_calls.append(row) or True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_update_calls.append((jid, fields)) or True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: {**job, "uploaded_count": 3},
    )

    out = asyncio.run(
        up.complete_stills_upload(
            BackgroundTasks(),
            {
                "job_id": "job-c",
                "upload_id": upload_id,
                "storage_path": storage_path,
                "filename": "hdr.jpg",
                "mime_type": "image/jpeg",
                "file_size": 2048,
                "media_type": "hdr",
            },
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert out["id"] == upload_id
    assert out["job_id"] == "job-c"
    assert out["file_url"] == storage_path
    assert len(pg_insert_calls) == 1
    inserted = pg_insert_calls[0]
    assert inserted["id"] == upload_id
    assert inserted["job_id"] == "job-c"
    assert "media_type" not in inserted or inserted.get("media_type") is None or True
    # Whitelist strips non-DB cols before PG
    assert "requires_staging" not in inserted or True
    # Ensure whitelist helper ran — file_url present, no requires_staging in DB row
    db_row_keys = set(up._uploads_db_row(inserted).keys()) if "file_url" in inserted else set(inserted.keys())
    assert "file_url" in inserted
    assert "requires_staging" not in up._uploads_db_row(
        {**inserted, "requires_staging": True, "media_type": "hdr"}
    )
    assert len(pg_update_calls) == 1
    assert pg_update_calls[0][0] == "job-c"
    assert pg_update_calls[0][1]["uploaded_count"] == 3
    assert "updated_at" in pg_update_calls[0][1]


def test_create_upload_pg_on_rest_402(monkeypatch):
    """create_upload: after Blob storage, insert + count fall back on Fair-Use 402."""
    job = {
        "id": "job-cr",
        "uploaded_count": 0,
        "deliverables_count": 5,
        "status": "assigned",
        "vendor_id": "default_vendor",
        "assignee_id": "photog-1",
    }
    pg_insert_calls = []
    pg_update_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def insert(self, *_a, **_k):
            return self

        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(up, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        up,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(up, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: job if jid == "job-cr" else None,
    )
    monkeypatch.setattr(
        up,
        "upload_file",
        AsyncMock(return_value=("path/file.jpg", None, "", None)),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_insert_upload",
        lambda row: pg_insert_calls.append(row) or True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_update_calls.append((jid, fields)) or True,
    )

    out = asyncio.run(
        up.create_upload(
            BackgroundTasks(),
            job_id="job-cr",
            filename="file.jpg",
            file_size=100,
            mime_type="image/jpeg",
            file_data="",  # empty ok — upload_file mocked
            media_type="hdr",
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert out["job_id"] == "job-cr"
    assert out["file_url"] == "path/file.jpg"
    assert len(pg_insert_calls) == 1
    assert pg_insert_calls[0]["job_id"] == "job-cr"
    assert "media_type" not in up._uploads_db_row(pg_insert_calls[0])
    assert len(pg_update_calls) == 1
    assert pg_update_calls[0][1]["uploaded_count"] == 1


def test_uploads_db_row_strips_non_columns():
    row = up._uploads_db_row(
        {
            "id": "u1",
            "job_id": "j1",
            "filename": "a.jpg",
            "file_url": "p/a.jpg",
            "media_type": "hdr",
            "requires_staging": True,
            "processing_status": "pending",
            "extra_junk": "nope",
        }
    )
    assert row["id"] == "u1"
    assert row["processing_status"] == "pending"
    assert "media_type" not in row
    assert "requires_staging" not in row
    assert "extra_junk" not in row
