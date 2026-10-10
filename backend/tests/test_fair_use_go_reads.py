"""Fair-Use: GO auth JWT local verify + job list via OS Postgres proxy."""
from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest

from app.shared import auth as auth_mod
from app.shared import pg_fallback as pf
from app.shared.job_scope import KAM_MEMBER_ID


def test_decode_supabase_user_from_jwt(monkeypatch):
    secret = "test-jwt-secret-for-fair-use"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    token = jwt.encode(
        {
            "sub": "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580",
            "email": "akashorter@gmail.com",
            "aud": "authenticated",
            "role": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    out = auth_mod.decode_supabase_user_from_jwt(token)
    assert out is not None
    uid, email = out
    assert uid.startswith("0c3fa7c2")
    assert email == "akashorter@gmail.com"


def test_decode_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "right-secret")
    token = jwt.encode(
        {"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 3600},
        "wrong-secret",
        algorithm="HS256",
    )
    assert auth_mod.decode_supabase_user_from_jwt(token) is None


def test_pg_select_vendor_member_proxies_to_os(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL_NON_POOLING", raising=False)
    monkeypatch.setenv("INTERNAL_API_KEYS", "test-internal-key")
    monkeypatch.setenv("BRICKS_OS_API_URL", "https://api.brxs.ca")
    monkeypatch.setattr(pf, "_pg_connect", lambda: None)

    class FakeResp:
        status_code = 200
        content = b'{"ok":true}'
        text = '{"ok":true}'

        def json(self):
            return {
                "ok": True,
                "member": {"id": KAM_MEMBER_ID, "email": "akashorter@gmail.com", "name": "Kam"},
            }

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResp()

    with patch("httpx.post", side_effect=fake_post):
        member = pf.pg_select_vendor_member(KAM_MEMBER_ID, "akashorter@gmail.com")
    assert member["id"] == KAM_MEMBER_ID
    assert captured["url"].endswith("/api/internal/vendor-member")
    assert captured["headers"]["X-Internal-Api-Key"] == "test-internal-key"


def test_pg_select_jobs_proxies_to_os(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL_NON_POOLING", raising=False)
    monkeypatch.setenv("INTERNAL_API_KEYS", "test-internal-key")
    monkeypatch.setattr(pf, "_pg_connect", lambda: None)

    class FakeResp:
        status_code = 200
        content = b'{"ok":true}'
        text = '{"ok":true}'

        def json(self):
            return {
                "ok": True,
                "jobs": [{"id": "job-1", "assignee_id": KAM_MEMBER_ID, "status": "assigned"}],
                "total": 1,
            }

    with patch("httpx.post", return_value=FakeResp()):
        jobs, total = pf.pg_select_jobs_for_assignees([KAM_MEMBER_ID], limit=20, offset=0)
    assert total == 1
    assert jobs[0]["id"] == "job-1"


def test_is_rest_quota_detects_402():
    assert pf._is_rest_quota_error('{"message":"exceed_storage_size_quota"}')
    assert pf._is_rest_quota_error("402 Payment Required")
    assert not pf._is_rest_quota_error("column does not exist")
