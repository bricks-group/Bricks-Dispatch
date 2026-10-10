"""Fair-Use: public /apply inserts via Postgres when PostgREST is 402."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.routers import onboarding as ob
from app.shared import pg_fallback as pf
from app.shared.schemas import VendorApplicationCreate


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota (Fair Use restricted)"


def test_pg_insert_vendor_application_sql(monkeypatch):
    executed = {}

    class Cur:
        def execute(self, sql, params=None):
            executed["sql"] = sql
            executed["params"] = params
            self.description = [
                ("id",), ("email",), ("name",), ("role",), ("application_token",),
                ("vendor_id",), ("onboarding_status",), ("is_active",),
            ]
            self._row = (
                params[0] if params else "x",
                "a@b.com",
                "Alex",
                "Applicant",
                "tok",
                "default_vendor",
                "pending",
                False,
            )

        def fetchone(self):
            return self._row

        def fetchall(self):
            return [self._row]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            pass

    monkeypatch.setattr(pf, "_pg_connect", lambda: Conn())
    out = pf.pg_insert_vendor_application(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "email": "A@B.com",
            "name": "Alex",
            "role": "Applicant",
            "application_token": "tok",
            "vendor_id": "default_vendor",
            "onboarding_status": "pending",
            "is_active": False,
            "portfolio_links": ["https://example.com"],
            "equipment_list": ["Canon"],
            "service_areas": ["Mississauga"],
        }
    )
    assert out is not None
    assert "INSERT INTO public.vendor_members" in executed["sql"]
    assert "RETURNING *" in executed["sql"]


def test_apply_inner_falls_back_on_402(monkeypatch):
    class Chain:
        def __init__(self):
            self._op = "select"

        def select(self, *_a, **_k):
            self._op = "select"
            return self

        def insert(self, *_a, **_k):
            self._op = "insert"
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            if self._op == "select":
                raise _QuotaErr()
            raise _QuotaErr()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(ob, "get_db", AsyncMock(return_value=Db()))
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_select_vendor_member",
        lambda actor_id="", email="": None,
    )
    inserted = {}

    def _insert(row):
        inserted["row"] = row
        return dict(row)

    monkeypatch.setattr("app.shared.pg_fallback.pg_insert_vendor_application", _insert)
    monkeypatch.setattr(
        "app.shared.pg_fallback.pg_upsert_vendor_availability",
        lambda row: dict(row),
    )

    app = VendorApplicationCreate(
        name="Mississauga Photog",
        email="mississauga.photog@example.com",
        phone="+1 416 555 0100",
        specialties=["photo"],
        portfolio_links=["https://portfolio.example.com"],
        equipment_list=["Sony A7IV"],
        city="Mississauga, ON",
        accepted_terms=True,
        ic_agreement_version="1.7",
        hst_exempt=True,
    )
    out = asyncio.run(ob._apply_to_bricks_inner(app))
    assert out["success"] is True
    assert out["member_id"]
    assert out["application_token"]
    assert inserted["row"]["email"] == "mississauga.photog@example.com"
    assert inserted["row"]["application_token"] == out["application_token"]
