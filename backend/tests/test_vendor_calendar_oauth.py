"""Vendor calendar OAuth: Supabase-first Google, optional direct, Microsoft needs secret."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_oauth_env(monkeypatch):
    for key in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CALENDAR_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_OAUTH_REDIRECT_URI",
        "GOOGLE_SUPABASE_CALENDAR_REDIRECT",
        "MICROSOFT_OAUTH_CLIENT_ID",
        "AZURE_AD_CLIENT_ID",
        "MS_GRAPH_CLIENT_ID",
        "MICROSOFT_OAUTH_CLIENT_SECRET",
        "AZURE_AD_CLIENT_SECRET",
        "MS_GRAPH_CLIENT_SECRET",
        "MICROSOFT_OAUTH_REDIRECT_URI",
        "VENDOR_CALENDAR_OAUTH_REDIRECT",
        "VENDOR_CALENDAR_SUCCESS_REDIRECT",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_google_oauth_configured_supabase_without_secret(monkeypatch):
    from app.services.vendor_calendars import google_oauth_configured, google_connect_via

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    assert google_oauth_configured() is False
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon")
    assert google_oauth_configured() is True
    assert google_connect_via() == "supabase"


def test_google_oauth_configured_direct_secret_without_supabase(monkeypatch):
    from app.services.vendor_calendars import google_oauth_configured, google_connect_via, google_direct_oauth

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    assert google_direct_oauth() is True
    assert google_oauth_configured() is True
    assert google_connect_via() == "direct"


def test_google_prefers_supabase_when_both(monkeypatch):
    from app.services.vendor_calendars import google_connect_via

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    assert google_connect_via() == "supabase"


def test_microsoft_oauth_configured_requires_secret(monkeypatch):
    from app.services.vendor_calendars import microsoft_oauth_configured

    monkeypatch.setenv("AZURE_AD_CLIENT_ID", "ms-cid")
    assert microsoft_oauth_configured() is False
    monkeypatch.setenv("AZURE_AD_CLIENT_SECRET", "ms-sec")
    assert microsoft_oauth_configured() is True


def test_build_google_authorize_url_encodes_redirect(monkeypatch):
    from app.services.vendor_calendars import build_google_authorize_url
    from urllib.parse import urlparse, parse_qs

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://dispatch-api.brxs.ca/api/vendor-os/calendars/google/callback",
    )
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "test-state-secret")
    from app.services.vendor_calendars import read_oauth_state
    url = build_google_authorize_url("member-123")
    qs = parse_qs(urlparse(url).query)
    assert qs["state"] != ["member-123"]
    assert read_oauth_state(qs["state"][0], "google")["m"] == "member-123"
    assert qs["redirect_uri"] == [
        "https://dispatch-api.brxs.ca/api/vendor-os/calendars/google/callback"
    ]


def test_supabase_connect_payload(monkeypatch):
    from app.services.vendor_calendars import build_google_supabase_connect_payload

    body = build_google_supabase_connect_payload()
    assert body["mode"] == "supabase"
    assert body["scope"] == "https://www.googleapis.com/auth/calendar.freebusy"
    assert body["redirect_uri"].endswith("/auth/google-calendar")


@pytest.mark.asyncio
async def test_exchange_google_code_success(monkeypatch):
    from app.services.vendor_calendars import exchange_google_code

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://example.com/cb")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, headers=None, json=None):
            return FakeResp()

        async def get(self, *a, **k):
            return FakeResp()

    with patch("app.services.vendor_calendars.httpx.AsyncClient", FakeClient):
        tokens = await exchange_google_code("abc")
    assert tokens["access_token"] == "at"


@pytest.mark.asyncio
async def test_exchange_google_code_fail(monkeypatch):
    from app.services.vendor_calendars import exchange_google_code

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://example.com/cb")

    class FakeResp:
        status_code = 400

        def json(self):
            return {"error": "invalid_grant", "error_description": "Bad code"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, headers=None, json=None):
            return FakeResp()

    with patch("app.services.vendor_calendars.httpx.AsyncClient", FakeClient):
        with pytest.raises(RuntimeError, match="Bad code|invalid_grant"):
            await exchange_google_code("bad")


@pytest.mark.asyncio
async def test_google_calendar_ping_freebusy():
    from app.services.vendor_calendars import google_calendar_ping

    class FakeResp:
        status_code = 200

        def json(self):
            return {"calendars": {"primary": {"busy": []}}}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

        async def get(self, *a, **k):
            return FakeResp()

    with patch("app.services.vendor_calendars.httpx.AsyncClient", FakeClient):
        ping = await google_calendar_ping("tok")
    assert ping["ok"] is True
    assert ping["via"] == "freebusy"


@pytest.mark.asyncio
async def test_google_callback_requires_direct_secret(monkeypatch):
    from app.routers import vendor_os as vos

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon")
    monkeypatch.setenv("VENDOR_CALENDAR_SUCCESS_REDIRECT", "https://go.brxs.ca/availability")
    # No secret → direct callback rejects (use supabase complete-session instead)
    resp = await vos.google_calendar_oauth_callback(code="c", state="mem-1")
    assert resp.status_code == 302
    assert "calendar_error=" in resp.headers["location"]


@pytest.mark.asyncio
async def test_google_callback_redirects_on_exchange_fail(monkeypatch):
    from app.routers import vendor_os as vos

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("VENDOR_CALENDAR_SUCCESS_REDIRECT", "https://go.brxs.ca/availability")

    with patch(
        "app.services.vendor_calendars.exchange_google_code",
        AsyncMock(side_effect=RuntimeError("Bad code")),
    ):
        resp = await vos.google_calendar_oauth_callback(code="c", state="mem-1")

    assert resp.status_code == 302
    assert "calendar_error=" in resp.headers["location"]


def test_calendar_success_redirect_default():
    from app.services.vendor_calendars import calendar_success_redirect_base

    assert calendar_success_redirect_base() == "https://go.brxs.ca/availability"


@pytest.mark.asyncio
async def test_persist_google_oauth_requires_ping_and_refresh():
    """Connected is only returned after refresh token + Calendar ping. Token dump is not enough."""
    from app.services.vendor_calendars import persist_google_oauth_if_verified

    db = MagicMock()

    no_refresh = await persist_google_oauth_if_verified(
        db,
        member_id="m1",
        vendor_id="default_vendor",
        access_token="atok",
        refresh_token="",
    )
    assert no_refresh["connected"] is False
    assert no_refresh["status"] == "noscope"
    db.table.assert_not_called()

    with patch(
        "app.services.vendor_calendars.google_calendar_ping",
        AsyncMock(return_value={"ok": False, "status": 403, "via": "freebusy"}),
    ):
        ping_fail = await persist_google_oauth_if_verified(
            db,
            member_id="m1",
            vendor_id="default_vendor",
            access_token="atok",
            refresh_token="rtok",
        )
    assert ping_fail["connected"] is False
    assert ping_fail["status"] == "noscope"
    db.table.assert_not_called()

    upserted = []

    async def fake_upsert(*_a, **kw):
        upserted.append(kw)
        return {
            "member_id": kw["member_id"],
            "provider": "google",
            "access_token": kw["token_payload"]["access_token"],
            "refresh_token": kw["token_payload"]["refresh_token"],
        }

    with patch(
        "app.services.vendor_calendars.google_calendar_ping",
        AsyncMock(return_value={"ok": True, "status": 200, "via": "freebusy"}),
    ), patch(
        "app.services.vendor_calendars.upsert_oauth_connection",
        side_effect=fake_upsert,
    ):
        ok = await persist_google_oauth_if_verified(
            db,
            member_id="m1",
            vendor_id="default_vendor",
            access_token="atok",
            refresh_token="rtok",
        )
    assert ok["connected"] is True
    assert ok["ping"] == "freebusy"
    assert len(upserted) == 1


@pytest.mark.asyncio
async def test_google_connect_payload_does_not_fake_connected(monkeypatch):
    """POST /calendars/google/connect with tokens still requires ping — never fake Connected."""
    from app.routers import vendor_os as vos

    actor = "photog-google-1"
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": actor, "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=MagicMock()))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")

    with patch(
        "app.services.vendor_calendars.google_calendar_ping",
        AsyncMock(return_value={"ok": False, "status": 500, "via": "freebusy"}),
    ):
        out = await vos.connect_vendor_calendar(
            "google",
            payload={"access_token": "atok", "refresh_token": "rtok"},
            authorization="Bearer x",
            x_user_id=None,
        )
    assert out["connected"] is False
    assert out["status"] == "error"
