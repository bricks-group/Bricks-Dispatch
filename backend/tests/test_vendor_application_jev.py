"""Jev first pass on vendor applications. Kam still approves."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from app.routers.onboarding import apply_to_bricks
from app.services.jev_hold import (
    APPLICANT_INSTRUCTIONS,
    APPLICANT_QUESTION_KEY,
    DECISIONS_URL,
    JEV_MODEL,
)
from app.shared.job_scope import KAM_MEMBER_ID
from app.shared.schemas import VendorApplicationCreate

_BANNED_MAILBOXES = (
    "akashorter@gmail.com",
    "raph.mercs@gmail.com",
    "hiring@brxs.ca",
)


def _application() -> VendorApplicationCreate:
    return VendorApplicationCreate(
        name="Alex Rivera",
        email="alex.rivera.example@example.com",
        phone="+1 416 555 0199",
        vendor_type="photographer",
        specialties=["photo"],
        portfolio_links=["https://portfolio.example.com/alex"],
        equipment_list=["Sony A7IV, 16-35mm"],
        city="Toronto",
        accepted_terms=True,
        hst_exempt=True,
        drone_certified=True,
    )


class _Result:
    def __init__(self, data):
        self.data = data


class _DB:
    def __init__(self):
        self.members = {}
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _Chain(self, name)


class _Chain:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.op = "select"
        self.payload = None
        self.eqs = {}

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.eqs[key] = value
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        if self.table == "vendor_availability" and self.op == "insert":
            return _Result(self.payload)
        if self.table != "vendor_members":
            raise AssertionError((self.table, self.op))
        if self.op == "select" and "email" in self.eqs:
            return _Result(None)
        if self.op == "select" and self.eqs.get("id") == KAM_MEMBER_ID:
            return _Result({"id": KAM_MEMBER_ID, "phone": "+14165550100"})
        if self.op == "select":
            return _Result(self.db.members.get(self.eqs.get("id")))
        if self.op == "insert":
            self.db.inserts.append(self.payload)
            self.db.members[self.payload["id"]] = dict(self.payload)
            return _Result(self.payload)
        if self.op == "update":
            self.db.updates.append(dict(self.payload))
            row = self.db.members.get(self.eqs.get("id"))
            if row is not None:
                row.update(self.payload)
            return _Result(self.payload)
        raise AssertionError(self.op)


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _client(post_impl):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            return await post_impl(self, url, json=json, headers=headers)

    return FakeClient


def _noul(probability, reasons=None, key=APPLICANT_QUESTION_KEY):
    answers = {key: {"type": "noul", "noul": probability}}
    for reason_key, value in (reasons or {}).items():
        answers[reason_key] = {"type": "noul", "noul": value}
    return {"answers": answers}


def _assert_plain_copy(text):
    low = text.lower()
    assert "jev" not in low
    assert "poor-fit" not in low
    assert "yes-probability" not in low


def _assert_no_printed_score(text):
    import re
    # CSS in the email shell uses values like -0.5px. Only flag a score token.
    visible = re.sub(r"<[^>]+>", " ", text)
    assert re.search(r"\b0\.\d+\b", visible) is None


def _opened_check(urls):
    from app.services.portfolio_check import LinkCheck, OK_PAGE, PortfolioCheck
    return PortfolioCheck(tuple(LinkCheck(u, OK_PAGE) for u in urls))


async def _apply(monkeypatch, client_cls, api_key="test-not-a-real-key", channels_fail=False,
                 portfolio=None):
    monkeypatch.delenv("ADMIN_NOTIFICATION_EMAIL", raising=False)
    if api_key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", api_key)
    db = _DB()
    if channels_fail:
        sms = MagicMock(side_effect=RuntimeError("twilio down"))
        email = AsyncMock(side_effect=RuntimeError("resend down"))
    else:
        sms = MagicMock(return_value=True)
        email = AsyncMock(return_value=True)
    db.sms = sms
    db.email = email
    with patch("app.routers.onboarding.get_db", AsyncMock(return_value=db)), \
            patch("app.notifications.send_dispatch_sms", sms), \
            patch("app.services.notifications.send_email_via_resend", email), \
            patch("app.services.jev_hold.httpx.AsyncClient", client_cls), \
            patch("app.services.portfolio_check.check_portfolio",
                  AsyncMock(side_effect=portfolio or (lambda urls: _opened_check(urls)))):
        bg = BackgroundTasks()
        result = await apply_to_bricks(_application(), bg)
    db.background = bg
    return result, db


def _assert_no_personal_mailbox(db):
    sms_body = db.sms.call_args.args[1]
    email_kwargs = db.email.await_args.kwargs
    blob = " ".join([
        sms_body,
        email_kwargs["to"],
        email_kwargs["subject"],
        email_kwargs["html"],
    ])
    for mailbox in _BANNED_MAILBOXES:
        assert mailbox not in blob
    assert email_kwargs["to"] == "raphael@brxs.ca"
    assert "sk-or-" not in blob
    assert "application_token" not in blob


@pytest.mark.asyncio
async def test_apply_schedules_evidence_review_and_stays_pending(monkeypatch):
    """Apply no longer asks the text model; it stores the evidence and queues
    the automatic review (which alone texts/emails Kam). Status stays pending."""
    from tests.test_applicant_review import _application as evidence_application

    async def must_not_post(self, url, json=None, headers=None):
        raise AssertionError("apply must not call the old text guess")

    import sys
    monkeypatch.setattr(sys.modules[__name__], "_application", lambda: evidence_application())
    result, db = await _apply(monkeypatch, _client(must_not_post))
    row = db.inserts[0]
    assert row["onboarding_status"] == "pending"
    assert result["success"] is True
    assert any(t.func.__name__ == "review_application" for t in db.background.tasks)
    prof = row["application_profile"]
    assert prof["location"] == {"city": "Toronto", "in_gta": True}
    assert len(prof["samples"]) == 10 and prof["equipment"]["camera_body"] == "Sony A7 IV"
    assert db.sms.call_count == 0 and db.email.await_count == 0  # sent by the queued review, not inline
    assert db.updates == []


def test_fit_sentences_and_reason_bullets():
    from app.services.jev_hold import applicant_reason_lines
    from app.services.vendor_selection import (
        vendor_application_email_body,
        vendor_application_sms_body,
    )

    weak_reasons = applicant_reason_lines(
        _noul(0.91, {"equipment": 0.8, "city": 0.2, "portfolio": 0.51, "drone": 0.5}),
        0.91,
    )
    # The text model never saw the portfolio: no portfolio/city/drone line.
    assert weak_reasons == ("Listed equipment may not suit this work.",)
    ok_reasons = applicant_reason_lines(
        _noul(0.7, {"equipment": 0.5, "city": 0.2, "portfolio": 0.9, "drone": 0.1}),
        0.7,
    )
    assert ok_reasons == ()
    no_factors = applicant_reason_lines(_noul(0.91), 0.91)
    assert no_factors == ()

    sms = vendor_application_sms_body(
        "Alex Rivera", "Toronto", "photographer", 0.4, ok_reasons
    )
    email = vendor_application_email_body(
        "Alex Rivera", "Toronto", "photographer", 0.4, ok_reasons
    )
    assert "This does not look like a weak fit." in sms
    assert "This does not look like a weak fit." in email
    assert "- " not in sms
    assert "portfolio" not in sms.lower()
    bare = vendor_application_sms_body("Alex Rivera", "Toronto", "photographer", 0.91, ())
    assert bare == (
        "New Bricks photographer application: Alex Rivera in Toronto. "
        "This looks like a weak fit.\n"
        "Pending your approval."
    )
    silent = vendor_application_sms_body(
        "Alex Rivera", "Toronto", "photographer", None, ("Equipment looks suitable for this work.",)
    )
    assert "weak fit" not in silent
    assert "Equipment" not in silent
    mailed = vendor_application_email_body(
        "Alex Rivera",
        "Toronto",
        "photographer",
        None,
        (),
        [
            "https://portfolio.example.com/alex",
            "storage/key/not-a-link",
            "alex.rivera.example@example.com",
            "https://cdn.example.com/sample.jpg",
        ],
    )
    assert "Portfolio:" in mailed
    assert "https://portfolio.example.com/alex" in mailed
    assert "https://cdn.example.com/sample.jpg" in mailed
    assert "storage/key" not in mailed
    assert "alex.rivera.example@example.com" not in mailed
    sms_link = vendor_application_sms_body(
        "Alex Rivera",
        "Toronto",
        "photographer",
        None,
        (),
        [
            "https://portfolio.example.com/alex",
            "storage/key/not-a-link",
            "alex.rivera.example@example.com",
        ],
    )
    assert sms_link == (
        "New Bricks photographer application: Alex Rivera in Toronto.\n"
        "https://portfolio.example.com/alex\n"
        "Pending your approval."
    )
    sms_none = vendor_application_sms_body(
        "Alex Rivera", "Toronto", "photographer", None, (), ["storage/key/not-a-link"]
    )
    assert sms_none == (
        "New Bricks photographer application: Alex Rivera in Toronto. Pending your approval."
    )
    assert "http" not in sms_none
    assert "weak fit" not in mailed
    for copy in (sms, email, bare, silent, mailed, sms_link, sms_none):
        _assert_plain_copy(copy)
        _assert_no_printed_score(copy)


def test_application_notify_source_has_no_personal_gmail():
    src = Path(__file__).resolve().parents[1].joinpath(
        "app/services/vendor_selection.py"
    ).read_text()
    start = src.index("def vendor_application_sms_body")
    end = src.index("async def _escalate_no_vendor")
    body = src[start:end]
    for mailbox in _BANNED_MAILBOXES:
        assert mailbox not in body
    assert 'to=HOLD_ADMIN_EMAIL' in body
    assert "cc=" not in body
    assert "bcc=" not in body
    assert "raphael@brxs.ca" in src[src.index("HOLD_ADMIN_EMAIL"):src.index("HOLD_ADMIN_EMAIL") + 80]

    onboarding = Path(__file__).resolve().parents[1].joinpath(
        "app/routers/onboarding.py"
    ).read_text()
    apply_src = onboarding[onboarding.index("async def apply_to_bricks"):onboarding.index("async def _apply_to_bricks_inner")]
    assert "email_to" not in apply_src
    assert "akashorter@gmail.com" not in apply_src
    assert "raph.mercs@gmail.com" not in apply_src
