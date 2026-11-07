"""Launch security audit 2026-11-07: signed/bound/single-use calendar OAuth state + SSRF guard."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import calendar_oauth_state as cos
from app.services import calendar_url_guard as guard


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "unit-test-only")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://dispatch-api.brxs.ca/api/vendor-os/calendars/google/callback")
    monkeypatch.setenv("VENDOR_CALENDAR_SUCCESS_REDIRECT", "https://go.brxs.ca/availability")


class NonceDb:
    """Primary-key semantics: second insert of the same nonce raises."""
    def __init__(self, fail=False):
        self.keys, self.fail, self.lock = set(), fail, asyncio.Lock()

    def table(self, name):
        db = self

        class Q:
            def __init__(self): self.row = None
            def insert(self, row): self.row = row; return self
            def select(self, *a): return self
            def eq(self, *a): return self
            def maybe_single(self): return self
            async def execute(self):
                if name == "vendor_members":
                    return SimpleNamespace(data={"id": "member-a", "vendor_id": "v1"})
                await asyncio.sleep(0)
                if db.fail:
                    raise RuntimeError("simulated outage")
                async with db.lock:
                    k = self.row["nonce_sha256"]
                    if k in db.keys:
                        raise RuntimeError("duplicate key value violates unique constraint")
                    db.keys.add(k)
                return SimpleNamespace(data=[self.row])
        return Q()


def _nonce(state):
    return cos.read_state(state, "google")["nonce"]


def test_roundtrip_and_tamper():
    s = cos.mint_state("member-a", "google")
    assert cos.read_state(s, "google")["member_id"] == "member-a"
    raw, sig = s.split(".")
    forged = cos._b64e(cos._b64d(raw).replace(b"member-a", b"member-b")) + "." + sig
    for bad in (forged, "member-a", "", s + "x"):
        with pytest.raises(cos.OAuthStateError):
            cos.read_state(bad, "google")
    with pytest.raises(cos.OAuthStateError):
        cos.read_state(s, "microsoft")


def test_expiry():
    s = cos.mint_state("member-a", "google", now=1000)
    with pytest.raises(cos.OAuthStateError):
        cos.read_state(s, "google", now=1000 + cos.STATE_TTL_SECONDS + 1)


def test_missing_secret_fails_closed(monkeypatch):
    for k in ("CALENDAR_OAUTH_STATE_SECRET", "CALENDAR_TOKEN_ENCRYPTION_KEY", "SUPABASE_JWT_SECRET", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(cos.OAuthStateError):
        cos.mint_state("member-a", "google")


def test_consume_requires_cookie_binding_and_is_single_use():
    db = NonceDb()
    s = cos.mint_state("member-a", "google")
    with pytest.raises(cos.OAuthStateError):
        asyncio.run(cos.consume_state(s, "google", cookie_nonce=None, db=db))
    with pytest.raises(cos.OAuthStateError):
        asyncio.run(cos.consume_state(s, "google", cookie_nonce="other", db=db))
    assert asyncio.run(cos.consume_state(s, "google", cookie_nonce=_nonce(s), db=db)) == "member-a"
    with pytest.raises(cos.OAuthStateError):
        asyncio.run(cos.consume_state(s, "google", cookie_nonce=_nonce(s), db=db))


def test_concurrent_consume_only_one_wins():
    db = NonceDb()
    s = cos.mint_state("member-a", "google")

    async def both():
        return await asyncio.gather(
            cos.consume_state(s, "google", cookie_nonce=_nonce(s), db=db),
            cos.consume_state(s, "google", cookie_nonce=_nonce(s), db=db),
            return_exceptions=True,
        )
    out = asyncio.run(both())
    assert sum(1 for x in out if x == "member-a") == 1
    assert sum(1 for x in out if isinstance(x, cos.OAuthStateError)) == 1


def test_db_outage_fails_closed():
    s = cos.mint_state("member-a", "google")
    with pytest.raises(cos.OAuthStateError):
        asyncio.run(cos.consume_state(s, "google", cookie_nonce=_nonce(s), db=NonceDb(fail=True)))


def _req(cookies):
    return SimpleNamespace(cookies=cookies)


def test_callback_rejects_unsigned_member_id_state_before_token_exchange():
    from app.routers import vendor_os
    exch = AsyncMock(return_value={"access_token": "t"})
    with patch("app.services.vendor_calendars.exchange_google_code", exch), \
         patch.object(vendor_os, "get_db", AsyncMock(return_value=NonceDb())):
        r = asyncio.run(vendor_os.google_calendar_oauth_callback(_req({}), code="c", state="member-a"))
    assert r.status_code == 302 and "calendar_error" in r.headers["location"]
    exch.assert_not_called()


def test_callback_accepts_bound_state_and_rejects_replay():
    from app.routers import vendor_os
    db = NonceDb()
    s = cos.mint_state("member-a", "google")
    exch = AsyncMock(return_value={"access_token": "t", "refresh_token": "r", "expires_in": 3600})
    upsert = AsyncMock()
    with patch("app.services.vendor_calendars.exchange_google_code", exch), \
         patch("app.services.vendor_calendars.google_calendar_ping", AsyncMock(return_value={"ok": True})), \
         patch("app.services.vendor_calendars.upsert_oauth_connection", upsert), \
         patch.object(vendor_os, "get_db", AsyncMock(return_value=db)):
        ok = asyncio.run(vendor_os.google_calendar_oauth_callback(_req({cos.COOKIE_NAME: _nonce(s)}), code="c", state=s))
        again = asyncio.run(vendor_os.google_calendar_oauth_callback(_req({cos.COOKIE_NAME: _nonce(s)}), code="c2", state=s))
    assert "calendar_connected=google" in ok.headers["location"]
    assert upsert.await_args.kwargs["member_id"] == "member-a"
    assert "calendar_error" in again.headers["location"]
    assert exch.await_count == 1


def test_start_sets_httponly_lax_cookie_and_redirects_to_google():
    from app.routers import vendor_os
    s = cos.mint_state("member-a", "google")
    r = asyncio.run(vendor_os.google_calendar_oauth_start(state=s))
    assert r.status_code == 302 and r.headers["location"].startswith("https://accounts.google.com/")
    c = r.headers["set-cookie"].lower()
    assert cos.COOKIE_NAME in c and "httponly" in c and "secure" in c and "samesite=lax" in c
    bad = asyncio.run(vendor_os.google_calendar_oauth_start(state="member-a"))
    assert "calendar_error" in bad.headers["location"] and "set-cookie" not in bad.headers


def test_start_url_derived_from_callback():
    from app.services.vendor_calendars import build_google_start_url
    u = build_google_start_url("abc")
    assert u == "https://dispatch-api.brxs.ca/api/vendor-os/calendars/google/start?state=abc"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.16.1.1", "192.168.1.1", "169.254.169.254",
                                "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "::ffff:127.0.0.1", "fc00::1", "224.0.0.1"])
def test_private_addresses_rejected(ip):
    with pytest.raises(guard.UnsafeCalendarURL):
        guard.vet_url("https://cal.example.com/x.ics", resolver=lambda *_: [ip])


def test_mixed_resolution_rejected():
    with pytest.raises(guard.UnsafeCalendarURL):
        guard.vet_url("https://cal.example.com/x.ics", resolver=lambda *_: ["93.184.216.34", "10.0.0.1"])


@pytest.mark.parametrize("url", ["http://cal.example.com/a.ics", "ftp://x/a", "file:///etc/passwd",
                                 "https://u:p@cal.example.com/a.ics", "https://cal.example.com:8443/a.ics", "https:///a"])
def test_bad_urls_rejected(url):
    with pytest.raises(guard.UnsafeCalendarURL):
        guard.vet_url(url, resolver=lambda *_: ["93.184.216.34"])


def test_webcal_normalized_and_public_ok():
    norm, host, ip = guard.vet_url("webcal://p01-calendars.icloud.com/published/2/abc", resolver=lambda *_: ["17.250.1.1"])
    assert norm.startswith("https://p01-calendars.icloud.com/") and ip == "17.250.1.1"


def test_ics_busy_uses_guard_and_fails_soft(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.services.vendor_calendars import _ics_busy
    monkeypatch.setattr(guard, "_default_resolver", lambda *_: ["127.0.0.1"])
    now = datetime.now(timezone.utc)
    assert asyncio.run(_ics_busy("https://evil.example.com/a.ics", now, now + timedelta(hours=1))) is None


@pytest.mark.parametrize("url", ["https://127.0.0.1/a.ics", "https://[::1]/a", "https://169.254.169.254/latest",
                                 "https://localhost/a.ics", "http://cal.example.com/a.ics"])
def test_save_time_rejects_obvious_private(url):
    with pytest.raises(guard.UnsafeCalendarURL):
        guard.vet_url_for_save(url)


def test_connect_ics_rejects_private_url(monkeypatch):
    from app.routers import vendor_os as vos
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="m1"))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value={"id": "m1", "vendor_id": "v"}))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=NonceDb()))
    out = asyncio.run(vos.connect_vendor_calendar("ics", payload={"ics_url": "https://169.254.169.254/latest/meta-data"},
                                                  authorization="Bearer x", x_user_id=None))
    assert out["status"] == "invalid_url" and out["connected"] is False
