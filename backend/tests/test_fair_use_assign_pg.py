"""Fair-Use: assign / reassign job writes via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.routers import vendor_os as vos
from app.services import vendor_selection as vs
from app.shared import pg_fallback as pf
from app.shared.job_scope import KAM_MEMBER_ID, ORG_VENDOR_ID
from app.shared.schemas import JobAssignmentUpdate


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_pg_update_job_accepts_assignee_and_slot_fields(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_update_job(
        "job-assign",
        {
            "assignee_id": KAM_MEMBER_ID,
            "status": "assigned",
            "estimated_duration_min": 90,
            "selected_slot_start": "2026-09-20T13:00:00+00:00",
            "selected_slot_end": "2026-09-20T14:30:00+00:00",
            "updated_at": "2026-09-20T12:00:00+00:00",
        },
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.jobs" in sql
    for col in (
        "assignee_id",
        "status",
        "estimated_duration_min",
        "selected_slot_start",
        "selected_slot_end",
        "updated_at",
    ):
        assert col in sql
    assert cur.execute.call_args[0][1][-1] == "job-assign"


def test_patch_job_assign_falls_back_to_pg_on_402(monkeypatch):
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

    monkeypatch.setattr(vs, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_calls.append((jid, fields)) or True,
    )

    patch = {
        "assignee_id": "photog-1",
        "status": "assigned",
        "estimated_duration_min": 60,
        "updated_at": "2026-09-20T12:00:00+00:00",
    }
    asyncio.run(vs._patch_job_assign("job-p1", patch))
    assert len(pg_calls) == 1
    assert pg_calls[0][0] == "job-p1"
    assert pg_calls[0][1]["assignee_id"] == "photog-1"
    assert pg_calls[0][1]["status"] == "assigned"


def test_assign_job_to_member_manual_pg_on_402(monkeypatch):
    """Owner PUT assign: REST job select + update 402 → Postgres fallback."""
    kam = {
        "id": KAM_MEMBER_ID,
        "vendor_id": ORG_VENDOR_ID,
        "role": "Owner",
        "title": "Owner",
        "email": "akashorter@icloud.com",
        "payout_mode": "owner_full",
        "is_active": True,
        "onboarding_status": "approved",
        "name": "Kam",
    }
    job_row = {
        "id": "job-manual",
        "vendor_id": ORG_VENDOR_ID,
        "assignee_id": "",
        "status": "pending_assignment",
        "address": "10 Main St",
        "booking_id": None,
    }
    pg_job_calls = []
    pg_update_calls = []
    pg_member_calls = []

    class Chain:
        def __init__(self, table):
            self._table = table
            self._op = "select"

        def select(self, *_a, **_k):
            self._op = "select"
            return self

        def update(self, *_a, **_k):
            self._op = "update"
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain(name)

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=KAM_MEMBER_ID))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=kam))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(vos, "write_audit_event", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: pg_job_calls.append(jid)
        or (
            {**job_row, "assignee_id": KAM_MEMBER_ID, "status": "assigned"}
            if len(pg_job_calls) > 1
            else dict(job_row)
        ),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_member",
        lambda mid, email="": pg_member_calls.append(mid) or kam,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields, **_k: pg_update_calls.append((jid, fields)) or True,
    )
    monkeypatch.setattr(
        "app.services.client_comms.notify_booking_confirmed",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setenv("BOOKING_CALLBACK_URL", "")

    out = asyncio.run(
        vos.assign_job_to_member(
            "job-manual",
            JobAssignmentUpdate(member_id=KAM_MEMBER_ID),
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert "job-manual" in pg_job_calls
    assert pg_update_calls and pg_update_calls[0][0] == "job-manual"
    assert pg_update_calls[0][1]["assignee_id"] == KAM_MEMBER_ID
    assert pg_update_calls[0][1]["status"] == "assigned"
    assert out["assignee_id"] == KAM_MEMBER_ID
    assert out["status"] == "assigned"
    assert out.get("assignee_name") == "Kam"


def test_pg_select_occupied_jobs_empty_ids(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_select_occupied_jobs_by_assignees([]) == {}
    assert pf.pg_select_occupied_jobs_by_assignees(["", "  "]) == {}


def test_pg_select_occupied_jobs_passes_scheduled_date(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("assignee_id",),
        ("scheduled_date",),
        ("status",),
    ]
    cur.fetchall.return_value = [
        ("job-a", "photog-1", "2026-09-20", "assigned"),
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    out = pf.pg_select_occupied_jobs_by_assignees(
        ["photog-1", "photog-2"], scheduled_date="2026-09-20T15:00:00Z"
    )
    assert "photog-1" in out
    assert out["photog-1"][0]["id"] == "job-a"
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "assignee_id = ANY(%s)" in sql
    assert "status = ANY(%s)" in sql
    assert "scheduled_date = %s" in sql
    assert params[0] == ["photog-1", "photog-2"]
    assert params[1] == ["assigned", "in_progress", "pending_assignment", "completed"]
    assert params[2] == "2026-09-20"


def test_load_occupied_jobs_falls_back_to_pg_on_402(monkeypatch):
    pg_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "jobs"
            return Chain()

    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_occupied_jobs_by_assignees",
        lambda ids, scheduled_date=None: pg_calls.append((list(ids), scheduled_date))
        or {
            "photog-1": [
                {
                    "id": "job-occ",
                    "assignee_id": "photog-1",
                    "scheduled_date": "2026-09-21",
                    "status": "assigned",
                }
            ]
        },
    )

    out = asyncio.run(
        vs.load_occupied_jobs_by_vendor(
            Db(),
            ["photog-1", "photog-2"],
            scheduled_date="2026-09-21",
        )
    )
    assert len(pg_calls) == 1
    assert pg_calls[0][0] == ["photog-1", "photog-2"]
    assert pg_calls[0][1] == "2026-09-21"
    assert out["photog-1"][0]["id"] == "job-occ"
    assert out["photog-2"] == []


def test_pg_insert_ops_escalation_whitelists_and_generates_id(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)

    ok = pf.pg_insert_ops_escalation(
        {
            "booking_id": "book-1",
            "job_id": "job-esc-1",
            "escalation_type": "no_vendor_available",
            "attempt_count": 2,
            "error_message": "no eligible vendor",
            "status": "open",
            "evil_col": "dropme",
        }
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "INSERT INTO public.ops_escalations" in sql
    assert "evil_col" not in sql
    assert "booking_id" in sql and "job_id" in sql
    assert "escalation_type" in sql and "attempt_count" in sql
    assert "error_message" in sql and "status" in sql and "id" in sql
    # id + created_at auto-generated; booking/job/type/attempt/err/status present
    assert "book-1" in params
    assert "job-esc-1" in params
    assert "no_vendor_available" in params
    assert 2 in params
    assert "open" in params


def test_pg_insert_ops_escalation_empty_row(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_insert_ops_escalation({}) is False
    assert pf.pg_insert_ops_escalation(None) is False  # type: ignore[arg-type]


def test_mark_dispatch_failed_falls_back_to_pg_on_402(monkeypatch):
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

    monkeypatch.setattr(vs, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_calls.append((jid, fields)) or True,
    )

    asyncio.run(vs._mark_dispatch_failed("job-df-1"))
    assert len(pg_calls) == 1
    assert pg_calls[0][0] == "job-df-1"
    assert pg_calls[0][1]["status"] == "dispatch_failed"
    assert pg_calls[0][1]["assignee_id"] == ""
    assert "updated_at" in pg_calls[0][1]


def test_mark_dispatch_failed_never_raises_on_pg_miss(monkeypatch):
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

    monkeypatch.setattr(vs, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda *_a, **_k: False,
    )
    # Must not raise
    asyncio.run(vs._mark_dispatch_failed("job-df-2"))


def test_escalate_no_vendor_falls_back_to_pg_on_402(monkeypatch):
    esc_calls = []
    mark_calls = []
    notify_calls = []

    class Chain:
        def insert(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "ops_escalations"
            return Chain()

    job = MagicMock()
    job.id = "job-esc-2"
    job.booking_id = "book-2"
    job.address = "1 Test St"
    job.package_name = "Essentials"
    job.min_tier_required = 1

    monkeypatch.setattr(vs, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        vs,
        "_mark_dispatch_failed",
        AsyncMock(side_effect=lambda jid: mark_calls.append(jid)),
    )
    monkeypatch.setattr(
        vs,
        "_notify_owner_no_vendor",
        AsyncMock(side_effect=lambda j, err: notify_calls.append((j.id, err))),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_insert_ops_escalation",
        lambda row: esc_calls.append(dict(row)) or True,
    )
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    asyncio.run(vs._escalate_no_vendor(job, 3, "no eligible vendor"))
    assert mark_calls == ["job-esc-2"]
    assert notify_calls and notify_calls[0][0] == "job-esc-2"
    assert len(esc_calls) == 1
    assert esc_calls[0]["job_id"] == "job-esc-2"
    assert esc_calls[0]["booking_id"] == "book-2"
    assert esc_calls[0]["escalation_type"] == "no_vendor_available"
    assert esc_calls[0]["attempt_count"] == 3
    assert esc_calls[0]["status"] == "open"
    assert "no eligible vendor" in esc_calls[0]["error_message"]


def test_notify_owner_phone_falls_back_to_pg_on_402(monkeypatch):
    pg_calls = []
    sms_calls = []

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

    job = MagicMock()
    job.id = "job-sms-1"
    job.address = "42 King St"
    job.scheduled_date = "2026-09-21"
    job.booking_id = "book-sms"

    monkeypatch.setattr(vs, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_member",
        lambda mid, email="": pg_calls.append(mid) or {"id": mid, "phone": "+15551234567"},
    )
    monkeypatch.setattr(
        "app.notifications.send_dispatch_sms",
        lambda phone, body: sms_calls.append((phone, body)),
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.notifications.create_notification",
        AsyncMock(return_value=None),
        raising=False,
    )

    asyncio.run(vs._notify_owner_no_vendor(job, "no eligible vendor"))
    assert pg_calls == [KAM_MEMBER_ID]
    assert sms_calls and sms_calls[0][0] == "+15551234567"
    assert "42 King St" in sms_calls[0][1]

