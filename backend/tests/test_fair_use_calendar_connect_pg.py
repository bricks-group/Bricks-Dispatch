"""Fair-Use: calendar Connect/Disconnect via Postgres on PostgREST 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routers import vendor_os as vos
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


def test_pg_upsert_vendor_calendar_connection_sql(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("member_id",),
        ("vendor_id",),
        ("provider",),
        ("access_token",),
        ("refresh_token",),
        ("token_expires_at",),
        ("caldav_url",),
        ("caldav_username",),
        ("caldav_password",),
        ("ics_url",),
        ("created_at",),
        ("updated_at",),
    ]
    cur.fetchall.return_value = [
        (
            "conn-1",
            "photog-1",
            "default_vendor",
            "google",
            "enc-access",
            "enc-refresh",
            "2026-09-21T13:00:00+00:00",
            None,
            None,
            None,
            None,
            "2026-09-21T12:00:00+00:00",
            "2026-09-21T12:00:00+00:00",
        ),
    ]
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    out = pf.pg_upsert_vendor_calendar_connection(
        {
            "member_id": "photog-1",
            "vendor_id": "default_vendor",
            "provider": "google",
            "access_token": "enc-access",
            "refresh_token": "enc-refresh",
            "token_expires_at": "2026-09-21T13:00:00+00:00",
            "evil_col": "dropme",
        }
    )
    assert out is not None
    assert out["provider"] == "google"
    assert out["access_token"] == "enc-access"
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "INSERT INTO public.vendor_calendar_connections" in sql
    assert "ON CONFLICT (member_id, provider) DO UPDATE SET" in sql
    assert "access_token = EXCLUDED.access_token" in sql
    assert "updated_at = EXCLUDED.updated_at" in sql
    assert "created_at = EXCLUDED.created_at" not in sql
    assert "evil_col" not in sql
    assert "photog-1" in params
    assert "google" in params


def test_pg_upsert_vendor_calendar_connection_requires_keys(monkeypatch):
    called = []

    def boom():
        called.append(1)
        return None

    monkeypatch.setattr(pf, "_pg_connect", boom)
    assert pf.pg_upsert_vendor_calendar_connection({}) is None
    assert pf.pg_upsert_vendor_calendar_connection({"member_id": "x"}) is None
    assert pf.pg_upsert_vendor_calendar_connection({"provider": "google"}) is None
    assert called == []


def test_pg_delete_vendor_calendar_connection_sql(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    assert pf.pg_delete_vendor_calendar_connection("photog-1", "apple") is True
    sql = cur.execute.call_args[0][0]
    params = cur.execute.call_args[0][1]
    assert "DELETE FROM public.vendor_calendar_connections" in sql
    assert "member_id = %s" in sql
    assert "provider = %s" in sql
    assert params == ("photog-1", "apple")


def test_pg_delete_vendor_calendar_connection_requires_keys(monkeypatch):
    monkeypatch.setattr(pf, "_pg_connect", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    assert pf.pg_delete_vendor_calendar_connection("", "google") is False
    assert pf.pg_delete_vendor_calendar_connection("m", "") is False


def test_upsert_oauth_connection_falls_back_on_402(monkeypatch):
    upsert_calls = []

    class Chain:
        def upsert(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(
        "app.services.calendar_token_crypto.encrypt_connection_secrets",
        lambda row: {**row, "access_token": "enc:" + str(row.get("access_token") or "")},
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_upsert_vendor_calendar_connection",
        lambda row: upsert_calls.append(dict(row)) or {**row, "id": "pg-1"},
    )

    out = asyncio.get_event_loop().run_until_complete(
        vc.upsert_oauth_connection(
            db,
            member_id="photog-oauth",
            vendor_id="default_vendor",
            provider="google",
            token_payload={
                "access_token": "atok",
                "refresh_token": "rtok",
                "expires_in": 3600,
            },
        )
    )
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["member_id"] == "photog-oauth"
    assert upsert_calls[0]["provider"] == "google"
    assert out["access_token"].startswith("enc:")


def test_upsert_oauth_connection_raises_when_pg_fails(monkeypatch):
    class Chain:
        def upsert(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(
        "app.services.calendar_token_crypto.encrypt_connection_secrets",
        lambda row: dict(row),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_upsert_vendor_calendar_connection",
        lambda row: None,
    )

    with pytest.raises(RuntimeError, match="Fair-Use"):
        asyncio.get_event_loop().run_until_complete(
            vc.upsert_oauth_connection(
                db,
                member_id="photog-oauth",
                vendor_id="default_vendor",
                provider="google",
                token_payload={"access_token": "atok", "refresh_token": "rtok"},
            )
        )


def test_connect_apple_falls_back_on_402(monkeypatch):
    actor = "photog-apple-1"
    upsert_calls = []

    class Chain:
        def upsert(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor", "email": "a@ex.com"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        "app.services.calendar_token_crypto.encrypt_connection_secrets",
        lambda row: dict(row),
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_upsert_vendor_calendar_connection",
        lambda row: upsert_calls.append(dict(row)) or row,
    )
    monkeypatch.setattr(
        "app.services.vendor_calendars.apple_caldav_ping",
        AsyncMock(return_value={"ok": True, "status": 207, "via": "caldav"}),
    )

    out = asyncio.get_event_loop().run_until_complete(
        vos.connect_vendor_calendar(
            "apple",
            payload={
                "caldav_url": "https://caldav.icloud.com/",
                "app_password": "xxxx-yyyy",
                "username": "a@ex.com",
            },
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["provider"] == "apple"
    assert upsert_calls[0]["caldav_url"] == "https://caldav.icloud.com/"
    assert out.get("connected") is True
    assert out.get("provider") == "apple"


def test_connect_ics_falls_back_on_402(monkeypatch):
    actor = "photog-ics-1"
    upsert_calls = []

    class Chain:
        def upsert(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_upsert_vendor_calendar_connection",
        lambda row: upsert_calls.append(dict(row)) or row,
    )
    monkeypatch.setattr(
        "app.services.vendor_calendars.ics_feed_ping",
        AsyncMock(return_value={"ok": True, "status": 200, "via": "ics"}),
    )

    out = asyncio.get_event_loop().run_until_complete(
        vos.connect_vendor_calendar(
            "ics",
            payload={"ics_url": "https://example.com/cal.ics"},
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["ics_url"] == "https://example.com/cal.ics"
    assert out.get("connected") is True


def test_disconnect_falls_back_on_402(monkeypatch):
    actor = "photog-disc-1"
    delete_calls = []

    class Chain:
        def delete(self):
            return self

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_delete_vendor_calendar_connection",
        lambda mid, prov: delete_calls.append((mid, prov)) or True,
    )

    out = asyncio.get_event_loop().run_until_complete(
        vos.disconnect_vendor_calendar(
            "google", authorization="Bearer x", x_user_id=None
        )
    )
    assert delete_calls == [(actor, "google")]
    assert out["connected"] is False
    assert out["status"] == "not_connected"


def test_disconnect_errors_when_pg_fails(monkeypatch):
    actor = "photog-disc-2"

    class Chain:
        def delete(self):
            return self

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    db = MagicMock()
    db.table.return_value = Chain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_delete_vendor_calendar_connection",
        lambda mid, prov: False,
    )

    out = asyncio.get_event_loop().run_until_complete(
        vos.disconnect_vendor_calendar(
            "ics", authorization="Bearer x", x_user_id=None
        )
    )
    assert out["status"] == "error"
    assert "Fair-Use" in out["detail"]
