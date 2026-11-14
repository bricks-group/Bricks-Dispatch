"""Fair-Use: GO job list via OS Postgres proxy."""
from __future__ import annotations

from unittest.mock import patch

from app.shared import pg_fallback as pf
from app.shared.job_scope import KAM_MEMBER_ID


# HS256 decode_supabase_user_from_jwt tests removed 2026-11-14: the local
# fallback is ES256-only now (tests/test_fair_use_auth_es256.py).


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
