"""Connected means Stripe-ready — not mere stripe_account_id presence.

Loads the service file directly so conftest's stripe_connect_service mock
does not swallow classify_connect_account.
"""
from types import SimpleNamespace
from pathlib import Path
import importlib.util

_path = Path(__file__).resolve().parents[1] / "app" / "services" / "stripe_connect_service.py"
_spec = importlib.util.spec_from_file_location("stripe_connect_service_real", _path)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
classify_connect_account = _mod.classify_connect_account


def test_no_account_is_not_connected():
    out = classify_connect_account(None)
    assert out["connected"] is False
    assert out["connect_status"] == "not_connected"


def test_created_but_not_submitted_is_pending_not_connected():
    acct = SimpleNamespace(
        id="acct_pending",
        details_submitted=False,
        payouts_enabled=False,
        charges_enabled=False,
        requirements=SimpleNamespace(disabled_reason=None),
    )
    out = classify_connect_account(acct)
    assert out["connected"] is False
    assert out["connect_status"] == "pending"


def test_ready_account_is_connected():
    acct = SimpleNamespace(
        id="acct_ready",
        details_submitted=True,
        payouts_enabled=True,
        charges_enabled=True,
        requirements=SimpleNamespace(disabled_reason=None),
    )
    out = classify_connect_account(acct)
    assert out["connected"] is True
    assert out["connect_status"] == "connected"


def test_disabled_account_is_restricted_not_connected():
    acct = SimpleNamespace(
        id="acct_bad",
        details_submitted=True,
        payouts_enabled=False,
        charges_enabled=False,
        requirements=SimpleNamespace(disabled_reason="requirements.past_due"),
    )
    out = classify_connect_account(acct)
    assert out["connected"] is False
    assert out["connect_status"] == "restricted"


# --- Transfers gate (E2E 2026-11-02) ---------------------------------------
# A TEST Express account with details_submitted, transfers active and only
# proof_of_liveness past_due (payouts_enabled False) was refused every payout
# as "restricted". A platform Transfer only needs the transfers capability.
import asyncio

import pytest


def _acct(**kw):
    base = dict(id="acct_x", details_submitted=True, payouts_enabled=False, charges_enabled=True,
                capabilities={"transfers": "active"},
                requirements=SimpleNamespace(disabled_reason="requirements.past_due"))
    base.update(kw)
    return SimpleNamespace(**base)


def test_liveness_past_due_with_transfers_active_can_receive_transfers():
    out = classify_connect_account(_acct())
    assert out["connect_status"] == "restricted"   # still honest for the UI
    assert out["connected"] is False
    assert out["transfers_active"] is True
    assert out["can_receive_transfers"] is True


def test_transfers_inactive_cannot_receive_transfers():
    out = classify_connect_account(_acct(capabilities={"transfers": "inactive"}))
    assert out["can_receive_transfers"] is False


def test_not_submitted_cannot_receive_transfers():
    out = classify_connect_account(_acct(details_submitted=False))
    assert out["can_receive_transfers"] is False


def test_rejected_account_cannot_receive_transfers():
    out = classify_connect_account(_acct(requirements=SimpleNamespace(disabled_reason="rejected.fraud")))
    assert out["can_receive_transfers"] is False


def test_capabilities_as_object():
    out = classify_connect_account(_acct(capabilities=SimpleNamespace(transfers="active")))
    assert out["can_receive_transfers"] is True


def test_no_account_cannot_receive_transfers():
    assert classify_connect_account(None).get("can_receive_transfers") in (None, False)


def _contractor(monkeypatch):
    monkeypatch.setattr(_mod, "load_contractor_row", lambda vid, mid=None: {
        "id": "c1", "stripe_account_id": "acct_x", "full_name": "Test Vendor", "member_id": "m1"})
    monkeypatch.setattr(_mod.stripe_lib, "api_key", "sk_test_x")


def test_handle_payout_transfers_when_only_bank_payouts_are_blocked(monkeypatch):
    _contractor(monkeypatch)
    monkeypatch.setattr(_mod, "sync_contractor_connect_status",
                        lambda cid, aid: classify_connect_account(_acct()))
    sent = {}

    class _T:
        id = "tr_test_1"

    def _create(**kw):
        sent.update(kw)
        return _T()

    monkeypatch.setattr(_mod.stripe_lib.Transfer, "create", staticmethod(_create))
    tid = asyncio.run(_mod.handle_payout_to_vendor("default_vendor", 5515, "bk", "pay-1", "m1"))
    assert tid == "tr_test_1"
    assert sent["destination"] == "acct_x" and sent["amount"] == 5515 and sent["currency"] == "cad"
    assert sent["idempotency_key"] == "payout_pay-1"


def test_handle_payout_refuses_without_transfers_capability(monkeypatch):
    _contractor(monkeypatch)
    monkeypatch.setattr(_mod, "sync_contractor_connect_status",
                        lambda cid, aid: classify_connect_account(_acct(capabilities={"transfers": "pending"})))

    def _boom(**kw):
        raise AssertionError("Transfer.create must not be called")

    monkeypatch.setattr(_mod.stripe_lib.Transfer, "create", staticmethod(_boom))
    with pytest.raises(ValueError) as e:
        asyncio.run(_mod.handle_payout_to_vendor("default_vendor", 5515, "bk", "pay-1", "m1"))
    msg = str(e.value)
    assert "Test Vendor" in msg and "transfers=inactive" in msg and "requirements.past_due" in msg


def test_handle_payout_refuses_when_stripe_unverifiable(monkeypatch):
    # sync fail paths carry no can_receive_transfers key: fail closed.
    _contractor(monkeypatch)
    monkeypatch.setattr(_mod, "sync_contractor_connect_status",
                        lambda cid, aid: {"connected": False, "connect_status": "pending"})
    monkeypatch.setattr(_mod.stripe_lib.Transfer, "create",
                        staticmethod(lambda **kw: (_ for _ in ()).throw(AssertionError("no transfer"))))
    with pytest.raises(ValueError):
        asyncio.run(_mod.handle_payout_to_vendor("default_vendor", 5515, "bk", "pay-1", "m1"))
