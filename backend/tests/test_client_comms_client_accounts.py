"""Gap audit X3: client-account jobs get client wording and the client home link."""
import asyncio

import pytest

from app.services import client_comms as cc


@pytest.mark.parametrize("job,expected", [
    ({"package_id": "pm_turnover"}, True),
    ({"package_id": "cre_leasing_m"}, True),
    ({"package_id": "inv_acquisition"}, True),
    ({"package_id": "mapped_monthly"}, True),
    ({"booking_id": "cb_123", "package_id": ""}, True),
    ({"job_type": "site_progress_visual"}, True),
    ({"billing_type": "client_plan_credit"}, True),
    ({"package_id": "keystone", "job_type": "photo", "booking_id": "lead_1"}, False),
    ({}, False),
])
def test_is_client_account_job(job, expected):
    assert cc.is_client_account_job(job) is expected


def _capture(monkeypatch, job):
    sent = []

    async def load(_jid, _cols):
        return dict(job)

    async def name(_aid, fb):
        return "Sam"

    async def email(to, subject, html, text=None):
        sent.append((to, subject, html))
        return True

    async def push(*a, **k):
        return {"sent": 0}

    async def share(_jid):
        return "https://app.brxs.ca/s/tok"

    monkeypatch.setattr(cc, "_load_job", load)
    monkeypatch.setattr(cc, "_resolve_photographer_name", name)
    monkeypatch.setattr(cc, "_email_agent_direct", email)
    monkeypatch.setattr(cc, "_push_agent_new_booking", push)
    monkeypatch.setattr(cc, "_client_share_link", share)
    monkeypatch.setattr(cc, "_agent_allows", lambda *_: True)
    monkeypatch.setattr(cc, "_when_line", lambda _j: "Mon 10am")
    return sent


PM_JOB = {"id": "j1", "address": "88 Queen St", "agent_email": "pm@example.com",
          "agent_name": "Pat", "package_id": "pm_turnover", "package_name": "Turnover"}
AGENT_JOB = {"id": "j2", "address": "1 King St", "agent_email": "a@example.com",
             "agent_name": "Alex", "package_id": "keystone", "job_type": "photo"}


def test_client_confirm_email_has_no_listing_wording(monkeypatch):
    sent = _capture(monkeypatch, PM_JOB)
    asyncio.run(cc.notify_booking_confirmed("j1"))
    (_, subject, html), = sent
    assert subject.startswith("Visit confirmed")
    assert "/developer/overview" in html
    assert "/properties/" not in html
    assert "listing" not in html.lower()
    assert "GO" not in html.replace("Google", "")


def test_agent_confirm_email_keeps_listing_link(monkeypatch):
    sent = _capture(monkeypatch, AGENT_JOB)
    asyncio.run(cc.notify_booking_confirmed("j2"))
    (_, subject, html), = sent
    assert subject.startswith("Shoot confirmed")
    assert "/properties/j2" in html
    assert "Bricks GO" not in html


def test_client_delivery_email_no_seller(monkeypatch):
    sent = _capture(monkeypatch, PM_JOB)
    status = asyncio.run(cc.notify_photos_delivered("j1", []))
    assert status == "sent"
    (_, _, html), = sent
    assert "seller" not in html
    assert "listing" not in html.lower()
    assert "/developer/overview" in html


def test_client_capture_delivered_no_seller(monkeypatch):
    sent = _capture(monkeypatch, PM_JOB)
    ok = asyncio.run(cc.notify_capture_delivered("j1", products=["floor plan"]))
    assert ok
    (_, _, html), = sent
    assert "seller" not in html and "listing file" not in html
    assert "/developer/overview" in html


def test_agent_delivery_email_still_mentions_seller(monkeypatch):
    sent = _capture(monkeypatch, AGENT_JOB)
    asyncio.run(cc.notify_photos_delivered("j2", []))
    (_, _, html), = sent
    assert "seller" in html
    assert "/properties/j2" in html
