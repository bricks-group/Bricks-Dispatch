"""Fair-Use: enhance→listing media_delivery + uploads updates via Postgres on PostgREST 402."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.shared import pg_fallback as pf


def _mock_conn(monkeypatch, *, rowcount: int = 1, fetchall=None, description=None):
    cur = MagicMock()
    cur.rowcount = rowcount
    cur.fetchall.return_value = fetchall or []
    cur.description = description
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)
    return cur


def test_pg_update_upload_whitelists_enhance_fields(monkeypatch):
    cur = _mock_conn(monkeypatch)
    ok = pf.pg_update_upload(
        "up-1",
        {
            "processing_status": "done",
            "processed_url": "enhanced/job/up-1.jpg",
            "provider": "bricks_inhouse",
            "qc_variant_url": "enhanced/job/qc_up-1.jpg",
            "media_type": "hdr",  # must NOT appear
        },
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "UPDATE public.uploads" in sql
    assert "processed_url" in sql
    assert "processing_status" in sql
    assert "media_type" not in sql
    assert params[-1] == "up-1"


def test_pg_update_upload_rejects_empty(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_update_upload("", {"processing_status": "done"}) is False
    assert pf.pg_update_upload("up-1", {}) is False
    assert pf.pg_update_upload("up-1", {"media_type": "x"}) is False


def test_pg_upsert_media_delivery_on_job_id(monkeypatch):
    cur = _mock_conn(monkeypatch)
    urls = [{"upload_id": "a", "url": "https://example/x.jpg", "filename": "x.jpg"}]
    ok = pf.pg_upsert_media_delivery(
        {
            "job_id": "job-1",
            "delivery_urls": urls,
            "delivered_at": "2026-09-24T10:00:00+00:00",
            "notification_status": "pending",
        }
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "INSERT INTO public.media_delivery" in sql
    assert "ON CONFLICT (job_id)" in sql
    assert "delivery_urls" in sql
    assert params[0] == "job-1"
    assert json.loads(params[1]) == urls


def test_pg_upsert_media_delivery_with_booking_id(monkeypatch):
    cur = _mock_conn(monkeypatch)
    ok = pf.pg_upsert_media_delivery(
        {
            "job_id": "job-2",
            "booking_id": "book-9",
            "delivery_urls": [],
            "delivered_at": "2026-09-24T10:00:00+00:00",
            "notification_status": "pending",
        }
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "booking_id" in sql
    assert params[0] == "job-2"
    assert params[1] == "book-9"


def test_pg_select_media_delivery_by_job_id(monkeypatch):
    desc = [
        ("id",), ("job_id",), ("booking_id",), ("delivery_urls",),
        ("delivered_at",), ("notified_at",), ("notification_status",), ("created_at",),
    ]
    cur = _mock_conn(
        monkeypatch,
        fetchall=[("md-1", "job-1", None, [], "2026-09-24", None, "pending", "2026-09-24")],
        description=desc,
    )
    row = pf.pg_select_media_delivery_by_job_id("job-1")
    assert row is not None
    assert row["id"] == "md-1"
    assert row["job_id"] == "job-1"
    assert "SELECT" in cur.execute.call_args[0][0]
    assert "media_delivery" in cur.execute.call_args[0][0]


def test_pg_claim_job_for_processing(monkeypatch):
    cur = _mock_conn(monkeypatch, rowcount=1)
    assert pf.pg_claim_job_for_processing("job-claim") is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.jobs" in sql
    assert "processing" in sql
    assert "NOT IN" in sql
    assert cur.execute.call_args[0][1][2] == "job-claim"


def test_pg_claim_job_for_processing_no_row(monkeypatch):
    _mock_conn(monkeypatch, rowcount=0)
    assert pf.pg_claim_job_for_processing("job-done") is False


def test_pg_update_media_delivery_notification(monkeypatch):
    cur = _mock_conn(monkeypatch)
    ok = pf.pg_update_media_delivery(
        "job-1",
        {"notification_status": "sent", "notified_at": "2026-09-24T11:00:00+00:00"},
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.media_delivery" in sql
    assert "notification_status" in sql


def test_finalize_uses_pg_helpers_on_quota():
    """Source guard: finalize must catch 402 and call media_delivery / uploads PG helpers."""
    from pathlib import Path
    from app.routers import post_production as pp
    from app.services import post_production as svc

    router_src = Path(pp.__file__).read_text()
    svc_src = Path(svc.__file__).read_text()
    assert "pg_upsert_media_delivery" in router_src
    assert "pg_select_media_delivery_by_job_id" in router_src
    assert "pg_select_uploads_by_job_id" in router_src
    assert "pg_update_job" in router_src
    assert "_safe_update_upload" in svc_src
    assert "pg_claim_job_for_processing" in svc_src
    assert "pg_update_upload" in svc_src
