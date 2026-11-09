"""Weekly Monday payout batch: eligible after delivery + payment proof, transferred on Monday."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import payout_source as ps
from app.services import refund_service as rs
from tests._fake_ledger import FakeLedger
from tests._fake_supabase import FakeSupabase

TORONTO = ZoneInfo("America/Toronto")


def run(c):
    return asyncio.run(c)


@pytest.fixture
def monday_on(monkeypatch):
    monkeypatch.setenv("PAYOUT_SCHEDULE_MONDAY", "1")


@pytest.fixture
def pay_env(monkeypatch, monday_on):
    from app.services import payout_service as svc, probation_hold
    import app.services.stripe_connect_service as scs

    FakeLedger(monkeypatch)
    calls = []

    async def transfer(**k):
        calls.append(k)
        return f"tr_{len(calls)}"

    async def src(db, job, with_charge=True):
        return {"livemode": True, "refunded": False, "charge_id": "ch_1", "source": "t"}

    async def block(db, job, src=None, key_mode=None):
        return None

    async def no_hold(db, job):
        return None

    async def rate(*a, **k):
        return 10000

    monkeypatch.setattr(scs, "handle_payout_to_vendor", transfer)
    monkeypatch.setattr(ps, "booking_source", src)
    monkeypatch.setattr(ps, "payment_block", block)
    monkeypatch.setattr(ps, "stripe_key_mode", lambda: True)
    monkeypatch.setattr(probation_hold, "hold_for_payout", no_hold)
    monkeypatch.setattr(svc, "get_fixed_payout_amount", rate)
    return svc, calls


def _job():
    return {
        "id": "j1", "booking_id": "L1", "status": "delivered",
        "vendor_id": "default_vendor", "assignee_id": "m1",
        "address": "1 Test Lane", "rack_price": 300, "vendor_tier": 1,
        "package_id": "essentials",
    }


def test_eligible_job_is_scheduled_not_transferred(pay_env):
    svc, calls = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    out = run(svc.trigger_payout_for_job(db, "j1"))
    assert out["status"] == "scheduled"
    assert calls == []
    row = db.rows("payouts", job_id="j1")[0]
    assert row["status"] == "scheduled"
    assert db.tables["jobs"][0]["payout_status"] == "scheduled"


def test_monday_release_pays_once(pay_env):
    svc, calls = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    run(svc.trigger_payout_for_job(db, "j1"))
    first = run(svc.release_scheduled_payouts(db))
    assert first["released"][0]["result"]["status"] == "paid"
    assert [c["payout_id"] for c in calls] == [db.rows("payouts", job_id="j1")[0]["id"]]
    run(svc.trigger_payout_for_job(db, "j1"))
    run(svc.release_scheduled_payouts(db))
    assert len(calls) == 1
    assert db.rows("payouts", job_id="j1")[0]["status"] == "paid"


def test_refund_before_monday_cancels_the_row(pay_env):
    svc, calls = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    run(svc.trigger_payout_for_job(db, "j1"))
    out = run(rs.apply_booking_refund(db, "L1", full=True, refund_id="re_m"))
    assert db.rows("payouts", job_id="j1")[0]["status"] == "cancelled"
    assert out["jobs"][0]["action"] == "payout_cancelled"
    released = run(svc.release_scheduled_payouts(db))
    assert released["released"] == []
    assert calls == []


def test_monday_sweep_runs_once_after_nine_et(pay_env):
    svc, calls = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    run(svc.trigger_payout_for_job(db, "j1"))
    sunday = datetime(2026, 11, 8, 10, 0, tzinfo=TORONTO)
    monday_early = datetime(2026, 11, 9, 8, 30, tzinfo=TORONTO)
    monday = datetime(2026, 11, 9, 9, 5, tzinfo=TORONTO)
    assert run(svc.maybe_run_monday_payouts(db, sunday)) is None
    assert run(svc.maybe_run_monday_payouts(db, monday_early)) is None
    first = run(svc.maybe_run_monday_payouts(db, monday))
    assert first and first["week"] == "2026-W46"
    assert first["released"][0]["result"]["status"] == "paid"
    again = run(svc.maybe_run_monday_payouts(db, monday.replace(hour=15)))
    assert again["skipped"] == "already_ran"
    assert len(calls) == 1


def test_next_monday_skips_the_current_monday_after_nine():
    from app.services.payout_service import next_monday_0930
    after = datetime(2026, 11, 9, 10, 0, tzinfo=TORONTO)
    nxt = next_monday_0930(after)
    assert nxt.date().isoformat() == "2026-11-16"
    before = datetime(2026, 11, 9, 8, 0, tzinfo=TORONTO)
    assert next_monday_0930(before).date().isoformat() == "2026-11-09"
