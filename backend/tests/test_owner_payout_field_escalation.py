"""Managers must not be able to make a contractor the platform owner.

A vendor admin (role or title containing admin or manager) can send
PUT /api/vendor-os/team/{member_id}, POST /api/vendor-os/team, and
PATCH /api/admin/applicants/{member_id}. Those handlers persist role, title,
and payout_mode. is_owner_member treats payout_mode owner_full, or a title or
role of Owner, as the platform owner, and trigger_payout_for_job then pays
owner_payout_cents (the package subtotal) instead of the vendor_payout_rates
cell. handle_payout_to_vendor sends that amount to the member's Stripe
account, or, when the member has no contractors row, connect_member_key
selects the company row.

These tests call those handlers and then trigger_payout_for_job. They fail
while a non-owner can be paid the package subtotal. They pass when only
OWNER_MEMBER_ID is treated as the owner for payouts.

Profile self-update is not covered: ProfileUpdateBody has no role, title, or
payout_mode. confirm-payment already checks OWNER_MEMBER_ID and is not covered.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import admin_ops, vendor_os
from app.services import payout_service
from app.services.trade_specs import probation_jobs
from app.shared import auth as auth_mod
from app.shared.schemas import VendorMemberCreate, VendorMemberUpdate
from app.shared.vendor_identity import OWNER_MEMBER_ID, is_owner_member

VENDOR_ID = "vendor-west-1"
RATE_CENTS = 5515  # photos_only / small / tier 1, vendor_payout_rate_card.py
FULL_CENTS = 29900  # jobs.subtotal_cents, owner_payout_cents
COMPANY_ACCOUNT = "acct_company_placeholder"
MANAGER_ID = "11111111-1111-4111-8111-111111111111"
ACCOMPLICE_ID = "22222222-2222-4222-8222-222222222222"
PHOTOGRAPHER_ID = "33333333-3333-4333-8333-333333333333"
APPLICANT_ID = "44444444-4444-4444-8444-444444444444"
SESSION = "Bearer session-placeholder"


class _Result:
    def __init__(self, data, count=0):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, tables: dict, name: str, sync: bool):
        self.tables = tables
        self.name = name
        self.sync = sync
        self.op = "select"
        self.payload = None
        self.filters: list[tuple] = []
        self.columns = "*"
        self.single = False
        self.limit_n: int | None = None

    def select(self, columns="*", count=None):
        self.op = "select"
        self.columns = columns or "*"
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = dict(payload)
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def ilike(self, column, value):
        self.filters.append(("ilike", column, value))
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _matches(self, row: dict) -> bool:
        for kind, column, value in self.filters:
            current = row.get(column)
            if kind == "eq" and current != value:
                return False
            if kind == "neq" and current == value:
                return False
            if kind == "is" and value == "null" and current is not None:
                return False
            if kind == "in" and current not in value:
                return False
            if kind == "ilike" and str(current or "").lower() != str(value or "").lower():
                return False
        return True

    def _project(self, row: dict) -> dict:
        if self.columns == "*":
            return dict(row)
        keys = [part.strip() for part in str(self.columns).split(",") if part.strip()]
        return {key: row.get(key) for key in keys}

    def _run(self) -> _Result:
        rows = self.tables.setdefault(self.name, [])
        if self.op == "insert":
            stored = dict(self.payload)
            rows.append(stored)
            return _Result([dict(stored)], 1)
        matched = [row for row in rows if self._matches(row)]
        if self.op == "update":
            for row in matched:
                row.update(self.payload)
            copied = [dict(row) for row in matched]
            return _Result(copied, len(copied))
        if self.op == "delete":
            self.tables[self.name] = [row for row in rows if row not in matched]
            return _Result([dict(row) for row in matched], len(matched))
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        projected = [self._project(row) for row in matched]
        if self.single:
            if len(projected) == 1:
                return _Result(projected[0], 1)
            return _Result(None, 0)
        return _Result(projected, len(projected))

    def execute(self):
        if self.sync:
            return self._run()

        async def _go():
            return self._run()

        return _go()


class _User:
    def __init__(self, user_id: str, email: str):
        self.id = user_id
        self.email = email
        self.email_confirmed_at = "2026-01-01T00:00:00+00:00"
        self.identities = []


class _Auth:
    def __init__(self, box: dict):
        self.box = box

    async def get_user(self, token: str):
        class _Response:
            pass

        response = _Response()
        response.user = self.box["user"]
        return response


class _Database:
    def __init__(self, tables: dict, box: dict, sync: bool):
        self.tables = tables
        self.auth = _Auth(box)
        self.sync = sync

    def table(self, name: str) -> _Query:
        return _Query(self.tables, name, self.sync)


class _Account:
    def __init__(self, account_id: str):
        self.id = account_id
        self.details_submitted = True
        self.payouts_enabled = True
        self.charges_enabled = True
        self.requirements = None
        self.capabilities = {"transfers": "active"}


class _Transfer:
    id = "tr_placeholder"


def _load_stripe_module():
    path = Path(__file__).resolve().parents[1] / "app" / "services" / "stripe_connect_service.py"
    spec = importlib.util.spec_from_file_location("stripe_connect_service_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_STRIPE = None


def _stripe_module():
    global _STRIPE
    if _STRIPE is None:
        _STRIPE = _load_stripe_module()
    return _STRIPE


class World:
    def __init__(self, monkeypatch):
        self.tables: dict[str, list] = {
            "vendor_payout_rates": [
                {
                    "package_id": "photos_only",
                    "sqft_tier": "small",
                    "vendor_tier": 1,
                    "amount_cents": RATE_CENTS,
                }
            ],
            "contractors": [
                {
                    "id": "contractor-company",
                    "vendor_id": VENDOR_ID,
                    "member_id": None,
                    "stripe_account_id": COMPANY_ACCOUNT,
                    "full_name": "Company payouts",
                    "connect_status": "connected",
                }
            ],
        }
        self.box: dict = {"user": None}
        self.db = _Database(self.tables, self.box, sync=False)
        self.transfers: list[dict] = []
        stripe_mod = _stripe_module()
        stripe_mod.supabase = _Database(self.tables, self.box, sync=True)
        stripe_mod.stripe_lib.api_key = "placeholder"
        stripe_mod.stripe_lib.Account.retrieve = lambda account_id: _Account(account_id)

        def _create_transfer(**params):
            self.transfers.append(dict(params))
            return _Transfer()

        stripe_mod.stripe_lib.Transfer.create = _create_transfer
        monkeypatch.setattr(
            sys.modules["app.services.stripe_connect_service"],
            "handle_payout_to_vendor",
            stripe_mod.handle_payout_to_vendor,
        )

        async def _get_db():
            return self.db

        monkeypatch.setattr(auth_mod, "get_db", _get_db)
        monkeypatch.setattr(vendor_os, "get_db", _get_db)
        monkeypatch.setattr(admin_ops, "get_db", _get_db)
        monkeypatch.setattr(payout_service, "_payout_rate_cache", {})
        monkeypatch.setattr(payout_service, "_payout_rate_cache_ts", 0.0)

    def add_member(self, member_id: str, *, role: str, title: str, email: str, payout_mode: str = "ic_rate_card") -> dict:
        row = {
            "id": member_id,
            "vendor_id": VENDOR_ID,
            "name": email.split("@")[0],
            "email": email,
            "phone": "",
            "role": role,
            "title": title,
            "payout_mode": payout_mode,
            "tier": 1,
            "is_active": True,
            "onboarding_status": "approved",
            "worker_type": "contractor",
            "auth_user_id": member_id,
            "vendor_type": "photographer",
        }
        self.tables.setdefault("vendor_members", []).append(row)
        return row

    def add_connect(self, member_id: str, account_id: str) -> None:
        self.tables["contractors"].append(
            {
                "id": f"contractor-{member_id}",
                "vendor_id": VENDOR_ID,
                "member_id": member_id,
                "stripe_account_id": account_id,
                "full_name": member_id,
                "connect_status": "connected",
            }
        )

    def login(self, member_id: str) -> None:
        row = self.member(member_id)
        self.box["user"] = _User(member_id, row["email"])

    def member(self, member_id: str) -> dict:
        for row in self.tables.get("vendor_members", []):
            if row["id"] == member_id:
                return row
        raise AssertionError(f"missing member {member_id}")

    def call(self, coro):
        try:
            return asyncio.run(coro), None
        except HTTPException as exc:
            return None, exc

    def update_member(self, member_id: str, **fields):
        return self.call(
            vendor_os.update_vendor_member(
                member_id,
                VendorMemberUpdate(**fields),
                authorization=SESSION,
                x_user_id=None,
            )
        )

    def create_member(self, **fields):
        return self.call(
            vendor_os.create_vendor_member(
                VendorMemberCreate(**fields),
                authorization=SESSION,
                x_user_id=None,
            )
        )

    def patch_applicant(self, member_id: str, **fields):
        return self.call(
            admin_ops.update_applicant_status(
                member_id,
                VendorMemberUpdate(**fields),
                authorization=SESSION,
                x_user_id=None,
            )
        )

    def pay(self, member_id: str) -> dict:
        self.transfers.clear()
        payout_service._payout_rate_cache = {}
        payout_service._payout_rate_cache_ts = 0.0
        reviews = self.tables.setdefault("vendor_probation_reviews", [])
        for index in range(probation_jobs()):
            reviews.append(
                {
                    "member_id": member_id,
                    "job_id": f"prior-{index}-{member_id}",
                    "payout_hold": "released",
                }
            )
        job_id = str(uuid.uuid4())
        self.tables.setdefault("jobs", []).append(
            {
                "id": job_id,
                "vendor_id": VENDOR_ID,
                "assignee_id": member_id,
                "package_id": "photos_only",
                "sqft": 800,
                "vendor_tier": 1,
                "rack_price": 299,
                "package_price": 299,
                "subtotal_cents": FULL_CENTS,
                "status": "delivered",
                "add_ons": [],
            }
        )
        return asyncio.run(payout_service.trigger_payout_for_job(self.db, job_id))


def _payout_cents(result: dict) -> int:
    if result.get("base_amount") is not None:
        return int(result["base_amount"])
    if result.get("amount") is not None:
        return int(result["amount"])
    raise AssertionError(result)


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def test_contractor_is_paid_the_rate_card_not_the_package_subtotal(world: World):
    """The harness pays the fixed cell when the member is not an owner."""
    account = "acct_photographer_placeholder"
    world.add_member(
        PHOTOGRAPHER_ID,
        role="Photographer",
        title="Photographer IC",
        email="photographer@example.com",
    )
    world.add_connect(PHOTOGRAPHER_ID, account)
    result = world.pay(PHOTOGRAPHER_ID)
    assert _payout_cents(result) == RATE_CENTS, result
    assert world.transfers, result
    assert world.transfers[0]["amount"] == RATE_CENTS
    assert world.transfers[0]["destination"] == account
    assert world.transfers[0]["destination"] != COMPANY_ACCOUNT


def test_platform_owner_id_is_paid_the_package_subtotal(world: World):
    """The fixed owner id keeps the package subtotal even with contractor labels."""
    world.add_member(
        OWNER_MEMBER_ID,
        role="Photographer",
        title="Photographer IC",
        email="platform-owner@example.com",
        payout_mode="ic_rate_card",
    )
    result = world.pay(OWNER_MEMBER_ID)
    assert is_owner_member(world.member(OWNER_MEMBER_ID))
    assert _payout_cents(result) == FULL_CENTS, result
    assert world.transfers, result
    assert world.transfers[0]["amount"] == FULL_CENTS
    assert world.transfers[0]["destination"] == COMPANY_ACCOUNT


def test_photographer_cannot_write_owner_payout_fields(world: World):
    account = "acct_photographer_placeholder"
    world.add_member(
        PHOTOGRAPHER_ID,
        role="Photographer",
        title="Photographer IC",
        email="photographer@example.com",
    )
    world.add_connect(PHOTOGRAPHER_ID, account)
    world.login(PHOTOGRAPHER_ID)
    body, exc = world.update_member(PHOTOGRAPHER_ID, payout_mode="owner_full", title="Owner", role="Owner")
    assert body is None
    assert exc is not None and exc.status_code == 403
    assert world.member(PHOTOGRAPHER_ID)["payout_mode"] == "ic_rate_card"
    result = world.pay(PHOTOGRAPHER_ID)
    assert _payout_cents(result) == RATE_CENTS, result
    assert world.transfers[0]["destination"] == account
    assert world.transfers[0]["amount"] == RATE_CENTS


def _assert_contractor_outcome(world: World, member_id: str, result: dict, personal_account: str | None):
    row = world.member(member_id)
    detail = {
        "result": result,
        "transfers": world.transfers,
        "member": {key: row.get(key) for key in ("id", "role", "title", "payout_mode")},
        "is_owner_member": is_owner_member(row),
    }
    assert row["id"] != OWNER_MEMBER_ID
    assert not is_owner_member(row), detail
    assert _payout_cents(result) == RATE_CENTS, detail
    assert _payout_cents(result) != FULL_CENTS, detail
    for transfer in world.transfers:
        assert transfer["amount"] == RATE_CENTS, detail
        assert transfer["destination"] != COMPANY_ACCOUNT, detail
    if personal_account:
        assert world.transfers, detail
        assert world.transfers[0]["destination"] == personal_account, detail
    else:
        assert world.transfers == [], detail


def test_manager_cannot_pay_themselves_the_package_subtotal_via_payout_mode(world: World):
    account = "acct_manager_placeholder"
    world.add_member(MANAGER_ID, role="Manager", title="Studio Manager", email="manager@example.com")
    world.add_connect(MANAGER_ID, account)
    world.login(MANAGER_ID)
    world.update_member(MANAGER_ID, payout_mode="owner_full")
    result = world.pay(MANAGER_ID)
    _assert_contractor_outcome(world, MANAGER_ID, result, account)


def test_manager_cannot_pay_themselves_the_package_subtotal_via_title(world: World):
    account = "acct_manager_placeholder"
    world.add_member(MANAGER_ID, role="Manager", title="Studio Manager", email="manager@example.com")
    world.add_connect(MANAGER_ID, account)
    world.login(MANAGER_ID)
    world.update_member(MANAGER_ID, title="Owner")
    result = world.pay(MANAGER_ID)
    _assert_contractor_outcome(world, MANAGER_ID, result, account)


def test_manager_cannot_pay_themselves_the_package_subtotal_via_role(world: World):
    account = "acct_manager_placeholder"
    world.add_member(MANAGER_ID, role="Manager", title="Studio Manager", email="manager@example.com")
    world.add_connect(MANAGER_ID, account)
    world.login(MANAGER_ID)
    world.update_member(MANAGER_ID, role="Owner")
    result = world.pay(MANAGER_ID)
    _assert_contractor_outcome(world, MANAGER_ID, result, account)


def test_title_substring_manager_cannot_pay_an_accomplice_the_package_subtotal(world: World):
    manager_account = "acct_manager_placeholder"
    accomplice_account = "acct_accomplice_placeholder"
    world.add_member(
        MANAGER_ID,
        role="Photographer",
        title="Regional Manager",
        email="regional.manager@example.com",
    )
    world.add_member(
        ACCOMPLICE_ID,
        role="Photographer",
        title="Photographer IC",
        email="accomplice@example.com",
    )
    world.add_connect(MANAGER_ID, manager_account)
    world.add_connect(ACCOMPLICE_ID, accomplice_account)
    world.login(MANAGER_ID)
    world.update_member(ACCOMPLICE_ID, payout_mode="owner_full")
    result = world.pay(ACCOMPLICE_ID)
    _assert_contractor_outcome(world, ACCOMPLICE_ID, result, accomplice_account)
    for transfer in world.transfers:
        assert transfer["destination"] != manager_account


def test_admin_create_with_owner_role_does_not_pay_the_package_subtotal(world: World):
    world.add_member(MANAGER_ID, role="Admin", title="Dispatch Admin", email="admin@example.com")
    world.login(MANAGER_ID)
    body, exc = world.create_member(
        name="New Contractor",
        email="new.contractor@example.com",
        phone="",
        role="Owner",
        vendor_type="photographer",
    )
    created = [row for row in world.tables.get("vendor_members", []) if row.get("email") == "new.contractor@example.com"]
    if not created:
        assert exc is not None and exc.status_code in (400, 403, 422), (body, exc)
        return
    assert exc is None or exc.status_code in (400, 403, 422)
    created_id = created[0]["id"]
    assert created_id != OWNER_MEMBER_ID
    result = world.pay(created_id)
    _assert_contractor_outcome(world, created_id, result, None)


def test_admin_applicant_patch_cannot_set_owner_payout_fields(world: World):
    world.add_member(MANAGER_ID, role="Admin", title="Dispatch Admin", email="admin@example.com")
    world.add_member(
        APPLICANT_ID,
        role="Photographer",
        title="Photographer IC",
        email="applicant@example.com",
    )
    world.login(MANAGER_ID)
    world.patch_applicant(APPLICANT_ID, payout_mode="owner_full", title="Owner", role="Owner")
    result = world.pay(APPLICANT_ID)
    _assert_contractor_outcome(world, APPLICANT_ID, result, None)


def test_approving_an_applicant_cannot_keep_owner_title_or_payout_mode(world: World):
    world.add_member(MANAGER_ID, role="Admin", title="Dispatch Admin", email="admin@example.com")
    world.add_member(
        APPLICANT_ID,
        role="Photographer",
        title="Photographer IC",
        email="applicant@example.com",
    )
    world.member(APPLICANT_ID)["onboarding_status"] = "pending"
    world.login(MANAGER_ID)
    world.patch_applicant(
        APPLICANT_ID,
        onboarding_status="approved",
        payout_mode="owner_full",
        title="Owner",
        role="Owner",
    )
    result = world.pay(APPLICANT_ID)
    _assert_contractor_outcome(world, APPLICANT_ID, result, None)
