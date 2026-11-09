"""Photo delivery email: jobs.agent_email first, share-page link, honest status.

Prod had 3/3 media_delivery rows notification_status='failed' and zero sends in
Resend: the photo path only delegated to Companion by booking_id, and www / e2e
bookings have no Companion bookings row (ids aren't even UUIDs).
"""
import asyncio
from unittest.mock import AsyncMock

from app.routers import post_production as pp
from app.services import client_comms as cc

JOB = {"id": "j1", "booking_id": "e2e-20261011-weball", "address": "1 Main St",
       "agent_email": "agent@example.com", "agent_name": "Alex"}


def _wire(monkeypatch, job=JOB, share="https://app.brxs.ca/share/tok", allows=True, send_ok=True):
    sent = []

    async def send(to, subject, html):
        sent.append((to, subject, html))
        return send_ok

    monkeypatch.setattr(cc, "_load_job", AsyncMock(return_value=dict(job) if job else {}))
    monkeypatch.setattr(cc, "_client_share_link", AsyncMock(return_value=share))
    monkeypatch.setattr(cc, "_agent_allows", lambda e, f: allows)
    monkeypatch.setattr(cc, "_email_agent_direct", send)
    return sent


def test_agent_email_gets_share_link_not_signed_urls(monkeypatch):
    sent = _wire(monkeypatch)
    urls = [{"url": "https://x.supabase.co/storage/v1/object/sign/a.jpg?token=t", "filename": "a.jpg"}]
    assert asyncio.run(cc.notify_photos_delivered("j1", urls)) == "sent"
    to, subject, html = sent[0]
    assert to == "agent@example.com"
    assert subject == "Your photos are ready · 1 Main St"
    assert "https://app.brxs.ca/share/tok" in html
    assert "object/sign" not in html and "24 hours" not in html
    assert "Bricks OS" not in html and " GO" not in html


def test_video_delivery_says_media(monkeypatch):
    sent = _wire(monkeypatch)
    asyncio.run(cc.notify_photos_delivered("j1", [{"filename": "reel_tour.mp4", "url": "u"}]))
    assert sent[0][1] == "Your media is ready · 1 Main St"


def test_share_mint_failure_falls_back_to_desk(monkeypatch):
    sent = _wire(monkeypatch, share="")
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "sent"
    assert "https://app.brxs.ca/properties/j1" in sent[0][2]


def test_muted_and_failed_are_recorded_honestly(monkeypatch):
    _wire(monkeypatch, allows=False)
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "muted"
    _wire(monkeypatch, send_ok=False)
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "failed"


def test_no_agent_and_non_uuid_booking_is_skipped_not_companion(monkeypatch):
    _wire(monkeypatch, job={**JOB, "agent_email": None})
    called = AsyncMock(return_value={"sent": True})
    monkeypatch.setattr(cc, "_call_booking_comms_json", called)
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "skipped"
    assert not called.called


def test_companion_fallback_reads_sent_flag(monkeypatch):
    _wire(monkeypatch, job={**JOB, "agent_email": None, "booking_id": "63173f95-2df1-4962-9c2e-6d6bcf254e77"})
    monkeypatch.setattr(cc, "_call_booking_comms_json", AsyncMock(return_value={"sent": False}))
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "skipped"
    monkeypatch.setattr(cc, "_call_booking_comms_json", AsyncMock(return_value=None))
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "failed"
    payload = {}

    async def grab(path, body):
        payload.update(body)
        return {"sent": True}

    monkeypatch.setattr(cc, "_call_booking_comms_json", grab)
    assert asyncio.run(cc.notify_photos_delivered("j1", [{"url": "signed", "filename": "a.jpg"}])) == "sent"
    assert payload["delivery_urls"] == [] and payload["share_url"].startswith("https://")


def test_post_production_records_status(monkeypatch):
    # 2026-11-09: sends go through the once-only delivery ledger.
    from tests._fake_ledger import FakeLedger
    FakeLedger(monkeypatch)
    patched = []
    monkeypatch.setattr(cc, "notify_photos_delivered", AsyncMock(return_value="sent"))
    monkeypatch.setattr(pp, "_patch_media_delivery", AsyncMock(side_effect=lambda j, f: patched.append(f)))
    assert asyncio.run(pp._send_delivery_notification("j1", [])) == "sent"
    assert patched[0] == {"notification_status": "sending"}
    assert patched[1]["notification_status"] == "sent" and "notified_at" in patched[1]
    # Never twice: a second call for the same job does not send again.
    assert asyncio.run(pp._send_delivery_notification("j1", [])) == "sent_before"
    monkeypatch.setattr(cc, "notify_photos_delivered", AsyncMock(side_effect=RuntimeError("x")))
    assert asyncio.run(pp._send_delivery_notification("j2", [])) == "failed"
    assert patched[-1] == {"notification_status": "failed"}
