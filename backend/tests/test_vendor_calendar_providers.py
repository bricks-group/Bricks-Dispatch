"""Per-provider busy/free mocks + multi-provider OR for auto-assign."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vendor_calendars import (
    UI_CONNECT_PROVIDERS,
    _apple_caldav_busy,
    _ics_busy,
    _microsoft_schedule,
    persist_apple_caldav_if_verified,
    persist_ics_if_verified,
    persist_microsoft_oauth_if_verified,
    vendor_is_busy,
)

START = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)


class FakeResp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_microsoft_calendarview_busy_and_free():
    class Busy(FakeClient):
        async def get(self, url, headers=None, params=None):
            return FakeResp(200, {"value": [{"id": "1", "showAs": "busy"}]})

        async def post(self, *a, **k):
            raise AssertionError("getSchedule should not run when calendarView succeeds")

    class Free(FakeClient):
        async def get(self, url, headers=None, params=None):
            return FakeResp(200, {"value": [{"id": "1", "showAs": "free"}]})

        async def post(self, *a, **k):
            raise AssertionError("getSchedule should not run when calendarView succeeds")

    with patch("app.services.vendor_calendars.httpx.AsyncClient", Busy):
        assert await _microsoft_schedule("tok", START, END, "m@test.com") is True
        member = {"id": "m1", "email": "m@test.com", "microsoft_calendar_token": "tok"}
        assert await vendor_is_busy(member, START, END) is True

    with patch("app.services.vendor_calendars.httpx.AsyncClient", Free):
        assert await _microsoft_schedule("tok", START, END, None) is False
        member = {"id": "m1", "microsoft_calendar_token": "tok"}
        assert await vendor_is_busy(member, START, END) is False


@pytest.mark.asyncio
async def test_apple_caldav_busy_and_free():
    class Busy(FakeClient):
        async def request(self, method, url, content=None, headers=None, auth=None):
            assert method == "REPORT"
            return FakeResp(207, text="BEGIN:VEVENT\nDTSTART:20260910T140000Z\nEND:VEVENT")

    class Free(FakeClient):
        async def request(self, method, url, content=None, headers=None, auth=None):
            return FakeResp(207, text="<D:multistatus xmlns:D='DAV:'></D:multistatus>")

    with patch("app.services.calendar_url_guard.guarded_request", AsyncMock(return_value=(207, "BEGIN:VEVENT\nDTSTART:20260910T140000Z\nEND:VEVENT"))):
        assert await _apple_caldav_busy("https://caldav.icloud.com/", "pw", "u", START, END) is True
        member = {
            "id": "m1",
            "email": "u@icloud.com",
            "apple_caldav_url": "https://caldav.icloud.com/",
            "apple_caldav_password": "pw",
        }
        assert await vendor_is_busy(member, START, END) is True

    with patch("app.services.calendar_url_guard.guarded_request", AsyncMock(return_value=(207, "<D:multistatus/>"))):
        assert await _apple_caldav_busy("https://caldav.icloud.com/", "pw", "u", START, END) is False
        member = {
            "id": "m1",
            "apple_caldav_url": "https://caldav.icloud.com/",
            "apple_caldav_password": "pw",
        }
        assert await vendor_is_busy(member, START, END) is False


@pytest.mark.asyncio
async def test_ics_busy_and_free():
    busy_ics = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
        "DTSTART:20260910T140000Z\nDTEND:20260910T143000Z\n"
        "END:VEVENT\nEND:VCALENDAR"
    )
    free_ics = "BEGIN:VCALENDAR\nEND:VCALENDAR"

    class Busy(FakeClient):
        async def get(self, url, *a, **k):
            return FakeResp(200, text=busy_ics)

    class Free(FakeClient):
        async def get(self, url, *a, **k):
            return FakeResp(200, text=free_ics)

    with patch("app.services.calendar_url_guard.guarded_request", AsyncMock(return_value=(200, busy_ics))):
        assert await _ics_busy("https://example.com/cal.ics", START, END) is True
        member = {"id": "m1", "calendar_ics_url": "https://example.com/cal.ics"}
        assert await vendor_is_busy(member, START, END) is True

    with patch("app.services.calendar_url_guard.guarded_request", AsyncMock(return_value=(200, free_ics))):
        assert await _ics_busy("https://example.com/cal.ics", START, END) is False
        member = {"id": "m1", "ics_url": "https://example.com/cal.ics"}
        assert await vendor_is_busy(member, START, END) is False


@pytest.mark.asyncio
async def test_vendor_is_busy_or_across_providers():
    """ANY connected calendar busy → True. All free → False. None connected → None."""
    member = {
        "id": "m1",
        "email": "m@test.com",
        "google_calendar_token": "gtok",
        "microsoft_calendar_token": "mtok",
        "apple_caldav_url": "https://caldav.icloud.com/",
        "apple_caldav_password": "pw",
    }

    async def google_free_ms_busy(conn, _m, _s, _e, db=None):
        if conn.get("provider") == "microsoft":
            return True
        return False

    async def all_free(conn, _m, _s, _e, db=None):
        return False

    with patch("app.services.vendor_calendars._provider_busy", side_effect=google_free_ms_busy):
        assert await vendor_is_busy(member, START, END) is True

    with patch("app.services.vendor_calendars._provider_busy", side_effect=all_free):
        assert await vendor_is_busy(member, START, END) is False

    assert await vendor_is_busy({"id": "none"}, START, END) is None


@pytest.mark.asyncio
async def test_persist_microsoft_requires_graph_ping():
    db = MagicMock()
    out = await persist_microsoft_oauth_if_verified(
        db, member_id="m1", vendor_id="v", access_token="atok", refresh_token=""
    )
    assert out["connected"] is False
    assert out["provider"] == "microsoft"

    with patch(
        "app.services.vendor_calendars.microsoft_calendar_ping",
        AsyncMock(return_value={"ok": False, "status": 403, "via": "graph"}),
    ):
        ping_fail = await persist_microsoft_oauth_if_verified(
            db, member_id="m1", vendor_id="v", access_token="atok", refresh_token="rtok"
        )
    assert ping_fail["connected"] is False
    db.table.assert_not_called()

    upserted = []

    async def fake_upsert(*_a, **kw):
        upserted.append(kw)
        return {
            "member_id": kw["member_id"],
            "provider": "microsoft",
            "access_token": "atok",
            "refresh_token": "rtok",
        }

    with patch(
        "app.services.vendor_calendars.microsoft_calendar_ping",
        AsyncMock(return_value={"ok": True, "status": 200, "via": "graph"}),
    ), patch("app.services.vendor_calendars.upsert_oauth_connection", side_effect=fake_upsert):
        ok = await persist_microsoft_oauth_if_verified(
            db, member_id="m1", vendor_id="v", access_token="atok", refresh_token="rtok"
        )
    assert ok["connected"] is True
    assert ok["ping"] == "graph"
    assert len(upserted) == 1


@pytest.mark.asyncio
async def test_persist_apple_requires_caldav_ping():
    db = MagicMock()
    missing = await persist_apple_caldav_if_verified(
        db, member_id="m1", vendor_id="v", caldav_url="", password="", username="u"
    )
    assert missing["connected"] is False
    assert missing["status"] == "needs_app_password"

    with patch(
        "app.services.vendor_calendars.apple_caldav_ping",
        AsyncMock(return_value={"ok": False, "status": 401, "via": "caldav"}),
    ):
        bad = await persist_apple_caldav_if_verified(
            db,
            member_id="m1",
            vendor_id="v",
            caldav_url="https://caldav.icloud.com/",
            password="pw",
            username="u",
        )
    assert bad["connected"] is False
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_persist_ics_requires_feed_ping():
    db = MagicMock()
    with patch(
        "app.services.vendor_calendars.ics_feed_ping",
        AsyncMock(return_value={"ok": False, "status": 404, "via": "ics"}),
    ):
        bad = await persist_ics_if_verified(
            db, member_id="m1", vendor_id="v", ics_url="https://example.com/no.ics"
        )
    assert bad["connected"] is False
    db.table.assert_not_called()


def test_ui_offers_all_four_providers():
    assert UI_CONNECT_PROVIDERS == ("google", "microsoft", "apple", "ics")


@pytest.mark.asyncio
async def test_microsoft_connect_starts_oauth(monkeypatch):
    from app.routers import vendor_os as vos

    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "unit-test-only")
    actor = "photog-ms-1"
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor", "email": "a@ex.com"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=MagicMock()))
    monkeypatch.setenv("AZURE_AD_CLIENT_ID", "ms-cid")
    monkeypatch.setenv("AZURE_AD_CLIENT_SECRET", "ms-sec")
    monkeypatch.setenv(
        "MICROSOFT_OAUTH_REDIRECT_URI",
        "https://dispatch-api.brxs.ca/api/vendor-os/calendars/microsoft/callback",
    )

    out = await vos.connect_vendor_calendar("microsoft", payload={}, authorization="Bearer x", x_user_id=None)
    assert out["connected"] is False
    assert out["status"] == "oauth_ready"
    assert "/calendars/microsoft/start?state=" in (out.get("oauth_url") or "")


@pytest.mark.asyncio
async def test_microsoft_connect_needs_env_without_secret(monkeypatch):
    from app.routers import vendor_os as vos

    actor = "photog-ms-2"
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=MagicMock()))
    monkeypatch.setenv("AZURE_AD_CLIENT_ID", "ms-cid")
    for key in ("AZURE_AD_CLIENT_SECRET", "MICROSOFT_OAUTH_CLIENT_SECRET", "MS_GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    # no secret → honest needs_env, not a fake Connected and not a silent hide
    out = await vos.connect_vendor_calendar("microsoft", payload={}, authorization="Bearer x", x_user_id=None)
    assert out["connected"] is False
    assert out["status"] == "needs_env"
    assert not out.get("oauth_url")
