import asyncio, os
import pytest
from tests._fake_ledger import FakeLedger
from tests._fake_supabase import FakeSupabase
from app.services import payout_source as ps

def run(c):
    return asyncio.run(c)

@pytest.fixture
def pay_env(monkeypatch):
    monkeypatch.setenv("PAYOUT_SCHEDULE_MONDAY", "1")
    from app.services import payout_service as svc, probation_hold
    import app.services.stripe_connect_service as scs
    FakeLedger(monkeypatch)
    calls = []
    async def transfer(**k):
        calls.append(k); return f"tr_{len(calls)}"
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
    return {"id": "j1", "booking_id": "L1", "status": "delivered", "vendor_id": "default_vendor",
            "assignee_id": "m1", "address": "1 Test Lane", "rack_price": 300, "vendor_tier": 1,
            "package_id": "essentials"}

def test_second_trigger_on_scheduled_does_not_crash(pay_env):
    svc, calls = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    first = run(svc.trigger_payout_for_job(db, "j1"))
    assert first["status"] == "scheduled"
    second = run(svc.trigger_payout_for_job(db, "j1"))
    assert second["status"] == "scheduled"
    assert calls == []

def test_pending_row_converts_to_scheduled_when_flag_flips(pay_env):
    svc, calls = pay_env
    from app.services.payout_service import payout_row_id
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    db.tables["payouts"] = [{"id": payout_row_id("j1"), "job_id": "j1", "status": "pending", "amount": 10000}]
    out = run(svc.trigger_payout_for_job(db, "j1"))
    assert out["status"] == "scheduled"
    assert db.rows("payouts", job_id="j1")[0]["status"] == "scheduled"
    assert calls == []
