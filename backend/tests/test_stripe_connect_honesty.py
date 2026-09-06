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
