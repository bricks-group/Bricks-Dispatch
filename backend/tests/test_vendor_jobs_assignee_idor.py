"""GET /api/vendor-os/jobs must not return another member's jobs.

These tests are expected to fail while a signed-in non-admin can pass
`assignee_id` and receive that member's rows. They pass once a non-admin
list is always limited to photographer_assignee_ids(actor), including the
PostgREST path and the Fair-Use Postgres fallback.

Run from backend/:

    python -m pytest tests/test_vendor_jobs_assignee_idor.py -q
"""
from __future__ import annotations

import sys

# tests/conftest.py stubs httpx before this module loads. The route is
# exercised through FastAPI's TestClient, which needs the real httpx.
_httpx = sys.modules.get("httpx")
if _httpx is not None and not getattr(_httpx, "__file__", None):
    for _name in list(sys.modules):
        if _name == "httpx" or _name.startswith("httpx."):
            del sys.modules[_name]

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.vendor_os import router
from app.shared.auth import is_vendor_admin
from app.shared.fair_use_auth import clear_auth_cooldown
from app.shared.job_scope import KAM_MEMBER_ID, photographer_assignee_ids

# PyJWT requires a key for HS256. The handler reads amr without checking
# the signature (session_amr_methods). This is not a credential.
_JWT_PLACEHOLDER = "placeholder-placeholder-placeholder"

ATTACKER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"
MANAGER_ID = "33333333-3333-4333-8333-333333333333"

OWN_JOB = "own-job-1"
OWNER_JOB_A = "owner-job-a"
OWNER_JOB_B = "owner-job-b"
OTHER_JOB = "other-job-1"

OWNER_MARKER = "OWNERBOOK-LOCKBOX-908172"
OWNER_MARKER_PAGE2 = "OWNERBOOK-LOCKBOX-PAGE2-771003"
OTHER_MARKER = "OTHERBOOK-GATE-334455"
OWN_MARKER = "OWNBOOK-NOTE-ATTACKER"


def _serialize_filter_value(value):
    """Same wire form as postgrest 2.34.1 base_request_builder._serialize_filter_value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _ilike(value: str, pattern: str) -> bool:
    import re

    escaped = re.escape(pattern).replace("%", ".*").replace("_", ".")
    return re.search("^" + escaped + "$", value, re.IGNORECASE) is not None


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _match_atom(row: dict, expr: str) -> bool:
    """One PostgREST filter atom: column.op.value, AND-combined by the caller."""
    column, op, criteria = expr.split(".", 2)
    raw = row.get(column)
    if op == "eq":
        return _serialize_filter_value(raw) == criteria
    if op == "neq":
        return _serialize_filter_value(raw) != criteria
    if op == "is":
        if criteria == "null":
            return raw is None
        if criteria == "true":
            return raw is True
        if criteria == "false":
            return raw is False
        return _serialize_filter_value(raw) == criteria
    if op == "ilike":
        return _ilike(str(raw or ""), criteria)
    if op == "in":
        inner = criteria[1:-1] if criteria.startswith("(") and criteria.endswith(")") else criteria
        return _serialize_filter_value(raw) in _split_top_level(inner)
    raise AssertionError(f"unhandled filter atom {expr}")


def _row_matches(row: dict, params: list[tuple[str, str]]) -> bool:
    """PostgREST ANDs query params. An `or` param ORs the atoms inside it."""
    for key, value in params:
        if key == "or" or key.endswith(".or"):
            inner = value[1:-1] if value.startswith("(") and value.endswith(")") else value
            atoms = [part for part in _split_top_level(inner) if part != ""]
            if not any(_match_atom(row, atom) for atom in atoms):
                return False
            continue
        if not _match_atom(row, f"{key}.{value}"):
            return False
    return True


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db, table: str):
        self.db = db
        self.table = table
        self.rows = [dict(row) for row in db.tables.get(table, [])]
        self.params: list[tuple[str, str]] = []
        self.count_exact = False
        self.single = False
        self._order: list[tuple[str, bool]] = []
        self._offset = 0
        self._limit = None

    def select(self, *columns, count=None, **kwargs):
        self.count_exact = count == "exact"
        return self

    def eq(self, column, value):
        self.params.append((column, f"eq.{_serialize_filter_value(value)}"))
        return self

    def neq(self, column, value):
        self.params.append((column, f"neq.{_serialize_filter_value(value)}"))
        return self

    def in_(self, column, values):
        rendered = ",".join(_serialize_filter_value(value) for value in values)
        self.params.append((column, f"in.({rendered})"))
        return self

    def ilike(self, column, pattern):
        self.params.append((column, f"ilike.{pattern}"))
        return self

    def or_(self, filters, reference_table=None):
        key = f"{reference_table}.or" if reference_table else "or"
        self.params.append((key, f"({filters})"))
        return self

    def is_(self, column, value):
        if value is None:
            shown = "null"
        elif value is True:
            shown = "true"
        elif value is False:
            shown = "false"
        else:
            shown = str(value)
        self.params.append((column, f"is.{shown}"))
        return self

    def order(self, column, desc=False, **kwargs):
        self._order.append((column, bool(desc)))
        return self

    def limit(self, size, **kwargs):
        self._limit = size
        return self

    def range(self, start, end, foreign_table=None):
        # postgrest BaseSelectRequestBuilder.range: offset=start, limit=end-start+1
        self._offset = start
        self._limit = end - start + 1
        return self

    def maybe_single(self):
        self.single = True
        return self

    async def execute(self):
        if self.table == "jobs" and self.db.jobs_quota and self.count_exact:
            raise RuntimeError("402 exceed_storage")
        matched = [dict(row) for row in self.rows if _row_matches(row, self.params)]
        for column, desc in reversed(self._order):
            matched.sort(
                key=lambda row, col=column: (row.get(col) is None, str(row.get(col) or "")),
                reverse=desc,
            )
        total = len(matched)
        if self._limit is not None:
            matched = matched[self._offset:self._offset + self._limit]
        count = total if self.count_exact else None
        if self.single:
            if not matched:
                return None
            if len(matched) > 1:
                raise RuntimeError("Cannot coerce the result to a single JSON object")
            return _Result(matched[0], count)
        return _Result(matched, count)


class _Write:
    def __init__(self, db, table: str, kind: str, payload):
        self.db = db
        self.table = table
        self.kind = kind
        self.payload = payload
        self.params: list[tuple[str, str]] = []

    def eq(self, column, value):
        self.params.append((column, f"eq.{_serialize_filter_value(value)}"))
        return self

    def is_(self, column, value):
        shown = "null" if value is None else str(value)
        self.params.append((column, f"is.{shown}"))
        return self

    def in_(self, column, values):
        rendered = ",".join(_serialize_filter_value(value) for value in values)
        self.params.append((column, f"in.({rendered})"))
        return self

    async def execute(self):
        self.db.mutations.append((self.kind, self.table, self.payload, list(self.params)))
        rows = self.db.tables.setdefault(self.table, [])
        if self.kind == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            copied = [dict(row) for row in payload]
            rows.extend(copied)
            return _Result(copied, len(copied))
        changed = []
        for row in list(rows):
            if not _row_matches(row, self.params):
                continue
            if self.kind == "update":
                row.update(self.payload)
                changed.append(dict(row))
            elif self.kind == "delete":
                rows.remove(row)
                changed.append(dict(row))
        return _Result(changed, len(changed))


class _Table:
    def __init__(self, db, name: str):
        self.db = db
        self.name = name

    def select(self, *columns, **kwargs):
        query = _Query(self.db, self.name)
        return query.select(*columns, **kwargs)

    def insert(self, payload):
        return _Write(self.db, self.name, "insert", payload)

    def update(self, payload):
        return _Write(self.db, self.name, "update", payload)

    def delete(self):
        return _Write(self.db, self.name, "delete", None)


class _User:
    def __init__(self, user_id: str, email: str):
        self.id = user_id
        self.email = email
        self.email_confirmed_at = "2026-11-01T00:00:00+00:00"
        self.identities = []
        self.user_metadata = {}


class _UserResp:
    def __init__(self, user: _User):
        self.user = user


class _Auth:
    def __init__(self):
        self._users: dict[str, _User] = {}

    def accept(self, user: _User) -> str:
        token = jwt.encode(
            {
                "sub": user.id,
                "email": user.email,
                "amr": [{"method": "otp"}],
                "role": "authenticated",
            },
            _JWT_PLACEHOLDER,
            algorithm="HS256",
        )
        self._users[token] = user
        return token

    async def get_user(self, token: str):
        user = self._users.get(token)
        if user is None:
            raise RuntimeError("invalid session")
        return _UserResp(user)


class JobsDB:
    """In-memory stand-in for the service-role query the handler builds.

    Equality, `in`, `or`, `ilike`, `is`, order and range follow the filter
    names postgrest-py 2.34.1 puts on the request. Rows that do not match
    those filters are not returned.
    """

    def __init__(self, members: list[dict], jobs: list[dict], *, jobs_quota: bool = False):
        self.tables = {
            "vendor_members": [dict(row) for row in members],
            "jobs": [dict(row) for row in jobs],
            "transactions": [],
            "vendor_payout_rates": [],
        }
        self.auth = _Auth()
        self.mutations: list[tuple] = []
        self.jobs_quota = jobs_quota

    def table(self, name: str):
        return _Table(self, name)


class _PgCursor:
    """Applies the SQL pg_select_jobs_for_assignees sends: assignee_id = ANY(%s)."""

    def __init__(self, jobs: list[dict]):
        self.jobs = jobs
        self.description = []
        self._one = None
        self._all: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        if "assignee_id = ANY(%s)" not in compact:
            raise AssertionError(compact)
        ids = [str(value) for value in params[0]]
        index = 1
        status = None
        if "status = %s" in compact:
            status = params[index]
            index += 1
        matched = []
        for job in self.jobs:
            if str(job.get("assignee_id") or "") not in ids:
                continue
            if status is not None and job.get("status") != status:
                continue
            matched.append(dict(job))
        matched.sort(
            key=lambda job: (
                job.get("scheduled_date") is None,
                str(job.get("scheduled_date") or ""),
                job.get("time_window_start") is None,
                str(job.get("time_window_start") or ""),
            )
        )
        if compact.startswith("SELECT count(*)"):
            self._one = (len(matched),)
            self._all = [self._one]
            self.description = [("count",)]
            return
        limit = int(params[index])
        offset = int(params[index + 1])
        page = matched[offset:offset + limit]
        columns: list[str] = []
        seen: set[str] = set()
        for row in page:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
        if not columns:
            columns = ["id"]
        self.description = [(column,) for column in columns]
        self._all = [tuple(row.get(column) for column in columns) for row in page]
        self._one = self._all[0] if self._all else None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)


class _PgConn:
    def __init__(self, jobs: list[dict]):
        self.jobs = jobs

    def cursor(self):
        return _PgCursor(self.jobs)

    def close(self):
        return None


def _job(job_id: str, assignee_id: str, scheduled_date: str, marker: str, address: str) -> dict:
    return {
        "id": job_id,
        "assignee_id": assignee_id,
        "vendor_id": "default_vendor",
        "status": "assigned",
        "scheduled_date": scheduled_date,
        "time_window_start": "09:00",
        "time_window_end": "11:00",
        "address": address,
        "city": "Toronto",
        "state": "ON",
        "zip_code": "M5V 1J2",
        "job_type": "photo",
        "property_details": f"details {marker}",
        "client_instructions": marker,
        "agent_name": f"Agent {marker}",
        "agent_email": f"{marker.lower()}@example.com",
        "agent_brokerage": f"Brokerage {marker}",
        "rack_price": 499,
        "package_price": 449,
        "subtotal_cents": 44900,
        "tax_cents": 5837,
        "total_cents": 50737,
        "platform_margin": 188.25,
        "vendor_payout": 164.45,
    }


def _books() -> list[dict]:
    return [
        _job(OWNER_JOB_A, KAM_MEMBER_ID, "2026-11-01", OWNER_MARKER, "14 Rosehill Avenue"),
        _job(OWNER_JOB_B, KAM_MEMBER_ID, "2026-11-02", OWNER_MARKER_PAGE2, "88 Harbour Square"),
        _job(OTHER_JOB, OTHER_ID, "2026-11-04", OTHER_MARKER, "500 King Street West"),
        _job(OWN_JOB, ATTACKER_ID, "2026-11-20", OWN_MARKER, "9 Attacker Crescent"),
    ]


def _photographer() -> dict:
    # Shape written by link_member_after_otp when no member row exists.
    return {
        "id": ATTACKER_ID,
        "email": "new.photographer@example.com",
        "name": "New Photographer",
        "role": "Photographer",
        "vendor_id": "default_vendor",
    }


def _applicant() -> dict:
    return {
        "id": ATTACKER_ID,
        "email": "applicant@example.com",
        "name": "New Applicant",
        "role": "Applicant",
        "title": "",
        "vendor_id": "default_vendor",
        "is_active": False,
        "onboarding_status": "pending",
    }


def _manager() -> dict:
    return {
        "id": MANAGER_ID,
        "email": "manager@example.com",
        "name": "Dispatch Manager",
        "role": "Manager",
        "title": "Dispatch Manager",
        "vendor_id": "default_vendor",
        "is_active": True,
    }


def _client(monkeypatch, db: JobsDB) -> TestClient:
    async def get_db():
        return db

    monkeypatch.setattr("app.routers.vendor_os.get_db", get_db)
    monkeypatch.setattr("app.shared.auth.get_db", get_db)
    monkeypatch.setattr("app.shared.database.get_db", get_db)
    clear_auth_cooldown()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _headers(db: JobsDB, member: dict) -> dict:
    token = db.auth.accept(_User(member["id"], member["email"]))
    return {"Authorization": f"Bearer {token}"}


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if rows is None:
        rows = payload.get("jobs") or []
    return rows


def _assert_no_job_writes(db: JobsDB) -> None:
    writes = [entry for entry in db.mutations if entry[1] == "jobs"]
    assert writes == []


def _assert_caller_book(response, member: dict, forbidden: list[str]) -> None:
    assert response.status_code == 200, response.text
    body = response.text
    for marker in forbidden:
        assert marker not in body, body
    payload = response.json()
    items = _items(payload)
    allowed = {str(value) for value in photographer_assignee_ids(member["id"], member)}
    assert allowed
    assert OWN_JOB in {item.get("id") for item in items}, payload
    for item in items:
        assert str(item.get("assignee_id") or "") in allowed, item
        assert item.get("id") not in {OWNER_JOB_A, OWNER_JOB_B, OTHER_JOB}
    assert payload.get("total") == 1
    _jobs_in_both = payload.get("jobs") or []
    assert {item.get("id") for item in _jobs_in_both} == {item.get("id") for item in items}


@pytest.fixture(autouse=True)
def _cooldown_clear():
    clear_auth_cooldown()
    yield
    clear_auth_cooldown()


@pytest.mark.parametrize("member_factory", [_photographer, _applicant])
@pytest.mark.parametrize(
    "target_id,marker",
    [
        (KAM_MEMBER_ID, OWNER_MARKER),
        (OTHER_ID, OTHER_MARKER),
    ],
)
def test_non_admin_assignee_filter_returns_only_caller_jobs(monkeypatch, member_factory, target_id, marker):
    member = member_factory()
    assert is_vendor_admin(member) is False
    db = JobsDB([member], _books())
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"assignee_id": target_id, "page": 1, "page_size": 100, "status": "assigned"},
        headers=_headers(db, member),
    )
    assert marker not in response.text, response.text
    _assert_caller_book(response, member, [OWNER_MARKER, OWNER_MARKER_PAGE2, OTHER_MARKER])
    _assert_no_job_writes(db)


def test_second_page_of_foreign_assignee_does_not_return_more_foreign_jobs(monkeypatch):
    member = _photographer()
    db = JobsDB([member], _books())
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"assignee_id": KAM_MEMBER_ID, "page": 2, "page_size": 1},
        headers=_headers(db, member),
    )
    assert response.status_code == 200, response.text
    assert OWNER_MARKER_PAGE2 not in response.text, response.text
    assert OWNER_JOB_B not in {item.get("id") for item in _items(response.json())}
    _assert_no_job_writes(db)


@pytest.mark.parametrize("member_factory", [_photographer, _applicant])
def test_fair_use_fallback_assignee_filter_returns_only_caller_jobs(monkeypatch, member_factory):
    member = member_factory()
    assert is_vendor_admin(member) is False
    db = JobsDB([member], _books(), jobs_quota=True)
    monkeypatch.setattr(
        "app.shared.pg_fallback._pg_connect",
        lambda: _PgConn(db.tables["jobs"]),
    )
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"assignee_id": KAM_MEMBER_ID, "page": 1, "page_size": 100},
        headers=_headers(db, member),
    )
    assert OWNER_MARKER not in response.text, response.text
    _assert_caller_book(response, member, [OWNER_MARKER, OWNER_MARKER_PAGE2, OTHER_MARKER])
    _assert_no_job_writes(db)


def test_non_admin_without_assignee_filter_sees_only_own_job(monkeypatch):
    member = _photographer()
    db = JobsDB([member], _books())
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"page": 1, "page_size": 100},
        headers=_headers(db, member),
    )
    _assert_caller_book(response, member, [OWNER_MARKER, OWNER_MARKER_PAGE2, OTHER_MARKER])
    assert OWN_MARKER in response.text


@pytest.mark.parametrize("assignee_id", ["all", "unassigned"])
def test_non_admin_all_and_unassigned_do_not_open_other_books(monkeypatch, assignee_id):
    member = _photographer()
    db = JobsDB([member], _books())
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"assignee_id": assignee_id, "page": 1, "page_size": 100},
        headers=_headers(db, member),
    )
    _assert_caller_book(response, member, [OWNER_MARKER, OWNER_MARKER_PAGE2, OTHER_MARKER])


def test_vendor_admin_assignee_filter_returns_that_members_jobs(monkeypatch):
    manager = _manager()
    assert is_vendor_admin(manager) is True
    assert KAM_MEMBER_ID not in photographer_assignee_ids(manager["id"], manager)
    db = JobsDB([manager, _photographer()], _books())
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"assignee_id": KAM_MEMBER_ID, "page": 1, "page_size": 100},
        headers=_headers(db, manager),
    )
    assert response.status_code == 200, response.text
    ids = {item.get("id") for item in _items(response.json())}
    assert OWNER_JOB_A in ids
    assert OWNER_JOB_B in ids
    assert OWNER_MARKER in response.text
    assert OWN_JOB not in ids
    assert OTHER_JOB not in ids
    _assert_no_job_writes(db)


def test_missing_session_returns_no_jobs(monkeypatch):
    db = JobsDB([_photographer()], _books())
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"assignee_id": KAM_MEMBER_ID, "page_size": 100},
        headers={"X-User-Id": KAM_MEMBER_ID},
    )
    assert response.status_code == 401, response.text
    assert OWNER_MARKER not in response.text
    _assert_no_job_writes(db)


def test_rejected_session_returns_no_jobs(monkeypatch):
    member = _photographer()
    db = JobsDB([member], _books())
    client = _client(monkeypatch, db)
    response = client.get(
        "/api/vendor-os/jobs",
        params={"assignee_id": KAM_MEMBER_ID},
        headers={"Authorization": "Bearer not-a-session"},
    )
    assert response.status_code == 401, response.text
    assert OWNER_MARKER not in response.text
    _assert_no_job_writes(db)
