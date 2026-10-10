"""Fair-Use email+password login: PG crypt verify + JWT mint (no real DB)."""
from __future__ import annotations

from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException

from app.routers import auth as auth_router
from app.shared import auth as auth_mod
from app.shared.job_scope import KAM_MEMBER_ID


@pytest.fixture(autouse=True)
def _jwt_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit-test-fair-use-jwt-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://rbywjnnctjglhcdemnnj.supabase.co")


def test_mint_supabase_access_token_shape():
    token = auth_mod.mint_supabase_access_token(
        "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580",
        "akashorter@gmail.com",
        expires_in=3600,
    )
    payload = jwt.decode(
        token,
        "unit-test-fair-use-jwt-secret",
        algorithms=["HS256"],
        audience="authenticated",
    )
    assert payload["sub"].startswith("0c3fa7c2")
    assert payload["email"] == "akashorter@gmail.com"
    assert payload["role"] == "authenticated"
    assert payload["iss"].endswith("/auth/v1")
    out = auth_mod.decode_supabase_user_from_jwt(token)
    assert out is not None
    uid, email = out
    assert uid.startswith("0c3fa7c2")
    assert email == "akashorter@gmail.com"


@pytest.mark.asyncio
async def test_fair_use_login_missing_fields():
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_login({})
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "missing_fields"

    with pytest.raises(HTTPException) as ei2:
        await auth_router.vendor_os_fair_use_login({"email": "a@b.com"})
    assert ei2.value.status_code == 400
    assert ei2.value.detail["code"] == "missing_fields"


@pytest.mark.asyncio
async def test_fair_use_login_pg_unavailable(monkeypatch):
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: None)
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_login(
            {"email": "a@b.com", "password": "x"}
        )
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "fair_use_pg_unavailable"


@pytest.mark.asyncio
async def test_fair_use_login_invalid_credentials(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: fake_conn)
    monkeypatch.setattr(auth_router, "pg_verify_auth_password", lambda e, p: None)
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_login(
            {"email": "a@b.com", "password": "wrong"}
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["code"] == "invalid_credentials"
    fake_conn.close.assert_called()


@pytest.mark.asyncio
async def test_fair_use_login_success(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: fake_conn)
    monkeypatch.setattr(
        auth_router,
        "pg_verify_auth_password",
        lambda e, p: {"id": "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580", "email": e},
    )
    monkeypatch.setattr(
        auth_router,
        "pg_select_vendor_member",
        lambda uid, email: {
            "id": KAM_MEMBER_ID,
            "email": email,
            "vendor_id": "default_vendor",
            "name": "Kam",
        },
    )

    async def _noop_audit(*_a, **_k):
        return None

    monkeypatch.setattr(auth_router, "write_audit_event", _noop_audit)

    out = await auth_router.vendor_os_fair_use_login(
        {"email": "akashorter@gmail.com", "password": "correct-horse"}
    )
    assert out["fair_use"] is True
    assert out["member_id"] == KAM_MEMBER_ID
    assert out["user"]["email"] == "akashorter@gmail.com"
    assert out["access_token"]
    assert out["refresh_token"] == out["access_token"]
    assert out["expires_in"] == 7 * 24 * 3600
    decoded = auth_mod.decode_supabase_user_from_jwt(out["access_token"])
    assert decoded is not None
    assert decoded[0].startswith("0c3fa7c2")


@pytest.mark.asyncio
async def test_fair_use_login_no_vendor_member(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: fake_conn)
    monkeypatch.setattr(
        auth_router,
        "pg_verify_auth_password",
        lambda e, p: {"id": "11111111-1111-1111-1111-111111111111", "email": e},
    )
    monkeypatch.setattr(auth_router, "pg_select_vendor_member", lambda uid, email: None)
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_login(
            {"email": "ic@example.com", "password": "x"}
        )
    assert ei.value.status_code == 403
    assert ei.value.detail["code"] == "vendor_member_not_found"


@pytest.mark.asyncio
async def test_fair_use_signup_missing_fields():
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_signup({})
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "missing_fields"


@pytest.mark.asyncio
async def test_fair_use_signup_password_too_short(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: fake_conn)
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_signup(
            {"email": "new@example.com", "password": "short"}
        )
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "password_too_short"


@pytest.mark.asyncio
async def test_fair_use_signup_pg_unavailable(monkeypatch):
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: None)
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_signup(
            {"email": "new@example.com", "password": "longenough"}
        )
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "fair_use_pg_unavailable"


@pytest.mark.asyncio
async def test_fair_use_signup_email_taken(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: fake_conn)

    def _raise(_e, _p, full_name=""):
        raise ValueError("email_taken")

    monkeypatch.setattr(auth_router, "pg_create_auth_user", _raise)
    with pytest.raises(HTTPException) as ei:
        await auth_router.vendor_os_fair_use_signup(
            {"email": "taken@example.com", "password": "longenough", "name": "Taken"}
        )
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "email_taken"


@pytest.mark.asyncio
async def test_fair_use_signup_success(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(auth_router, "_pg_connect", lambda: fake_conn)
    monkeypatch.setattr(
        auth_router,
        "pg_create_auth_user",
        lambda e, p, full_name="": {"id": "22222222-2222-2222-2222-222222222222", "email": e},
    )
    monkeypatch.setattr(
        auth_router,
        "pg_upsert_vendor_member",
        lambda uid, email, name="": {
            "id": uid,
            "email": email,
            "vendor_id": "default_vendor",
            "name": name or "New",
        },
    )

    async def _noop_audit(*_a, **_k):
        return None

    monkeypatch.setattr(auth_router, "write_audit_event", _noop_audit)

    out = await auth_router.vendor_os_fair_use_signup(
        {"email": "new@example.com", "password": "longenough", "name": "New Photog"}
    )
    assert out["fair_use"] is True
    assert out["needs_onboarding"] is True
    assert out["user"]["email"] == "new@example.com"
    assert out["access_token"]
    decoded = auth_mod.decode_supabase_user_from_jwt(out["access_token"])
    assert decoded is not None
    assert decoded[0].startswith("22222222")


@pytest.mark.asyncio
async def test_resolve_minted_fair_use_token_after_signature_reject(monkeypatch):
    """Live bug: get_user fails signature on fair-use HS256 mint; local decode must win.

    Regression for auth/me / jobs / uploads 401 invalid_session_token when Auth
    reports 'unable to parse or verify signature' on tokens minted by fair-use-login.
    """
    member_id = "ffd2492b-00df-44eb-83c5-a02888d26358"
    auth_uid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    email = "e2e-go+vendor@brxs.ca"
    token = auth_mod.mint_supabase_access_token(auth_uid, email, expires_in=3600)

    class _Auth:
        async def get_user(self, tok):
            raise Exception(
                "invalid JWT: unable to parse or verify signature, "
                "token signature is invalid"
            )

    class _Db:
        auth = _Auth()

    async def fake_get_db():
        return _Db()

    async def fake_lookup(uid, em=None):
        assert uid == auth_uid
        return {
            "id": member_id,
            "email": email,
            "vendor_id": "default_vendor",
            "role": "Photographer",
            "is_active": True,
        }

    monkeypatch.setattr(auth_mod, "get_db", fake_get_db)
    monkeypatch.setattr(auth_mod, "lookup_vendor_member", fake_lookup)

    actor = await auth_mod.resolve_vendor_actor_id(f"Bearer {token}", None)
    assert actor == member_id

    async def fake_get_member(aid, em=None):
        assert aid == member_id
        return {
            "id": member_id,
            "email": email,
            "vendor_id": "default_vendor",
            "role": "Photographer",
            "is_active": True,
            "name": "E2E",
        }

    monkeypatch.setattr(auth_mod, "get_vendor_member", fake_get_member)
    # auth router imports resolve/get at module level — patch there too
    monkeypatch.setattr(auth_router, "resolve_vendor_actor_id", auth_mod.resolve_vendor_actor_id)
    monkeypatch.setattr(auth_router, "get_vendor_member", fake_get_member)
    me = await auth_router.vendor_os_me(authorization=f"Bearer {token}")
    assert me["id"] == member_id


@pytest.mark.asyncio
async def test_resolve_garbage_token_still_401(monkeypatch):
    """Garbage Bearer must still 401 — do not accept unsigned junk."""

    class _Auth:
        async def get_user(self, tok):
            raise Exception(
                "invalid JWT: unable to parse or verify signature, "
                "token signature is invalid"
            )

    class _Db:
        auth = _Auth()

    async def fake_get_db():
        return _Db()

    monkeypatch.setattr(auth_mod, "get_db", fake_get_db)
    # Ensure local decode sees the test secret but garbage still fails
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit-test-fair-use-jwt-secret")

    with pytest.raises(HTTPException) as ei:
        await auth_mod.resolve_vendor_actor_id("Bearer not-a-real-jwt", None)
    assert ei.value.status_code == 401
    assert ei.value.detail["code"] == "invalid_session_token"


@pytest.mark.asyncio
async def test_resolve_wrong_secret_token_still_401(monkeypatch):
    """Token signed with a different secret must not authenticate."""
    import time as _time

    bad = jwt.encode(
        {
            "aud": "authenticated",
            "role": "authenticated",
            "sub": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "email": "x@y.com",
            "exp": int(_time.time()) + 3600,
        },
        "some-other-secret",
        algorithm="HS256",
    )
    if isinstance(bad, bytes):
        bad = bad.decode("utf-8")

    class _Auth:
        async def get_user(self, tok):
            raise Exception("invalid JWT: unable to parse or verify signature")

    class _Db:
        auth = _Auth()

    async def fake_get_db():
        return _Db()

    monkeypatch.setattr(auth_mod, "get_db", fake_get_db)

    with pytest.raises(HTTPException) as ei:
        await auth_mod.resolve_vendor_actor_id(f"Bearer {bad}", None)
    assert ei.value.status_code == 401
    assert ei.value.detail["code"] == "invalid_session_token"
