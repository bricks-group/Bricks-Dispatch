"""OTP verify links vendor_members server-side with the service role (2026-11-02).

Prod bug: verify_otp ran on the shared service-role client; supabase-py then
sent the vendor's JWT on every table call, so RLS hid the unlinked row
(auth_user_id NULL) and blocked the insert/link. Magic link worked.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import vendor_otp_link as vol

UID = "33333333-aaaa-4bbb-8ccc-000000000003"
OTHER = "44444444-aaaa-4bbb-8ccc-000000000004"
EMAIL = "kam+bricks-test-vendor@example.com"


class _Table:
    """Tiny in-memory vendor_members with the postgrest calls the linker uses."""

    def __init__(self, rows):
        self.rows = rows
        self.updates = []
        self.inserts = []

    def __call__(self, name):
        assert name == "vendor_members"
        return _Q(self)


class _Q:
    def __init__(self, t):
        self.t, self.filters, self.single, self.op, self.payload = t, [], False, "select", None

    def select(self, *a):
        return self

    def eq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: str(r.get(c) or "") == str(v))
        return self

    def ilike(self, c, v):
        self.filters.append(lambda r, c=c, v=v: str(r.get(c) or "").lower() == str(v).lower())
        return self

    def is_(self, c, v):
        assert v == "null"
        self.filters.append(lambda r, c=c: r.get(c) is None)
        return self

    def limit(self, *a):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def update(self, p):
        self.op, self.payload = "update", p
        return self

    def insert(self, p):
        self.op, self.payload = "insert", p
        return self

    async def execute(self):
        match = [r for r in self.t.rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in match:
                r.update(self.payload)
            self.t.updates.append((self.payload, [r["id"] for r in match]))
            return SimpleNamespace(data=match)
        if self.op == "insert":
            self.t.rows.append(dict(self.payload))
            self.t.inserts.append(self.payload)
            return SimpleNamespace(data=[self.payload])
        if self.single:
            return SimpleNamespace(data=match[0]) if match else None
        return SimpleNamespace(data=match)


def _db(rows):
    t = _Table([dict(r) for r in rows])
    db = MagicMock()
    db.table = t
    return db, t


def _run(db, email=EMAIL, uid=UID):
    return asyncio.run(vol.link_member_after_otp(db, uid, email, ""))


def test_unlinked_member_is_linked_by_verified_email():
    db, t = _db([{"id": "m1", "email": EMAIL.upper(), "auth_user_id": None, "role": "Photographer"}])
    out = _run(db)
    assert out["action"] == "linked" and out["member"]["id"] == "m1"
    assert t.rows[0]["auth_user_id"] == UID
    assert t.inserts == []


def test_member_already_linked_to_same_user_is_kept():
    db, t = _db([{"id": "m1", "email": EMAIL, "auth_user_id": UID}])
    out = _run(db)
    assert out["action"] == "existing" and t.updates == [] and t.inserts == []


def test_code_login_never_claims_another_users_row():
    db, t = _db([{"id": "m1", "email": EMAIL, "auth_user_id": OTHER}])
    out = _run(db)
    assert out["action"] == "conflict" and out["member"] is None
    assert t.rows[0]["auth_user_id"] == OTHER
    assert t.updates == [] and t.inserts == []  # no duplicate row either


def test_wildcard_email_does_not_match():
    db, t = _db([{"id": "m1", "email": "kamxbricks-test-vendor@example.com", "auth_user_id": None}])
    out = _run(db, email="kam_bricks-test-vendor@example.com")
    assert out["action"] == "created"
    assert t.rows[0]["auth_user_id"] is None


def test_unknown_email_creates_default_row():
    db, t = _db([])
    out = _run(db)
    assert out["action"] == "created" and t.inserts[0]["id"] == UID
    assert t.inserts[0]["role"] == "Photographer"


def test_link_update_is_conditional_on_null():
    """Race: the row gets linked to someone else between read and write."""
    db, t = _db([{"id": "m1", "email": EMAIL, "auth_user_id": None}])
    real = _Q.execute

    async def racing(self):
        if self.op == "update":
            t.rows[0]["auth_user_id"] = OTHER
        return await real(self)

    with patch.object(_Q, "execute", racing):
        _run(db)
    assert t.rows[0]["auth_user_id"] == OTHER


# --- the root cause: the shared client must never carry a user JWT ---------

_REAL_CLIENT_SCRIPT = r"""
import asyncio, os, sys
from types import SimpleNamespace
sys.path.insert(0, os.getcwd())
from app.shared import database as dbm
dbm.url, dbm.key, dbm._db = "https://example.supabase.co", "service-key", None

async def go():
    shared = await dbm.get_db()
    assert shared.options.headers["Authorization"] == "Bearer service-key"
    # The old bug, on the real library: signing in on a client flips its header.
    throwaway = await dbm.new_auth_client()
    assert throwaway is not shared
    throwaway._listen_to_auth_events("SIGNED_IN", SimpleNamespace(access_token="vendor-jwt"))
    assert throwaway.options.headers["Authorization"] == "Bearer vendor-jwt"
    assert shared.options.headers["Authorization"] == "Bearer service-key"
    assert (await dbm.get_db()) is shared
    # If anything signs in on the shared client, get_db replaces it.
    shared._listen_to_auth_events("SIGNED_IN", SimpleNamespace(access_token="vendor-jwt"))
    fresh = await dbm.get_db()
    assert fresh is not shared
    assert fresh.options.headers["Authorization"] == "Bearer service-key"
    print("OK")

asyncio.run(go())
"""


def test_shared_client_never_keeps_a_user_jwt_real_supabase():
    """Runs against the real supabase-py (conftest stubs it in-process)."""
    import os
    import subprocess
    import sys
    probe = subprocess.run([sys.executable, "-c", "import supabase._async.client"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("real supabase-py not installed")
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUPABASE")}
    env.update(SUPABASE_URL="https://example.supabase.co", SUPABASE_KEY="service-key")
    r = subprocess.run([sys.executable, "-c", _REAL_CLIENT_SCRIPT], cwd=backend, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


def test_verify_route_uses_throwaway_client_and_service_role_link():
    from app.routers import auth as auth_router
    user = SimpleNamespace(id=UID, email=EMAIL, user_metadata={})
    session = SimpleNamespace(access_token="a", refresh_token="r", expires_in=3600)
    otp_client = MagicMock()
    otp_client.auth.verify_otp = AsyncMock(return_value=SimpleNamespace(session=session, user=user))
    shared, t = _db([{"id": "m1", "email": EMAIL, "auth_user_id": None}])
    shared.auth.verify_otp = AsyncMock(side_effect=AssertionError("verify on shared client"))
    with patch("app.shared.database.new_auth_client", AsyncMock(return_value=otp_client)), \
         patch.object(auth_router, "get_db", AsyncMock(return_value=shared)), \
         patch.object(auth_router, "write_audit_event", AsyncMock()):
        out = asyncio.run(auth_router.vendor_os_verify({"email": EMAIL, "token": "12345678"}))
    assert out["access_token"] == "a"
    otp_client.auth.verify_otp.assert_awaited_once()
    assert t.rows[0]["auth_user_id"] == UID


def test_resume_too_small_says_too_small():
    from fastapi import HTTPException
    from app.routers import onboarding
    body = SimpleNamespace(files=[SimpleNamespace(name="cv.pdf", type="application/pdf", size=200, kind="resume")],
                           session="")
    with pytest.raises(HTTPException) as e:
        asyncio.run(onboarding.mint_sample_uploads(body))
    assert "too small" in e.value.detail and "under 10 MB" not in e.value.detail


def test_resume_too_large_still_says_10_mb():
    from fastapi import HTTPException
    from app.routers import onboarding
    body = SimpleNamespace(files=[SimpleNamespace(name="cv.pdf", type="application/pdf",
                                                  size=11 * 1024 * 1024, kind="resume")], session="")
    with pytest.raises(HTTPException) as e:
        asyncio.run(onboarding.mint_sample_uploads(body))
    assert "under 10 MB" in e.value.detail
