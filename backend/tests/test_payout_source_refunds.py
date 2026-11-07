"""Launch gaps 2026-11-07: sandbox bookings never pay live; refunds hold payouts;
transfers link to the booking's charge."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import payout_source as ps
from app.services import refund_service as rs


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.patch = db, table, {}, None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def limit(self, *_a):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def update(self, fields):
        self.patch = fields
        return self

    async def execute(self):
        rows = [r for r in self.db.rows.get(self.table, [])
                if all(str(r.get(k)) == str(v) for k, v in self.filters.items())]
        if self.patch is not None:
            for r in rows:
                r.update(self.patch)
            self.db.updates.append((self.table, dict(self.filters), dict(self.patch)))
        if getattr(self, "_single", False):
            return SimpleNamespace(data=rows[0] if rows else None)
        return SimpleNamespace(data=rows)


class FakeDB:
    def __init__(self, **rows):
        self.rows = rows
        self.updates = []

    def table(self, name):
        return _Q(self, name)


def run(c):
    return asyncio.run(c)


# ── mode detection ─────────────────────────────────────────────────────────
def test_key_and_session_modes():
    assert ps.stripe_key_mode("sk_live_x") is True
    assert ps.stripe_key_mode("rk_live_x") is True
    assert ps.stripe_key_mode("sk_test_x") is False
    assert ps.stripe_key_mode("") is None
    assert ps.session_mode("cs_test_a") is False
    assert ps.session_mode("cs_live_a") is True
    assert ps.session_mode(None) is None


def test_sandbox_lead_is_detected_and_blocked_under_live_key():
    db = FakeDB(media_booking_leads=[{"id": "L1", "stripe_session_id": "cs_test_abc", "status": "paid"}])
    src = run(ps.booking_source(db, {"booking_id": "L1"}))
    assert src["livemode"] is False and src["source"] == "media_booking_leads"
    assert ps.payout_block_reason(src, key_mode=True) == "sandbox_booking"
    # A test-mode Dispatch (sk_test) may move test money for a test booking.
    assert ps.payout_block_reason(src, key_mode=False) is None


def test_property_details_marker_wins_even_without_lead():
    src = run(ps.booking_source(FakeDB(), {"booking_id": "", "property_details": "paid · stripe cs_test_zzz"}))
    assert src["livemode"] is False
    assert ps.payout_block_reason(src, key_mode=True) == "sandbox_booking"


def test_commercial_booking_livemode_false_blocks():
    db = FakeDB(commercial_bookings=[{"id": "C1", "stripe_session_id": "cs_live_q", "livemode": False, "status": "paid"}])
    src = run(ps.booking_source(db, {"booking_id": "C1"}, with_charge=False))
    assert src["livemode"] is False
    assert ps.payout_block_reason(src, key_mode=True) == "sandbox_booking"


def test_unknown_source_keeps_paying():
    src = run(ps.booking_source(FakeDB(), {"booking_id": "nope"}))
    assert src["livemode"] is None
    assert ps.payout_block_reason(src, key_mode=True) is None


def test_lookup_errors_never_raise():
    db = MagicMock()  # .execute() is not awaitable -> swallowed
    src = run(ps.booking_source(db, {"booking_id": "x"}))
    assert src["livemode"] is None


def test_refunded_lead_or_job_blocks():
    db = FakeDB(media_booking_leads=[{"id": "L2", "stripe_session_id": "cs_live_a", "status": "refunded"}])
    src = run(ps.booking_source(db, {"booking_id": "L2"}, with_charge=False))
    assert ps.payout_block_reason(src, key_mode=True) == "booking_refunded"
    src2 = run(ps.booking_source(FakeDB(), {"payout_status": "refunded"}))
    assert ps.payout_block_reason(src2, key_mode=True) == "booking_refunded"


def test_live_session_charge_is_linked_and_refund_seen():
    db = FakeDB(media_booking_leads=[{"id": "L3", "stripe_session_id": "cs_live_ok", "status": "paid"}])
    sess = {"payment_intent": {"latest_charge": {"id": "ch_1", "amount": 11187, "amount_refunded": 0,
                                                  "refunded": False, "livemode": True}}}
    with patch.object(ps, "stripe_key_mode", return_value=True), \
         patch("app.services.stripe_connect_service.stripe_lib.checkout.Session.retrieve", return_value=sess) as r:
        src = run(ps.booking_source(db, {"booking_id": "L3"}))
    assert r.call_args.kwargs["expand"] == ["payment_intent.latest_charge"]
    assert src["charge_id"] == "ch_1" and src["refunded"] is False
    sess["payment_intent"]["latest_charge"].update(amount_refunded=11187, refunded=True)
    with patch.object(ps, "stripe_key_mode", return_value=True), \
         patch("app.services.stripe_connect_service.stripe_lib.checkout.Session.retrieve", return_value=sess):
        src = run(ps.booking_source(db, {"booking_id": "L3"}))
    assert src["refunded"] is True


# ── trigger_payout_for_job integration ─────────────────────────────────────
def _job(**kw):
    j = {"id": "J1", "booking_id": "L1", "status": "delivered", "vendor_id": "v", "assignee_id": "m1",
         "package_id": "floor_plan_only", "rack_price": 99, "package_price": 99}
    j.update(kw)
    return j


def test_trigger_payout_skips_sandbox_booking_before_any_transfer():
    from app.services import payout_service as svc
    db = FakeDB(media_booking_leads=[{"id": "L1", "stripe_session_id": "cs_test_abc", "status": "paid"}])
    with patch.object(svc, "_db_select_job", return_value=_job()), \
         patch.object(ps, "stripe_key_mode", return_value=True), \
         patch("app.services.stripe_connect_service.handle_payout_to_vendor") as pay:
        out = run(svc.trigger_payout_for_job(db, "J1"))
    assert out["skipped"] == "sandbox_booking"
    pay.assert_not_called()


def test_trigger_payout_noshow_half_is_also_blocked_for_sandbox():
    from app.services import payout_service as svc
    db = FakeDB(media_booking_leads=[{"id": "L1", "stripe_session_id": "cs_test_abc", "status": "paid"}])
    with patch.object(svc, "_db_select_job", return_value=_job(status="agent_no_show")), \
         patch.object(ps, "stripe_key_mode", return_value=True):
        out = run(svc.trigger_payout_for_job(db, "J1", noshow_claim_id="c1"))
    assert out["skipped"] == "sandbox_booking"


# ── source_transaction on the transfer ─────────────────────────────────────
# conftest stubs stripe + stripe_connect_service; load the real file with a fake
# stripe so the Transfer.create arguments can be asserted.
def _real_scs():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "app" / "services" / "stripe_connect_service.py"
    spec = importlib.util.spec_from_file_location("scs_real_payout_source", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class InvalidRequestError(Exception):
        def __init__(self, msg, param=None):
            super().__init__(msg)
            self.param = param

    mod.stripe_lib = SimpleNamespace(Transfer=MagicMock(), error=SimpleNamespace(InvalidRequestError=InvalidRequestError))
    mod.load_contractor_row = lambda *_a, **_k: {"id": "c", "stripe_account_id": "acct_x"}
    mod.sync_contractor_connect_status = lambda *_a, **_k: {"can_receive_transfers": True}
    return mod


def test_transfer_uses_source_transaction_when_charge_known():
    scs = _real_scs()
    scs.stripe_lib.Transfer.create.return_value = SimpleNamespace(id="tr_1")
    tid = run(scs.handle_payout_to_vendor("v", 2500, "L1", payout_id="p1", source_charge_id="ch_1"))
    assert tid == "tr_1"
    kw = scs.stripe_lib.Transfer.create.call_args.kwargs
    assert kw["source_transaction"] == "ch_1" and kw["idempotency_key"] == "payout_p1"
    assert kw["transfer_group"] == "booking_L1" and kw["metadata"]["source_charge"] == "ch_1"


def test_transfer_falls_back_to_balance_when_source_rejected():
    scs = _real_scs()
    err = scs.stripe_lib.error.InvalidRequestError("too big", param="amount")
    scs.stripe_lib.Transfer.create.side_effect = [err, SimpleNamespace(id="tr_2")]
    tid = run(scs.handle_payout_to_vendor("v", 2500, "L1", payout_id="p1", source_charge_id="ch_1"))
    assert tid == "tr_2"
    second = scs.stripe_lib.Transfer.create.call_args_list[1].kwargs
    assert "source_transaction" not in second and second["idempotency_key"] == "payout_p1_bal"


def test_transfer_without_charge_is_unchanged():
    scs = _real_scs()
    scs.stripe_lib.Transfer.create.return_value = SimpleNamespace(id="tr_3")
    run(scs.handle_payout_to_vendor("v", 2500, "L1", payout_id="p1"))
    kw = scs.stripe_lib.Transfer.create.call_args.kwargs
    assert "source_transaction" not in kw and kw["idempotency_key"] == "payout_p1"


# ── refunds ────────────────────────────────────────────────────────────────
def test_full_refund_before_payout_cancels_job_and_blocks_payout():
    db = FakeDB(jobs=[_job(status="assigned", payout_status="unpaid")], payouts=[])
    out = run(rs.apply_booking_refund(db, "L1", full=True, refund_id="re_1", charge_id="ch_1"))
    j = db.rows["jobs"][0]
    assert out["jobs"][0]["action"] == "payout_blocked" and out["jobs"][0]["job_cancelled"]
    assert j["status"] == "cancelled" and j["payout_status"] == "refunded"
    assert "refunded:re_1" in j["ai_flags"]


def test_full_refund_cancels_held_payout():
    db = FakeDB(jobs=[_job(payout_status="pending")],
                payouts=[{"id": "P1", "job_id": "J1", "status": "held", "stripe_transfer_id": None}])
    out = run(rs.apply_booking_refund(db, "L1", full=True, refund_id="re_2"))
    assert out["jobs"][0]["action"] == "payout_cancelled"
    assert db.rows["payouts"][0]["status"] == "cancelled"
    # delivered job keeps its status (work happened); payout can no longer run
    assert db.rows["jobs"][0]["status"] == "delivered" and db.rows["jobs"][0]["payout_status"] == "refunded"


def test_full_refund_after_transfer_flags_without_clawback():
    db = FakeDB(jobs=[_job(payout_status="paid")],
                payouts=[{"id": "P1", "job_id": "J1", "status": "paid", "stripe_transfer_id": "tr_9"}])
    with patch.object(rs, "_reverse_transfer") as rev:
        out = run(rs.apply_booking_refund(db, "L1", full=True, refund_id="re_3"))
    rev.assert_not_called()
    assert out["jobs"][0]["action"] == "paid_needs_review"
    assert "refund_after_payout" in db.rows["jobs"][0]["ai_flags"]
    assert db.rows["payouts"][0]["status"] == "paid"


def test_explicit_reverse_paid_reverses_transfer():
    db = FakeDB(jobs=[_job(payout_status="paid")],
                payouts=[{"id": "P1", "job_id": "J1", "status": "paid", "stripe_transfer_id": "tr_9"}])
    with patch.object(rs, "_reverse_transfer", return_value="trr_1") as rev:
        out = run(rs.apply_booking_refund(db, "L1", full=True, refund_id="re_4", reverse_paid=True))
    rev.assert_called_once_with("tr_9", "re_4", "P1")
    assert out["jobs"][0]["action"] == "transfer_reversed"
    assert db.rows["payouts"][0]["status"] == "reversed"


def test_partial_refund_only_flags():
    db = FakeDB(jobs=[_job(status="assigned", payout_status="unpaid")], payouts=[])
    out = run(rs.apply_booking_refund(db, "L1", full=False, refund_id="re_5", amount_refunded=3000))
    j = db.rows["jobs"][0]
    assert out["jobs"][0]["action"] == "partial_refund_recorded"
    assert j["status"] == "assigned" and j["payout_status"] == "unpaid"


def test_refund_endpoint_requires_internal_key(monkeypatch):
    from fastapi import HTTPException
    from app.routers import payment_events as pe
    monkeypatch.setenv("INTERNAL_API_KEYS", "k1")

    class Req:
        def __init__(self, body):
            self._b = body

        async def json(self):
            return self._b

    with pytest.raises(HTTPException) as e:
        run(pe.booking_refunded(Req({"booking_id": "L1"}), x_internal_api_key=None))
    assert e.value.status_code == 401
    db = FakeDB(jobs=[], payouts=[])

    async def _db():
        return db
    monkeypatch.setattr(pe, "get_db", _db)
    out = run(pe.booking_refunded(Req({"booking_id": "L1", "full": True}), x_internal_api_key="k1"))
    assert out["ok"] is True and out["jobs"] == []
