"""GET /api/vendor-os/team must not hand every vendor_members row to a non-admin.

An applicant who posts /api/onboarding/apply is stored on vendor_id
default_vendor (VendorMember's default). After the emailed-code login path
links that row, GET /api/vendor-os/team resolves the session, builds
vendor_scope_ids (always including default_vendor) and select * with no
is_vendor_admin check. The same credential (application_token) is what
GET /api/onboarding/status/{id}, POST /api/onboarding/documents,
POST /api/onboarding/portfolio and POST /api/onboarding/apply/{id}/submitted
accept.

These tests fail while that listing returns other members' rows. They pass
when a non-admin is refused or receives a projection that omits other
people's contact, location, tax, insurance, notes, profiles and tokens;
when no listing contains application_token; and when strings taken only
from that response cannot read or extend another applicant's documents.

The query stand-in follows postgrest-py 2.34.1 (the postgrest pin of
supabase 2.34.1, which satisfies backend/requirements.txt supabase>=2.0.0):
in_ is an IN filter, range(start, end) is offset=start and limit=end-start+1
(base_request_builder.py), count=exact is the filtered total from
Content-Range (the number after the slash; PostgREST pagination docs), and
maybe_single() with zero rows returns None
(_async/request_builder.py AsyncMaybeSingleRequestBuilder.execute).
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import BackgroundTasks, HTTPException

from app.routers import auth as auth_router
from app.routers import onboarding
from app.routers import vendor_os
from app.services import email_login_prep as elp
from app.services.vendor_otp_link import link_member_after_otp
from app.shared import auth as auth_mod
from app.shared import fair_use_auth as fua
from app.shared.schemas import VendorApplicationCreate

VICTIM_ID = "44444444-aaaa-4bbb-8ccc-000000000004"
CONTRACTOR_ID = "55555555-aaaa-4bbb-8ccc-000000000005"
UNRELATED_ID = "66666666-aaaa-4bbb-8ccc-000000000006"
OTHER_ORG_ID = "77777777-aaaa-4bbb-8ccc-000000000007"
ADMIN_ID = "88888888-aaaa-4bbb-8ccc-000000000008"

VICTIM_EMAIL = "casey.victim@example.com"
CONTRACTOR_EMAIL = "pat.contractor@example.com"
UNRELATED_EMAIL = "quinn.unrelated@example.com"
OTHER_ORG_EMAIL = "rory.other@example.com"
ADMIN_EMAIL = "owner.desk@example.com"
APPLICANT_EMAIL = "ada.applicant@example.com"

VICTIM_TOKEN = "placeholder-victim-application-token"
CONTRACTOR_TOKEN = "placeholder-contractor-application-token"
UNRELATED_TOKEN = "placeholder-unrelated-application-token"
VICTIM_DOC_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
VICTIM_DOC_URL = "onboarding/victim/coi-placeholder.pdf"

SENSITIVE_KEYS = {
    "email",
    "phone",
    "home_lat",
    "home_lng",
    "hst_number",
    "wsib_number",
    "insurance_policy_number",
    "internal_notes",
    "application_token",
    "application_profile",
    "rpas_certificate_number",
}


def _ilike(value, pattern: str) -> bool:
    """SQL ILIKE: % and _ are wildcards, match is case-insensitive."""
    out = []
    for ch in str(pattern):
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return re.fullmatch("".join(out), str(value or ""), flags=re.IGNORECASE) is not None


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    """In-memory PostgREST chain for the filters these handlers actually send."""

    def __init__(self, db, table: str):
        self.db = db
        self.table = table
        self.op = "select"
        self.payload = None
        self.filters = []
        self._limit = None
        self._offset = 0
        self._single = False
        self._order = None
        self._count = None

    def select(self, *_cols, **kwargs):
        self.op = "select"
        self._count = kwargs.get("count")
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def update(self, fields):
        self.op, self.payload = "update", fields
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, column, value):
        self.filters.append(lambda row: str(row.get(column)) == str(value))
        return self

    def in_(self, column, values):
        wanted = {str(v) for v in values}
        self.filters.append(lambda row: str(row.get(column)) in wanted)
        return self

    def ilike(self, column, pattern):
        self.filters.append(lambda row: _ilike(row.get(column), pattern))
        return self

    def or_(self, filters: str, reference_table=None):
        # Handler shape: "name.ilike.%q%,email.ilike.%q%,role.ilike.%q%"
        parts = []
        for piece in str(filters).split(","):
            col, op, pat = piece.split(".", 2)
            if op != "ilike":
                raise AssertionError(f"unhandled or_ filter {piece}")
            parts.append((col, pat))

        def _match(row):
            return any(_ilike(row.get(col), pat) for col, pat in parts)

        self.filters.append(_match)
        return self

    def is_(self, column, value):
        if str(value) == "null":
            self.filters.append(lambda row: row.get(column) is None)
        else:
            self.filters.append(lambda row: row.get(column) == value)
        return self

    def order(self, column, desc=False, **_kwargs):
        self._order = (column, desc)
        return self

    def limit(self, n, **_kwargs):
        self._limit = n
        return self

    def range(self, start: int, end: int, foreign_table=None):
        # postgrest 2.34.1 BaseSelectRequestBuilder.range
        self._offset = start
        self._limit = end - start + 1
        return self

    def maybe_single(self):
        self._single = True
        return self

    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def _matched(self):
        return [row for row in self._rows() if all(fn(row) for fn in self.filters)]

    async def execute(self):
        if self.op == "select":
            matched = self._matched()
            total = len(matched)
            if self._order:
                column, desc = self._order
                matched = sorted(matched, key=lambda row: str(row.get(column) or ""), reverse=desc)
            page = matched[self._offset:]
            if self._limit is not None:
                page = page[: self._limit]
            if self._single:
                # AsyncMaybeSingleRequestBuilder: zero rows -> None
                if len(page) == 0:
                    return None
                if len(page) > 1:
                    raise AssertionError("maybe_single matched more than one row")
                return _Result(copy.deepcopy(page[0]), count=total if self._count else None)
            return _Result(copy.deepcopy(page), count=total if self._count else None)
        if self.op == "insert":
            new = [dict(row) for row in (self.payload if isinstance(self.payload, list) else [self.payload])]
            for row in new:
                row.setdefault("id", str(uuid.uuid4()))
                self._rows().append(row)
            return _Result(copy.deepcopy(new))
        if self.op == "upsert":
            row = dict(self.payload)
            rows = self._rows()
            hit = next((item for item in rows if str(item.get("id")) == str(row.get("id"))), None)
            if hit:
                hit.update(row)
                return _Result([copy.deepcopy(hit)])
            row.setdefault("id", str(uuid.uuid4()))
            rows.append(row)
            return _Result([copy.deepcopy(row)])
        if self.op == "update":
            hit = self._matched()
            for row in hit:
                row.update(copy.deepcopy(self.payload))
            return _Result(copy.deepcopy(hit))
        if self.op == "delete":
            hit = self._matched()
            self.db.tables[self.table] = [row for row in self._rows() if row not in hit]
            return _Result(copy.deepcopy(hit))
        raise AssertionError(self.op)


class _Auth:
    def __init__(self):
        self.users = {}
        self.otp_calls = []
        self.admin = self

    async def create_user(self, attrs):
        uid = str(uuid.uuid4())
        user = SimpleNamespace(
            id=uid,
            email=attrs["email"],
            email_confirmed_at="2026-11-24T00:00:00Z",
            identities=[],
        )
        self.users[uid] = user
        return SimpleNamespace(user=user)

    async def sign_in_with_otp(self, attrs):
        self.otp_calls.append(dict(attrs))
        return SimpleNamespace(user=None)

    async def get_user(self, token: str):
        # Supabase Auth already verified the access token. This stand-in only
        # returns the user created above when the token's sub is that user.
        # session_amr_methods in resolve_vendor_actor_id still reads the JWT.
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
            )
        except Exception as exc:
            raise Exception("invalid JWT") from exc
        user = self.users.get(str(payload.get("sub") or ""))
        if user is None:
            raise Exception("invalid JWT")
        return SimpleNamespace(user=user)


class _Db:
    def __init__(self):
        self.tables: dict[str, list] = {}
        self.auth = _Auth()

    def table(self, name):
        return _Query(self, name)


def _member(**overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "vendor_id": "default_vendor",
        "name": "Row",
        "email": "row@example.com",
        "phone": "4165550100",
        "role": "Photographer",
        "title": "Photographer IC",
        "status": "available",
        "is_active": True,
        "onboarding_status": "approved",
        "auth_user_id": None,
        "application_token": None,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _seed(db: _Db) -> None:
    db.tables["vendor_members"] = [
        _member(
            id=VICTIM_ID,
            name="Casey Victim",
            email=VICTIM_EMAIL,
            phone="4165550191",
            role="Applicant",
            title="Photographer IC",
            is_active=False,
            onboarding_status="pending",
            home_lat=43.70001,
            home_lng=-79.40001,
            hst_number="HST-VICTIM-0001",
            wsib_number="WSIB-VICTIM-0001",
            insurance_policy_number="POLICY-VICTIM-0001",
            internal_notes="INTERNAL-NOTE-VICTIM",
            application_token=VICTIM_TOKEN,
            application_profile={"note": "profile-secret-victim"},
            rpas_certificate_number="RPAS-VICTIM-0001",
            created_at="2026-02-01T00:00:00+00:00",
        ),
        _member(
            id=CONTRACTOR_ID,
            name="Pat Contractor",
            email=CONTRACTOR_EMAIL,
            phone="4165550192",
            home_lat=43.80002,
            home_lng=-79.50002,
            hst_number="HST-CONTRACTOR-0001",
            wsib_number="WSIB-CONTRACTOR-0001",
            insurance_policy_number="POLICY-CONTRACTOR-0001",
            internal_notes="INTERNAL-NOTE-CONTRACTOR",
            application_token=CONTRACTOR_TOKEN,
            application_profile={"note": "profile-secret-contractor"},
            created_at="2026-03-01T00:00:00+00:00",
        ),
        _member(
            id=UNRELATED_ID,
            vendor_id="secret-org",
            name="Quinn Unrelated",
            email=UNRELATED_EMAIL,
            phone="4165550193",
            application_token=UNRELATED_TOKEN,
            internal_notes="INTERNAL-NOTE-UNRELATED",
            created_at="2026-04-01T00:00:00+00:00",
        ),
    ]
    db.tables["vendor_documents"] = [
        {
            "id": VICTIM_DOC_ID,
            "vendor_id": "default_vendor",
            "member_id": VICTIM_ID,
            "doc_type": "coi",
            "file_url": VICTIM_DOC_URL,
            "status": "pending",
        }
    ]
    db.tables["vendor_terminations"] = []
    db.tables["jobs"] = []


def _patch_db(monkeypatch, db: _Db) -> None:
    fua.clear_auth_cooldown()
    get_db = AsyncMock(return_value=db)
    monkeypatch.setattr(auth_mod, "get_db", get_db)
    monkeypatch.setattr(vendor_os, "get_db", get_db)
    monkeypatch.setattr(onboarding, "get_db", get_db)
    monkeypatch.setattr(auth_router, "get_db", get_db)
    monkeypatch.setattr(elp, "_pg_auth_user", lambda email: None)

    async def _upload(raw_bytes, job_id, upload_id, filename, mime_type):
        return (f"onboarding/{upload_id}/{filename}", None, "", None)

    monkeypatch.setattr(onboarding, "upload_file", _upload)


def _token_for(uid: str) -> str:
    return jwt.encode(
        {"sub": uid, "amr": [{"method": "otp", "timestamp": 1}], "role": "authenticated"},
        "test-signing-key-not-a-secret-32b!",
        algorithm="HS256",
    )


def _walk_strings(value) -> list[str]:
    found = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_walk_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_strings(item))
    return found


def _bearer(db: _Db, uid: str) -> str:
    return "Bearer " + _token_for(uid)


async def _apply_applicant(db: _Db) -> dict:
    application = VendorApplicationCreate(
        name="Ada Applicant",
        email=APPLICANT_EMAIL,
        phone="4165550148",
        vendor_type="cleaner",
        specialties=["cleaning"],
        equipment_list=["vacuum mop buckets cloths"],
        city="Toronto",
        accepted_terms=True,
        hst_exempt=True,
        home_lat=43.65001,
        home_lng=-79.38001,
    )
    result = await onboarding.apply_to_bricks(application, BackgroundTasks())
    row = next(r for r in db.tables["vendor_members"] if r["email"] == APPLICANT_EMAIL)
    assert row["vendor_id"] == "default_vendor"
    assert row["is_active"] is False
    assert row["role"] == "Applicant"
    assert row.get("application_token")
    login = await auth_router.vendor_os_login(SimpleNamespace(email=APPLICANT_EMAIL))
    assert login["message"] == "OTP sent to email"
    assert db.auth.otp_calls
    uid = next(iter(db.auth.users))
    linked = await link_member_after_otp(db, uid, APPLICANT_EMAIL, "Ada Applicant")
    assert linked["action"] == "linked"
    assert linked["member"]["id"] == result["member_id"]
    return {"member_id": result["member_id"], "auth_uid": uid, "application_token": result["application_token"]}


async def _register(db: _Db, member_id: str, email: str) -> str:
    created = await db.auth.create_user({"email": email, "email_confirm": True})
    uid = created.user.id
    linked = await link_member_after_otp(db, uid, email, "")
    assert linked["member"]["id"] == member_id
    return uid


async def _team(authorization: str, search=None, status=None):
    try:
        body = await vendor_os.get_vendor_team(
            search=search,
            status=status,
            page=1,
            page_size=100,
            authorization=authorization,
            x_user_id=None,
        )
    except HTTPException as exc:
        return exc
    return body


def _listing_text(body) -> str:
    if isinstance(body, HTTPException):
        return ""
    return json.dumps(body, default=str)


def _candidate_tokens(body) -> list[str]:
    if isinstance(body, HTTPException):
        return []
    seen = []
    for value in _walk_strings(body):
        if len(value) >= 16 and value not in seen:
            seen.append(value)
    return seen


async def _abuse_with_listing(db: _Db, body) -> list[str]:
    """Use only strings the team response actually contained."""
    problems = []
    text = _listing_text(body)
    if not isinstance(body, HTTPException):
        if UNRELATED_EMAIL in text or UNRELATED_TOKEN in text or "INTERNAL-NOTE-UNRELATED" in text:
            problems.append("listing included a member outside vendor_id scope")
        for needle in (
            VICTIM_EMAIL,
            VICTIM_TOKEN,
            "4165550191",
            "HST-VICTIM-0001",
            "WSIB-VICTIM-0001",
            "POLICY-VICTIM-0001",
            "INTERNAL-NOTE-VICTIM",
            "profile-secret-victim",
            "RPAS-VICTIM-0001",
            "43.70001",
            "-79.40001",
            CONTRACTOR_EMAIL,
            CONTRACTOR_TOKEN,
            "4165550192",
            "HST-CONTRACTOR-0001",
            "WSIB-CONTRACTOR-0001",
            "POLICY-CONTRACTOR-0001",
            "INTERNAL-NOTE-CONTRACTOR",
            "profile-secret-contractor",
            "43.80002",
            "-79.50002",
        ):
            if needle in text:
                problems.append(f"listing disclosed {needle}")
        items = body.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id")) != str(body.get("actor_id") or ""):
                leaked = SENSITIVE_KEYS.intersection(item.keys())
                if leaked:
                    problems.append(f"member {item.get('id')} still has {sorted(leaked)}")
            if item.get("application_token"):
                problems.append("listing returned application_token")
            if item.get("id") == VICTIM_ID:
                problems.append("inactive applicant included in team listing")

    before_docs = [dict(row) for row in db.tables.get("vendor_documents", [])]
    before_victim = copy.deepcopy(next(row for row in db.tables["vendor_members"] if row["id"] == VICTIM_ID))
    candidates = _candidate_tokens(body) or [None]
    for token in candidates:
        try:
            status = await onboarding.get_application_status(VICTIM_ID, token=token)
        except HTTPException:
            status = None
        if isinstance(status, dict):
            blob = json.dumps(status, default=str)
            if VICTIM_DOC_ID in blob or VICTIM_DOC_URL in blob or VICTIM_EMAIL in blob:
                problems.append("onboarding status returned another applicant's documents")
        try:
            uploaded = await onboarding.upload_compliance_document(
                member_id=VICTIM_ID,
                doc_type="resume",
                filename="note.pdf",
                file_data="eA==",
                expiry_date=None,
                token=token,
                application_token=None,
            )
        except HTTPException:
            uploaded = None
        if isinstance(uploaded, dict) and uploaded.get("success"):
            problems.append("onboarding documents accepted a token from the team listing")
        try:
            portfolio = await onboarding.upload_portfolio_sample(
                member_id=VICTIM_ID,
                media_type="hdr",
                filename="sample.jpg",
                file_data="eA==",
                link=None,
                token=token,
            )
        except HTTPException:
            portfolio = None
        if isinstance(portfolio, dict) and portfolio.get("success"):
            problems.append("onboarding portfolio accepted a token from the team listing")
        try:
            submitted = await onboarding.application_documents_submitted(
                VICTIM_ID, BackgroundTasks(), {"token": token}
            )
        except HTTPException:
            submitted = None
        if isinstance(submitted, dict) and submitted.get("ok") is True:
            problems.append("onboarding submitted accepted a token from the team listing")

    after_docs = db.tables.get("vendor_documents", [])
    if len(after_docs) != len(before_docs) or any(row not in before_docs for row in after_docs):
        problems.append("a team-listing token wrote a vendor_documents row")
    after_victim = next(row for row in db.tables["vendor_members"] if row["id"] == VICTIM_ID)
    if after_victim.get("portfolio_samples") != before_victim.get("portfolio_samples"):
        problems.append("a team-listing token changed the victim portfolio")
    if after_victim.get("application_token") != before_victim.get("application_token"):
        problems.append("victim application_token changed")
    return problems


def test_team_requires_a_session():
    db = _Db()
    _seed(db)

    async def _run():
        # patch inside so the helper's monkeypatch isn't required
        return await vendor_os.get_vendor_team(
            page=1, page_size=100, authorization=None, x_user_id=None
        )

    # get_db is still the real client unless patched. Auth fails before the query
    # when no Authorization and no internal key are present.
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_run())
    assert caught.value.status_code == 401


def test_user_id_header_alone_does_not_list_the_team(monkeypatch):
    db = _Db()
    _seed(db)
    _patch_db(monkeypatch, db)

    async def _run():
        return await vendor_os.get_vendor_team(
            page=1,
            page_size=100,
            authorization=None,
            x_user_id=VICTIM_ID,
        )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_run())
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "search,status",
    [(None, None), ("Casey", None), (None, "available")],
)
def test_applicant_cannot_read_other_members_or_their_tokens(monkeypatch, search, status):
    db = _Db()
    _seed(db)
    _patch_db(monkeypatch, db)

    async def _run():
        applicant = await _apply_applicant(db)
        body = await _team(_bearer(db, applicant["auth_uid"]), search=search, status=status)
        problems = await _abuse_with_listing(db, body)
        # The apply response already gave the caller their own token. A team
        # listing must not repeat it. Checked without interpolating the value.
        if not isinstance(body, HTTPException):
            if applicant["application_token"] in _listing_text(body):
                problems.append("listing returned the caller's application_token")
        assert problems == []

    asyncio.run(_run())


def test_other_org_non_admin_does_not_receive_default_vendor_rows(monkeypatch):
    db = _Db()
    _seed(db)
    db.tables["vendor_members"].append(
        _member(
            id=OTHER_ORG_ID,
            vendor_id="other-studio",
            name="Rory Other",
            email=OTHER_ORG_EMAIL,
            role="Photographer",
            title="Photographer IC",
            is_active=True,
            onboarding_status="approved",
            created_at="2026-05-01T00:00:00+00:00",
        )
    )
    _patch_db(monkeypatch, db)

    async def _run():
        uid = await _register(db, OTHER_ORG_ID, OTHER_ORG_EMAIL)
        body = await _team(_bearer(db, uid))
        problems = await _abuse_with_listing(db, body)
        assert problems == []

    asyncio.run(_run())


def test_active_non_admin_cannot_read_other_members(monkeypatch):
    db = _Db()
    _seed(db)
    actor_id = "99999999-aaaa-4bbb-8ccc-000000000009"
    actor_email = "sam.shooter@example.com"
    db.tables["vendor_members"].append(
        _member(
            id=actor_id,
            name="Sam Shooter",
            email=actor_email,
            role="Photographer",
            title="Photographer IC",
            is_active=True,
            onboarding_status="approved",
            created_at="2026-05-02T00:00:00+00:00",
        )
    )
    _patch_db(monkeypatch, db)

    async def _run():
        uid = await _register(db, actor_id, actor_email)
        body = await _team(_bearer(db, uid))
        problems = await _abuse_with_listing(db, body)
        assert problems == []

    asyncio.run(_run())


def test_admin_listing_omits_tokens_and_inactive_applicants(monkeypatch):
    db = _Db()
    _seed(db)
    db.tables["vendor_members"].append(
        _member(
            id=ADMIN_ID,
            name="Owner Desk",
            email=ADMIN_EMAIL,
            role="Owner",
            title="Owner",
            is_active=True,
            onboarding_status="approved",
            created_at="2026-05-03T00:00:00+00:00",
        )
    )
    _patch_db(monkeypatch, db)

    async def _run():
        uid = await _register(db, ADMIN_ID, ADMIN_EMAIL)
        body = await _team(_bearer(db, uid))
        assert not isinstance(body, HTTPException)
        text = _listing_text(body)
        assert VICTIM_TOKEN not in text
        assert CONTRACTOR_TOKEN not in text
        assert "application_token" not in text
        ids = [item.get("id") for item in body.get("items") or []]
        assert VICTIM_ID not in ids
        assert UNRELATED_ID not in ids
        # Admins still need the active roster. The token stays off that roster.
        assert CONTRACTOR_ID in ids

    asyncio.run(_run())
