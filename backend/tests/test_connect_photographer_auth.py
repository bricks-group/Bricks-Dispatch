"""Photographer role can start/view Stripe Connect (not admin-only)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.shared.auth import can_access_stripe_connect, is_vendor_admin


def test_can_access_stripe_connect_photographer_active():
    photog = {
        "id": "p-1",
        "role": "Photographer",
        "title": "Photographer IC",
        "is_active": True,
        "vendor_id": "default_vendor",
    }
    assert is_vendor_admin(photog) is False
    assert can_access_stripe_connect(photog) is True


def test_can_access_stripe_connect_applicant_inactive_denied():
    applicant = {
        "id": "a-1",
        "role": "Applicant",
        "is_active": False,
        "vendor_id": "default_vendor",
    }
    assert can_access_stripe_connect(applicant) is False


def test_can_access_stripe_connect_admin_override():
    admin = {"id": "o-1", "role": "Owner", "is_active": False, "vendor_id": "default_vendor"}
    assert is_vendor_admin(admin) is True
    assert can_access_stripe_connect(admin) is True


def test_stripe_connect_status_allows_photographer(monkeypatch):
    from app.routers import stripe_connect_status as sc_status

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            out = MagicMock()
            out.data = {}
            return out

    class Db:
        def table(self, name):
            return Chain()

    photog = {
        "id": "photog-1",
        "role": "Photographer",
        "is_active": True,
        "vendor_id": "default_vendor",
        "name": "Mississauga Photog",
        "email": "photog@example.com",
    }
    monkeypatch.setattr(sc_status, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(sc_status, "get_vendor_member", AsyncMock(return_value=photog))
    monkeypatch.setattr(sc_status, "get_db", AsyncMock(return_value=Db()))

    out = asyncio.run(
        sc_status.get_stripe_connect_status(authorization="Bearer x", x_user_id=None)
    )
    assert out["connected"] is False
    assert out["connect_status"] == "not_connected"


def test_stripe_connect_status_denies_inactive_applicant(monkeypatch):
    from app.routers import stripe_connect_status as sc_status

    applicant = {
        "id": "a-1",
        "role": "Applicant",
        "is_active": False,
        "vendor_id": "default_vendor",
    }
    monkeypatch.setattr(sc_status, "resolve_vendor_actor_id", AsyncMock(return_value="a-1"))
    monkeypatch.setattr(sc_status, "get_vendor_member", AsyncMock(return_value=applicant))

    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            sc_status.get_stripe_connect_status(authorization="Bearer x", x_user_id=None)
        )
    assert ei.value.status_code == 403


def test_stripe_connect_onboard_allows_photographer(monkeypatch):
    import sys
    from app.routers import vendor_os as vos

    photog = {
        "id": "photog-1",
        "role": "Photographer",
        "is_active": True,
        "vendor_id": "default_vendor",
        "name": "Mississauga Photog",
        "email": "photog@example.com",
    }
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=photog))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    async def _fake_link(**kwargs):
        return "https://connect.stripe.com/setup/s/test"

    scs = sys.modules.get("app.services.stripe_connect_service")
    assert scs is not None
    monkeypatch.setattr(scs, "create_connect_account_and_link", _fake_link)
    monkeypatch.setattr(vos, "write_audit_event", AsyncMock())

    payload = vos.StripeConnectOnboardRequest(return_url="https://dispatch.brxs.ca/settings")
    out = asyncio.run(
        vos.start_stripe_connect_onboarding(
            payload, authorization="Bearer x", x_user_id=None
        )
    )
    assert out["url"].startswith("https://connect.stripe.com/")
