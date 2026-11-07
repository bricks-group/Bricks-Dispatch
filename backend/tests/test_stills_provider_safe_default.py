"""Paid stills processing only when explicitly configured (launch review 2026-11-07)."""
from __future__ import annotations

import asyncio

import pytest

from app.services import autoenhance_service as ae
from app.services import post_production as pp


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("POSTPROD_STILLS_PROVIDER", "PAID_STILLS_PROCESSING_DISABLED", "POSTPROD_ALLOW_FOTELLO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUTOENHANCE_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ae, "_plan_blocked", False, raising=False)


def test_default_is_inhouse_even_with_key():
    assert pp.stills_provider_preference() == "inhouse"
    assert pp.prefer_autoenhance_stills() is False
    assert ae.is_available() is False


@pytest.mark.parametrize("val", ["manual", "none", "off", "inhouse", "bricks_inhouse", "typo"])
def test_non_autoenhance_values_are_inhouse(monkeypatch, val):
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", val)
    assert pp.stills_provider_preference() == "inhouse"
    assert ae.is_available() is False


def test_explicit_autoenhance_enables(monkeypatch):
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    assert pp.stills_provider_preference() == "autoenhance"
    assert ae.is_available() is True


def test_kill_switch_overrides_explicit_autoenhance(monkeypatch):
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    monkeypatch.setenv("PAID_STILLS_PROCESSING_DISABLED", "1")
    assert pp.stills_provider_preference() == "inhouse"
    assert ae.is_available() is False


def test_legacy_async_triggers_noop_by_default(monkeypatch):
    monkeypatch.setattr(pp, "AUTOENHANCE_API_KEY", "k")
    monkeypatch.setattr(pp, "FOTELLO_API_KEY", "k")
    assert asyncio.run(pp.trigger_autoenhance_edit("https://example.invalid/a.jpg")) is None
    assert asyncio.run(pp.trigger_fotello_edit("https://example.invalid/a.jpg")) is None
