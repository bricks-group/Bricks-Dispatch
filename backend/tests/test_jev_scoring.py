"""Jev fit scales the existing vendor score inside bricks_smart_assign.

assign_vendor_for_job uses that scorer. A Jev failure leaves the ranking unchanged.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.bricks_ai_dispatch import bricks_smart_assign
from app.services.jev_hold import apply_jev_fit, jev_candidate_poor_fits
from app.shared.schemas import Job, VendorMember


def _member(member_id: str, quality: float) -> VendorMember:
    return VendorMember(
        id=member_id,
        name=member_id,
        email=f"{member_id}@test.com",
        specialty="photo",
        skills=["photo"],
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        quality_rating=quality,
    )


def _job() -> Job:
    return Job(
        id="job_score_1",
        booking_id="bk_score_1",
        address="10 King St",
        city="Toronto",
        state="ON",
        job_type="photo",
        time_window_start="09:00",
        scheduled_date="2026-10-10",
    )


def test_apply_jev_fit_scales_score_and_fail_open_is_identity():
    assert apply_jev_fit(80.0, None) == 80.0
    assert apply_jev_fit(80.0, 0.0) == 80.0
    assert apply_jev_fit(80.0, 0.25) == 60.0


@pytest.mark.asyncio
async def test_jev_poor_fit_lowers_the_higher_base_score(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    high = _member("high", 5.0)
    low = _member("low", 3.0)
    job = _job()

    with patch("app.services.jev_hold.jev_candidate_poor_fits", AsyncMock(return_value={})):
        baseline = await bricks_smart_assign(job, [high, low])
    assert baseline == "high"

    with patch(
        "app.services.jev_hold.jev_candidate_poor_fits",
        AsyncMock(return_value={"high": 0.95, "low": 0.05}),
    ):
        flipped = await bricks_smart_assign(job, [high, low])
    assert flipped == "low"


@pytest.mark.asyncio
async def test_jev_failure_keeps_todays_ranking(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            raise TimeoutError("jev timed out")

    with patch("app.services.jev_hold.httpx.AsyncClient", FakeClient):
        chosen = await bricks_smart_assign(_job(), [_member("high", 5.0), _member("low", 3.0)])
        fits = await jev_candidate_poor_fits({"booking_id": "bk", "id": "job"}, [_member("high", 5.0)])
    assert fits == {}
    assert chosen == "high"


@pytest.mark.asyncio
async def test_missing_key_does_not_change_ranking(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("Jev must not be called without OPENROUTER_API_KEY")

    with patch("app.services.jev_hold.httpx.AsyncClient", FakeClient):
        chosen = await bricks_smart_assign(_job(), [_member("high", 5.0), _member("low", 3.0)])
    assert chosen == "high"


@pytest.mark.asyncio
async def test_poor_fit_that_still_assigns_does_not_notify(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    sms = MagicMock(side_effect=AssertionError("poor-fit assign must not text"))
    email = AsyncMock(side_effect=AssertionError("poor-fit assign must not email"))
    with patch("app.services.jev_hold.jev_candidate_poor_fits", AsyncMock(return_value={"high": 0.95, "low": 0.05})), \
            patch("app.notifications.send_dispatch_sms", sms), \
            patch("app.services.notifications.send_email_via_resend", email):
        chosen = await bricks_smart_assign(_job(), [_member("high", 5.0), _member("low", 3.0)])
    assert chosen == "low"
    assert sms.call_count == 0
    assert email.await_count == 0
