"""Fair-Use: payouts + Stripe Connect via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services import payout_service as ps
from app.services import stripe_connect_service as scs
from app.shared import pg_fallback as pf


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def _mock_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close = MagicMock()
    return conn


def test_pg_update_job_accepts_payout_amount(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    ok = pf.pg_update_job("job-pay", {"payout_amount": 14950})
    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "UPDATE public.jobs" in sql
    assert "payout_amount" in sql
    assert 14950 in cur.execute.call_args[0][1]


def test_pg_insert_payout_and_update(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("job_id",),
        ("vendor_id",),
        ("amount",),
        ("status",),
        ("created_at",),
    ]
    cur.fetchone.return_value = (
        "pay-1",
        "job-1",
        "default_vendor",
        10000,
        "pending",
        "2026-09-22T00:00:00+00:00",
    )
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    out = pf.pg_insert_payout(
        {
            "job_id": "job-1",
            "vendor_id": "default_vendor",
            "amount": 10000,
            "status": "pending",
            "contractor_id": "photog-1",
        }
    )
    assert out is not None
    assert out["job_id"] == "job-1"
    assert "INSERT INTO public.payouts" in cur.execute.call_args[0][0]

    cur2 = MagicMock()
    cur2.rowcount = 1
    cur2.__enter__ = lambda s: s
    cur2.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur2))
    assert pf.pg_update_payout("pay-1", {"status": "paid", "stripe_transfer_id": "tr_1"}) is True
    assert "UPDATE public.payouts" in cur2.execute.call_args[0][0]
    assert "stripe_transfer_id" in cur2.execute.call_args[0][0]


def test_pg_contractor_helpers(monkeypatch):
    cur = MagicMock()
    cur.rowcount = 1
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [
        ("id",),
        ("stripe_account_id",),
        ("connect_status",),
        ("vendor_id",),
        ("full_name",),
        ("email",),
    ]
    cur.fetchone.return_value = (
        "c-1",
        "acct_1",
        "pending",
        "default_vendor",
        "Cam",
        "cam@bricks.internal",
    )
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur))

    row = pf.pg_select_contractor_by_vendor_id("default_vendor")
    assert row["id"] == "c-1"
    assert row["stripe_account_id"] == "acct_1"

    cur2 = MagicMock()
    cur2.rowcount = 1
    cur2.__enter__ = lambda s: s
    cur2.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(pf, "_pg_connect", lambda: _mock_conn(cur2))
    assert pf.pg_update_contractor("c-1", {"connect_status": "connected"}) is True
    assert "UPDATE public.contractors" in cur2.execute.call_args[0][0]


def test_get_fixed_payout_amount_pg_on_402(monkeypatch):
    ps._payout_rate_cache = {}
    ps._payout_rate_cache_ts = 0.0

    class Chain:
        def select(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            assert name == "vendor_payout_rates"
            return Chain()

    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_payout_rates",
        lambda: [
            {
                "package_id": "standard",
                "sqft_tier": "small",
                "vendor_tier": 1,
                "amount_cents": 10465,
            }
        ],
    )
    amount = asyncio.run(ps.get_fixed_payout_amount(Db(), "photos_only", 1200, 1))
    assert amount == 10465


def test_trigger_payout_for_job_pg_on_402(monkeypatch):
    """Full happy path with every REST call 402 → Postgres helpers."""
    job = {
        "id": "job-fu",
        "vendor_id": "default_vendor",
        "assignee_id": "photog-1",
        "rack_price": 299,
        "package_price": 299,
        "package_id": "standard",
        "sqft": 1200,
        "vendor_tier": 1,
        "booking_id": "bk-1",
    }
    member = {
        "id": "photog-1",
        "tier": 1,
        "hst_number": None,
        "role": "Photographer",
        "title": "Photographer",
        "payout_mode": "ic",
        "email": "p@example.com",
    }
    pg_calls = {"job": 0, "member": 0, "payout_sel": 0, "insert": 0, "job_upd": 0, "payout_upd": []}

    class Chain:
        def select(self, *_a, **_k):
            return self

        def insert(self, *_a, **_k):
            return self

        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def lte(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_job_by_id",
        lambda jid: pg_calls.__setitem__("job", pg_calls["job"] + 1) or job,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_member",
        lambda actor_id="", email="": pg_calls.__setitem__("member", pg_calls["member"] + 1) or member,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_payout_by_job_id",
        lambda jid: pg_calls.__setitem__("payout_sel", pg_calls["payout_sel"] + 1) or None,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_payout_rates",
        lambda: [
            {
                "package_id": "standard",
                "sqft_tier": "small",
                "vendor_tier": 1,
                "amount_cents": 10465,
            }
        ],
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_trip_fees",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_insert_payout",
        lambda row: pg_calls.__setitem__("insert", pg_calls["insert"] + 1)
        or {**row, "id": "pay-fu-1"},
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_job",
        lambda jid, fields: pg_calls.__setitem__("job_upd", pg_calls["job_upd"] + 1) or True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_payout",
        lambda pid, fields: pg_calls["payout_upd"].append((pid, fields)) or True,
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_trip_fees_by_ids",
        lambda *a, **k: True,
    )

    async def _xfer(**kwargs):
        return "tr_fu_1"

    # conftest stubs stripe_connect_service as MagicMock; set AsyncMock on it.
    monkeypatch.setattr(scs, "handle_payout_to_vendor", AsyncMock(side_effect=_xfer))

    ps._payout_rate_cache = {}
    ps._payout_rate_cache_ts = 0.0

    result = asyncio.run(ps.trigger_payout_for_job(Db(), "job-fu"))
    assert result["status"] == "paid"
    assert result["payout_id"] == "pay-fu-1"
    assert result["stripe_transfer_id"] == "tr_fu_1"
    assert result["base_amount"] == 10465
    assert pg_calls["job"] >= 1
    assert pg_calls["insert"] == 1
    assert pg_calls["job_upd"] == 1
    assert any(f.get("status") == "paid" for _pid, f in pg_calls["payout_upd"])



def _load_real_scs():
    """Load real stripe_connect_service (conftest stubs the package module)."""
    import importlib.util
    from pathlib import Path as _P
    _path = _P(__file__).resolve().parents[1] / "app" / "services" / "stripe_connect_service.py"
    _spec = importlib.util.spec_from_file_location("stripe_connect_service_fu", _path)
    mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(mod)
    return mod


def test_handle_payout_to_vendor_pg_on_402(monkeypatch):
    scs_real = _load_real_scs()
    member_calls = []
    contractor_calls = []

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            raise _QuotaErr()

    class Client:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(scs_real, "supabase", Client())
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_member",
        lambda actor_id="", email="": member_calls.append(actor_id)
        or {"id": actor_id, "vendor_id": "default_vendor"},
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_contractor_by_vendor_id",
        lambda vid: contractor_calls.append(vid)
        or {
            "id": "c-1",
            "stripe_account_id": "acct_ready",
            "connect_status": "connected",
            "vendor_id": vid,
        },
    )
    monkeypatch.setattr(
        scs_real,
        "sync_contractor_connect_status",
        lambda cid, aid: {
            "connected": True,
            "connect_status": "connected",
            "details_submitted": True,
            "payouts_enabled": True,
            "charges_enabled": True,
        },
    )

    class Transfer:
        id = "tr_pg_1"

    monkeypatch.setattr(
        scs_real.stripe_lib.Transfer,
        "create",
        staticmethod(lambda **kw: Transfer()),
    )
    monkeypatch.setattr(scs_real.stripe_lib, "api_key", "sk_test_x")

    tid = asyncio.run(
        scs_real.handle_payout_to_vendor(
            vendor_id="default_vendor",
            amount=10000,
            booking_id="bk-1",
            payout_id="pay-1",
            contractor_id="photog-1",
        )
    )
    assert tid == "tr_pg_1"
    assert member_calls == ["photog-1"]
    assert contractor_calls == ["default_vendor"]


def test_create_connect_account_pg_on_402(monkeypatch):
    scs_real = _load_real_scs()

    class Chain:
        def select(self, *_a, **_k):
            return self

        def insert(self, *_a, **_k):
            return self

        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            raise _QuotaErr()

    class Client:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(scs_real, "supabase", Client())
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_contractor_by_vendor_id",
        lambda vid: None,
    )
    inserted = []

    def _insert(row):
        inserted.append(row)
        return {"id": "c-new", "vendor_id": row["vendor_id"], "stripe_account_id": None}

    monkeypatch.setattr("app.shared.pg_fallback.pg_insert_contractor", _insert)
    updates = []
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_contractor",
        lambda cid, fields: updates.append((cid, fields)) or True,
    )

    class Acct:
        id = "acct_new"

    class Link:
        url = "https://connect.stripe.com/setup/e/test"

    monkeypatch.setattr(scs_real.stripe_lib.Account, "create", staticmethod(lambda **kw: Acct()))
    monkeypatch.setattr(scs_real.stripe_lib.AccountLink, "create", staticmethod(lambda **kw: Link()))
    monkeypatch.setattr(scs_real.stripe_lib, "api_key", "sk_test_x")

    url = asyncio.run(
        scs_real.create_connect_account_and_link(
            "default_vendor",
            "https://go.brxs.ca/refresh",
            "https://go.brxs.ca/return",
            full_name="Cam",
            email="cam@example.com",
        )
    )
    assert url.startswith("https://connect.stripe.com/")
    assert inserted and inserted[0]["vendor_id"] == "default_vendor"
    assert updates and updates[0][0] == "c-new"
    assert updates[0][1]["stripe_account_id"] == "acct_new"
    assert updates[0][1]["connect_status"] == "pending"


def test_list_payouts_router_pg_on_402(monkeypatch):
    from app.routers import payouts as payouts_router

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def range(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(payouts_router, "require_vendor_admin", AsyncMock(return_value=None))
    monkeypatch.setattr(payouts_router, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_list_payouts",
        lambda **kw: [{"id": "pay-1", "status": "paid", "amount": 100}],
    )
    out = asyncio.run(
        payouts_router.list_payouts(
            vendor_id=None,
            status=None,
            limit=50,
            offset=0,
            authorization="Bearer x",
            x_user_id=None,
            x_internal_api_key=None,
        )
    )
    assert out == [{"id": "pay-1", "status": "paid", "amount": 100}]


def test_payout_summary_router_pg_on_402(monkeypatch):
    from app.routers import payouts as payouts_router

    class Chain:
        def select(self, *_a, **_k):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(payouts_router, "require_vendor_admin", AsyncMock(return_value=None))
    monkeypatch.setattr(payouts_router, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_payouts_status_amounts",
        lambda: [
            {"status": "paid", "amount": 100},
            {"status": "pending", "amount": 50},
            {"status": "failed", "amount": 25},
        ],
    )
    out = asyncio.run(
        payouts_router.payout_summary(
            authorization="Bearer x",
            x_user_id=None,
            x_internal_api_key=None,
        )
    )
    assert out["total_payouts"] == 3
    assert out["paid_count"] == 1
    assert out["paid_amount"] == 100
    assert out["pending_amount"] == 50
    assert out["failed_amount"] == 25


def test_stripe_connect_status_pg_on_402(monkeypatch):
    from app.routers import stripe_connect_status as sc_status

    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(sc_status, "resolve_vendor_actor_id", AsyncMock(return_value="owner-1"))
    monkeypatch.setattr(
        sc_status,
        "get_vendor_member",
        AsyncMock(return_value={"id": "owner-1", "vendor_id": "default_vendor", "role": "Owner"}),
    )
    monkeypatch.setattr(sc_status, "is_vendor_admin", lambda m: True)
    monkeypatch.setattr(sc_status, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_contractor_by_vendor_id",
        lambda vid: {
            "id": "c-1",
            "stripe_account_id": "acct_1",
            "connect_status": "pending",
            "vendor_id": vid,
        },
    )
    scs_real = _load_real_scs()
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.services.stripe_connect_service",
        scs_real,
    )
    monkeypatch.setattr(
        scs_real,
        "sync_contractor_connect_status",
        lambda cid, aid: {
            "connected": True,
            "connect_status": "connected",
            "details_submitted": True,
            "payouts_enabled": True,
            "charges_enabled": True,
        },
    )
    out = asyncio.run(
        sc_status.get_stripe_connect_status(authorization="Bearer x", x_user_id=None)
    )
    assert out["connected"] is True
    assert out["connect_status"] == "connected"


def test_connect_webhook_pg_on_402(monkeypatch):
    from app.routers import stripe_connect_webhook as wh

    class Chain:
        def select(self, *_a, **_k):
            return self

        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setenv("STRIPE_CONNECT_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(wh, "get_db", AsyncMock(return_value=Db()))

    # Use real classify_connect_account
    scs_real = _load_real_scs()
    monkeypatch.setattr(wh, "classify_connect_account", scs_real.classify_connect_account)

    event = {
        "type": "account.updated",
        "data": {
            "object": {
                "id": "acct_wh",
                "details_submitted": True,
                "payouts_enabled": True,
                "charges_enabled": True,
                "requirements": {"disabled_reason": None},
            }
        },
    }
    monkeypatch.setattr(
        wh.stripe_lib.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig, secret: event),
    )
    updates = []
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_contractor_by_stripe_account_id",
        lambda aid: {"id": "c-wh", "stripe_account_id": aid},
    )
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_update_contractor",
        lambda cid, fields: updates.append((cid, fields)) or True,
    )

    class FakeReq:
        async def body(self):
            return b"{}"

    out = asyncio.run(wh.stripe_connect_webhook(FakeReq(), stripe_signature="t=1,v1=x"))
    assert out["ok"] is True
    assert out["contractor_id"] == "c-wh"
    assert updates and updates[0][0] == "c-wh"
    assert updates[0][1]["connect_status"] == "connected"
