"""'How did you hear about Bricks?' — source / source_detail on applications."""
from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.routers.onboarding import _validate_application, apply_to_bricks
from app.services.vendor_selection import vendor_application_email_body
from app.shared.applicant_source import (
    SOURCE_LABELS,
    found_us_line,
    normalize_applicant_source,
)
from app.shared.schemas import VendorApplicationCreate

from tests.test_vendor_application_jev import (  # reuse the fake DB and channels
    _apply,
    _application,
    _assert_no_printed_score,
    _assert_plain_copy,
    _client,
    _noul,
    _Resp,
)


def test_labels_match_www_options():
    assert list(SOURCE_LABELS.values()) == [
        "Instagram", "Facebook", "LinkedIn", "TikTok", "Google search", "Indeed",
        "Craigslist/Kijiji", "Referral from a photographer",
        "Referral from a realtor/brokerage", "Event or meetup", "Other",
    ]


def test_normalize_rules():
    assert normalize_applicant_source(None, None) == (None, None)
    assert normalize_applicant_source("", "x") == (None, None)
    assert normalize_applicant_source("Instagram", "ignored") == ("instagram", None)
    assert normalize_applicant_source("referral_realtor", "") == ("referral_realtor", None)
    assert normalize_applicant_source("referral_photographer", "  Sam   Lee ") == ("referral_photographer", "Sam Lee")
    assert normalize_applicant_source("other", " a podcast ") == ("other", "a podcast")
    assert len(normalize_applicant_source("other", "x" * 500)[1]) == 80
    with pytest.raises(ValueError):
        normalize_applicant_source("other", "   ")
    with pytest.raises(ValueError):
        normalize_applicant_source("myspace", None)


def test_found_us_line():
    assert found_us_line(None) == ""
    assert found_us_line("bogus") == ""
    assert found_us_line("google") == "Found us via: Google search"
    assert found_us_line("referral_photographer", "Sam Lee") == "Found us via: Referral from a photographer (Sam Lee)"
    assert found_us_line("referral_realtor") == "Found us via: Referral from a realtor/brokerage"
    assert found_us_line("other", "Jev said 0.91") == "Found us via: Other (said)"
    assert found_us_line("other", "Jev") == "Found us via: Other"


def test_email_body_has_one_found_line_and_no_score():
    body = vendor_application_email_body(
        "Alex Rivera", "Toronto", "photographer", 0.91, ("- Equipment does not look suitable.",),
        ["https://portfolio.example.com/alex"], source="tiktok",
    )
    lines = body.split("\n")
    assert lines[1] == "Found us via: TikTok"
    assert sum(1 for l in lines if l.startswith("Found us via:")) == 1
    _assert_plain_copy(body)
    _assert_no_printed_score(body)
    legacy = vendor_application_email_body("Alex Rivera", "Toronto", "photographer", None)
    assert "Found us via" not in legacy


@pytest.mark.asyncio
async def test_validate_rejects_unknown_and_blank_other():
    app = _application()
    app.source = "myspace"
    with pytest.raises(HTTPException):
        await _validate_application(app)
    app = _application()
    app.source = "other"
    app.source_detail = ""
    with pytest.raises(HTTPException) as exc:
        await _validate_application(app)
    assert "where you heard" in exc.value.detail


@pytest.mark.asyncio
async def test_apply_stores_source_and_emails_found_line(monkeypatch):
    import tests.test_vendor_application_jev as base

    async def post(self, url, json=None, headers=None):
        return _Resp(200, _noul(0.2))

    orig = base._application

    def with_source():
        a = orig()
        a.source = "referral_photographer"
        a.source_detail = "Sam Lee"
        return a

    monkeypatch.setattr(base, "_application", with_source)
    result, db = await _apply(monkeypatch, _client(post))
    row = db.inserts[0]
    assert row["source"] == "referral_photographer"
    assert row["source_detail"] == "Sam Lee"
    html = db.email.await_args.kwargs["html"]
    assert "Found us via: Referral from a photographer (Sam Lee)" in html
    _assert_plain_copy(html)
    _assert_no_printed_score(html)


@pytest.mark.asyncio
async def test_apply_without_source_keeps_null(monkeypatch):
    async def post(self, url, json=None, headers=None):
        return _Resp(200, _noul(0.2))

    result, db = await _apply(monkeypatch, _client(post))
    row = db.inserts[0]
    assert row["source"] is None and row["source_detail"] is None
    assert "Found us via" not in db.email.await_args.kwargs["html"]
