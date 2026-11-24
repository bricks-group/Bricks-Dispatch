"""Office listing search must not return another brokerage's transactions.

The handler under test is search_office_listings / attach_job_listing. Queries
run on the service-role client (RLS bypassed), so the only row filter is the
PostgREST query string the handler builds.

This stand-in applies that query the way PostgREST v13.0.7 does:

- each filter parameter is its own logic-tree statement, AND-ed in the WHERE
  (Plan.hs addFilterToLogicForest; QueryBuilder.hs readPlanToQuery)
- a missing parameter is not a predicate (no WHERE term is invented)
- ilike maps '*' to '%' then uses SQL ILIKE (SqlFragment.hs star / OpILike)
- eq is SQL '='
- order + limit run after the filter
- maybe_single is the postgrest-py 2.33/2.34 client rule: no row -> None,
  one row -> that object, more than one row -> error

service_role is created BYPASSRLS (supabase postgres init-scripts
00000000000000-initial-schema.sql). The stand-in therefore does not add an
RLS predicate of its own. supabase-py 2.34.1 sends the service key as
Authorization and apiKey and does not attach the caller's user JWT.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from app.routers import vendor_os as vos
from app.shared import auth as auth_mod
from app.shared.fair_use_auth import clear_auth_cooldown


# Placeholder only. session_amr_methods decodes without checking the signature.
_JWT_PLACEHOLDER = "placeholder-not-a-secret-padding!!"

AUTH_UID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MEMBER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OTHER_MEMBER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ATTACKER_EMAIL = "applicant@example.com"

BROKERAGE_A = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
BROKERAGE_B = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
BROKERAGE_C = "ffffffff-ffff-4fff-8fff-ffffffffffff"

LISTING_A = "11111111-1111-4111-8111-111111111111"
LISTING_B = "22222222-2222-4222-8222-222222222222"
LISTING_C = "33333333-3333-4333-8333-333333333333"

ADDR_A = "4 King Street"
ADDR_B = "88 King Street West"
ADDR_C = "12 King Street East"

OWN_JOB = "44444444-4444-4444-8444-444444444444"
UNSCOPED_JOB = "55555555-5555-4555-8555-555555555555"
OTHER_JOB = "66666666-6666-4666-8666-666666666666"

FOREIGN_MARKERS = (LISTING_B, LISTING_C, ADDR_B, ADDR_C)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ilike(value, pattern: str) -> bool:
    """SQL ILIKE after PostgREST's '*' -> '%' rewrite. Default escape is backslash."""
    text = "" if value is None else str(value)
    pattern = pattern.replace("*", "%")
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            parts.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
        i += 1
    return re.fullmatch("".join(parts), text, flags=re.IGNORECASE) is not None


def _eq(left, right) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return str(left) == str(right)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table: str):
        self._db = db
        self._table = table
        self._filters: list[tuple] = []
        self._order: tuple | None = None
        self._limit: int | None = None
        self._single = False
        self._op = "select"
        self._patch: dict | None = None

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def ilike(self, column, pattern):
        self._filters.append(("ilike", column, pattern))
        return self

    def in_(self, column, values):
        self._filters.append(("in", column, list(values)))
        return self

    def is_(self, column, value):
        self._filters.append(("is", column, value))
        return self

    def order(self, column, *, desc: bool = False, **_k):
        self._order = (column, desc)
        return self

    def limit(self, size, **_k):
        self._limit = size
        return self

    def maybe_single(self):
        self._single = True
        return self

    def update(self, patch):
        self._op = "update"
        self._patch = dict(patch)
        return self

    def _matches(self, row: dict) -> bool:
        for op, column, value in self._filters:
            cell = row.get(column)
            if op == "eq" and not _eq(cell, value):
                return False
            if op == "ilike" and not _ilike(cell, value):
                return False
            if op == "in" and str(cell) not in {str(v) for v in value}:
                return False
            if op == "is":
                # postgrest-py sends None as the criteria "null" (IS NULL).
                if value in (None, "null"):
                    if cell is not None:
                        return False
                elif not _eq(cell, value):
                    return False
        return True

    def _rows(self) -> list[dict]:
        matched = [row for row in self._db.tables[self._table] if self._matches(row)]
        if self._order:
            column, desc = self._order
            matched.sort(key=lambda row: (row.get(column) is None, str(row.get(column) or "")), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return matched

    async def execute(self):
        self._db.calls.append(
            {
                "table": self._table,
                "op": self._op,
                "filters": list(self._filters),
                "single": self._single,
            }
        )
        if self._op == "update":
            matched = self._rows()
            for row in matched:
                row.update(self._patch or {})
            self._db.updates.append((self._table, dict(self._patch or {}), [row.get("id") for row in matched]))
            return _Result([dict(row) for row in matched])

        matched = [dict(row) for row in self._rows()]
        if not self._single:
            return _Result(matched)
        # postgrest-py AsyncMaybeSingleRequestBuilder.execute
        if len(matched) == 0:
            return None
        if len(matched) == 1:
            return _Result(matched[0])
        raise RuntimeError("Cannot coerce the result to a single JSON object")


class _Auth:
    def __init__(self, user):
        self._user = user

    async def get_user(self, token: str):
        # Stand-in for Supabase Auth: a non-empty bearer is the session the
        # product already issued. No tenant filter is applied here.
        if not token:
            raise RuntimeError("missing token")
        return SimpleNamespace(user=self._user)


class _Db:
    def __init__(self, tables, user):
        self.tables = tables
        self.auth = _Auth(user)
        self.calls: list[dict] = []
        self.updates: list[tuple] = []

    def table(self, name: str):
        return _Query(self, name)


def _session_token() -> str:
    return jwt.encode(
        {"sub": AUTH_UID, "amr": [{"method": "otp"}], "email_verified": True},
        _JWT_PLACEHOLDER,
        algorithm="HS256",
    )


def _world():
    user = SimpleNamespace(
        id=AUTH_UID,
        email=ATTACKER_EMAIL,
        email_confirmed_at="2026-11-01T00:00:00+00:00",
        identities=[],
        user_metadata={},
    )
    tables = {
        "vendor_members": [
            {
                "id": MEMBER_ID,
                "auth_user_id": AUTH_UID,
                "email": ATTACKER_EMAIL,
                "name": "Applicant",
                "role": "Applicant",
                "title": "",
                "is_active": False,
                "onboarding_status": "pending",
                "vendor_id": "attacker-vendor",
            }
        ],
        "jobs": [
            {
                "id": OWN_JOB,
                "assignee_id": MEMBER_ID,
                "vendor_id": "attacker-vendor",
                "brokerage_id": BROKERAGE_A,
                "status": "assigned",
                "transaction_id": None,
            },
            {
                "id": UNSCOPED_JOB,
                "assignee_id": MEMBER_ID,
                "vendor_id": "attacker-vendor",
                "brokerage_id": None,
                "status": "assigned",
                "transaction_id": None,
            },
            {
                "id": OTHER_JOB,
                "assignee_id": OTHER_MEMBER,
                "vendor_id": "other-vendor",
                "brokerage_id": BROKERAGE_B,
                "status": "assigned",
                "transaction_id": None,
            },
        ],
        "transactions": [
            {
                "id": LISTING_A,
                "property_address": ADDR_A,
                "city": "Toronto",
                "deal_type": "purchase",
                "brokerage_id": BROKERAGE_A,
                "created_at": "2026-11-01T00:00:00+00:00",
            },
            {
                "id": LISTING_B,
                "property_address": ADDR_B,
                "city": "Toronto",
                "deal_type": "purchase",
                "brokerage_id": BROKERAGE_B,
                "created_at": "2026-11-02T00:00:00+00:00",
            },
            {
                "id": LISTING_C,
                "property_address": ADDR_C,
                "city": "Mississauga",
                "deal_type": "lease",
                "brokerage_id": BROKERAGE_C,
                "created_at": "2026-11-03T00:00:00+00:00",
            },
        ],
    }
    return _Db(tables, user)


def _install(monkeypatch, db):
    clear_auth_cooldown()

    async def fake_get_db():
        return db

    monkeypatch.setattr(auth_mod, "get_db", fake_get_db)
    monkeypatch.setattr(vos, "get_db", fake_get_db)


def _assert_no_foreign(payload) -> None:
    blob = str(payload)
    leaked = [mark for mark in FOREIGN_MARKERS if mark in blob]
    assert not leaked, f"foreign listing fields returned: {leaked} in {payload}"


def _search(db, monkeypatch, **kwargs):
    _install(monkeypatch, db)
    token = _session_token()
    try:
        return _run(
            vos.search_office_listings(
                authorization=f"Bearer {token}",
                x_user_id=None,
                **kwargs,
            )
        )
    except HTTPException as exc:
        return exc


def _job_row(db, job_id: str) -> dict:
    for row in db.tables["jobs"]:
        if row["id"] == job_id:
            return row
    raise AssertionError(f"missing job {job_id}")


def test_listings_route_is_registered():
    paths = {getattr(route, "path", None) for route in vos.router.routes}
    assert "/api/vendor-os/listings" in paths
    assert "/api/vendor-os/jobs/{job_id}/listing" in paths


def test_scoped_job_returns_only_that_brokerage(monkeypatch):
    """A job that has a brokerage_id may return that office's match and nothing else."""
    db = _world()
    out = _search(db, monkeypatch, q="king", job_id=OWN_JOB)
    assert isinstance(out, dict), out
    addresses = [item.get("address") for item in out["items"]]
    assert addresses == [ADDR_A]
    _assert_no_foreign(out)


def test_address_search_without_job_id_hides_other_brokerages(monkeypatch):
    db = _world()
    out = _search(db, monkeypatch, q="king", job_id=None)
    _assert_no_foreign(out)
    if isinstance(out, dict):
        assert out.get("items") == []


def test_uuid_lookup_without_job_id_hides_other_brokerages(monkeypatch):
    db = _world()
    out = _search(db, monkeypatch, q=LISTING_B, job_id=None)
    _assert_no_foreign(out)
    if isinstance(out, dict):
        assert out.get("items") == []


def test_address_fragment_without_job_id_hides_that_listing(monkeypatch):
    db = _world()
    out = _search(db, monkeypatch, q="88 king", job_id=None)
    _assert_no_foreign(out)
    if isinstance(out, dict):
        assert out.get("items") == []


def test_job_without_brokerage_id_does_not_search_every_office(monkeypatch):
    db = _world()
    out = _search(db, monkeypatch, q="king", job_id=UNSCOPED_JOB)
    _assert_no_foreign(out)
    if isinstance(out, dict):
        assert out.get("items") == []


def test_uuid_lookup_on_job_without_brokerage_id_is_empty(monkeypatch):
    db = _world()
    out = _search(db, monkeypatch, q=LISTING_C, job_id=UNSCOPED_JOB)
    _assert_no_foreign(out)
    if isinstance(out, dict):
        assert out.get("items") == []


def test_other_photographers_job_does_not_open_its_brokerage(monkeypatch):
    db = _world()
    out = _search(db, monkeypatch, q="king", job_id=OTHER_JOB)
    _assert_no_foreign(out)
    assert isinstance(out, HTTPException)
    assert out.status_code in (403, 404)


def test_attach_on_job_without_brokerage_does_not_read_or_write_foreign_listing(monkeypatch):
    db = _world()
    _install(monkeypatch, db)
    token = _session_token()
    try:
        out = _run(
            vos.attach_job_listing(
                UNSCOPED_JOB,
                vos.JobListingUpdate(transaction_id=LISTING_B),
                f"Bearer {token}",
                None,
            )
        )
    except HTTPException as exc:
        out = exc
    _assert_no_foreign(out)
    assert _job_row(db, UNSCOPED_JOB).get("transaction_id") in (None, "")
    assert not any(patch.get("transaction_id") == LISTING_B for _table, patch, _ids in db.updates)


def test_attach_refuses_other_brokerage_on_scoped_job(monkeypatch):
    db = _world()
    _install(monkeypatch, db)
    token = _session_token()
    with pytest.raises(HTTPException) as ei:
        _run(
            vos.attach_job_listing(
                OWN_JOB,
                vos.JobListingUpdate(transaction_id=LISTING_B),
                f"Bearer {token}",
                None,
            )
        )
    assert ei.value.status_code in (403, 404)
    _assert_no_foreign(ei.value.detail)
    assert _job_row(db, OWN_JOB).get("transaction_id") in (None, "")


def test_unauthenticated_listing_search_is_rejected(monkeypatch):
    db = _world()
    _install(monkeypatch, db)
    with pytest.raises(HTTPException) as ei:
        _run(vos.search_office_listings(q="king", job_id=None, authorization=None, x_user_id=MEMBER_ID))
    assert ei.value.status_code == 401
    assert db.calls == []
