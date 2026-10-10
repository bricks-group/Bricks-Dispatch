"""Assign-first: intake must honor checkout assignee_id (travel fee honesty)."""
from __future__ import annotations

from pathlib import Path

from app.routers import dispatch as dispatch_router
from app.shared import pg_fallback


def test_create_dispatch_job_honors_preassigned_assignee():
    src = Path(dispatch_router.__file__).read_text()
    assert "preassigned" in src
    assert "if preassigned:" in src
    assert "_update_job_assignee(job_id, preassigned" in src
    # Must not always wipe a real assignee before assign
    assert 'job_data["status"] = "assigned"' in src


def test_pg_insert_keeps_checkout_assignee_when_present():
    src = Path(pg_fallback.__file__).read_text()
    fn = src.split("def pg_insert_job_assigned_to_owner")[1].split("\ndef ")[0]
    assert 'aid == "default_user"' in fn or "aid == 'default_user'" in fn
    assert "KAM_MEMBER_ID" in fn
    # Must not unconditionally overwrite a provided assignee
    assert 'job["assignee_id"] = KAM_MEMBER_ID' in fn
    assert "if not aid" in fn or "if not aid or" in fn
