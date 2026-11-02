"""E2E phase 2 blockers: QC holds, idempotent re-complete, payout ledger, owner lookup, email login."""
import asyncio
import importlib.util
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.qc_media_kinds import QC_HELD, is_qc_held
from app.services.post_production import is_already_processed
from app.services import payout_service as ps


class Q:
    """Tiny chainable fake for db.table(...)...execute()."""

    def __init__(self, data=None, log=None, table=None):
        self.data, self.log, self.table = data, log if log is not None else [], table

    def __getattr__(self, name):
        def f(*a, **k):
            self.log.append((self.table, name, a))
            return self
        return f

    async def execute(self):
        return SimpleNamespace(data=self.data)


def test_qc_held_covers_flagged_and_rejected():
    assert set(QC_HELD) == {"rejected", "flagged"}
    assert is_qc_held({"qc_status": "flagged"})
    assert not is_qc_held({"qc_status": "passed"})
    assert not is_qc_held({"qc_status": None})
    assert not is_qc_held(None)


def test_already_processed_assets_are_skipped():
    assert is_already_processed({"processing_status": "done"})
    assert is_already_processed({"processing_status": "skipped"})
    assert not is_already_processed({"processing_status": "pending"})
    assert not is_already_processed({"processing_status": "failed"})
    assert not is_already_processed({})


def test_travel_payout_cents():
    add_ons = [{"key": "travel", "included": True, "amount_cents": 4500}, {"key": "staging"}]
    assert ps.travel_payout_cents(add_ons) == 4500
    assert ps.travel_payout_cents('[{"key":"travel_fee","amount_cents":"1200"}]') == 1200
    assert ps.travel_payout_cents(None) == 0
    assert ps.travel_payout_cents([{"key": "travel", "amount_cents": -5}]) == 0


def test_ledger_fields_dollars():
    f = ps.ledger_fields({"package_price": 400}, 15000)
    assert f == {"payout_amount": 15000, "vendor_payout": 150.0, "platform_margin": 250.0}
    f = ps.ledger_fields({"package_price": None}, 15000)
    assert "platform_margin" not in f and f["vendor_payout"] == 150.0


def test_cap_is_half_of_paid():
    from app.services.client_service_payouts import cap_cents
    assert cap_cents({"package_price": 300}, 20000) == 15000
    assert cap_cents({"package_price": 300}, 10000) == 10000


def _load_real(modname, relpath):
    path = pathlib.Path(__file__).resolve().parents[1] / relpath
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


class SyncQ:
    def __init__(self, results):
        self.results, self.calls = results, []

    def table(self, _t):
        return self

    def select(self, *_a):
        self.calls.append(["select"])
        return self

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls[-1].append((name, a))
            return self
        return f

    def execute(self):
        return SimpleNamespace(data=self.results.pop(0))


def _scs(monkeypatch, results):
    mod = _load_real("app.services._real_scs_test", "app/services/stripe_connect_service.py")
    fake = SyncQ(results)
    monkeypatch.setattr(mod, "supabase", fake, raising=False)
    for name in ("get_supabase", "_sb", "_supabase"):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda: fake)
    return mod, fake


def test_owner_lookup_falls_back_to_single_company_row(monkeypatch):
    mod, fake = _scs(monkeypatch, [[], [{"id": "co"}]])
    assert mod.load_contractor_row("default_vendor", None) == {"id": "co"}


def test_owner_lookup_ambiguous_company_rows_returns_none(monkeypatch):
    mod, _ = _scs(monkeypatch, [[], [{"id": "a"}, {"id": "b"}]])
    assert mod.load_contractor_row("default_vendor", None) is None


def test_member_lookup_never_falls_back(monkeypatch):
    mod, fake = _scs(monkeypatch, [[]])
    assert mod.load_contractor_row("v1", "m1") is None
    assert len(fake.calls) == 1


def _admin_ops(monkeypatch, admin=True):
    from app.routers import admin_ops as ao
    monkeypatch.setattr(ao, "resolve_vendor_actor_id", AsyncMock(return_value="kam"))
    monkeypatch.setattr(ao, "get_vendor_member", AsyncMock(return_value={"id": "kam"}))
    monkeypatch.setattr(ao, "is_vendor_admin", lambda m: admin)
    return ao


def _rows(monkeypatch, ao, rows):
    async def load(db, table, column, value, cols="*"):
        return rows.get(table)
    monkeypatch.setattr(ao, "_load_one", load)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=MagicMock()))


def test_retry_payout_requires_admin(monkeypatch):
    ao = _admin_ops(monkeypatch, admin=False)
    with pytest.raises(Exception) as e:
        asyncio.run(ao.retry_delivered_job_payout("j1", "Bearer x", None))
    assert "403" in str(getattr(e.value, "status_code", e.value)) or "ermission" in str(e.value.__dict__)


def test_retry_payout_rejects_undelivered(monkeypatch):
    ao = _admin_ops(monkeypatch)
    _rows(monkeypatch, ao, {"jobs": {"id": "j1", "status": "completed"}})
    with pytest.raises(Exception) as e:
        asyncio.run(ao.retry_delivered_job_payout("j1", "Bearer x", None))
    assert getattr(e.value, "status_code", None) == 409


def test_retry_payout_already_paid_is_noop(monkeypatch):
    ao = _admin_ops(monkeypatch)
    _rows(monkeypatch, ao, {"jobs": {"id": "j1", "status": "delivered"}, "media_delivery": {"id": "md"},
                            "payouts": {"id": "p1", "status": "paid", "stripe_transfer_id": "tr_1"}})
    trig = AsyncMock()
    monkeypatch.setattr(ps, "trigger_payout_for_job", trig)
    out = asyncio.run(ao.retry_delivered_job_payout("j1", "Bearer x", None))
    assert out["status"] == "already_paid" and out["stripe_transfer_id"] == "tr_1"
    trig.assert_not_called()


def test_retry_payout_failed_calls_trigger(monkeypatch):
    ao = _admin_ops(monkeypatch)
    _rows(monkeypatch, ao, {"jobs": {"id": "j1", "status": "delivered"}, "media_delivery": {"id": "md"},
                            "payouts": {"id": "p1", "status": "failed"}})
    trig = AsyncMock(return_value={"payout_id": "p1", "status": "paid", "stripe_transfer_id": "tr_2"})
    monkeypatch.setattr(ps, "trigger_payout_for_job", trig)
    out = asyncio.run(ao.retry_delivered_job_payout("j1", "Bearer x", None))
    assert out["status"] == "paid" and out["stripe_transfer_id"] == "tr_2"
    trig.assert_awaited_once()


def test_qc_release_rejects_unheld(monkeypatch):
    ao = _admin_ops(monkeypatch)
    _rows(monkeypatch, ao, {"uploads": {"id": "u1", "job_id": "j1", "qc_status": "passed"}})
    with pytest.raises(Exception) as e:
        asyncio.run(ao.release_qc_hold("u1", "Bearer x", None))
    assert getattr(e.value, "status_code", None) == 409


def test_qc_release_folds_into_delivery(monkeypatch):
    ao = _admin_ops(monkeypatch)
    log = []
    db = MagicMock()
    db.table = lambda t: Q([{"id": "u1"}], log, t)
    rows = {"uploads": {"id": "u1", "job_id": "j1", "qc_status": "flagged", "qc_notes": "blurry"},
            "media_delivery": {"id": "md", "delivery_urls": []}}

    async def load(_db, table, column, value, cols="*"):
        return rows.get(table)
    monkeypatch.setattr(ao, "_load_one", load)
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=db))
    from app.routers import post_production as pp
    fold = AsyncMock(return_value=True)
    monkeypatch.setattr(pp, "_fold_late_into_existing_delivery", fold)
    out = asyncio.run(ao.release_qc_hold("u1", "Bearer x", None))
    assert out["status"] == "released"
    upd = [c for c in log if c[1] == "update"][0][2][0]
    assert upd["qc_status"] == "passed" and "released by owner" in upd["qc_notes"]
    fold.assert_awaited_once()


def test_restore_delivered_status(monkeypatch):
    from app.routers import post_production as pp
    log = []
    db = MagicMock()
    db.table = lambda t: Q([{"id": "j1"}], log, t)
    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    assert asyncio.run(pp._restore_delivered_status("j1")) is True
    assert ("jobs", "in_", ("status", ["processing", "completed"])) in log


def test_email_login_prep_only_for_vendor_emails():
    from app.services import email_login_prep as elp
    db = MagicMock()
    db.table = lambda t: Q([], None, t)
    db.auth.admin.create_user = AsyncMock()
    assert asyncio.run(elp.ensure_confirmed_vendor_auth_user(db, "stranger@example.com")) == "not_vendor"
    db.auth.admin.create_user.assert_not_called()
    assert asyncio.run(elp.ensure_confirmed_vendor_auth_user(db, "bad")) == "invalid"


def test_email_login_prep_creates_confirmed_user():
    from app.services import email_login_prep as elp
    db = MagicMock()
    db.table = lambda t: Q([{"id": "m"}], None, t)
    db.auth.admin.create_user = AsyncMock()
    assert asyncio.run(elp.ensure_confirmed_vendor_auth_user(db, "New@Vendor.ca")) == "created"
    db.auth.admin.create_user.assert_awaited_once_with({"email": "new@vendor.ca", "email_confirm": True})
