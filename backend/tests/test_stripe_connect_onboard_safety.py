"""Connect onboarding must survive a failed audit write, use a 25s Stripe
timeout off the event loop, and only accept brxs.ca return URLs."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import vendor_os as vos

@pytest.fixture(autouse=True)
def _preserve_scs_mock_attrs():
    """Leave create_connect_account_and_link on the conftest MagicMock after
    our monkeypatch undoes, so later test modules can setattr it."""
    import sys
    from unittest.mock import AsyncMock
    scs_mod = sys.modules.get("app.services.stripe_connect_service")
    yield
    if scs_mod is not None and not hasattr(scs_mod, "create_connect_account_and_link"):
        scs_mod.create_connect_account_and_link = AsyncMock()
    if scs_mod is not None and not hasattr(scs_mod, "connect_member_key"):
        scs_mod.connect_member_key = lambda m: (m or {}).get("id")


_spec = importlib.util.spec_from_file_location(
    "real_stripe_connect_service_safety",
    Path(__file__).resolve().parents[1] / "app" / "services" / "stripe_connect_service.py",
)
scs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scs)


class _QuotaError(Exception):
    """Stand-in for a Supabase REST 402 (exceed_storage_size_quota)."""

    code = "402"
    message = "exceed_storage_size_quota"


class FakeTable:
    def __init__(self, db):
        self.db, self.op, self.row = db, None, None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def is_(self, col=None, val=None, *_a, **_k):
        self.db.null_filters.append((col, val))
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, row):
        self.op, self.row = "insert", dict(row)
        return self

    def update(self, row):
        self.op, self.row = "update", dict(row)
        return self

    def execute(self):
        if self.op == "select":
            return SimpleNamespace(data=list(self.db.existing))
        if self.op == "insert":
            self.db.inserts.append(self.row)
            return SimpleNamespace(data=[{"id": "c-1", **self.row}])
        self.db.updates.append(self.row)
        return SimpleNamespace(data=[{}])


class FakeDb:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.inserts, self.updates, self.null_filters = [], [], []

    def table(self, name):
        assert name == "contractors"
        return FakeTable(self)


@pytest.fixture
def fake_stripe(monkeypatch):
    calls = {}

    def _acct(**k):
        calls["acct"] = k
        return SimpleNamespace(id="acct_test")

    def _link(**k):
        calls["link"] = k
        return SimpleNamespace(url="https://connect.stripe.com/setup/e/x")

    monkeypatch.setattr(scs.stripe_lib.Account, "create", _acct, raising=False)
    monkeypatch.setattr(scs.stripe_lib.AccountLink, "create", _link, raising=False)
    return calls


def _photog():
    return {
        "id": "photog-1",
        "role": "Photographer",
        "is_active": True,
        "vendor_id": "default_vendor",
        "name": "Test Photog",
        "email": "photog@example.com",
    }


def test_failing_audit_write_still_returns_link(monkeypatch):
    """A Supabase 402 on the audit row must not 500 or drop the Stripe link
    after the account has already been created."""
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=_photog()))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    created = {"account": False}

    async def _fake_link(**kwargs):
        created["account"] = True
        return "https://connect.stripe.com/setup/s/live"

    import sys
    scs_mod = sys.modules.get("app.services.stripe_connect_service")
    monkeypatch.setattr(scs_mod, "create_connect_account_and_link", _fake_link, raising=False)
    monkeypatch.setattr(scs_mod, "connect_member_key", lambda m: m.get("id"), raising=False)

    async def _boom(*_a, **_k):
        raise _QuotaError("exceed_storage_size_quota")

    monkeypatch.setattr(vos, "write_audit_event", _boom)

    payload = vos.StripeConnectOnboardRequest(return_url="https://go.brxs.ca/onboarding?stripe=return")
    out = asyncio.run(
        vos.start_stripe_connect_onboarding(payload, authorization="Bearer x", x_user_id=None)
    )
    assert created["account"] is True
    assert out["url"] == "https://connect.stripe.com/setup/s/live"


def test_reject_non_brxs_return_url(monkeypatch):
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=_photog()))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    payload = vos.StripeConnectOnboardRequest(return_url="https://evil.example/phish")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(vos.start_stripe_connect_onboarding(payload, authorization="Bearer x", x_user_id=None))
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "invalid_return_url"


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://go.brxs.ca/onboarding?stripe=return", True),
        ("https://dispatch.brxs.ca/settings", True),
        ("https://brxs.ca/", True),
        ("https://www.brxs.ca/x", True),
        ("http://go.brxs.ca/onboarding", False),
        ("https://evil.example/", False),
        ("https://brxs.ca.evil.com/", False),
        ("https://evil.com/brxs.ca", False),
        ("https://user:pass@go.brxs.ca/x", False),
        ("javascript:alert(1)", False),
        ("", False),
    ],
)
def test_is_allowed_connect_return_url(url, ok):
    assert vos.is_allowed_connect_return_url(url) is ok


def test_onboard_stripe_calls_use_25s_timeout_and_to_thread(monkeypatch, fake_stripe):
    class FakeClient:
        def __init__(self, timeout=None):
            self._timeout = timeout

    monkeypatch.setattr(scs.stripe_lib, "default_http_client", FakeClient(80))
    monkeypatch.setattr(scs.stripe_lib.http_client, "RequestsClient", FakeClient)

    threaded = []
    real = asyncio.to_thread

    async def _track(fn, *a, **k):
        threaded.append(getattr(fn, "__qualname__", getattr(fn, "__name__", str(fn))))
        return await real(fn, *a, **k)

    monkeypatch.setattr(scs.asyncio, "to_thread", _track)

    db = FakeDb(existing=[])
    monkeypatch.setattr(scs, "supabase", db)
    url = asyncio.run(
        scs.create_connect_account_and_link(
            vendor_id="v-1",
            refresh_url="https://go.brxs.ca/r",
            return_url="https://go.brxs.ca/r",
            full_name="A",
            email="a@example.com",
            member_id="m-1",
        )
    )
    assert url.startswith("https://connect.stripe.com/")
    assert scs.stripe_lib.default_http_client._timeout == 25
    assert len(threaded) >= 2, threaded
    assert "acct" in fake_stripe and "link" in fake_stripe


def test_sync_sets_connect_onboarded_at_only_when_connected(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(scs, "supabase", db)
    account = SimpleNamespace(
        id="acct_1",
        details_submitted=True,
        payouts_enabled=True,
        charges_enabled=True,
        requirements=SimpleNamespace(disabled_reason=None),
        capabilities=SimpleNamespace(transfers="active"),
    )
    monkeypatch.setattr(scs.stripe_lib, "api_key", "sk_test")
    monkeypatch.setattr(scs.stripe_lib.Account, "retrieve", lambda *_a, **_k: account, raising=False)
    out = scs.sync_contractor_connect_status("c-1", "acct_1")
    assert out["connect_status"] == "connected"
    assert any("connect_onboarded_at" in u for u in db.updates)
    assert ("connect_onboarded_at", "null") in db.null_filters


def test_sync_does_not_set_onboarded_at_when_pending(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(scs, "supabase", db)
    account = SimpleNamespace(
        id="acct_1",
        details_submitted=False,
        payouts_enabled=False,
        charges_enabled=False,
        requirements=SimpleNamespace(disabled_reason=None),
        capabilities=SimpleNamespace(transfers="inactive"),
    )
    monkeypatch.setattr(scs.stripe_lib, "api_key", "sk_test")
    monkeypatch.setattr(scs.stripe_lib.Account, "retrieve", lambda *_a, **_k: account, raising=False)
    out = scs.sync_contractor_connect_status("c-1", "acct_1")
    assert out["connect_status"] == "pending"
    assert all("connect_onboarded_at" not in u for u in db.updates)
