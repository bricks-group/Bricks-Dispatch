"""No confirmed payment, no payout (Kam 2026-11-09): unit tests for the rule."""
import asyncio
import uuid

import pytest

from app.services import payout_source as ps
from tests._fake_ledger import FakeLedger
from tests._fake_supabase import FakeSupabase

pytestmark = pytest.mark.real_payment_proof


def run(c):
    return asyncio.run(c)


def U():
    return str(uuid.uuid4())


def _db(**tables):
    db = FakeSupabase()
    for k, v in tables.items():
        db.tables[k] = v
    return db


def block(db, job, km=True, src=None):
    return run(ps.payment_block(db, job, src or {}, key_mode=km))


@pytest.fixture(autouse=True)
def _ledger(monkeypatch):
    return FakeLedger(monkeypatch)


def test_test_orders_never_pay_even_on_a_test_key():
    for bid in ("e2e-go-fairuse-x", "test-1", "E2E_listing"):
        assert block(_db(), {"id": "j", "booking_id": bid}, km=False) == "test_booking"


def test_unknown_key_blocks():
    assert block(_db(), {"id": "j", "booking_id": U()}, km=None) == "stripe_key_unknown"


def test_no_or_unknown_booking_blocks():
    assert block(_db(), {"id": "j"}) == "no_booking"
    assert block(_db(), {"id": "j", "booking_id": U()}) == "booking_not_found"
    assert block(_db(), {"id": "j", "booking_id": "not-a-uuid"}) == "booking_not_found"


def test_live_paid_lead_passes_and_mode_must_match_the_key():
    b = U()
    db = _db(media_booking_leads=[{"id": b, "stripe_session_id": "cs_live_1", "status": "paid"}])
    assert block(db, {"id": "j", "booking_id": b}, km=True) is None
    assert block(db, {"id": "j", "booking_id": b}, km=False) == "live_payment_test_key"
    t = U()
    db.tables["media_booking_leads"].append({"id": t, "stripe_session_id": "cs_test_1", "status": "paid"})
    assert block(db, {"id": "j", "booking_id": t}, km=True) == "sandbox_booking"
    assert block(db, {"id": "j", "booking_id": t}, km=False) is None


def test_refunds_block():
    a, b = U(), U()
    db = _db(media_booking_leads=[{"id": a, "stripe_session_id": "cs_live_1", "status": "refunded"},
                                  {"id": b, "stripe_session_id": "cs_live_2", "status": "partially_refunded"}])
    assert block(db, {"id": "j", "booking_id": a}) == "booking_refunded"
    assert block(db, {"id": "j", "booking_id": b}) == "booking_partially_refunded"
    assert block(_db(), {"id": "j", "booking_id": U()}, src={"refunded": True}) == "booking_refunded"


def test_commercial_booking_with_cb_prefix():
    c = U()
    db = _db(commercial_bookings=[{"id": c, "stripe_session_id": "cs_live_c", "livemode": True, "status": "pending"}])
    assert block(db, {"id": "j", "booking_id": f"cb_{c}"}) == "booking_unpaid"
    db.tables["commercial_bookings"][0]["status"] = "paid"
    assert block(db, {"id": "j", "booking_id": f"cb_{c}"}) is None
    db.tables["commercial_bookings"][0]["livemode"] = False
    assert block(db, {"id": "j", "booking_id": f"cb_{c}"}) == "sandbox_booking"


def test_app_booking_needs_a_paid_stripe_session():
    b = U()
    row = {"id": b, "status": "confirmed", "stripe_session_id": "cs_live_b", "stripe_payment_status": "unpaid"}
    db = _db(bookings=[row])
    assert block(db, {"id": "j", "booking_id": b}) == "booking_unpaid"
    row["stripe_payment_status"] = "paid"
    assert block(db, {"id": "j", "booking_id": b}) is None
    row["stripe_session_id"] = None
    assert block(db, {"id": "j", "booking_id": b}) == "payment_mode_unknown"
    row.update(stripe_session_id="cs_live_b", refunded_at="2026-11-09", refund_amount=500, total_cents=10000)
    assert block(db, {"id": "j", "booking_id": b}) == "booking_partially_refunded"
    row["refund_amount"] = 10000
    assert block(db, {"id": "j", "booking_id": b}) == "booking_refunded"


def test_lookup_error_is_not_read_as_paid():
    db = _db()
    db.down["media_booking_leads"] = True
    assert block(db, {"id": "j", "booking_id": U()}) == "booking_lookup_failed"


def test_owner_confirmation_covers_only_no_payment_on_record(_ledger):
    _ledger.rows["payment-confirmed:j"] = {"id": "payment-confirmed:j", "entity_id": "j", "created_at": "x"}
    assert block(_db(), {"id": "j"}) is None
    assert block(_db(), {"id": "j", "booking_id": U()}) is None
    assert block(_db(), {"id": "j", "booking_id": "e2e-x"}) == "test_booking"
    r = U()
    db = _db(media_booking_leads=[{"id": r, "stripe_session_id": "cs_live_1", "status": "refunded"}])
    assert block(db, {"id": "j", "booking_id": r}) == "booking_refunded"
    t = U()
    db = _db(media_booking_leads=[{"id": t, "stripe_session_id": "cs_test_1", "status": "paid"}])
    assert block(db, {"id": "j", "booking_id": t}) == "sandbox_booking"


# ---------------------------------------------------------------- recording

def _job(jid="j1", bid=None):
    return {"id": jid, "booking_id": bid, "status": "delivered", "vendor_id": "default_vendor",
            "assignee_id": "m1", "address": "1 Test Lane"}


@pytest.fixture
def rec(monkeypatch):
    from app.services import delivery_ledger as dl
    sent = []

    async def alert(key, job_id, title, lines):
        if key in {s[0] for s in sent}:
            return False
        sent.append((key, title, lines)); return True
    monkeypatch.setattr(dl, "alert_kam", alert)
    return sent


def test_blocked_payout_is_recorded_and_kam_alerted_once(monkeypatch, rec):
    from app.services import payout_service as svc
    import app.services.stripe_connect_service as scs
    calls = []

    async def transfer(**k):
        calls.append(k); return "tr_x"
    monkeypatch.setattr(scs, "handle_payout_to_vendor", transfer)
    monkeypatch.setattr(ps, "stripe_key_mode", lambda: True)
    db = _db(jobs=[_job()])
    for _ in range(3):
        out = run(svc.trigger_payout_for_job(db, "j1"))
    assert out["skipped"] == "no_booking" and out["blocked"] is True
    assert not calls
    assert [(p["status"], p["block_reason"]) for p in db.rows("payouts", job_id="j1")] == [("blocked", "no_booking")]
    assert len(rec) == 1 and "no confirmed payment" in rec[0][1]
    assert any("no booking linked" in l for l in rec[0][2])
    assert not any(code in line for line in rec[0][2] for code in ps.BLOCK_TEXT)  # plain words, no codes


def test_lookup_failure_is_retriable_and_not_alerted(monkeypatch, rec):
    from app.services import payout_service as svc
    monkeypatch.setattr(ps, "stripe_key_mode", lambda: True)
    db = _db(jobs=[_job(bid=U())])
    db.down["media_booking_leads"] = True
    run(svc.trigger_payout_for_job(db, "j1"))
    assert [p["status"] for p in db.rows("payouts", job_id="j1")] == ["failed"] and not rec


def test_block_never_overwrites_a_paid_payout(monkeypatch, rec):
    from app.services import payout_service as svc
    db = _db(jobs=[_job()], payouts=[{"id": "p", "job_id": "j1", "status": "paid", "stripe_transfer_id": "tr_1"}])
    run(svc._record_block(db, _job(), "booking_refunded"))
    assert db.one("payouts", id="p")["status"] == "paid" and "block_reason" not in db.one("payouts", id="p")


def test_block_reason_column_missing_still_records_status(monkeypatch, rec):
    from app.services import payout_service as svc
    db = _db(jobs=[_job()])
    real = db.table

    class NoCol(Exception):
        code = "PGRST204"

    def table(name):
        q = real(name)
        if name == "payouts":
            orig = q.execute

            async def execute():
                if q.op in ("insert", "update") and "block_reason" in (q.payload or {}):
                    raise NoCol("Could not find the 'block_reason' column of 'payouts'")
                return await orig()
            q.execute = execute
        return q
    db.table = table
    out = run(svc._record_block(db, _job(), "no_booking"))
    assert out["status"] == "blocked" and db.rows("payouts", job_id="j1")[0]["status"] == "blocked"
