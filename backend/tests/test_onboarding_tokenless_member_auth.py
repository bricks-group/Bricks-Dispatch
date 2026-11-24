"""Tokenless vendor members must not use the public onboarding document routes.

GET /api/onboarding/status/{member_id}, POST /api/onboarding/documents,
POST /api/onboarding/portfolio, and POST /api/onboarding/apply/{member_id}/submitted
call _check_application_token. That helper returns without rejecting when
vendor_members.application_token is missing. Only /apply sets a token. The
owner seed, OTP sign-in insert, and admin create_vendor_member do not.

These tests fail while that skip is in place. They pass when a request is
rejected before any document read, storage upload, row insert, portfolio
update, or review task — including when a later read of the same member
would see a token that matches the request. A matching applicant token still
works, and it only acts on that member.

The owner id under test is KAM_MEMBER_ID from app.shared.job_scope. Tokens
in this file are placeholders, not live credentials.
"""
from __future__ import annotations

import asyncio
import base64
import copy
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.routers import onboarding as ob
from app.routers import vendor_os
from app.services import storage as storage_mod
from app.services import vendor_otp_link
from app.services.vendor_profile import ensure_owner_profile
from app.shared.job_scope import KAM_MEMBER_ID
from app.shared.schemas import VendorMemberCreate

PLACEHOLDER_TOKEN = "placeholder-application-token"
OTHER_TOKEN = "placeholder-other-member-token"
FILE_BYTES = b"fixture-onboarding-bytes"
ORIGINAL_SAMPLE = "fixture-original-sample"
STORED_KEY = "fixture-storage-key"
STORED_CERT = "fixture-certificate-number"
SWAPPED_KEY = "fixture-swapped-storage-key"
SWAPPED_CERT = "fixture-swapped-certificate"
OTP_USER_ID = "11111111-1111-4111-8111-111111111111"
OTP_EMAIL = "otp-member@example.com"


def _run(coro):
    return asyncio.run(coro)


class _SeedChain:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.op = "select"
        self.payload = None
        self.single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def or_(self, *_a, **_k):
        return self

    def ilike(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = row
        return self

    def update(self, patch):
        self.op = "update"
        self.payload = patch
        return self

    async def execute(self):
        if self.op == "insert":
            self.db.inserts.append((self.name, self.payload))
            return SimpleNamespace(data=[self.payload])
        if self.op == "update":
            self.db.updates.append((self.name, self.payload))
            return SimpleNamespace(data=[])
        if self.single:
            return SimpleNamespace(data=None)
        return SimpleNamespace(data=[])


class SeedDb:
    """Empty vendor_members, so the real create paths insert a new row."""

    def __init__(self):
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _SeedChain(self, name)


class _Bucket:
    def __init__(self, db, bucket):
        self.db = db
        self.bucket = bucket

    async def upload(self, path, file, file_options=None):
        # storage.upload_file awaits this with the object key and the raw bytes.
        self.db.uploads.append({
            "bucket": self.bucket,
            "path": path,
            "body": bytes(file),
            "options": file_options,
        })

    async def create_signed_url(self, _path, _expiry):
        # No signed URL: the post-upload compliance task returns before Gemini.
        return {}


class _StorageApi:
    def __init__(self, db):
        self.db = db

    def from_(self, bucket):
        return _Bucket(self.db, bucket)


class _Chain:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.op = "select"
        self.payload = None
        self.filters = {}
        self.single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, key, val):
        self.filters[key] = val
        return self

    def or_(self, *_a, **_k):
        return self

    def ilike(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = row
        return self

    def update(self, patch):
        self.op = "update"
        self.payload = patch
        return self

    async def execute(self):
        return self.db._execute(self)


class AttackDb:
    """In-memory vendor_members / vendor_documents plus the storage bucket
    upload_file writes through.

    after_first_member_read runs only after the first member row has been
    copied for the caller. It changes the stored row so a second lookup
    would see a token that matches the attacker's request. The handler must
    not act on that later row.
    """

    def __init__(self):
        self.members = {}
        self.documents = []
        self.uploads = []
        self.inserts = []
        self.updates = []
        self.ops = []
        self.member_snapshots = []
        self.member_reads = 0
        self.after_first_member_read = None
        self.storage = _StorageApi(self)

    def table(self, name):
        return _Chain(self, name)

    def add_member(self, row):
        self.members[str(row["id"])] = copy.deepcopy(row)

    def add_document(self, member_id):
        self.documents.append({
            "id": f"doc-{member_id}",
            "member_id": member_id,
            "doc_type": "coi",
            "file_url": STORED_KEY,
            "status": "verified",
            "validation_result": {
                "outcome": "verified",
                "certificate_number": STORED_CERT,
                "policy_number": "fixture-policy-number",
            },
        })

    def arm_second_read(self, member_id, token=PLACEHOLDER_TOKEN):
        self.armed_token = token

        def _swap():
            row = self.members[str(member_id)]
            row["application_token"] = token
            self.documents.append({
                "id": f"swapped-{member_id}",
                "member_id": member_id,
                "doc_type": "rpas_certificate",
                "file_url": SWAPPED_KEY,
                "status": "verified",
                "validation_result": {"certificate_number": SWAPPED_CERT},
            })

        self.after_first_member_read = _swap

    def _members(self, filters):
        rows = list(self.members.values())
        if "id" in filters:
            rows = [r for r in rows if str(r.get("id")) == str(filters["id"])]
        if "application_token" in filters:
            rows = [r for r in rows if r.get("application_token") == filters["application_token"]]
        if "auth_user_id" in filters:
            rows = [r for r in rows if str(r.get("auth_user_id") or "") == str(filters["auth_user_id"])]
        return rows

    def _execute(self, chain):
        self.ops.append((chain.op, chain.name, dict(chain.filters), chain.payload))
        if chain.op == "insert":
            self.inserts.append((chain.name, chain.payload))
            if chain.name == "vendor_documents" and isinstance(chain.payload, dict):
                self.documents.append(copy.deepcopy(chain.payload))
            return SimpleNamespace(data=[chain.payload])
        if chain.op == "update":
            self.updates.append((chain.name, chain.payload, dict(chain.filters)))
            if chain.name == "vendor_members" and "id" in chain.filters:
                row = self.members.get(str(chain.filters["id"]))
                if row and isinstance(chain.payload, dict):
                    row.update(chain.payload)
            return SimpleNamespace(data=[chain.payload] if chain.payload else [])
        if chain.name == "vendor_members":
            rows = self._members(chain.filters)
            if chain.single:
                if not rows:
                    return SimpleNamespace(data=None)
                snapshot = copy.deepcopy(rows[0])
                self.member_snapshots.append(snapshot)
                self.member_reads += 1
                if self.member_reads == 1 and self.after_first_member_read:
                    self.after_first_member_read()
                return SimpleNamespace(data=snapshot)
            return SimpleNamespace(data=copy.deepcopy(rows))
        if chain.name == "vendor_documents":
            docs = self.documents
            if "member_id" in chain.filters:
                docs = [d for d in docs if str(d.get("member_id")) == str(chain.filters["member_id"])]
            if "doc_type" in chain.filters:
                docs = [d for d in docs if d.get("doc_type") == chain.filters["doc_type"]]
            return SimpleNamespace(data=copy.deepcopy(docs))
        return SimpleNamespace(data=[] if not chain.single else None)


def _app():
    app = FastAPI()
    app.include_router(ob.router)
    return app


async def _invoke(app, method, path, *, body=b"", content_type=None):
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    headers = []
    if content_type:
        headers.append((b"content-type", content_type.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("203.0.113.5", 4444),
        "server": ("test", 80),
    }
    if "?" in path:
        path_only, query = path.split("?", 1)
        scope["path"] = path_only
        scope["raw_path"] = path_only.encode()
        scope["query_string"] = query.encode()
    await app(scope, receive, send)
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        done, still = await asyncio.wait(pending, timeout=2)
        for task in done:
            if not task.cancelled():
                task.exception()
        for task in still:
            task.cancel()
    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], raw


def _multipart(fields):
    boundary = "----onboarding-test-boundary"
    chunks = []
    for name, value in fields:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _bind(monkeypatch, db):
    async def _get_db():
        return db

    monkeypatch.setattr(ob, "get_db", _get_db)
    monkeypatch.setattr(storage_mod, "get_db", _get_db)
    monkeypatch.setattr(storage_mod, "blob_fallback_enabled", lambda: False)


def _tokenless_member(member_id, *, token=None, samples=None):
    return {
        "id": member_id,
        "name": "Fixture Member",
        "email": "fixture-member@example.com",
        "onboarding_status": "approved",
        "is_active": True,
        "application_token": token,
        "vendor_id": "default_vendor",
        "portfolio_samples": list(samples or [{"media_type": "hdr", "url": ORIGINAL_SAMPLE}]),
        "portfolio_links": [],
    }


def _attack_db(member, *, arm=True, extra_members=()):
    db = AttackDb()
    db.add_member(member)
    db.add_document(member["id"])
    for row in extra_members:
        db.add_member(row)
    if arm:
        db.arm_second_read(member["id"])
    return db


def _assert_rejected(status, body, db, member_id):
    assert db.member_snapshots, "handler never loaded a vendor_members row"
    first = db.member_snapshots[0]
    assert str(first.get("id")) == str(member_id)
    assert not first.get("application_token")
    # The row a second lookup would see now matches PLACEHOLDER_TOKEN.
    assert db.members[str(member_id)].get("application_token") == getattr(db, "armed_token", PLACEHOLDER_TOKEN)
    leaked = (STORED_KEY, STORED_CERT, SWAPPED_KEY, SWAPPED_CERT, ORIGINAL_SAMPLE, "fixture-policy-number")
    text = body.decode("utf-8", errors="replace")
    assert status == 404, (status, text)
    for marker in leaked:
        assert marker not in text
    assert not any(op[0] == "select" and op[1] == "vendor_documents" for op in db.ops)
    assert db.uploads == []
    assert db.inserts == []
    assert db.updates == []
    assert db.members[str(member_id)]["portfolio_samples"][0]["url"] == ORIGINAL_SAMPLE


def _status_path(member_id, token):
    path = f"/api/onboarding/status/{member_id}"
    if token is not None:
        path += "?" + urlencode({"token": token})
    return path


def _doc_fields(member_id, doc_type, token):
    fields = [
        ("member_id", member_id),
        ("doc_type", doc_type),
        ("filename", "coi.pdf" if doc_type == "coi" else "cert.png"),
        ("file_data", base64.b64encode(FILE_BYTES).decode()),
    ]
    if doc_type == "coi":
        # coi is an expiring kind. A future expiry is a normal form field;
        # without it the handler returns 400 before upload.
        fields.append(("expiry_date", "2099-01-01"))
    if token is not None:
        fields.append(("token", token))
    return fields


def _portfolio_fields(member_id, token):
    fields = [
        ("member_id", member_id),
        ("media_type", "hdr"),
        ("filename", "shot.jpg"),
        ("file_data", base64.b64encode(FILE_BYTES).decode()),
    ]
    if token is not None:
        fields.append(("token", token))
    return fields


@pytest.fixture
def review_calls(monkeypatch):
    calls = []

    async def _review(*args, **kwargs):
        calls.append((args, kwargs))
        return {"verdict": "skip"}

    monkeypatch.setattr(
        "app.services.applicant_review_runner.review_application",
        _review,
    )
    return calls


def test_public_onboarding_document_routes_declare_no_auth_dependency():
    wanted = {
        ("/api/onboarding/status/{member_id}", "GET"),
        ("/api/onboarding/documents", "POST"),
        ("/api/onboarding/portfolio", "POST"),
        ("/api/onboarding/apply/{member_id}/submitted", "POST"),
    }
    found = set()
    assert list(ob.router.dependencies) == []
    for route in ob.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            found.add((route.path, method))
        if any((route.path, method) in wanted for method in route.methods):
            assert list(route.dependencies) == []
    assert wanted <= found


def test_owner_seed_inserts_published_id_without_application_token():
    db = SeedDb()
    out = _run(ensure_owner_profile(db))
    assert out["action"] == "inserted"
    assert out["id"] == KAM_MEMBER_ID
    inserted = [row for name, row in db.inserts if name == "vendor_members"]
    assert len(inserted) == 1
    assert inserted[0]["id"] == KAM_MEMBER_ID
    assert not inserted[0].get("application_token")


def test_otp_sign_in_inserts_member_id_equal_to_auth_user_without_token():
    db = SeedDb()
    out = _run(vendor_otp_link.link_member_after_otp(db, OTP_USER_ID, OTP_EMAIL, "OTP Fixture"))
    assert out["action"] == "created"
    assert out["member"]["id"] == OTP_USER_ID
    assert "application_token" not in out["member"]
    inserted = [row for name, row in db.inserts if name == "vendor_members"]
    assert inserted[-1]["id"] == OTP_USER_ID
    assert not inserted[-1].get("application_token")


def test_admin_create_vendor_member_omits_application_token(monkeypatch):
    db = SeedDb()

    async def _get_db():
        return db

    async def _actor(*_a, **_k):
        return "admin-actor"

    async def _member(actor_id, email=None):
        return {"id": actor_id, "role": "Owner", "vendor_id": "default_vendor", "is_active": True}

    monkeypatch.setattr(vendor_os, "get_db", _get_db)
    monkeypatch.setattr(vendor_os, "resolve_vendor_actor_id", _actor)
    monkeypatch.setattr(vendor_os, "get_vendor_member", _member)
    created = _run(vendor_os.create_vendor_member(
        VendorMemberCreate(name="Added By Admin", email="admin-added@example.com"),
        authorization=None,
        x_user_id=None,
    ))
    assert created["id"]
    assert not created.get("application_token")
    inserted = [row for name, row in db.inserts if name == "vendor_members"]
    assert inserted[-1]["id"] == created["id"]
    assert not inserted[-1].get("application_token")


@pytest.mark.parametrize("token", [None, PLACEHOLDER_TOKEN])
def test_owner_status_rejected_for_tokenless_member(monkeypatch, token):
    member = _tokenless_member(KAM_MEMBER_ID)
    db = _attack_db(member)
    _bind(monkeypatch, db)
    status, body = _run(_invoke(_app(), "GET", _status_path(KAM_MEMBER_ID, token)))
    _assert_rejected(status, body, db, KAM_MEMBER_ID)


@pytest.mark.parametrize("doc_type", ["coi", "rpas_certificate"])
@pytest.mark.parametrize("token", [None, PLACEHOLDER_TOKEN])
def test_owner_document_upload_stores_nothing(monkeypatch, doc_type, token):
    member = _tokenless_member(KAM_MEMBER_ID)
    db = _attack_db(member)
    _bind(monkeypatch, db)
    body, content_type = _multipart(_doc_fields(KAM_MEMBER_ID, doc_type, token))
    status, raw = _run(_invoke(_app(), "POST", "/api/onboarding/documents", body=body, content_type=content_type))
    _assert_rejected(status, raw, db, KAM_MEMBER_ID)
    assert all(item["body"] != FILE_BYTES for item in db.uploads)


@pytest.mark.parametrize("token", [None, PLACEHOLDER_TOKEN])
def test_owner_portfolio_does_not_replace_samples(monkeypatch, token):
    member = _tokenless_member(KAM_MEMBER_ID)
    db = _attack_db(member)
    _bind(monkeypatch, db)
    body, content_type = _multipart(_portfolio_fields(KAM_MEMBER_ID, token))
    status, raw = _run(_invoke(_app(), "POST", "/api/onboarding/portfolio", body=body, content_type=content_type))
    _assert_rejected(status, raw, db, KAM_MEMBER_ID)


@pytest.mark.parametrize("token", [None, PLACEHOLDER_TOKEN])
def test_owner_submitted_does_not_schedule_review(monkeypatch, review_calls, token):
    member = _tokenless_member(KAM_MEMBER_ID)
    db = _attack_db(member)
    _bind(monkeypatch, db)
    payload = b"{}"
    if token is not None:
        payload = ('{"token": "%s"}' % token).encode()
    status, raw = _run(_invoke(
        _app(),
        "POST",
        f"/api/onboarding/apply/{KAM_MEMBER_ID}/submitted",
        body=payload,
        content_type="application/json",
    ))
    _assert_rejected(status, raw, db, KAM_MEMBER_ID)
    assert review_calls == []


def test_otp_created_member_status_rejected(monkeypatch):
    seed = SeedDb()
    created = _run(vendor_otp_link.link_member_after_otp(seed, OTP_USER_ID, OTP_EMAIL, "OTP Fixture"))
    member = _tokenless_member(created["member"]["id"])
    assert not created["member"].get("application_token")
    db = _attack_db(member)
    _bind(monkeypatch, db)
    status, body = _run(_invoke(_app(), "GET", _status_path(member["id"], None)))
    _assert_rejected(status, body, db, member["id"])


def test_admin_created_member_upload_stores_nothing(monkeypatch):
    seed = SeedDb()

    async def _get_db():
        return seed

    async def _actor(*_a, **_k):
        return "admin-actor"

    async def _member(actor_id, email=None):
        return {"id": actor_id, "role": "Owner", "vendor_id": "default_vendor", "is_active": True}

    monkeypatch.setattr(vendor_os, "get_db", _get_db)
    monkeypatch.setattr(vendor_os, "resolve_vendor_actor_id", _actor)
    monkeypatch.setattr(vendor_os, "get_vendor_member", _member)
    created = _run(vendor_os.create_vendor_member(
        VendorMemberCreate(name="Added By Admin", email="admin-added@example.com"),
        authorization=None,
        x_user_id=None,
    ))
    member = _tokenless_member(created["id"])
    db = _attack_db(member, arm=False, extra_members=[_tokenless_member("other-member", token=OTHER_TOKEN)])
    db.arm_second_read(created["id"], OTHER_TOKEN)
    _bind(monkeypatch, db)
    body, content_type = _multipart(_doc_fields(created["id"], "coi", OTHER_TOKEN))
    status, raw = _run(_invoke(_app(), "POST", "/api/onboarding/documents", body=body, content_type=content_type))
    _assert_rejected(status, raw, db, created["id"])


def test_empty_string_application_token_is_rejected(monkeypatch):
    member = _tokenless_member(KAM_MEMBER_ID, token="")
    db = _attack_db(member)
    _bind(monkeypatch, db)
    status, body = _run(_invoke(_app(), "GET", _status_path(KAM_MEMBER_ID, PLACEHOLDER_TOKEN)))
    _assert_rejected(status, body, db, KAM_MEMBER_ID)


def test_wrong_token_reads_nothing_and_does_not_accept_another_members_token(monkeypatch):
    victim = _tokenless_member("victim-member", token=PLACEHOLDER_TOKEN)
    other = _tokenless_member("other-member", token=OTHER_TOKEN)
    db = _attack_db(victim, arm=False)
    db.add_member(other)
    _bind(monkeypatch, db)
    status, body = _run(_invoke(_app(), "GET", _status_path("victim-member", OTHER_TOKEN)))
    assert status == 404
    assert STORED_KEY.encode() not in body
    assert not any(op[0] == "select" and op[1] == "vendor_documents" for op in db.ops)
    assert db.uploads == []
    assert db.inserts == []
    assert db.updates == []
    assert db.member_snapshots and db.member_snapshots[0]["id"] == "victim-member"


def test_matching_applicant_token_returns_only_that_members_documents(monkeypatch):
    member = _tokenless_member("applicant-member", token=PLACEHOLDER_TOKEN)
    other = _tokenless_member("other-member", token=OTHER_TOKEN)
    db = _attack_db(member, arm=False)
    db.add_member(other)
    db.add_document(other["id"])
    db.documents[-1]["file_url"] = "fixture-other-storage-key"
    db.documents[-1]["validation_result"] = {"certificate_number": "fixture-other-certificate"}
    _bind(monkeypatch, db)
    status, body = _run(_invoke(_app(), "GET", _status_path(member["id"], PLACEHOLDER_TOKEN)))
    assert status == 200, body
    text = body.decode()
    assert STORED_KEY in text
    assert STORED_CERT in text
    assert "fixture-other-storage-key" not in text
    assert "fixture-other-certificate" not in text
    assert SWAPPED_KEY not in text
    assert db.member_snapshots[0]["id"] == member["id"]
    assert db.member_snapshots[0]["application_token"] == PLACEHOLDER_TOKEN


def test_matching_applicant_token_upload_writes_only_that_member(monkeypatch):
    member = _tokenless_member("applicant-member", token=PLACEHOLDER_TOKEN)
    db = _attack_db(member, arm=False, extra_members=[_tokenless_member("other-member", token=OTHER_TOKEN)])
    _bind(monkeypatch, db)
    body, content_type = _multipart(_doc_fields(member["id"], "rpas_certificate", PLACEHOLDER_TOKEN))
    status, raw = _run(_invoke(_app(), "POST", "/api/onboarding/documents", body=body, content_type=content_type))
    assert status == 200, raw
    assert len(db.uploads) == 1
    upload = db.uploads[0]
    assert upload["body"] == FILE_BYTES
    assert upload["path"].startswith(f"bricks-go/jobs/onboarding/{member['id']}/")
    doc_inserts = [row for name, row in db.inserts if name == "vendor_documents"]
    assert len(doc_inserts) == 1
    assert doc_inserts[0]["member_id"] == member["id"]
    assert doc_inserts[0]["member_id"] != "other-member"
    assert doc_inserts[0]["file_url"] == upload["path"]
