"""Fair-Use: availability gate + calendar connections + vendor notify via Postgres."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services import notifications as notif
from app.services import vendor_availability_gate as vag
from app.services import vendor_calendars as vc
from app.shared import pg_fallback as pf


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def _mock_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    return conn


def test_pg_select_vendor_availability_by_member_ids_sql(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("member_id",),
        ("available",),
        ("work_start",),
        ("work_end",),
        ("jobs_per_day",),
        ("blocked_windows",),
    ]
    cur.fetchall.return_value = [
        ("photog-1", True, "08:00", "17:00", 4, '[{"start":"a","end":"b"}]'),
    ]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    out = pf.pg_select_vendor_availability_by_member_ids(["photog-1", "photog-2"])
    assert "photog-1" in out
    assert out["photog-1"]["available"] is True
    assert out["photog-1"]["work_start"] == "08:00"
    assert isinstance(out["photog-1"]["blocked_windows"], list)
    assert out["photog-1"]["blocked_windows"][0]["start"] == "a"
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "FROM public.vendor_availability" in sql
    assert "member_id = ANY(%s)" in sql
    assert params[0] == ["photog-1", "photog-2"]


def test_pg_select_vendor_availability_empty_ids(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_select_vendor_availability_by_member_ids([]) == {}
    assert pf.pg_select_vendor_availability_by_member_ids(["", "  "]) == {}


def test_pg_select_vendor_calendar_connections_sql(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("member_id",),
        ("provider",),
        ("access_token",),
    ]
    cur.fetchall.return_value = [
        ("photog-1", "google", "tok-abc"),
    ]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    rows = pf.pg_select_vendor_calendar_connections("photog-1")
    assert len(rows) == 1
    assert rows[0]["provider"] == "google"
    assert rows[0]["access_token"] == "tok-abc"
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "FROM public.vendor_calendar_connections" in sql
    assert "member_id = %s" in sql
    assert params[0] == "photog-1"


def test_pg_select_vendor_calendar_connections_empty(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_select_vendor_calendar_connections("") == []
    assert pf.pg_select_vendor_calendar_connections("  ") == []


def test_pg_insert_notification_whitelists_and_casts_data(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    ok = pf.pg_insert_notification(
        {
            "title": "New job assigned",
            "body": "10 Main St",
            "type": "job_assigned",
            "data": {"type": "job_assigned", "job_id": "job-1"},
            "member_id": "photog-1",
            "read": False,
            "evil_col": "dropme",
        }
    )
    assert ok is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "INSERT INTO public.notifications" in sql
    assert "evil_col" not in sql
    assert "title" in sql and "body" in sql and "type" in sql
    assert "data" in sql and "%s::jsonb" in sql
    assert "id" in sql and "member_id" in sql
    assert "New job assigned" in params
    assert "photog-1" in params
    assert any(isinstance(p, str) and "job-1" in p for p in params)


def test_pg_insert_notification_empty_row(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_insert_notification({}) is False
    assert pf.pg_insert_notification(None) is False  # type: ignore[arg-type]


def test_pg_select_push_tokens_for_member_sql(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [("id",), ("member_id",), ("token",), ("platform",), ("active",)]
    cur.fetchall.return_value = [
        ("tok-1", "photog-1", "ExponentPushToken[abc]", "expo", True),
    ]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    rows = pf.pg_select_push_tokens_for_member("photog-1")
    assert len(rows) == 1
    assert rows[0]["token"] == "ExponentPushToken[abc]"
    sql = cur.execute.call_args[0][0]
    assert "FROM public.push_tokens" in sql
    assert "active = true" in sql
    assert cur.execute.call_args[0][1][0] == "photog-1"


def test_load_availability_by_member_falls_back_on_402(monkeypatch):
    pg_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "vendor_availability"
            return Chain()

    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_availability_by_member_ids",
        lambda ids: pg_calls.append(list(ids))
        or {
            "photog-1": {
                "member_id": "photog-1",
                "available": False,
                "work_start": "08:00",
                "work_end": "17:00",
                "jobs_per_day": 2,
                "blocked_windows": [],
            }
        },
    )

    out = asyncio.run(vag.load_availability_by_member(Db(), ["photog-1", "photog-2"]))
    assert pg_calls == [["photog-1", "photog-2"]]
    assert out["photog-1"]["available"] is False
    assert out["photog-1"]["jobs_per_day"] == 2


def test_load_connections_falls_back_on_402(monkeypatch):
    pg_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "vendor_calendar_connections"
            return Chain()

    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_calendar_connections",
        lambda mid: pg_calls.append(mid)
        or [{"member_id": mid, "provider": "google", "access_token": "enc:tok"}],
    )
    monkeypatch.setattr(
        "app.services.calendar_token_crypto.decrypt_connection_secrets",
        lambda r: {**r, "access_token": "plain-tok"},
        raising=False,
    )
    # Import path used inside _load_connections
    import app.services.calendar_token_crypto as ctc

    monkeypatch.setattr(ctc, "decrypt_connection_secrets", lambda r: {**r, "access_token": "plain-tok"})
    monkeypatch.setattr(ctc, "needs_reencrypt", lambda r: False)
    monkeypatch.setattr(ctc, "encrypt_connection_secrets", lambda r: r)

    rows = asyncio.run(vc._load_connections(Db(), "photog-cal", decrypt=True))
    assert pg_calls == ["photog-cal"]
    assert len(rows) == 1
    assert rows[0]["provider"] == "google"
    assert rows[0]["access_token"] == "plain-tok"


def test_create_notification_falls_back_on_402(monkeypatch):
    pg_calls = []

    class Chain:
        def insert(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "notifications"
            return Chain()

    monkeypatch.setattr(notif, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_insert_notification",
        lambda row: pg_calls.append(dict(row)) or True,
    )
    monkeypatch.setattr(notif, "send_push_to_member", AsyncMock(return_value=False))

    out = asyncio.run(
        notif.create_notification(
            "New job assigned",
            "10 Main St · Fri 2:00 PM",
            ntype="job_assigned",
            data={"type": "job_assigned", "job_id": "job-n1"},
            member_id="photog-1",
            send_push=False,
        )
    )
    assert len(pg_calls) == 1
    assert pg_calls[0]["title"] == "New job assigned"
    assert pg_calls[0]["member_id"] == "photog-1"
    assert pg_calls[0]["type"] == "job_assigned"
    assert out["title"] == "New job assigned"


def test_send_push_to_member_falls_back_on_402(monkeypatch):
    pg_calls = []
    expo_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "push_tokens"
            return Chain()

    monkeypatch.setattr(notif, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_push_tokens_for_member",
        lambda mid: pg_calls.append(mid)
        or [
            {
                "id": "t1",
                "member_id": mid,
                "token": "ExponentPushToken[xyz]",
                "platform": "expo",
                "active": True,
            }
        ],
    )
    monkeypatch.setattr(
        notif,
        "_send_expo_push",
        AsyncMock(side_effect=lambda *a, **k: expo_calls.append(a) or True),
    )

    ok = asyncio.run(
        notif.send_push_to_member("photog-1", "New job assigned", "10 Main St", {"job_id": "j1"})
    )
    assert ok is True
    assert pg_calls == ["photog-1"]
    assert expo_calls and expo_calls[0][0] == "ExponentPushToken[xyz]"
