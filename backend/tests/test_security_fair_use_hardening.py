"""Security hardening: fair-use auth gate, no pre-confirmed emails, no
email-based member adoption, rate-limit key, HMAC fail-closed, blob-get
active-content headers."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.routers import auth as auth_router
from app.routers import capture as capture_router
from app.services import storage as st
from app.shared import pg_fallback as pf
from app.shared import rate_limit as rl


# ---------------------------------------------------------------- 1. gate

def _boom(*_a, **_k):
    raise AssertionError("fair-use path must not touch Postgres when disabled")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "false", "1", "TRUE", "True", "yes", " true"])
async def test_fair_use_signup_and_login_disabled_unless_literal_true(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("FAIR_USE_AUTH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("FAIR_USE_AUTH_ENABLED", value)
    monkeypatch.setattr(auth_router, "_pg_connect", _boom)
    monkeypatch.setattr(auth_router, "pg_create_auth_user", _boom)
    monkeypatch.setattr(auth_router, "pg_upsert_vendor_member", _boom)
    monkeypatch.setattr(auth_router, "pg_verify_auth_password", _boom)
    monkeypatch.setattr(auth_router, "mint_supabase_access_token", _boom)

    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_signup(
            {"email": "probe+sec@example.invalid", "password": "longenough1"}
        )
    assert ei.value.status_code == 404

    with pytest.raises(HTTPException) as ei2:
        await auth_router.vendor_os_fair_use_login(
            {"email": "probe+sec@example.invalid", "password": "longenough1"}
        )
    assert ei2.value.status_code == 404


# ---------------------------------------------------- 2. no email_confirmed_at

class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self.description = [("id",), ("email",)]
        self._next = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        s = " ".join(sql.split())
        if s.startswith("SELECT 1 FROM auth.users"):
            self._next = None
        elif "INSERT INTO auth.users" in s or s.startswith("SELECT id::text AS id, email::text"):
            self._next = (params[0], "new@example.invalid")
        else:
            self._next = None

    def fetchone(self):
        return self._next


class _FakeConn:
    def __init__(self, log):
        self.log = log
        self.autocommit = False

    def cursor(self):
        return _FakeCursor(self.log)

    def close(self):
        pass


def test_pg_create_auth_user_never_sets_email_confirmed_at(monkeypatch):
    import psycopg2

    log: list = []
    monkeypatch.setattr(pf, "_pg_dsn_candidates", lambda: ["postgresql://u:p@db.x.supabase.co:5432/postgres"])
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: _FakeConn(log))
    out = pf.pg_create_auth_user("new@example.invalid", "longenough1", full_name="N")
    assert out and out["id"]
    sqls = " ".join(s for s, _ in log)
    assert "INSERT INTO auth.users" in sqls
    assert "email_confirmed_at" not in sqls
    # identity row must not claim the email is verified
    ident = [p for s, p in log if "auth.identities" in s]
    assert ident and '"email_verified": true' not in ident[0][2]


def test_pg_fallback_source_has_no_email_confirmed_at():
    import inspect

    assert "email_confirmed_at" not in inspect.getsource(pf.pg_create_auth_user)


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


@pytest.mark.asyncio
async def test_fair_use_login_resolves_member_by_id_only(monkeypatch):
    monkeypatch.setenv("FAIR_USE_AUTH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit-test-secret")
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: MagicMock())
    monkeypatch.setattr(auth_router, "pg_verify_auth_password", lambda e, p: {"id": "uid-9", "email": e})
    seen = []

    def sel(uid, email):
        seen.append((uid, email))
        return None

    monkeypatch.setattr(auth_router, "pg_select_vendor_member", sel)
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_login({"email": "victim@example.com", "password": "pw"})
    assert ei.value.status_code == 403
    assert seen == [("uid-9", "")]


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
