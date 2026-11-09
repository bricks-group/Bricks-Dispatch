"""Fair-Use: confirm-arrive + job status writes via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.routers import dispatch as disp
from app.routers import vendor_os as vos
from app.shared import pg_fallback as pf
from app.shared.schemas import JobStatusUpdate


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_pg_update_job_accepts_status_and_arrive_fields(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_update_job(
        "job-arrive",
        {
            "status": "in_progress",
            "confirmed_arrive_at": "13:30",
            "time_window_start": "13:30",
            "time_window_end": "16:00",
            "preferred_window": "afternoon",
            "updated_at": "2026-09-20T12:00:00+00:00",
        },
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.jobs" in sql
    for col in (
        "status",
        "confirmed_arrive_at",
        "time_window_start",
        "time_window_end",
        "preferred_window",
        "updated_at",
    ):
        assert col in sql
    assert cur.execute.call_args[0][1][-1] == "job-arrive"


def test_load_visible_job_pg_on_rest_402(monkeypatch):
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
        "id": "job-vis",
        "assignee_id": "photog-1",
        "vendor_id": "default_vendor",
        "status": "assigned",
    }
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: row if jid == "job-vis" else None,
    )
    monkeypatch.setattr(vos, "job_visible_to", lambda *a, **k: True)
    monkeypatch.setattr(vos, "is_vendor_admin", lambda *_a, **_k: False)

    out = asyncio.run(
        vos._load_visible_job(Db(), "job-vis", "photog-1", {"id": "photog-1"}, admin_all=False)
    )
    assert out["id"] == "job-vis"
    assert out["status"] == "assigned"


def test_confirm_arrive_pg_on_rest_402(monkeypatch):
    """confirm_job_arrive_time falls back to pg_update_job on Fair-Use 402."""
    job = {
        "id": "job-arr",
        "assignee_id": "photog-1",
        "time_window_start": "12:00",
        "time_window_end": "16:00",
        "preferred_window": "afternoon",
        "status": "assigned",
    }
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

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "name": "Cam", "vendor_id": "default_vendor", "terms_accepted_at": "2026-10-01T00:00:00+00:00", "ic_agreement_version": "1.9", "ic_amendment_version": "IC-A1 v1.0"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(vos, "ensure_assigned_to_actor", lambda *a, **k: None)
    monkeypatch.setattr(vos, "stamp_capture", lambda j: j)
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_calls.append((jid, fields)) or True,
    )

    async def _noop_notify(*_a, **_k):
        return None

    monkeypatch.setattr(
        "app.services.notifications.notify_arrive_confirmed",
        _noop_notify,
        raising=False,
    )

    out = asyncio.run(
        vos.confirm_job_arrive_time(
            "job-arr",
            vos.ConfirmArriveRequest(arrive_at="13:30"),
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert len(pg_calls) == 1
    assert pg_calls[0][0] == "job-arr"
    fields = pg_calls[0][1]
    assert fields["confirmed_arrive_at"] == "13:30"
    assert fields["time_window_start"] == "13:30"
    assert fields["time_window_end"] == "16:00"
    assert fields["preferred_window"] == "afternoon"
    assert "updated_at" in fields
    assert out["confirmed_arrive_at"] == "13:30"


def test_update_vendor_job_status_pg_on_rest_402(monkeypatch):
    """update_vendor_job_status falls back to pg_update_job on Fair-Use 402."""
    job = {
        "id": "job-st",
        "assignee_id": "photog-1",
        "status": "assigned",
        "booking_id": None,
        "deliverables_count": 0,
        "uploaded_count": 0,
    }
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

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor", "terms_accepted_at": "2026-10-01T00:00:00+00:00", "ic_agreement_version": "1.9", "ic_amendment_version": "IC-A1 v1.0"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(vos, "ensure_assigned_to_actor", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_calls.append((jid, fields)) or True,
    )
    monkeypatch.setattr(
        "app.services.webhooks.send_dispatch_status_update_webhook",
        AsyncMock(return_value=None),
        raising=False,
    )

    # update_vendor_job_status may continue past the jobs update; stub webhook path
    # and any member metric updates that still hit REST (out of this slice).
    async def run():
        return await vos.update_vendor_job_status(
            "job-st",
            JobStatusUpdate(status="in_progress"),
            authorization="Bearer x",
            x_user_id=None,
        )

    out = asyncio.run(run())
    assert pg_calls and pg_calls[0][0] == "job-st"
    assert pg_calls[0][1]["status"] == "in_progress"
    assert "updated_at" in pg_calls[0][1]
    assert out is not None


def test_dispatch_update_job_status_select_and_update_pg_on_402(monkeypatch):
    """dispatch.update_job_status: select + update both fall back on Fair-Use 402."""
    pg_select_calls = []
    pg_update_calls = []

    class Chain:
        def select(self, *_a, **_k):
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
            assert name == "jobs"
            return Chain()

    monkeypatch.setattr(disp, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.auth.verify_internal_api_key",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: pg_select_calls.append(jid)
        or {
            "id": jid,
            "status": "assigned",
            "booking_id": None,
            "vendor_id": "default_vendor",
        },
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_update_calls.append((jid, fields)) or True,
    )

    out = asyncio.run(
        disp.update_job_status(
            "job-d1",
            JobStatusUpdate(status="in_progress"),
            authorization=None,
            x_user_id=None,
            x_internal_api_key="internal-ok",
        )
    )
    assert pg_select_calls == ["job-d1"]
    assert pg_update_calls and pg_update_calls[0][0] == "job-d1"
    assert pg_update_calls[0][1]["status"] == "in_progress"
    assert out["new_status"] == "in_progress"
