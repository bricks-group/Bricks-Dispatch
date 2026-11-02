"""Vendor Connect onboarding must not 500 when member vendor_id ('default_vendor')
has no public.vendors row (contractors_vendor_id_fkey)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import importlib.util
from pathlib import Path

from app.shared import pg_fallback as pf

# conftest stubs app.services.stripe_connect_service; load the real module.
_spec = importlib.util.spec_from_file_location(
    "real_stripe_connect_service",
    Path(__file__).resolve().parents[1] / "app" / "services" / "stripe_connect_service.py",
)
scs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scs)

FK_MSG = (
    'insert or update on table "contractors" violates foreign key constraint '
    '"contractors_vendor_id_fkey"'
)


class FakeTable:
    def __init__(self, db):
        self.db, self.op, self.row = db, None, None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
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
            return SimpleNamespace(data=[])
        if self.op == "insert":
            self.db.inserts.append(self.row)
            if self.row.get("vendor_id") and self.row["vendor_id"] not in self.db.vendors:
                raise Exception({"code": "23503", "message": FK_MSG})
            return SimpleNamespace(data=[{"id": "c-1", **self.row}])
        self.db.updates.append(self.row)
        return SimpleNamespace(data=[{}])


class FakeDb:
    def __init__(self, vendors=()):
        self.vendors, self.inserts, self.updates = set(vendors), [], []

    def table(self, name):
        assert name == "contractors"
        return FakeTable(self)


@pytest.fixture
def fake_stripe(monkeypatch):
    calls = {}
    monkeypatch.setattr(scs.stripe_lib.Account, "create",
                        lambda **k: calls.setdefault("acct", k) and SimpleNamespace(id="acct_test"))
    monkeypatch.setattr(scs.stripe_lib.AccountLink, "create",
                        lambda **k: calls.setdefault("link", k) and SimpleNamespace(url="https://connect.stripe.com/setup/e/x"))
    return calls


def test_member_without_vendors_row_gets_link(monkeypatch, fake_stripe):
    db = FakeDb(vendors=())
    monkeypatch.setattr(scs, "supabase", db)
    url = asyncio.run(scs.create_connect_account_and_link(
        vendor_id="default_vendor", refresh_url="https://go/r", return_url="https://go/r",
        full_name="Rajvir", email="r@example.com", member_id="m-1"))
    assert url.startswith("https://connect.stripe.com/")
    assert db.inserts[0]["vendor_id"] == "default_vendor"
    assert "vendor_id" not in db.inserts[1] and db.inserts[1]["member_id"] == "m-1"
    assert fake_stripe["acct"]["type"] == "express" and fake_stripe["acct"]["country"] == "CA"
    assert db.updates[0]["stripe_account_id"] == "acct_test"


def test_member_with_real_vendor_keeps_vendor_id(monkeypatch, fake_stripe):
    db = FakeDb(vendors={"default_vendor"})
    monkeypatch.setattr(scs, "supabase", db)
    asyncio.run(scs.create_connect_account_and_link(
        vendor_id="default_vendor", refresh_url="r", return_url="r",
        full_name="A", email="a@example.com", member_id="m-2"))
    assert len(db.inserts) == 1 and db.inserts[0]["vendor_id"] == "default_vendor"


def test_company_row_fk_still_raises(monkeypatch, fake_stripe):
    db = FakeDb(vendors=())
    monkeypatch.setattr(scs, "supabase", db)
    with pytest.raises(Exception):
        asyncio.run(scs.create_connect_account_and_link(
            vendor_id="default_vendor", refresh_url="r", return_url="r", member_id=None))
    assert "acct" not in fake_stripe


def test_pg_insert_contractor_accepts_member_without_vendor(monkeypatch):
    executed = {}

    class Cur:
        def execute(self, sql, vals):
            executed["sql"], executed["vals"] = sql, vals

        def fetchone(self):
            return None

        description = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

    class Conn:
        def cursor(self, *a, **k):
            return Cur()

        def commit(self):
            pass

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pf, "_pg_connect", lambda: Conn())
    pf.pg_insert_contractor({"member_id": "m-1", "full_name": "R", "email": "r@x"})
    assert "member_id" in executed.get("sql", "")
    assert pf.pg_insert_contractor({"full_name": "R", "email": "r@x"}) is None
