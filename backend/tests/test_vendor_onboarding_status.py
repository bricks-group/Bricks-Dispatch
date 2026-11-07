"""Approved vendors in a paused trade stay unassignable, but the profile
must not call them unapproved or lock them out of the app.

Stripe: a contractors row keyed by member_id (vendor_id and user_id null,
the live FK workaround) is that member's payout account. A payout-ready
Express account opens an Express login link; Stripe rejects a second
account_onboarding link once onboarding is already done.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.shared.vendor_identity import is_assignable, public_profile

MEMBER = "f83dbb0f-d3d8-42fa-bdcf-fe301328dce6"
CONTRACTOR = "b4fe9702-ac9c-49e5-a0a4-227a4e75170d"


def _repairs(**kw):
    base = {
        "id": MEMBER,
        "name": "Rajvir Singh",
        "email": "rajvir@example.com",
        "is_active": True,
        "onboarding_status": "approved",
        "onboarding_completed_at": "2026-11-03T00:00:00+00:00",
        "vendor_type": "handyman",
        "role": "Repairs",
        "title": "Repairs IC",
        "tier": 1,
        "terms_accepted_at": "2026-11-01T00:00:00+00:00",
        "coi_verified": False,
    }
    base.update(kw)
    return base


def test_approved_paused_repairs_vendor_is_not_labeled_unapproved():
    member = _repairs()
    assert is_assignable(member) is False
    pub = public_profile(member)
    assert pub["title"] == "Repairs IC"
    assert pub["tier_label"] == "C"
    assert pub["payout_label"] == "Fixed rate card"
    assert pub["onboarding_status"] == "approved"
    assert pub["assignable"] is False
    assert pub["app_access"] is True
    reason = pub["assignable_reason"] or ""
    assert "Not approved" not in reason
    assert "paus" in reason.lower()
    assert pub["insurance"]["verified"] is False


def test_pending_vendor_still_has_no_app_access():
    pub = public_profile(_repairs(onboarding_status="pending", onboarding_completed_at=None, is_active=True))
    assert pub["assignable"] is False
    assert pub["app_access"] is False
    assert "Pending" in (pub["assignable_reason"] or "")


def test_approved_photographer_stays_assignable():
    pub = public_profile(_repairs(
        vendor_type="photographer", role="Photographer", title="Photographer IC",
    ))
    assert pub["assignable"] is True
    assert pub["app_access"] is True
    assert pub["assignable_reason"] is None


_spec = importlib.util.spec_from_file_location(
    "real_stripe_connect_service_status",
    Path(__file__).resolve().parents[1] / "app" / "services" / "stripe_connect_service.py",
)
scs = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(scs)


class _Table:
    def __init__(self, db):
        self.db = db
        self.filters = []

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        self.db.queries.append(list(self.filters))
        if ("eq", "member_id", MEMBER) in self.filters:
            return SimpleNamespace(data=[self.db.member_row])
        return SimpleNamespace(data=[self.db.org_row])


class _Db:
    def __init__(self, member_row):
        self.member_row = member_row
        self.org_row = {
            "id": "org",
            "stripe_account_id": "acct_org",
            "connect_status": "connected",
            "vendor_id": "default_vendor",
            "member_id": None,
            "full_name": "Bricks",
        }
        self.queries = []

    def table(self, name):
        assert name == "contractors"
        return _Table(self)


def test_member_contractor_with_null_vendor_is_the_payout_account(monkeypatch):
    row = {
        "id": CONTRACTOR,
        "stripe_account_id": "acct_member",
        "connect_status": "connected",
        "vendor_id": None,
        "member_id": MEMBER,
        "user_id": None,
        "full_name": "Rajvir Singh",
    }
    db = _Db(row)
    monkeypatch.setattr(scs, "supabase", db)
    found = scs.load_contractor_row("default_vendor", MEMBER)
    assert found["id"] == CONTRACTOR
    assert found["stripe_account_id"] == "acct_member"
    assert any(("eq", "member_id", MEMBER) in q for q in db.queries)
    assert not any(("eq", "vendor_id", "default_vendor") in q for q in db.queries)


class _Stripe:
    """Stand-in so these tests do not delete attributes on the shared stripe stub."""

    def __init__(self):
        self.onboard_calls = []
        self.login_calls = []
        self.onboard = lambda **k: SimpleNamespace(url="https://connect.stripe.com/setup/s/new")
        self.login = lambda acct: SimpleNamespace(url="https://connect.stripe.com/express/login")

        class Account:
            outer = None

            @staticmethod
            def create(**k):
                return SimpleNamespace(id="acct_new")

            @staticmethod
            def create_login_link(acct):
                Account.outer.login_calls.append(acct)
                return Account.outer.login(acct)

        class AccountLink:
            outer = None

            @staticmethod
            def create(**k):
                AccountLink.outer.onboard_calls.append(k)
                return AccountLink.outer.onboard(**k)

        Account.outer = self
        AccountLink.outer = self
        self.Account = Account
        self.AccountLink = AccountLink


def _bind(monkeypatch, db, stripe):
    monkeypatch.setattr(scs, "supabase", db)
    monkeypatch.setattr(scs, "stripe_lib", stripe)


def test_connected_account_opens_express_login_not_a_second_onboarding_link(monkeypatch):
    row = {
        "id": CONTRACTOR,
        "stripe_account_id": "acct_member",
        "connect_status": "connected",
        "vendor_id": None,
        "member_id": MEMBER,
        "full_name": "Rajvir Singh",
    }
    stripe = _Stripe()
    stripe.login = lambda acct: SimpleNamespace(url="https://connect.stripe.com/express/login")
    stripe.onboard = lambda **_k: (_ for _ in ()).throw(AssertionError("onboarding link"))
    _bind(monkeypatch, _Db(row), stripe)
    url = asyncio.run(scs.create_connect_account_and_link(
        vendor_id="default_vendor",
        refresh_url="https://go.brxs.ca/settings",
        return_url="https://go.brxs.ca/settings",
        full_name="Rajvir Singh",
        email="rajvir@example.com",
        member_id=MEMBER,
    ))
    assert url == "https://connect.stripe.com/express/login"
    assert stripe.login_calls == ["acct_member"]
    assert stripe.onboard_calls == []


def test_pending_account_still_uses_onboarding_link(monkeypatch):
    row = {
        "id": CONTRACTOR,
        "stripe_account_id": "acct_pending",
        "connect_status": "pending",
        "vendor_id": None,
        "member_id": MEMBER,
        "full_name": "Rajvir Singh",
    }
    stripe = _Stripe()
    stripe.onboard = lambda **k: SimpleNamespace(url="https://connect.stripe.com/setup/s/pending")
    stripe.login = lambda _acct: (_ for _ in ()).throw(AssertionError("login link"))
    _bind(monkeypatch, _Db(row), stripe)
    url = asyncio.run(scs.create_connect_account_and_link(
        vendor_id="default_vendor", refresh_url="https://go.brxs.ca/r", return_url="https://go.brxs.ca/r",
        member_id=MEMBER,
    ))
    assert url == "https://connect.stripe.com/setup/s/pending"
    assert stripe.login_calls == []
    assert stripe.onboard_calls[0]["type"] == "account_onboarding"
    assert stripe.onboard_calls[0]["collection_options"] == {"fields": "currently_due"}


def test_finished_account_falls_back_when_stripe_rejects_onboarding_link(monkeypatch):
    row = {
        "id": CONTRACTOR,
        "stripe_account_id": "acct_done",
        "connect_status": "pending",
        "vendor_id": None,
        "member_id": MEMBER,
        "full_name": "Rajvir Singh",
    }
    stripe = _Stripe()

    def boom(**_k):
        raise Exception("account_onboarding is not allowed; account has already completed onboarding")

    stripe.onboard = boom
    stripe.login = lambda acct: SimpleNamespace(url="https://connect.stripe.com/express/acct_done")
    _bind(monkeypatch, _Db(row), stripe)
    url = asyncio.run(scs.create_connect_account_and_link(
        vendor_id="default_vendor", refresh_url="https://go.brxs.ca/r", return_url="https://go.brxs.ca/r",
        member_id=MEMBER,
    ))
    assert url == "https://connect.stripe.com/express/acct_done"
    assert stripe.login_calls == ["acct_done"]
