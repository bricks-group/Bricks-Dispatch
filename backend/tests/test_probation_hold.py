"""Probation payout hold (Kam, 2026-11-02): first N jobs hold the payout until
the client rating lands or the auto-release window passes with no complaint.
Stripe is mocked; nothing here can move money or send a message."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import payout_service as ps
from app.services import probation_hold as ph
from app.services import vendor_vetting as vv


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append((k, lambda x, v=v: str(x) == str(v)))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            rows.append(dict(self.payload))
            return _Res([self.payload])
        hit = [r for r in rows if all(f(r.get(k)) for k, f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return _Res([dict(r) for r in hit])


class FakeDB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return _Q(self, name)


NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
JOB = {"id": "j1", "assignee_id": "m1", "address": "1 Test St", "booking_id": "b1", "status": "completed",
       "vendor_id": "default_vendor", "rack_price": 225, "package_id": "hdr_photo"}


def _db(**extra):
    t = {"jobs": [dict(JOB)], "vendor_members": [{"id": "m1", "name": "Test Vendor", "vendor_type": "cleaner"}],
         "vendor_probation_reviews": [], "vendor_reviews": [], "uploads": [], "booking_disputes": [], "payouts": []}
    t.update(extra)
    return FakeDB(**t)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("OPS_ACTION_SECRET", "test-secret")


def test_decide_rules():
    row = {"release_after": (NOW + timedelta(hours=1)).isoformat()}
    assert ph.decide(row, None, False, False, NOW)[0] == "wait"
    assert ph.decide(row, 5, False, False, NOW)[0] == "release"
    assert ph.decide(row, 3, False, False, NOW)[0] == "release"
    assert ph.decide(row, 2, False, False, NOW) == ("block", "client rating 2/5")
    assert ph.decide(row, 5, True, False, NOW) == ("block", "QC failed")
    assert ph.decide(row, None, False, True, NOW)[0] == "dispute"
    assert ph.decide(row, 5, False, True, NOW)[0] == "dispute"  # a complaint outranks a good rating
    due = {"release_after": (NOW - timedelta(minutes=1)).isoformat()}
    assert ph.decide(due, None, False, False, NOW)[0] == "release"
    assert ph.decide(due, None, False, True, NOW)[0] == "dispute"


def test_qc_failed_threshold():
    assert not ph.qc_failed([])
    assert not ph.qc_failed([{"qc_status": "passed"}, {"qc_status": "passed"}, {"qc_status": "rejected"}])
    assert ph.qc_failed([{"qc_status": "passed"}, {"qc_status": "rejected"}])
    assert not ph.qc_failed([{"qc_status": "rejected", "provider": "bracket_sibling"}, {"qc_status": "passed"}])


def test_new_row_defaults_72h():
    r = ph.new_row("j1", "m1", NOW)
    assert r["payout_hold"] == "held" and r["status"] == "pending"
    assert r["release_after"] == (NOW + timedelta(hours=72)).isoformat()


def test_record_probation_job_creates_held_row():
    db = _db()
    asyncio.run(vv.record_probation_job(db, JOB))
    assert db.tables["vendor_probation_reviews"][0]["payout_hold"] == "held"


def _payout_patches(db):
    async def sel(_db, job_id):
        rows = [p for p in db.tables["payouts"] if p["job_id"] == job_id]
        return dict(rows[0]) if rows else None

    async def ins(_db, rec):
        db.tables["payouts"].append(dict(rec))
        return dict(rec)

    async def upd(_db, pid, fields):
        for p in db.tables["payouts"]:
            if p["id"] == pid:
                p.update(fields)

    stripe = AsyncMock(return_value="tr_test_1")
    return stripe, [
        patch.object(ps, "_db_select_job", AsyncMock(return_value=dict(JOB))),
        patch.object(ps, "_db_select_vendor_member", AsyncMock(return_value={"tier": 2})),
        patch.object(ps, "_db_select_payout_by_job", side_effect=sel),
        patch.object(ps, "_db_insert_payout", side_effect=ins),
        patch.object(ps, "_db_update_payout", side_effect=upd),
        patch.object(ps, "_db_update_job_payout_amount", AsyncMock()),
        patch.object(ps, "get_fixed_payout_amount", AsyncMock(return_value=7150)),
        patch("app.services.vendor_payout_rate_card.addon_payout_cents", return_value=0),
        patch("app.services.client_service_payouts.set_payout_cents", return_value=None),
        patch("app.services.commercial_jobs.is_employee", return_value=False),
        patch("app.services.stripe_connect_service.handle_payout_to_vendor", stripe),
        patch.object(ph, "alert_owner", AsyncMock(return_value=True)),
    ]


def _run(db, coro_fn):
    stripe, ps_ = _payout_patches(db)
    for p in ps_:
        p.start()
    try:
        return stripe, asyncio.run(coro_fn())
    finally:
        for p in reversed(ps_):
            p.stop()


def test_probation_payout_is_held_then_released_on_good_rating():
    db = _db()
    stripe, res = _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    assert res["status"] == "held" and stripe.await_count == 0
    assert db.tables["payouts"][0]["status"] == "held"
    db.tables["vendor_reviews"].append({"job_id": "j1", "vendor_id": "m1", "rating": 5})
    stripe, out = _run(db, lambda: ph.run_probation_sweep(db, job_id="j1"))
    assert out["results"][0]["action"] == "release" and stripe.await_count == 1
    assert db.tables["payouts"][0]["status"] == "paid"
    row = db.tables["vendor_probation_reviews"][0]
    assert row["payout_hold"] == "released" and row["status"] == "approved" and row["client_rating"] == 5
    # Re-running is a no-op.
    stripe, out = _run(db, lambda: ph.run_probation_sweep(db))
    assert out["checked"] == 0 and stripe.await_count == 0


def test_auto_release_after_window_with_no_complaint():
    db = _db()
    _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    stripe, out = _run(db, lambda: ph.run_probation_sweep(db, now=NOW))
    assert out["results"][0]["action"] == "wait" and stripe.await_count == 0
    later = datetime.now(timezone.utc) + timedelta(hours=73)
    stripe, out = _run(db, lambda: ph.run_probation_sweep(db, now=later))
    assert out["results"][0]["action"] == "release" and stripe.await_count == 1


def test_bad_rating_blocks_pauses_and_alerts_once():
    db = _db()
    _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    db.tables["vendor_reviews"].append({"job_id": "j1", "vendor_id": "m1", "rating": 1})
    stripe, out = _run(db, lambda: ph.run_probation_sweep(db))
    r = out["results"][0]
    assert r["action"] == "block" and r["paused"] is True and stripe.await_count == 0
    assert db.tables["vendor_members"][0]["paused_reason"] == "Probation: client rating 1/5"
    row = db.tables["vendor_probation_reviews"][0]
    assert row["payout_hold"] == "blocked" and row["status"] == "failed"
    assert db.tables["payouts"][0]["status"] == "held"
    # Blocked rows are not re-swept; a later payout trigger stays held.
    stripe, out = _run(db, lambda: ph.run_probation_sweep(db))
    assert out["checked"] == 0
    stripe, res = _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    assert res["status"] == "held" and stripe.await_count == 0


def test_qc_fail_at_delivery_blocks_immediately():
    db = _db(uploads=[{"job_id": "j1", "qc_status": "rejected"}, {"job_id": "j1", "qc_status": "rejected"}])
    stripe, res = _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    assert res["status"] == "held" and res["probation"]["action"] == "block"
    assert db.tables["vendor_members"][0]["paused_reason"] == "Probation: QC failed"


def test_open_dispute_keeps_hold_without_pausing():
    db = _db(booking_disputes=[{"booking_id": "b1", "status": "open"}])
    _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    later = datetime.now(timezone.utc) + timedelta(hours=100)
    stripe, out = _run(db, lambda: ph.run_probation_sweep(db, now=later))
    assert out["results"][0]["action"] == "dispute" and stripe.await_count == 0
    assert not db.tables["vendor_members"][0].get("paused_at")


def test_jobs_after_probation_pay_immediately():
    rows = [{"job_id": f"old{i}", "member_id": "m1", "payout_hold": "released"} for i in range(3)]
    db = _db(vendor_probation_reviews=rows)
    stripe, res = _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    assert res["status"] == "paid" and stripe.await_count == 1


def test_owner_one_tap_release_resumes_and_pays():
    db = _db()
    _run(db, lambda: ps.trigger_payout_for_job(db, "j1"))
    db.tables["vendor_reviews"].append({"job_id": "j1", "vendor_id": "m1", "rating": 2})
    _run(db, lambda: ph.run_probation_sweep(db))
    from app.services import ops_autopilot as ops
    url = ph.action_url("m1", "j1", "release")
    assert "/api/ops/act?t=" in url
    tok = ops.verify_action(url.split("t=", 1)[1])
    assert tok and tok.get("a") == "probation_release" and tok.get("p") == {"member_id": "m1", "job_id": "j1"}
    stripe, out = _run(db, lambda: ops.run_action(db, tok))
    assert out["ok"] and out["result"]["resumed"] and stripe.await_count == 1
    assert db.tables["vendor_members"][0]["paused_at"] is None
    assert db.tables["payouts"][0]["status"] == "paid"


def test_owner_and_noshow_never_held():
    db = _db()
    assert asyncio.run(ph.hold_for_payout(db, {"id": "j1", "assignee_id": "default_user"})) is None
    assert asyncio.run(ph.hold_for_payout(db, {"id": "j1", "assignee_id": "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580"})) is None


def test_routes_auth_and_confirm_page(monkeypatch):
    from fastapi import HTTPException
    from app.routers import onboarding

    class Req:
        def __init__(self, **h):
            self.headers = h

    monkeypatch.setenv("CRON_SECRET", "cron-x")
    monkeypatch.setenv("INTERNAL_API_KEYS", "ik-1")
    db = _db()
    monkeypatch.setattr(onboarding, "get_db", AsyncMock(return_value=db))
    for bad in (Req(), Req(**{"X-Internal-Api-Key": "nope"}), Req(Authorization="Bearer wrong")):
        with pytest.raises(HTTPException) as e:
            asyncio.run(onboarding.probation_sweep(bad))
        assert e.value.status_code == 401
    assert asyncio.run(onboarding.probation_sweep(Req(Authorization="Bearer cron-x")))["checked"] == 0
    assert asyncio.run(onboarding.probation_sweep(Req(**{"X-Internal-Api-Key": "ik-1"}), job_id="j1"))["ok"]
