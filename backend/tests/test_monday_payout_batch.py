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


def test_monday_batch_releases_claim_after_crash(pay_env, monkeypatch):
    svc, calls = pay_env
    db = FakeSupabase()
    second = dict(_job())
    second["id"] = "j2"
    second["booking_id"] = "L2"
    db.tables["jobs"] = [_job(), second]
    run(svc.trigger_payout_for_job(db, "j1"))
    run(svc.trigger_payout_for_job(db, "j2"))

    async def boom(db, now=None, deadline=None):
        rows = (await db.table("payouts").select("*").eq("status", "scheduled").execute()).data or []
        first = rows[0]
        await svc.trigger_payout_for_job(db, first["job_id"], pay_now=True)
        raise RuntimeError("host timeout")

    original_release = svc.release_scheduled_payouts
    monkeypatch.setattr(svc, "release_scheduled_payouts", boom)
    monday = datetime(2026, 11, 9, 9, 5, tzinfo=TORONTO)
    with pytest.raises(RuntimeError, match="host timeout"):
        run(svc.maybe_run_monday_payouts(db, monday))
    statuses = {row["job_id"]: row["status"] for row in db.tables["payouts"]}
    assert statuses["j1"] != statuses["j2"]
    assert "paid" in statuses.values() and "scheduled" in statuses.values()
    assert len(calls) == 1

    monkeypatch.setattr(svc, "release_scheduled_payouts", original_release)
    again = run(svc.maybe_run_monday_payouts(db, monday.replace(hour=10)))
    assert again.get("skipped") != "already_ran"
    assert {row["status"] for row in db.tables["payouts"]} == {"paid"}
    assert len(calls) == 2


def test_monday_batch_time_budget_retries_unpaid_rows(pay_env, monkeypatch):
    monkeypatch.setenv("PAYOUT_MONDAY_BUDGET_SEC", "0")
    svc, calls = pay_env
    db = FakeSupabase()
    second = dict(_job())
    second["id"] = "j2"
    second["booking_id"] = "L2"
    db.tables["jobs"] = [_job(), second]
    run(svc.trigger_payout_for_job(db, "j1"))
    run(svc.trigger_payout_for_job(db, "j2"))
    monday = datetime(2026, 11, 9, 9, 5, tzinfo=TORONTO)
    first = run(svc.maybe_run_monday_payouts(db, monday))
    assert first["incomplete"] is True
    assert first["claim"] == "released"
    assert len(first["released"]) == 1
    assert len(calls) == 1
    second_run = run(svc.maybe_run_monday_payouts(db, monday.replace(hour=10)))
    assert second_run.get("skipped") != "already_ran"
    assert second_run.get("incomplete") is False
    assert second_run.get("claim") == "held"
    assert {row["status"] for row in db.tables["payouts"]} == {"paid"}
    assert len(calls) == 2
    again = run(svc.maybe_run_monday_payouts(db, monday.replace(hour=12)))
    assert again["skipped"] == "already_ran"
    assert len(calls) == 2


def test_stale_monday_claim_lease_is_reclaimed(pay_env, monkeypatch):
    from datetime import timedelta, timezone
    svc, calls = pay_env
    ledger = FakeLedger(monkeypatch)
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    run(svc.trigger_payout_for_job(db, "j1"))
    monday = datetime(2026, 11, 9, 9, 5, tzinfo=TORONTO)
    key = "payouts:monday-batch:2026-W46"
    ledger.rows[key] = {
        "id": key, "entity_id": "payouts", "action": "monday_batch",
        "created_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
    }
    out = run(svc.maybe_run_monday_payouts(db, monday))
    assert out.get("skipped") != "already_ran"
    assert out["released"][0]["result"]["status"] == "paid"
    assert len(calls) == 1


def test_fresh_monday_claim_is_not_reclaimed(pay_env, monkeypatch):
    from datetime import timezone
    svc, calls = pay_env
    ledger = FakeLedger(monkeypatch)
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    run(svc.trigger_payout_for_job(db, "j1"))
    monday = datetime(2026, 11, 9, 9, 5, tzinfo=TORONTO)
    key = "payouts:monday-batch:2026-W46"
    ledger.rows[key] = {
        "id": key, "entity_id": "payouts", "action": "monday_batch",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    out = run(svc.maybe_run_monday_payouts(db, monday))
    assert out["skipped"] == "already_ran"
    assert calls == []
    assert db.rows("payouts", job_id="j1")[0]["status"] == "scheduled"


def test_monday_list_failure_holds_week_and_alerts(pay_env, monkeypatch):
    svc, calls = pay_env
    alerts = []

    async def alert(key, job_id, title, lines):
        alerts.append({"key": key, "job_id": job_id, "title": title, "lines": lines})
        return True

    monkeypatch.setattr("app.services.delivery_ledger.alert_kam", alert)

    class Boom:
        def table(self, name):
            raise RuntimeError("db down")

    listed = run(svc.release_scheduled_payouts(Boom()))
    assert listed["list_failed"] is True
    assert listed["incomplete"] is False
    assert listed["released"] == []

    monday = datetime(2026, 11, 9, 9, 5, tzinfo=TORONTO)
    out = run(svc.maybe_run_monday_payouts(Boom(), monday))
    assert out["claim"] == "held"
    assert out["list_failed"] is True
    assert out["alerted"] is True
    assert calls == []
    assert alerts[0]["key"] == "monday-payout-list:2026-W46"
    again = run(svc.maybe_run_monday_payouts(Boom(), monday.replace(hour=10)))
    assert again["skipped"] == "already_ran"
    assert len(alerts) == 1
