"""Fair-Use: when POSTGRES_URL is missing, Dispatch asks OS to insert the job."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app.shared import pg_fallback as pf
from app.shared.job_scope import KAM_MEMBER_ID


def test_pg_insert_falls_back_to_os_when_no_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL_NON_POOLING", raising=False)
    monkeypatch.setenv("INTERNAL_API_KEYS", "test-internal-key")
    monkeypatch.setenv("BRICKS_OS_API_URL", "https://api.brxs.ca")

    class FakeResp:
        status_code = 200
        content = b'{"ok":true,"job_id":"job-os-1"}'
        text = '{"ok":true,"job_id":"job-os-1"}'

        def json(self):
            return {"ok": True, "job_id": "job-os-1", "assignee_id": KAM_MEMBER_ID}

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr(pf, "_pg_connect", lambda: None)
    with patch("httpx.post", side_effect=fake_post):
        out = pf.pg_insert_job_assigned_to_owner({
            "id": "job-os-1",
            "booking_id": "b1",
            "address": "1 Main",
            "status": "unassigned",
            "assignee_id": "",
        })
    assert out == "job-os-1"
    assert captured["url"].endswith("/api/internal/create-assigned-job")
    assert captured["headers"]["X-Internal-Api-Key"] == "test-internal-key"
    assert captured["json"]["job"]["assignee_id"] == KAM_MEMBER_ID
    assert captured["json"]["job"]["status"] == "assigned"


def test_os_insert_skips_without_internal_key(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEYS", raising=False)
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    assert pf.os_insert_job_assigned_to_owner({"id": "x"}) is None
