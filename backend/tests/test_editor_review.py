"""Editor review: read-only previews, admin auth and once-per-job alerts."""
import asyncio
from unittest.mock import AsyncMock
import pytest


def test_review_media_only_counts_finished_qc_passed_files():
    from app.services import editor_review as er
    rows = [
        {"id": "raw", "filename": "clip.mp4", "provider": "source_clip", "file_url": "source", "qc_status": "passed", "processing_status": "done"},
        {"id": "film", "filename": "film.mp4", "provider": "editor_final", "file_url": "film", "qc_status": "passed", "processing_status": "done"},
        {"id": "held", "filename": "photo.jpg", "file_url": "held", "qc_status": "flagged", "processing_status": "done"},
        {"id": "photo", "filename": "photo.jpg", "file_url": "photo", "qc_status": "passed", "processing_status": "done"},
    ]
    entries = er.deliverable_entries(rows)
    assert {e["upload_id"] for e in entries} == {"film", "photo"}
    assert next(e for e in entries if e["upload_id"] == "film")["provider"] == "editor_final"


def test_hold_alert_is_once_and_escaped(monkeypatch):
    from app.services import editor_review as er
    claims = set()
    async def claim(key, job_id, details):
        if key in claims: return False
        claims.add(key); return True
    monkeypatch.setattr(er, "_claim_alert", claim)
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(er, "send_email_via_resend", sender)
    job = {"id": "job-1", "address": "<script>bad</script>", "client_name": "Kam"}
    verdict = {"ready": False, "editor_approved": False, "required": ["reel"], "present": [], "missing": ["reel"], "reasons": ["awaiting editor approval"]}
    assert asyncio.run(er.alert_editor_hold(job, verdict)) is True
    assert asyncio.run(er.alert_editor_hold(job, verdict)) is False
    assert sender.await_count == 1
    args = sender.await_args.kwargs
    assert args["to"] == "raph@brxs.ca"
    assert "&lt;script&gt;" in args["html"]
    assert "https://go.brxs.ca/admin/review#job-1" in args["html"]
    assert "Required: reel" in args["text"]


def test_no_alert_for_approved_or_ready(monkeypatch):
    from app.services import editor_review as er
    claim = AsyncMock()
    monkeypatch.setattr(er, "_claim_alert", claim)
    for verdict in ({"ready": True}, {"editor_approved": True}):
        assert asyncio.run(er.alert_editor_hold({"id": "j"}, verdict)) is False
    claim.assert_not_awaited()


def test_digest_claims_once_and_suppresses_individual_alerts(monkeypatch):
    from app.services import editor_review as er
    claims = set()
    async def claim(key, job_id, details):
        if key in claims: return False
        claims.add(key); return True
    monkeypatch.setattr(er, "_claim_alert", claim)
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(er, "send_email_via_resend", sender)
    jobs = [{"id": "j", "address": "Home", "client": "Client", "waiting_since": "2026-11-07T17:00:00Z", "verdict": {"reasons": ["awaiting editor approval"]}}]
    assert asyncio.run(er.send_initial_digest(jobs)) is True
    assert asyncio.run(er.send_initial_digest(jobs)) is False
    assert sender.await_count == 1
    assert "editor-hold-j" in claims


def test_review_list_requires_admin(monkeypatch):
    from app.routers import admin_ops
    from fastapi import HTTPException
    auth = AsyncMock(side_effect=HTTPException(status_code=403))
    monkeypatch.setattr(admin_ops, "_require_admin", auth)
    with pytest.raises(HTTPException):
        asyncio.run(admin_ops.list_editor_review(authorization="Bearer vendor", x_user_id=None))
    auth.assert_awaited_once()


def test_review_page_has_explicit_approval_and_authenticated_api():
    from pathlib import Path
    root = Path(__file__).parents[2]
    page = root / "web/src/pages/EditorReviewPage.tsx"
    assert page.exists(), "Admin review page is not built yet"
    source = page.read_text()
    assert "editor-approve" in source
    assert "controls" in source
    assert "window.confirm" in source
    assert "api.get" in source


def test_queue_excludes_legacy_delivered_and_cancelled_jobs(monkeypatch):
    from app.services import editor_review as er
    from app.routers import post_production as pp
    async def query(db, table, job_id=None):
        if table == "jobs":
            return [
                {"id": "open", "status": "assigned", "package_id": "photos_only"},
                {"id": "old", "status": "delivered", "package_id": "photos_only"},
                {"id": "cancel", "status": "cancelled", "package_id": "photos_only"},
            ]
        return []
    monkeypatch.setattr(er, "_query", query)
    monkeypatch.setattr(pp, "_list_delivered_capture_sessions", AsyncMock(return_value=[]))
    items = asyncio.run(er.list_review_items(None, include_media=False))
    assert [j["id"] for j in items] == ["open"]
    assert items[0]["verdict"]["missing"] == ["photos"]
    assert items[0]["waiting_since"] is None


def test_initial_digest_skips_jobs_that_have_not_reached_gate(monkeypatch):
    from app.services import editor_review as er
    claim = AsyncMock()
    monkeypatch.setattr(er, "_claim_alert", claim)
    assert asyncio.run(er.send_initial_digest([{"id": "future", "waiting_since": None}])) is False
    claim.assert_not_awaited()
