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


def _noul(probability, key=APPLICANT_QUESTION_KEY):
    return {"answers": {key: {"type": "noul", "noul": probability}}}


async def _apply(monkeypatch, client_cls, api_key="test-not-a-real-key", channels_fail=False):
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
            patch("app.services.jev_hold.httpx.AsyncClient", client_cls):
        result = await apply_to_bricks(_application(), BackgroundTasks())
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
    assert email_kwargs["to"] == "admin@brxs.ca"
    assert "sk-or-" not in blob
    assert "application_token" not in blob


@pytest.mark.asyncio
async def test_apply_notifies_kam_and_stores_jev_without_changing_status(monkeypatch):
    calls = []

    async def post(self, url, json=None, headers=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _Resp(200, _noul(0.91))

    result, db = await _apply(monkeypatch, _client(post))

    assert result["success"] is True
    assert "_internal_notes" not in result
    assert "yes_probability" not in result
    assert db.inserts[0]["onboarding_status"] == "pending"
    assert db.inserts[0]["is_active"] is False
    member = db.members[result["member_id"]]
    assert member["onboarding_status"] == "pending"
    assert member["is_active"] is False
    assert len(db.updates) == 1
    assert set(db.updates[0]) == {"internal_notes", "updated_at"}
    assert "onboarding_status" not in db.updates[0]
    assert "Jev poor-fit yes-probability 0.91" in db.updates[0]["internal_notes"]
    assert member["onboarding_status"] == "pending"

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == DECISIONS_URL
    assert call["json"]["model"] == JEV_MODEL
    assert list(call["json"]["questions"]) == [APPLICANT_QUESTION_KEY]
    question = call["json"]["questions"][APPLICANT_QUESTION_KEY]
    assert question["type"] == "noul"
    assert question["instructions"] == APPLICANT_INSTRUCTIONS
    state = call["json"]["state"]
    assert state["name"] == "Alex Rivera"
    assert state["city"] == "Toronto"
    assert state["vendor_type"] == "photographer"
    assert "Sony A7IV, 16-35mm" in state["equipment"]
    assert "https://portfolio.example.com/alex" in state["portfolio"]["links"]
    assert state["drone"] is True
    dumped = str(call["json"])
    assert "alex.rivera.example@example.com" not in dumped
    assert "416 555 0199" not in dumped
    assert result["application_token"] not in dumped

    assert db.sms.call_count == 1
    sms_to, sms_body = db.sms.call_args.args
    assert sms_to == "+14165550100"
    assert sms_body == (
        "New Bricks photographer application: Alex Rivera in Toronto. "
        "Jev yes-probability 0.91. Pending your approval."
    )
    assert db.email.await_count == 1
    assert db.email.await_args.kwargs["subject"] == (
        "New vendor application pending your approval: Alex Rivera"
    )
    html = db.email.await_args.kwargs["html"]
    assert "Alex Rivera in Toronto applied as a photographer." in html
    assert "Pending your approval." in html
    assert "Jev yes-probability: 0.91" in html
    _assert_no_personal_mailbox(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "http", "no_noul", "no_key"])
async def test_jev_failure_still_notifies_and_stays_pending(monkeypatch, mode):
    async def post(self, url, json=None, headers=None):
        if mode == "timeout":
            raise TimeoutError("jev timed out")
        if mode == "http":
            return _Resp(503, {"error": "unavailable"})
        if mode == "no_noul":
            return _Resp(200, {"answers": {}})
        raise AssertionError("Jev must not be called without OPENROUTER_API_KEY")

    result, db = await _apply(
        monkeypatch,
        _client(post),
        api_key=None if mode == "no_key" else "test-not-a-real-key",
    )

    assert result["success"] is True
    member = db.members[result["member_id"]]
    assert member["onboarding_status"] == "pending"
    assert member["is_active"] is False
    assert db.updates == []
    assert "Jev poor-fit" not in (member.get("internal_notes") or "")
    assert db.sms.call_count == 1
    sms_body = db.sms.call_args.args[1]
    assert sms_body == (
        "New Bricks photographer application: Alex Rivera in Toronto. "
        "Pending your approval."
    )
    assert "yes-probability" not in sms_body
    html = db.email.await_args.kwargs["html"]
    assert "Pending your approval." in html
    assert "yes-probability" not in html
    _assert_no_personal_mailbox(db)


@pytest.mark.asyncio
async def test_notify_failure_leaves_application_pending(monkeypatch):
    async def post(self, url, json=None, headers=None):
        return _Resp(200, _noul(0.91))

    result, db = await _apply(monkeypatch, _client(post), channels_fail=True)

    assert result["success"] is True
    member = db.members[result["member_id"]]
    assert member["onboarding_status"] == "pending"
    assert member["is_active"] is False
    assert db.sms.call_count == 1
    assert db.email.await_count == 1
    assert "approved" not in member["onboarding_status"]
    assert "rejected" not in member["onboarding_status"]


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
    assert "admin@brxs.ca" in src[src.index("HOLD_ADMIN_EMAIL"):src.index("HOLD_ADMIN_EMAIL") + 80]

    onboarding = Path(__file__).resolve().parents[1].joinpath(
        "app/routers/onboarding.py"
    ).read_text()
    apply_src = onboarding[onboarding.index("async def apply_to_bricks"):onboarding.index("async def _apply_to_bricks_inner")]
    assert "email_to" not in apply_src
    assert "akashorter@gmail.com" not in apply_src
    assert "raph.mercs@gmail.com" not in apply_src
