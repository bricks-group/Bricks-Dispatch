"""Fair-Use: GO notifications inbox list/read + push token register via Postgres."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.routers import notifications as nrouter
from app.services import notifications as notif
from app.shared import pg_fallback as pf


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def _mock_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    return conn


def test_pg_select_notifications_for_member_sql(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("title",),
        ("body",),
        ("type",),
        ("data",),
        ("read",),
        ("member_id",),
        ("created_at",),
    ]
    cur.fetchall.return_value = [
        (
            "n-1",
            "New job",
            "10 Main",
            "job_assigned",
            '{"job_id":"j1"}',
            False,
            "photog-1",
            "2026-09-21T12:00:00+00:00",
        ),
    ]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    rows = pf.pg_select_notifications_for_member("photog-1", limit=50)
    assert len(rows) == 1
    assert rows[0]["id"] == "n-1"
    assert rows[0]["title"] == "New job"
    assert rows[0]["data"]["job_id"] == "j1"
    assert rows[0]["read"] is False
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "FROM public.notifications" in sql
    assert "member_id = %s" in sql
    assert "ORDER BY created_at DESC" in sql
    assert params[0] == "photog-1"
    assert params[1] == 50


def test_pg_select_notifications_empty_member(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no connect")))
    assert pf.pg_select_notifications_for_member("") == []
    assert pf.pg_select_notifications_for_member("  ") == []


def test_pg_select_notification_by_id_sql(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("title",),
        ("body",),
        ("type",),
        ("data",),
        ("read",),
        ("member_id",),
        ("created_at",),
    ]
    cur.fetchone.return_value = (
        "n-9",
        "Hello",
        "Body",
        "info",
        "{}",
        True,
        "photog-1",
        "2026-09-21T12:00:00+00:00",
    )
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    row = pf.pg_select_notification_by_id("n-9")
    assert row is not None
    assert row["id"] == "n-9"
    assert row["read"] is True
    sql = cur.execute.call_args[0][0]
    assert "WHERE id = %s" in sql


def test_pg_mark_notification_read_scoped(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    ok = pf.pg_mark_notification_read("n-1", "photog-1")
    assert ok is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "UPDATE public.notifications SET read = true" in sql
    assert "id = %s" in sql and "member_id = %s" in sql
    assert "n-1" in params and "photog-1" in params


def test_pg_mark_all_notifications_read(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 3
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    n = pf.pg_mark_all_notifications_read("photog-1")
    assert n == 3
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.notifications SET read = true" in sql
    assert "member_id = %s" in sql


def test_pg_upsert_push_token_insert(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    # first SELECT finds nothing
    cur.fetchone.return_value = None
    cur.description = [("id",)]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    out = pf.pg_upsert_push_token(
        {
            "member_id": "photog-1",
            "token": "ExponentPushToken[abc]",
            "platform": "expo",
            "active": True,
        }
    )
    assert out is not None
    assert out["token"] == "ExponentPushToken[abc]"
    assert out["member_id"] == "photog-1"
    assert out.get("id")
    # last execute should be INSERT
    calls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("INSERT INTO public.push_tokens" in s for s in calls)


def test_pg_upsert_push_token_update_existing(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [("id",)]
    cur.fetchone.return_value = ("tok-row-1",)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    out = pf.pg_upsert_push_token(
        {
            "member_id": "photog-2",
            "token": "ExponentPushToken[xyz]",
            "platform": "ios",
            "active": True,
        }
    )
    assert out is not None
    assert out["id"] == "tok-row-1"
    calls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("UPDATE public.push_tokens SET member_id" in s for s in calls)


def test_list_for_member_falls_back_on_402(monkeypatch):
    db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute = AsyncMock(side_effect=_QuotaErr())
    db.table.return_value = chain

    async def _gdb():
        return db

    monkeypatch.setattr(nrouter, "get_db", _gdb)
    monkeypatch.setattr(
        pf,
        "pg_select_notifications_for_member",
        lambda mid, limit=100: [
            {
                "id": "n-1",
                "title": "Assigned",
                "body": "12 Oak",
                "type": "job_assigned",
                "data": {"job_id": "j1"},
                "read": False,
                "member_id": mid,
                "created_at": "2026-09-21T12:00:00+00:00",
            }
        ],
    )

    items = asyncio.get_event_loop().run_until_complete(nrouter._list_for_member("photog-1"))
    assert len(items) == 1
    assert items[0]["title"] == "Assigned"
    assert items[0]["read"] is False
    assert items[0]["message"] == "12 Oak"


def test_store_push_token_falls_back_on_402_insert(monkeypatch):
    db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.insert.return_value = chain
    # lookup raises 402, then insert raises 402
    chain.execute = AsyncMock(side_effect=_QuotaErr())
    db.table.return_value = chain

    async def _gdb():
        return db

    monkeypatch.setattr(notif, "get_db", _gdb)
    monkeypatch.setattr(pf, "pg_select_push_token_by_token", lambda t: None)
    monkeypatch.setattr(
        pf,
        "pg_upsert_push_token",
        lambda row: {"id": row.get("id") or "new-id", "token": row["token"], "member_id": row["member_id"]},
    )

    out = asyncio.get_event_loop().run_until_complete(
        notif.store_push_token("photog-1", "ExponentPushToken[abc]", "expo")
    )
    assert out["registered"] is True
    assert out["id"]
