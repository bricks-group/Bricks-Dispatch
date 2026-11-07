"""Kam 2026-11-07: Autoenhance stays the default until in-house is at its level.
Only an opt-in kill switch is added; with it unset behaviour is unchanged."""
import importlib

from app.services import autoenhance_service as ae
from app.services import post_production as pp


def test_unset_provider_defaults_to_autoenhance(monkeypatch):
    monkeypatch.delenv("POSTPROD_STILLS_PROVIDER", raising=False)
    monkeypatch.delenv("PAID_STILLS_PROCESSING_DISABLED", raising=False)
    assert pp.stills_provider_preference() == "autoenhance"


def test_explicit_inhouse_and_unknown_unchanged(monkeypatch):
    monkeypatch.delenv("PAID_STILLS_PROCESSING_DISABLED", raising=False)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    assert pp.stills_provider_preference() == "inhouse"
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "whatever")
    assert pp.stills_provider_preference() == "autoenhance"


def test_kill_switch_blocks_ae_and_holds_not_inhouse(monkeypatch):
    # Kam 2026-11-07: in-house only with POSTPROD_STILLS_PROVIDER=inhouse; the
    # kill switch makes AE unavailable, so stills are held for a re-run.
    monkeypatch.setenv("PAID_STILLS_PROCESSING_DISABLED", "1")
    monkeypatch.delenv("POSTPROD_STILLS_PROVIDER", raising=False)
    monkeypatch.setenv("AUTOENHANCE_API_KEY", "x")
    assert pp.stills_provider_preference() == "autoenhance"
    assert ae.is_available() is False


def test_ae_available_with_key_when_switch_off(monkeypatch):
    monkeypatch.delenv("PAID_STILLS_PROCESSING_DISABLED", raising=False)
    monkeypatch.setattr(ae, "api_key", lambda: "x")
    ae.clear_plan_block()
    assert ae.is_available() is True
