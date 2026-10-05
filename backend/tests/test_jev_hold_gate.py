"""Jev hold gate on booking intake auto-assign.

A yes-probability above 0.7 skips assign_vendor_for_job. A missing key,
timeout, or bad response still assigns the way intake does today.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers.dispatch import create_dispatch_job
from app.services.jev_hold import (
    DECISIONS_URL,
    HOLD_INSTRUCTIONS,
    JEV_MODEL,
    booking_decision_state,
    yes_probability,
)
from app.shared.schemas import JobIntakeRequest

_INTERNAL_KEY = "test-internal"


def _payload(**overrides) -> JobIntakeRequest:
    data = dict(
        booking_id="bk_hold_1",
        address="10 King St",
        city="Toronto",
        state="ON",
        time_window_start="09:00",
        time_window_end="11:00",
        job_type="photo",
        scheduled_date="2026-10-10",
        package_name="Essentials",
        package_id="essentials",
        vendor_tier=1,
        min_tier_required=3,
        preferred_tier=3,
    )
    data.update(overrides)
    return JobIntakeRequest(**data)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self._data = data

    def eq(self, *args, **kwargs):
        return self

    async def execute(self):
        return _Result(self._data)


class _Jobs:
    def __init__(self):
        self.inserts = []
        self.updates = []

    def insert(self, data):
        self.inserts.append(data)
        return _Query([{"id": data.get("id")}])

    def update(self, data):
        self.updates.append(data)
        return _Query([data])


class _Members:
    def __init__(self):
        self.queries = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        self.queries.append(args)
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return _Result({"phone": "+14165550100"})


class _DB:
    def __init__(self):
        self.jobs = _Jobs()
        self.members = _Members()

    def table(self, name):
        if name == "jobs":
            return self.jobs
        if name == "vendor_members":
            return self.members
        raise AssertionError(name)


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


def _noul(probability):
    return {"answers": {"hold_for_human": {"type": "noul", "noul": probability}}}


async def _intake(monkeypatch, payload, client_cls, api_key="test-not-a-real-key", channels_fail=False):
    monkeypatch.setenv("INTERNAL_API_KEYS", _INTERNAL_KEY)
    if api_key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", api_key)
    db = _DB()
    assign = AsyncMock(return_value="member_kam")
    notify = AsyncMock()
    if channels_fail:
        sms = MagicMock(side_effect=RuntimeError("twilio down"))
        email = AsyncMock(side_effect=RuntimeError("resend down"))
    else:
        sms = MagicMock(return_value=True)
        email = AsyncMock(return_value=True)
    db.sms = sms
    db.email = email
    with patch("app.routers.dispatch.get_db", AsyncMock(return_value=db)), \
            patch("app.routers.dispatch.assign_vendor_for_job", assign), \
            patch("app.routers.dispatch.create_notification", notify), \
            patch("app.notifications.send_dispatch_sms", sms), \
            patch("app.services.notifications.send_email_via_resend", email), \
            patch("app.services.jev_hold.httpx.AsyncClient", client_cls):
        result = await create_dispatch_job(payload, x_internal_api_key=_INTERNAL_KEY)
    return result, assign, notify, db


@pytest.mark.asyncio
async def test_high_hold_probability_skips_auto_assign(monkeypatch):
    calls = []

    async def post(self, url, json=None, headers=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": self.timeout})
        return _Resp(200, _noul(0.91))

    result, assign, notify, db = await _intake(monkeypatch, _payload(), _client(post))

    assert result["status"] == "held_for_review"
    assert result["assignee_id"] == ""
    assert assign.await_count == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == DECISIONS_URL
    assert call["headers"]["Authorization"] == "Bearer test-not-a-real-key"
    assert call["json"]["model"] == JEV_MODEL
    assert list(call["json"]["questions"]) == ["hold_for_human"]
    question = call["json"]["questions"]["hold_for_human"]
    assert question["type"] == "noul"
    assert question["instructions"] == HOLD_INSTRUCTIONS
    state = call["json"]["state"]
    assert state["service"]["job_type"] == "photo"
    assert state["service"]["package_name"] == "Essentials"
    assert state["location"]["city"] == "Toronto"
    assert "lat" in state["missing_fields"]
    assert "lng" in state["missing_fields"]
    assert state["vendor_match_quality"]["label"] == "below_min_tier"
    assert db.jobs.updates
    assert db.jobs.updates[0]["status"] == "held_for_review"
    assert "Held for a person" in db.jobs.updates[0]["dispatch_explanation"]
    assert notify.await_count == 1
    body = notify.await_args.kwargs["body"]
    assert "Assignee: held for a person" in body
    assert db.sms.call_count == 1
    sms_to, sms_body = db.sms.call_args.args
    assert sms_to == "+14165550100"
    assert sms_body == (
        "Bricks held a booking at 10 King St on 2026-10-10. "
        "Jev yes-probability 0.91. It is unassigned."
    )
    assert db.email.await_count == 1
    assert db.email.await_args.kwargs["to"] == "admin@brxs.ca"
    assert db.email.await_args.kwargs["to"] != "akashorter@gmail.com"
    assert db.email.await_args.kwargs["subject"] == "Bricks held a booking"
    html = db.email.await_args.kwargs["html"]
    assert "bk_hold_" in html
    assert "10 King St" in html
    assert "2026-10-10" in html
    assert "0.91" in html
    assert "akashorter@gmail.com" not in html
    assert notify.await_args.kwargs["email_html"]
    assert "held for a person" in notify.await_args.kwargs["email_html"]


@pytest.mark.asyncio
async def test_jev_timeout_still_auto_assigns(monkeypatch):
    async def post(self, url, json=None, headers=None):
        raise TimeoutError("jev timed out")

    result, assign, notify, db = await _intake(monkeypatch, _payload(), _client(post))

    assert result["status"] == "assigned"
    assert result["assignee_id"] == "member_kam"
    assert assign.await_count == 1
    assert db.jobs.updates == []
    body = notify.await_args.kwargs["body"]
    assert "Assignee: member_kam" in body
    assert "held for a person" not in body
    assert db.sms.call_count == 0
    assert db.email.await_count == 0


@pytest.mark.asyncio
async def test_missing_openrouter_key_still_auto_assigns(monkeypatch):
    async def post(self, url, json=None, headers=None):
        raise AssertionError("Jev must not be called without OPENROUTER_API_KEY")

    result, assign, _, db = await _intake(
        monkeypatch, _payload(), _client(post), api_key=None
    )

    assert result["status"] == "assigned"
    assert result["assignee_id"] == "member_kam"
    assert assign.await_count == 1
    assert db.sms.call_count == 0
    assert db.email.await_count == 0


@pytest.mark.asyncio
async def test_yes_probability_at_threshold_still_auto_assigns(monkeypatch):
    async def post(self, url, json=None, headers=None):
        return _Resp(200, _noul(0.7))

    result, assign, _, _ = await _intake(monkeypatch, _payload(), _client(post))

    assert result["status"] == "assigned"
    assert assign.await_count == 1


@pytest.mark.asyncio
async def test_http_error_still_auto_assigns(monkeypatch):
    async def post(self, url, json=None, headers=None):
        return _Resp(503, {"error": "unavailable"})

    result, assign, _, _ = await _intake(monkeypatch, _payload(), _client(post))

    assert result["status"] == "assigned"
    assert assign.await_count == 1


@pytest.mark.asyncio
async def test_progress_capture_does_not_ask_jev(monkeypatch):
    async def post(self, url, json=None, headers=None):
        raise AssertionError("progress-capture intake is not the booking auto-assign path")

    # Live JobIntakeRequest.booking_id is a required str (progress-capture
    # visits are not this endpoint). A saved job with no booking_id must
    # still skip Jev and auto-assign.
    data = _payload().model_dump()
    data["booking_id"] = None
    payload = JobIntakeRequest.model_construct(**data)
    result, assign, _, _ = await _intake(monkeypatch, payload, _client(post))

    assert result["status"] == "assigned"
    assert assign.await_count == 1


def test_yes_probability_reads_noul_only():
    assert yes_probability(_noul(0.71)) == 0.71
    assert yes_probability({"answers": {}}) is None
    assert yes_probability({"answers": {"hold_for_human": {"type": "noul"}}}) is None
    assert yes_probability("nope") is None


def test_booking_state_uses_fields_already_on_the_job():
    state = booking_decision_state({
        "job_type": "drone",
        "package_name": "",
        "address": "—",
        "city": "",
        "lat": None,
        "lng": None,
        "vendor_tier": 2,
        "min_tier_required": 2,
        "preferred_tier": 3,
    })
    assert state["service"]["job_type"] == "drone"
    assert "package_name" in state["missing_fields"]
    assert "city" in state["missing_fields"]
    assert "address" in state["missing_fields"]
    assert state["location"]["address"] == "—"
    assert state["vendor_match_quality"]["label"] == "meets_min_tier"


def test_openrouter_key_is_not_hardcoded():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("app/services/jev_hold.py").read_text()
    assert 'os.environ.get("OPENROUTER_API_KEY")' in src
    assert "sk-or-" not in src
    assert "Bearer " not in src or "Bearer {api_key}" in src


@pytest.mark.asyncio
async def test_paid_booking_blanks_are_not_a_hold_by_themselves(monkeypatch):
    """A low yes-probability still auto-assigns even when optional fields are blank."""

    async def post(self, url, json=None, headers=None):
        return _Resp(200, _noul(0.2))

    payload = _payload(city="", package_name="", scheduled_date="")
    result, assign, _, db = await _intake(monkeypatch, payload, _client(post))

    assert result["status"] == "assigned"
    assert result["assignee_id"] == "member_kam"
    assert assign.await_count == 1
    assert db.jobs.updates == []


def test_hold_criteria_do_not_treat_paid_booking_blanks_as_a_hold():
    from app.services.jev_hold import _decision_body

    criteria = _decision_body({"booking_id": "bk"})["questions"]["hold_for_human"]["criteria"]
    true = criteria["true"].lower()
    false = criteria["false"].lower()
    assert "do not answer true only because" in true
    assert "lat/lng" in true
    assert "not a hold by themselves" in false
    assert "missing lat/lng is not a hold" in false
    assert "not a reason to hold" in false


@pytest.mark.asyncio
async def test_hold_notify_failure_still_does_not_assign(monkeypatch):
    async def post(self, url, json=None, headers=None):
        return _Resp(200, _noul(0.91))

    result, assign, _, db = await _intake(
        monkeypatch, _payload(), _client(post), channels_fail=True
    )

    assert result["status"] == "held_for_review"
    assert result["assignee_id"] == ""
    assert assign.await_count == 0
    assert db.sms.call_count == 1
    assert db.email.await_count == 1
