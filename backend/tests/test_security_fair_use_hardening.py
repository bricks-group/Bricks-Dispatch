"""Security hardening: fair-use auth gate, no pre-confirmed emails, no
email-based member adoption, rate-limit key, HMAC fail-closed, blob-get
active-content headers."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.routers import auth as auth_router
from app.routers import capture as capture_router
from app.services import storage as st
from app.shared import pg_fallback as pf
from app.shared import rate_limit as rl


# ---------------------------------------------------------------- 1. gate

def _boom(*_a, **_k):
    raise AssertionError("must not touch Postgres")


def test_fair_use_minter_and_routes_are_deleted():
    # Sentinel 2026-11-14: the HS256 minter (7-day, no session_id) and its
    # /vendor-os/auth/fair-use-login and /fair-use-signup routes are gone.
    from app.shared import auth as auth_mod

    assert not hasattr(auth_mod, "mint_supabase_access_token")
    assert not hasattr(auth_mod, "decode_supabase_user_from_jwt")
    assert not hasattr(auth_router, "vendor_os_fair_use_login")
    assert not hasattr(auth_router, "vendor_os_fair_use_signup")
    assert not hasattr(pf, "pg_create_auth_user")
    assert not hasattr(pf, "pg_verify_auth_password")
    paths = {getattr(r, "path", "") for r in auth_router.router.routes}
    assert "/api/vendor-os/auth/fair-use-login" not in paths
    assert "/api/vendor-os/auth/fair-use-signup" not in paths


# ------------------------------------------------- 3. no adoption by email

def test_pg_upsert_vendor_member_does_not_adopt_by_email(monkeypatch):
    calls = []

    def fake_select(actor_id="", email=""):
        calls.append((actor_id, email))
        if email:
            # A victim row that only matches by email must never be returned.
            return {"id": "victim-member", "email": email}
        return None

    inserted = {"id": "new-uid", "email": "victim@example.com"}

    class _Cur:
        description = [("id",), ("email",)]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return (inserted["id"], inserted["email"])

    conn = MagicMock()
    conn.cursor.return_value = _Cur()
    monkeypatch.setattr(pf, "pg_select_vendor_member", fake_select)
    monkeypatch.setattr(pf, "_pg_connect", lambda: conn)
    out = pf.pg_upsert_vendor_member("new-uid", "victim@example.com", name="x")
    assert out["id"] == "new-uid"
    assert all(e == "" for _a, e in calls), calls


def test_pg_upsert_vendor_member_adopts_by_id(monkeypatch):
    own = {"id": "m1", "auth_user_id": "uid-1", "email": "a@b.c"}
    monkeypatch.setattr(pf, "pg_select_vendor_member", lambda actor_id="", email="": own if actor_id == "uid-1" and not email else None)
    monkeypatch.setattr(pf, "_pg_connect", _boom)
    assert pf.pg_upsert_vendor_member("uid-1", "a@b.c") is own


# ------------------------------------------------------- 4a. rate-limit key

def _req(headers: dict, host="10.0.0.9"):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()} | headers, client=SimpleNamespace(host=host))


def test_rate_limit_ignores_spoofed_x_forwarded_for():
    r = _req({"X-Forwarded-For": "1.2.3.4, 9.9.9.9", "x-real-ip": "203.0.113.7"})
    assert rl._rate_limit_key(r) == "203.0.113.7"
    r2 = _req({"X-Forwarded-For": "1.2.3.4"})
    assert rl._rate_limit_key(r2) == "10.0.0.9"
    r3 = SimpleNamespace(headers={}, client=None)
    assert rl._rate_limit_key(r3) == "127.0.0.1"


# ----------------------------------------------------- 4b. HMAC fail closed

def test_hmac_secret_fails_closed_without_env(monkeypatch):
    for k in ("CAPTURE_PUT_SECRET", "SUPABASE_JWT_SECRET", "BLOB_READ_WRITE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(st, "_blob_token", lambda: "")
    with pytest.raises(RuntimeError):
        st._hmac_secret()
    exp = int(time.time()) + 600
    import hashlib
    import hmac as _h

    forged = _h.new(b"bricks-capture-dev-secret", f"GET:a/b.html:{exp}".encode(), hashlib.sha256).hexdigest()
    assert st.verify_capture_hmac("GET", "a/b.html", exp, forged) is False


def test_hmac_secret_uses_capture_put_secret(monkeypatch):
    monkeypatch.setenv("CAPTURE_PUT_SECRET", "real-secret")
    assert st._hmac_secret() == b"real-secret"


# ------------------------------------------- 4c. blob-get active content

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,attach",
    [
        ("tours/j/tour.html", True),
        ("tours/j/tour.htm", True),
        ("x/plan.svg", True),
        ("x/data.xml", True),
        ("x/plan.png", False),
        ("x/a.jpg", False),
        ("x/a.json", False),
    ],
)
async def test_blob_get_active_content_is_attachment(monkeypatch, path, attach):
    async def fake_get(p):
        return b"<html><script>alert(1)</script></html>"

    monkeypatch.setattr(capture_router, "verify_capture_hmac", lambda *a: True)
    monkeypatch.setattr(capture_router, "_blob_get", fake_get)
    resp = await capture_router.blob_get_proxy(path, exp=int(time.time()) + 60, sig="x")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    cd = resp.headers.get("content-disposition") or ""
    if attach:
        assert cd.startswith("attachment")
    else:
        assert "attachment" not in cd
