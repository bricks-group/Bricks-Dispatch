"""Self-declared RPAS level must not make a contractor eligible for drone dispatch.

These tests exercise the signed-in profile and RPAS-upload routes, then
``assign_vendor_for_job``. They fail while a contractor can store
``rpas_certificate_level=advanced`` / ``drone_certified=true`` and the
dispatch gate treats those columns as proof of an Advanced certificate.

Supabase Auth is not running here. ``auth.get_user`` only accepts a JWT
minted in this file and returns the ``sub`` from it; ``lookup_vendor_member``
and every later check are the application code. Storage ``upload`` records
bytes locally so ``upload_file`` runs. The Transport Canada fetch is not
called: a failed download uses the real validator's empty-image return, and
a suspicious result is the real ``_determine_outcome`` for an illegitimate
extraction.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI

from app.routers import admin_ops, onboarding
from app.routers import vendor_profile as profile
from app.services import commercial_jobs, rpas_validator
from app.services.vendor_selection import assign_vendor_for_job
from app.shared.fair_use_auth import clear_auth_cooldown
from app.shared.schemas import VendorMember

MEMBER_ID = "11111111-2222-3333-4444-555555555555"
ADMIN_ID = "22222222-3333-4444-5555-666666666666"
EMAIL = "pilot@example.com"
ADMIN_EMAIL = "desk@example.com"
# Obvious placeholder. Not a live credential.
INTERNAL_KEY = "test-internal-key"
DRONE_JOB = "job-drone-listing"
ADDON_JOB = "job-photo-drone-addon"


def _token(sub: str, email: str) -> str:
    return jwt.encode(
        {"sub": sub, "email": email, "amr": [{"method": "otp"}], "role": "authenticated"},
        "test-signing-key",
        algorithm="HS256",
    )


def _approved_contractor() -> dict:
    return {
        "id": MEMBER_ID,
        "auth_user_id": MEMBER_ID,
        "vendor_id": "default_vendor",
        "name": "Uncertified Pilot",
        "email": EMAIL,
        "phone": "",
        "role": "Photographer",
        "title": "Photographer IC",
        "vendor_type": "photographer",
        "specialty": "photo",
        "skills": ["photo"],
        "status": "available",
        "is_active": True,
        "onboarding_status": "approved",
        "onboarding_completed_at": "2026-10-01T00:00:00+00:00",
        "tier": 1,
        "drone_certified": False,
        "rpas_advanced": False,
        "rpas_certificate_level": None,
        "rpas_certificate_number": None,
        "rpas_certificate_url": None,
        "service_areas": ["Toronto"],
        "home_city": "Toronto",
        "max_travel_km": 50,
        "quality_rating": 4.0,
        "on_time_rate": 1.0,
        "cancel_rate": 0.0,
        "decline_rate": 0.0,
        "active_assignments": 0,
        "total_assignments": 0,
    }


def _admin() -> dict:
    return {
        "id": ADMIN_ID,
        "auth_user_id": ADMIN_ID,
        "vendor_id": "default_vendor",
        "name": "Desk Admin",
        "email": ADMIN_EMAIL,
        "phone": "",
        "role": "admin",
        "title": "Admin",
        "vendor_type": "photographer",
        "specialty": "photo",
        "skills": ["photo"],
        "status": "offline",
        "is_active": True,
        "onboarding_status": "approved",
        "onboarding_completed_at": None,
        "tier": 1,
        "drone_certified": False,
        "rpas_advanced": False,
        "rpas_certificate_level": None,
        "service_areas": ["Toronto"],
        "home_city": "Toronto",
    }


def _job(job_id: str, job_type: str, add_ons=None) -> dict:
    return {
        "id": job_id,
        "vendor_id": "default_vendor",
        "address": "100 King Street",
        "city": "Toronto",
        "state": "ON",
        "zip_code": "M5H 1A1",
        "time_window_start": "10:00",
        "time_window_end": "12:00",
        "job_type": job_type,
        "status": "pending",
        "scheduled_date": "2026-12-01",
        "assignee_id": "",
        "add_ons": list(add_ons or []),
        "package_id": "photos",
        "package_name": "Photos",
        "lat": None,
        "lng": None,
    }


class _Bucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def upload(self, path, file, file_options=None):
        self.objects[path] = file
        return {"path": path}

    async def create_signed_url(self, path, expiry):
        # Not fetched. The validator's download step fails closed on this host.
        return {"signedURL": "http://127.0.0.1:9/rpas-cert"}


class _Storage:
    def __init__(self):
        self._buckets: dict[str, _Bucket] = {}

    def from_(self, name):
        return self._buckets.setdefault(name, _Bucket())


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else (len(data) if isinstance(data, list) else (1 if data else 0))


class _Query:
    def __init__(self, db, name):
        self._db = db
        self._name = name
        self._filters: list[tuple] = []
        self._op = "select"
        self._payload = None
        self._limit = None
        self._range = None
        self._order = None
        self._single = False
        self._count = None

    def select(self, *_cols, count=None):
        self._count = count
        return self

    def insert(self, row):
        self._op = "insert"
        self._payload = row
        return self

    def update(self, patch):
        self._op = "update"
        self._payload = patch
        return self

    def upsert(self, row, on_conflict=None, **_k):
        self._op = "upsert"
        self._payload = row
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def ilike(self, col, val):
        self._filters.append(("ilike", col, val))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def lt(self, col, val):
        self._filters.append(("lt", col, val))
        return self

    def gt(self, col, val):
        self._filters.append(("gt", col, val))
        return self

    def order(self, col, desc=False, **_k):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def maybe_single(self):
        self._single = True
        return self

    def _match_value(self, row, op, col, val) -> bool:
        have = row.get(col)
        if op == "eq":
            return have == val
        if op == "neq":
            return have != val
        if op == "in":
            return have in val
        if op == "ilike":
            left = str(have or "").lower()
            right = str(val or "").lower()
            if "%" in right or "_" in right:
                import re
                pattern = "^" + re.escape(right).replace("%", ".*").replace("_", ".") + "$"
                return re.match(pattern, left) is not None
            return left == right
        if op == "is":
            if val in (None, "null"):
                return have is None
            return have is val
        if op == "lt":
            return have is not None and have < val
        if op == "gt":
            return have is not None and have > val
        return False

    def _rows(self):
        rows = [r for r in self._db.tables.setdefault(self._name, [])]
        for op, col, val in self._filters:
            rows = [r for r in rows if self._match_value(r, op, col, val)]
        if self._order:
            col, desc = self._order
            rows = sorted(rows, key=lambda r: str(r.get(col) or ""), reverse=bool(desc))
        if self._range:
            start, end = self._range
            rows = rows[start:end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    async def execute(self):
        table = self._db.tables.setdefault(self._name, [])
        if self._op == "insert":
            row = dict(self._payload)
            table.append(row)
            return _Result([dict(row)])
        if self._op == "upsert":
            row = dict(self._payload)
            key = "id" if row.get("id") else "member_id"
            replaced = False
            for i, existing in enumerate(table):
                if key in row and existing.get(key) == row.get(key):
                    merged = {**existing, **row}
                    table[i] = merged
                    row = merged
                    replaced = True
                    break
            if not replaced:
                table.append(row)
            return _Result([dict(row)])
        if self._op == "update":
            matched = self._rows()
            for row in matched:
                row.update(self._payload)
            return _Result([dict(r) for r in matched])
        if self._op == "delete":
            matched = self._rows()
            for row in matched:
                table.remove(row)
            return _Result([dict(r) for r in matched])
        rows = self._rows()
        if self._single:
            if not rows:
                return None
            return _Result(dict(rows[0]))
        return _Result([dict(r) for r in rows])


class MemoryDb:
    def __init__(self):
        self.tables: dict[str, list] = {}
        self.storage = _Storage()
        self.auth = self

    def table(self, name):
        self.tables.setdefault(name, [])
        return _Query(self, name)

    async def get_user(self, token):
        try:
            claims = jwt.decode(
                token,
                options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
            )
        except Exception:
            return SimpleNamespace(user=None)
        sub = str(claims.get("sub") or "")
        email = claims.get("email")
        user = SimpleNamespace(
            id=sub,
            email=email,
            email_confirmed_at="2026-10-01T00:00:00+00:00",
            identities=[],
        )
        return SimpleNamespace(user=user)


def _install_db(db: MemoryDb) -> None:
    import app.shared.database as database

    previous = database.get_db

    async def get_db():
        return db

    database.get_db = get_db
    for module in list(sys.modules.values()):
        if module is None:
            continue
        if getattr(module, "get_db", None) is previous:
            module.get_db = get_db


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(profile.router)
    app.include_router(profile.os_router)
    app.include_router(onboarding.router)
    app.include_router(admin_ops.router)
    return app


async def _asgi(app, method, path, *, token=None, body=None, content_type=None, query="", extra_headers=None):
    messages = []

    async def receive():
        return {"type": "http.request", "body": body or b"", "more_body": False}

    async def send(message):
        messages.append(message)

    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if content_type:
        headers.append((b"content-type", content_type.encode()))
    for key, value in extra_headers or []:
        headers.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw.decode())
        except json.JSONDecodeError:
            parsed = raw.decode()
    return start["status"], parsed


def _multipart(filename: str, content: bytes, content_type: str = "application/octet-stream") -> tuple[bytes, str]:
    boundary = "----rpasformboundary"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body = head + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class World:
    def __init__(self, monkeypatch):
        clear_auth_cooldown()
        commercial_jobs._COMPANY_POLICY["at"] = 0.0
        commercial_jobs._COMPANY_POLICY["row"] = None
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEYS", INTERNAL_KEY)
        self.db = MemoryDb()
        self.db.tables["vendor_members"] = [_approved_contractor(), _admin()]
        self.db.tables["jobs"] = [
            _job(DRONE_JOB, "drone"),
            _job(ADDON_JOB, "photo", add_ons=["drone"]),
        ]
        self.app = _app()
        _install_db(self.db)
        self.pilot = _token(MEMBER_ID, EMAIL)
        self.admin = _token(ADMIN_ID, ADMIN_EMAIL)

    def member(self) -> dict:
        for row in self.db.tables["vendor_members"]:
            if row["id"] == MEMBER_ID:
                return row
        raise AssertionError("contractor row missing")

    def reset_contractor(self):
        row = self.member()
        row.clear()
        row.update(_approved_contractor())
        self.db.tables["vendor_documents"] = [
            d for d in self.db.tables.get("vendor_documents", []) if d.get("member_id") != MEMBER_ID
        ]
        for job in self.db.tables["jobs"]:
            job["assignee_id"] = ""
            job["status"] = "pending"

    def request(self, method, path, *, token=None, json_body=None, file=None, query="", extra_headers=None):
        token = self.pilot if token is None else token
        if file is not None:
            body, content_type = _multipart("note.txt", file)
        elif json_body is not None:
            body, content_type = json.dumps(json_body).encode(), "application/json"
        else:
            body, content_type = None, None
        return asyncio.run(_asgi(
            self.app, method, path,
            token=token, body=body, content_type=content_type,
            query=query, extra_headers=extra_headers,
        ))

    def assign(self, job_id: str):
        # The row must load into the same model assign_vendor_for_job uses.
        # A validation error would drop the contractor and hide the bug.
        VendorMember(**self.member())
        return asyncio.run(assign_vendor_for_job(job_id))


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def test_profile_update_without_a_session_is_rejected(world):
    status, body = world.request("PUT", "/api/profile", token="", json_body={"rpas_certificate_level": "advanced"})
    assert status == 401
    assert world.member()["drone_certified"] is False
    assert world.assign(DRONE_JOB) is None


@pytest.mark.parametrize("method,path", [
    ("PUT", "/api/profile"),
    ("PATCH", "/api/profile"),
    ("PUT", "/api/vendor-os/profile"),
    ("PATCH", "/api/vendor-os/profile"),
])
def test_self_declared_advanced_level_does_not_win_drone_dispatch(world, method, path):
    world.reset_contractor()
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None

    status, body = world.request(method, path, json_body={
        "rpas_certificate_level": "advanced",
        "rpas_certificate_number": "SELF-DECLARED",
        "skills": ["photo", "drone"],
    })
    assert status == 200, body
    # Dispatch outcome first: the signed-in write is what made this contractor selectable.
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None
    row = world.member()
    assert row.get("drone_certified") is not True
    assert str(row.get("rpas_certificate_level") or "").lower() != "advanced"
    assert body.get("drone_certified") is not True
    assert str(body.get("rpas_certificate_level") or "").lower() != "advanced"


def test_adding_the_drone_skill_does_not_certify_the_contractor(world):
    world.reset_contractor()
    status, body = world.request("PUT", "/api/profile", json_body={"skills": ["photo", "drone"]})
    assert status == 200, body
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None
    assert world.member().get("drone_certified") is not True
    assert str(world.member().get("rpas_certificate_level") or "").lower() != "advanced"
    assert body.get("drone_certified") is not True


@pytest.mark.parametrize("path", ["/api/profile/rpas", "/api/vendor-os/profile/rpas"])
def test_rpas_upload_does_not_grant_advanced_dispatch(world, path):
    """The form field ``level`` is omitted, so the route default applies."""
    world.reset_contractor()
    status, body = world.request("POST", path, file=b"this is not a drone certificate")
    assert status == 200, body
    docs = [
        d for d in world.db.tables.get("vendor_documents", [])
        if d.get("member_id") == MEMBER_ID and d.get("doc_type") == "rpas_certificate"
    ]
    assert docs, "the upload should still keep the file for review"
    assert all(d.get("status") == "pending" for d in docs)
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None
    row = world.member()
    assert row.get("drone_certified") is not True
    assert str(row.get("rpas_certificate_level") or "").lower() != "advanced"
    assert body.get("drone_certified") is not True
    assert str(body.get("rpas_certificate_level") or "").lower() != "advanced"


def test_explicit_advanced_on_upload_does_not_grant_dispatch(world):
    world.reset_contractor()
    boundary = "----rpaslevelboundary"
    content = b"still not a certificate"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="scan.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + content + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="level"\r\n\r\n'
        f"advanced\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="certificate_number"\r\n\r\n'
        f"SELF-2\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    status, parsed = asyncio.run(_asgi(
        world.app, "POST", "/api/profile/rpas",
        token=world.pilot,
        body=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    ))
    assert status == 200, parsed
    assert world.assign(DRONE_JOB) is None
    assert world.member().get("drone_certified") is not True
    assert str(world.member().get("rpas_certificate_level") or "").lower() != "advanced"


def test_stored_self_declared_flags_are_not_enough_for_dispatch(world):
    """Eligibility has to ignore the columns the profile route writes.

    A fix that only drops the field from the request body still fails this
    test: the row already has the values that route persists today, and no
    registry or admin verification.
    """
    world.reset_contractor()
    row = world.member()
    row["rpas_certificate_level"] = "advanced"
    row["drone_certified"] = True
    row["rpas_advanced"] = True
    row["rpas_certificate_number"] = "SELF-DECLARED"
    row["skills"] = ["photo", "drone"]
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None


def test_failed_certificate_check_does_not_keep_drone_eligibility(world, monkeypatch):
    world.reset_contractor()
    world.member().update({
        "rpas_certificate_level": "advanced",
        "drone_certified": True,
        "rpas_advanced": True,
        "skills": ["photo", "drone"],
    })
    world.db.tables.setdefault("vendor_documents", []).append({
        "id": str(uuid.uuid4()),
        "member_id": MEMBER_ID,
        "vendor_id": "default_vendor",
        "doc_type": "rpas_certificate",
        "file_url": "onboarding/member/note.txt",
        "status": "pending",
        "created_at": "2026-11-25T00:00:00+00:00",
    })

    async def no_image(_url):
        return None

    # Real validator: no image bytes → outcome stays "failed" (rpas_validator.py).
    monkeypatch.setattr(rpas_validator, "_fetch_image_bytes", no_image)
    status, body = world.request(
        "POST",
        f"/api/onboarding/validate-rpas/{MEMBER_ID}",
        token="",
        extra_headers=[("X-Internal-Api-Key", INTERNAL_KEY)],
    )
    assert status == 200, body
    assert body["outcome"] == "failed"
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None


def _suspicious_validation():
    async def validate(image_url: str, vendor_name: str, mime_type: str = "image/jpeg"):
        # Same extracted shape _ocr_extract / _gemini_extract produce, marked
        # illegitimate. Outcome comes from the real _determine_outcome.
        extracted = {
            "holder_name": vendor_name,
            "certificate_number": "00000000",
            "level": "advanced",
            "issue_date": None,
            "expiry_date": None,
            "issuing_authority": "Transport Canada",
            "is_expired": False,
            "is_legitimate": False,
            "concerns": ["certificate is not genuine"],
        }
        tc_result = {
            "found": False,
            "cert_on_registry": False,
            "name_on_registry": False,
            "level": None,
            "name_match": None,
            "tc_unavailable": False,
        }
        outcome, issues = rpas_validator._determine_outcome(extracted, tc_result, vendor_name)
        return {
            "validated_at": "2026-11-25T00:00:00+00:00",
            "outcome": outcome,
            "holder_name": extracted["holder_name"],
            "certificate_number": extracted["certificate_number"],
            "level": extracted["level"],
            "expiry_date": None,
            "tc_registry": tc_result,
            "issues": issues,
            "source": "ocr",
            "ocr_confidence": 0.0,
        }

    return validate


def test_suspicious_certificate_check_does_not_keep_drone_eligibility(world, monkeypatch):
    world.reset_contractor()
    world.member().update({
        "rpas_certificate_level": "advanced",
        "drone_certified": True,
        "rpas_advanced": True,
        "skills": ["photo", "drone"],
    })
    world.db.tables.setdefault("vendor_documents", []).append({
        "id": str(uuid.uuid4()),
        "member_id": MEMBER_ID,
        "vendor_id": "default_vendor",
        "doc_type": "rpas_certificate",
        "file_url": "onboarding/member/note.txt",
        "status": "pending",
        "created_at": "2026-11-25T00:00:00+00:00",
    })
    monkeypatch.setattr(rpas_validator, "validate_rpas_certificate", _suspicious_validation())
    status, body = world.request(
        "POST",
        f"/api/onboarding/validate-rpas/{MEMBER_ID}",
        token="",
        extra_headers=[("X-Internal-Api-Key", INTERNAL_KEY)],
    )
    assert status == 200, body
    assert body["outcome"] == "suspicious"
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None


def test_suspicious_batch_check_does_not_keep_drone_eligibility(world, monkeypatch):
    world.reset_contractor()
    world.member().update({
        "rpas_certificate_level": "advanced",
        "drone_certified": True,
        "rpas_advanced": True,
        "skills": ["photo", "drone"],
    })
    world.db.tables.setdefault("vendor_documents", []).append({
        "id": str(uuid.uuid4()),
        "member_id": MEMBER_ID,
        "vendor_id": "default_vendor",
        "doc_type": "rpas_certificate",
        "file_url": "onboarding/member/note.txt",
        "status": "pending",
        "created_at": "2026-11-25T00:00:00+00:00",
    })
    monkeypatch.setattr(rpas_validator, "validate_rpas_certificate", _suspicious_validation())
    status, body = world.request(
        "POST",
        "/api/admin/validate-rpas-batch",
        token=world.admin,
        query="dry_run=false",
    )
    assert status == 200, body
    assert body.get("flagged") == 1
    assert world.assign(DRONE_JOB) is None
    assert world.assign(ADDON_JOB) is None


def test_verified_registry_check_can_still_assign_a_drone_job(world, monkeypatch):
    """A registry outcome of verified is the path that may grant drone work."""
    world.reset_contractor()

    async def verified(image_url, vendor_name, mime_type="image/jpeg"):
        return {
            "validated_at": "2026-11-25T00:00:00+00:00",
            "outcome": "verified",
            "holder_name": vendor_name,
            "certificate_number": "TC-100",
            "level": "advanced",
            "expiry_date": None,
            "tc_registry": {"found": True, "tc_unavailable": False},
            "issues": [],
            "source": "ocr",
            "ocr_confidence": 1.0,
        }

    world.db.tables.setdefault("vendor_documents", []).append({
        "id": str(uuid.uuid4()),
        "member_id": MEMBER_ID,
        "vendor_id": "default_vendor",
        "doc_type": "rpas_certificate",
        "file_url": "onboarding/member/cert.jpg",
        "status": "pending",
        "created_at": "2026-11-25T00:00:00+00:00",
    })
    monkeypatch.setattr(rpas_validator, "validate_rpas_certificate", verified)
    status, body = world.request(
        "POST",
        f"/api/onboarding/validate-rpas/{MEMBER_ID}",
        token="",
        extra_headers=[("X-Internal-Api-Key", INTERNAL_KEY)],
    )
    assert status == 200, body
    assert body["outcome"] == "verified"
    assert world.assign(DRONE_JOB) == MEMBER_ID
