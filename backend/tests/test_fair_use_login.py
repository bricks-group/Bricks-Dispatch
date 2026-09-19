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
