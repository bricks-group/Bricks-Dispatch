"""GET /api/vendor-os/team must not hand a non-admin the organization directory.

These tests fail while the vulnerability is present. They are the spec for the
fix: a signed-in photographer or applicant, including one the email-code
sign-in just created, gets 403 insufficient_permissions, and an admin listing
returns only id, name, title, skills, assignable flags, and assigned_jobs.
The response must not carry application_token or other member columns, and a
token learned from this response must not open onboarding compliance documents.

Supabase Auth (verify_otp / get_user) is the only stand-in. Table calls go
through the same filter, select, order, and range chain the handlers build.
select=* returns every stored column; a named select returns only those
columns (PostgREST vertical filtering: the default select is *, all columns).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote

# server.py imports optional native stacks and sentry at import time.
for _name in (
    "cv2",
    "numpy",
    "rawpy",
    "pypdfium2",
    "sentry_sdk.integrations.fastapi",
    "sentry_sdk.integrations.asgi",
):
    sys.modules.setdefault(_name, MagicMock())

import pytest

import server
from app.routers import auth as auth_router
from app.routers import onboarding as onboarding_router
from app.routers import vendor_os as vendor_os_router
from app.shared import audit as audit_mod
from app.shared import auth as auth_mod
from app.shared import database as database_mod

# Obvious placeholders. Not credentials.
OTP_CODE = "test-otp"
VICTIM_ID = "55555555-5555-4555-8555-555555555555"
VICTIM_AUTH_ID = "77777777-7777-4777-8777-777777777777"
OTHER_ORG_ID = "66666666-6666-4666-8666-666666666666"
NEWCOMER_ID = "11111111-1111-4111-8111-111111111111"
APPLICANT_ID = "22222222-2222-4222-8222-222222222222"
APPLICANT_AUTH_ID = "22222222-2222-4222-8222-222222222223"
ACTIVE_ID = "33333333-3333-4333-8333-333333333333"
OWNER_ID = "44444444-4444-4444-8444-444444444444"

VICTIM_EMAIL = "victim@example.com"
VICTIM_PHONE = "416-555-0199"
VICTIM_TOKEN = "app-token-victim-alpha"
VICTIM_HST = "HST-VICTIM-100"
VICTIM_WSIB = "WSIB-VICTIM-200"
VICTIM_POLICY = "POLICY-VICTIM-300"
VICTIM_NOTE = "NOTE-VICTIM-reviewer-only"
VICTIM_PROFILE = "PROFILE-VICTIM-SECRET"
DOC_FILE = "doc-file-victim-coi"
OTHER_TOKEN = "app-token-other-org"

SECRETS = (
    VICTIM_EMAIL,
    VICTIM_PHONE,
    VICTIM_TOKEN,
    VICTIM_HST,
    VICTIM_WSIB,
    VICTIM_POLICY,
    VICTIM_NOTE,
    VICTIM_PROFILE,
    VICTIM_AUTH_ID,
    DOC_FILE,
    OTHER_TOKEN,
    "43.701",
    "-79.401",
)

# Remediation allow-list, plus the assigned_jobs count this handler adds.
ALLOWED_ITEM_KEYS = {
    "id",
    "name",
    "title",
    "skills",
    "assignable",
    "assignable_reason",
    "assigned_jobs",
}
ALLOWED_PAGE_KEYS = {"items", "total", "page", "page_size", "total_pages"}


def _victim_row():
    return {
        "id": VICTIM_ID,
        "vendor_id": "default_vendor",
        "auth_user_id": VICTIM_AUTH_ID,
        "name": "Victim Alpha",
        "email": VICTIM_EMAIL,
        "phone": VICTIM_PHONE,
        "role": "Applicant",
        "title": "Photographer IC",
        "vendor_type": "photographer",
        "specialty": "photo",
        "status": "available",
        "is_active": False,
        "onboarding_status": "pending",
        "home_lat": 43.701,
        "home_lng": -79.401,
        "hst_number": VICTIM_HST,
        "wsib_number": VICTIM_WSIB,
        "insurance_policy_number": VICTIM_POLICY,
        "internal_notes": VICTIM_NOTE,
        "application_token": VICTIM_TOKEN,
        "application_profile": {"credentials": {"wsib_number": VICTIM_PROFILE}},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _other_org_row():
    return {
        "id": OTHER_ORG_ID,
        "vendor_id": "other_org",
        "name": "Other Org",
        "email": "other@example.com",
        "role": "Photographer",
        "application_token": OTHER_TOKEN,
        "created_at": "2026-01-02T00:00:00+00:00",
    }


def _user(uid, email, name=""):
    return SimpleNamespace(
        id=uid,
        email=email,
        email_confirmed_at="2026-01-01T00:00:00+00:00",
        user_metadata={"name": name},
        identities=[],
    )


class _Auth:
    """Stand-in for the Supabase Auth calls verify_otp and get_user.

    The product calls these on a GoTrue client. Everything after a verified
    user id and email is this repository's code.
    """

    def __init__(self):
        self.by_email = {}
        self.by_token = {}

    def register(self, user, session_token):
        self.by_email[str(user.email).strip().lower()] = user
        self.by_token[session_token] = user

    async def verify_otp(self, payload):
        email = str((payload or {}).get("email") or "").strip().lower()
        code = (payload or {}).get("token")
        user = self.by_email.get(email)
        if not email or not code or user is None:
            raise RuntimeError("invalid otp")
        session_token = next(tok for tok, u in self.by_token.items() if u is user)
        session = SimpleNamespace(
            access_token=session_token,
            refresh_token="refresh-placeholder",
            expires_in=3600,
        )
        return SimpleNamespace(session=session, user=user)

    async def get_user(self, token):
        user = self.by_token.get(token)
        if user is None:
            raise RuntimeError("invalid session")
        return SimpleNamespace(user=user)


class _Query:
    """In-memory PostgREST query. select, filters, order, and range all apply.

    select=* copies every stored column. Any other select list returns only
    those columns, which is what PostgREST does for vertical filtering.
    count=exact is the match count before range. maybe_single() with no row
    returns None, matching postgrest-py AsyncMaybeSingleRequestBuilder.
    """

    def __init__(self, tables, name):
        self.tables = tables
        self.name = name
        self.filters = []
        self.op = "select"
        self.payload = None
        self.select_cols = "*"
        self.single = False
        self.count_mode = None
        self.order_key = None
        self.order_desc = False
        self.offset = 0
        self.limit_n = None

    def select(self, *cols, count=None):
        self.op = "select"
        self.select_cols = ",".join(cols) if cols else "*"
        self.count_mode = count
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, column, value):
        self.filters.append(lambda row, c=column, v=value: row.get(c) == v or str(row.get(c) if row.get(c) is not None else "") == str(v))
        return self

    def in_(self, column, values):
        wanted = {str(v) for v in values}
        self.filters.append(lambda row, c=column, wanted=wanted: str(row.get(c) if row.get(c) is not None else "") in wanted)
        return self

    def ilike(self, column, pattern):
        regex = _ilike_regex(pattern)
        self.filters.append(lambda row, c=column, regex=regex: regex.match(str(row.get(c) or "")) is not None)
        return self

    def is_(self, column, value):
        if value is None or value == "null":
            self.filters.append(lambda row, c=column: row.get(c) is None)
        else:
            self.filters.append(lambda row, c=column, v=value: row.get(c) is v)
        return self

    def order(self, column, desc=False, **_kwargs):
        self.order_key = column
        self.order_desc = bool(desc)
        return self

    def limit(self, size, **_kwargs):
        self.limit_n = size
        return self

    def range(self, start, end, foreign_table=None):
        self.offset = start
        self.limit_n = end - start + 1
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _matches(self):
        rows = self.tables.setdefault(self.name, [])
        return [row for row in rows if all(pred(row) for pred in self.filters)]

    def _project(self, row):
        cols = (self.select_cols or "*").strip()
        if cols == "*":
            return dict(row)
        names = [part.strip() for part in cols.split(",") if part.strip()]
        return {name: row.get(name) for name in names}

    async def execute(self):
        if self.op == "insert":
            row = dict(self.payload)
            self.tables.setdefault(self.name, []).append(row)
            return SimpleNamespace(data=[dict(row)], count=1)
        if self.op == "update":
            matched = self._matches()
            for row in matched:
                row.update(self.payload)
            return SimpleNamespace(data=[dict(row) for row in matched], count=len(matched))
        if self.op == "delete":
            matched = self._matches()
            keep = [row for row in self.tables.get(self.name, []) if row not in matched]
            self.tables[self.name] = keep
            return SimpleNamespace(data=[dict(row) for row in matched], count=len(matched))
        matched = self._matches()
        if self.order_key:
            matched = sorted(
                matched,
                key=lambda row: (row.get(self.order_key) is None, str(row.get(self.order_key) or "")),
                reverse=self.order_desc,
            )
        total = len(matched)
        page = matched[self.offset:]
        if self.limit_n is not None:
            page = page[: self.limit_n]
        projected = [self._project(row) for row in page]
        if self.single:
            if len(matched) == 0:
                return None
            if len(matched) > 1:
                raise RuntimeError("maybe_single matched more than one row")
            return SimpleNamespace(data=projected[0], count=1)
        count = total if self.count_mode else None
        return SimpleNamespace(data=projected, count=count)


def _ilike_regex(pattern: str) -> re.Pattern:
    """PostgreSQL ILIKE: % and _ are wildcards, everything else is literal."""
    out = []
    for char in str(pattern):
        if char == "%":
            out.append(".*")
        elif char == "_":
            out.append(".")
        else:
            out.append(re.escape(char))
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


class _DB:
    def __init__(self):
        self.tables = {
            "vendor_members": [_victim_row(), _other_org_row()],
            "vendor_documents": [
                {
                    "id": "doc-victim-1",
                    "member_id": VICTIM_ID,
                    "vendor_id": "default_vendor",
                    "doc_type": "coi",
                    "file_url": DOC_FILE,
                    "status": "pending",
                }
            ],
            "jobs": [],
            "audit_events": [],
        }
        self.auth = _Auth()

    def table(self, name):
        self.tables.setdefault(name, [])
        return _Query(self.tables, name)


def _http(method, path, *, query=b"", body=None, headers=None):
    messages = []
    payload = body if body is not None else b""
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if body is not None:
        hdrs.append((b"content-type", b"application/json"))
        hdrs.append((b"content-length", str(len(payload)).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": hdrs,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "root_path": "",
    }

    sent = False
    hold = asyncio.Event()

    async def receive():
        # One request body, then keep the connection open. Returning a second
        # http.request trips BaseHTTPMiddleware, and an immediate disconnect
        # cancels the response body before it is sent.
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await hold.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(server.app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    chunks = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
    return start["status"], b"".join(chunks)


def _bind(monkeypatch, db):
    async def get_db():
        return db

    async def new_auth_client():
        return db

    for module in (database_mod, auth_mod, audit_mod, auth_router, vendor_os_router, onboarding_router):
        if hasattr(module, "get_db"):
            monkeypatch.setattr(module, "get_db", get_db)
    monkeypatch.setattr(database_mod, "new_auth_client", new_auth_client)
    # verify() imports new_auth_client inside the function from this module.
    monkeypatch.setattr(database_mod, "new_auth_client", new_auth_client)


@pytest.fixture()
def world(monkeypatch):
    db = _DB()
    _bind(monkeypatch, db)
    return db


def _verify(email):
    status, body = _http(
        "POST",
        "/api/vendor-os/auth/verify",
        body=json.dumps({"email": email, "token": OTP_CODE}).encode(),
    )
    parsed = json.loads(body.decode() or "{}")
    return status, parsed


def _team(session_token):
    return _http(
        "GET",
        "/api/vendor-os/team",
        query=b"page=1&page_size=100",
        headers={"Authorization": f"Bearer {session_token}"},
    )


def _detail_code(body: bytes) -> str:
    try:
        parsed = json.loads(body.decode() or "{}")
    except json.JSONDecodeError:
        return ""
    detail = parsed.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return ""


def _assert_directory_closed(status, body, db):
    """Non-admin listing is refused, and nothing in that response opens documents."""
    text = body.decode(errors="replace")
    problems = []
    if status != 403 or _detail_code(body) != "insufficient_permissions":
        problems.append(f"team status {status} code {_detail_code(body)!r} body {text[:500]}")
    for secret in SECRETS:
        if secret in text:
            problems.append(f"directory body contains {secret}")
    # Use only material the directory response itself returned.
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            token = item.get("application_token")
            member_id = item.get("id")
            if not token or not member_id:
                continue
            doc_status, doc_body = _http(
                "GET",
                f"/api/onboarding/status/{quote(str(member_id))}",
                query=f"token={quote(str(token))}".encode(),
            )
            doc_text = doc_body.decode(errors="replace")
            if doc_status == 200 and (DOC_FILE in doc_text or VICTIM_TOKEN in doc_text):
                problems.append(
                    f"directory token opened compliance documents for {member_id}: {doc_text[:300]}"
                )
    assert not problems, "; ".join(problems)


def test_email_code_sign_in_creates_photographer_who_cannot_list_the_directory(world):
    """Attack step 1-4: verify an unknown email, then GET /team without an internal key."""
    session = "test-session-newcomer"
    world.auth.register(_user(NEWCOMER_ID, "newcomer@example.com", "Newcomer"), session)
    status, parsed = _verify("newcomer@example.com")
    assert status == 200, parsed
    assert parsed.get("access_token") == session
    created = [row for row in world.tables["vendor_members"] if row.get("id") == NEWCOMER_ID]
    assert created, "email-code sign-in did not create a vendor_members row"
    assert created[0]["role"] == "Photographer"
    assert created[0]["vendor_id"] == "default_vendor"
    # No X-Internal-Api-Key. /api/vendor-os is exempt from InternalAuthMiddleware.
    team_status, team_body = _team(parsed["access_token"])
    _assert_directory_closed(team_status, team_body, world)


def test_apply_then_sign_in_applicant_cannot_list_the_directory(world):
    """The public application row is linked by verify, then the same directory is requested."""
    world.tables["vendor_members"].append({
        "id": APPLICANT_ID,
        "vendor_id": "default_vendor",
        "auth_user_id": None,
        "email": "applicant@example.com",
        "name": "Applicant",
        "role": "Applicant",
        "is_active": False,
        "onboarding_status": "pending",
        "created_at": "2026-02-01T00:00:00+00:00",
    })
    session = "test-session-applicant"
    world.auth.register(_user(APPLICANT_AUTH_ID, "applicant@example.com", "Applicant"), session)
    status, parsed = _verify("applicant@example.com")
    assert status == 200, parsed
    linked = next(row for row in world.tables["vendor_members"] if row.get("id") == APPLICANT_ID)
    assert linked.get("auth_user_id") == APPLICANT_AUTH_ID
    team_status, team_body = _team(parsed["access_token"])
    _assert_directory_closed(team_status, team_body, world)


def test_active_approved_photographer_cannot_list_the_directory(world):
    """is_active and onboarding_status=approved still do not make a Photographer an admin."""
    world.tables["vendor_members"].append({
        "id": ACTIVE_ID,
        "vendor_id": "default_vendor",
        "auth_user_id": ACTIVE_ID,
        "email": "active@example.com",
        "name": "Active Photographer",
        "role": "Photographer",
        "title": "Photographer IC",
        "is_active": True,
        "onboarding_status": "approved",
        "onboarding_completed_at": "2026-03-01T00:00:00+00:00",
        "created_at": "2026-03-01T00:00:00+00:00",
    })
    session = "test-session-active"
    world.auth.register(_user(ACTIVE_ID, "active@example.com", "Active Photographer"), session)
    team_status, team_body = _team(session)
    _assert_directory_closed(team_status, team_body, world)


def test_admin_directory_is_an_allow_list(world):
    """Owner can list the org, and the payload still has no tax, token, or home coordinates."""
    world.tables["vendor_members"].append({
        "id": OWNER_ID,
        "vendor_id": "default_vendor",
        "auth_user_id": OWNER_ID,
        "email": "owner@example.com",
        "name": "Org Owner",
        "role": "Owner",
        "title": "Owner",
        "is_active": True,
        "onboarding_status": "approved",
        "created_at": "2026-01-03T00:00:00+00:00",
    })
    session = "test-session-owner"
    world.auth.register(_user(OWNER_ID, "owner@example.com", "Org Owner"), session)
    status, body = _team(session)
    text = body.decode(errors="replace")
    assert status == 200, text[:500]
    payload = json.loads(text)
    assert set(payload) <= ALLOWED_PAGE_KEYS
    items = payload["items"]
    assert any(item.get("name") == "Victim Alpha" for item in items)
    for item in items:
        assert set(item) <= ALLOWED_ITEM_KEYS, sorted(set(item) - ALLOWED_ITEM_KEYS)
    for secret in SECRETS:
        assert secret not in text
