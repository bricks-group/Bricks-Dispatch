"""Uploads must not treat the shared default_vendor org as permission.

Any signed-in member whose vendor_members.vendor_id is default_vendor — a
photographer approved for other jobs, a self-registered email-code account, or
an applicant — can today list, read, delete, and register uploads on every
job the org owns. These tests call the real /api/uploads handlers and the
real session lookup. They fail while that check is the org id, and they pass
once a member who is not the assignee (and not an active, approved admin)
is refused, with no upload row, count, storage, or post-production side effect.

Left untested: there is no other uploads route. Fair-Use Postgres fallbacks
are not on this path when the query returns normally.
"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.routers import auth as auth_router
from app.routers import onboarding, uploads
from app.shared import auth as shared_auth
from app.shared.fair_use_auth import clear_auth_cooldown
from app.shared.schemas import VendorApplicationCreate

ORG = "default_vendor"
VICTIM_JOB = "job-victim"
VICTIM_UPLOAD = "upload-victim"
VICTIM_ASSIGNEE = "22222222-2222-4222-8222-222222222222"
VICTIM_FILE = "bricks-go/jobs/job-victim/upload-victim/living-room.jpg"
VICTIM_THUMB = "thumbnail-marker-victim-listing"
OWN_JOB = "job-own"
OWN_UPLOAD = "upload-own"
OWN_FILE = "bricks-go/jobs/job-own/upload-own/kitchen.jpg"
OWN_THUMB = "thumbnail-marker-own-kitchen"
APPLICANT_JOB = "job-applicant"
APPLICANT_UPLOAD = "upload-applicant"
APPLICANT_FILE = "bricks-go/jobs/job-applicant/upload-applicant/hall.jpg"
APPLICANT_THUMB = "thumbnail-marker-applicant-assigned"


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.op = "select"
        self.payload = None
        self.single = False
        self._order = None
        self._limit = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda row, c=col, v=val: row.get(c) == v)
        return self

    def in_(self, col, vals):
        wanted = set(vals or [])
        self.filters.append(lambda row, c=col, w=wanted: row.get(c) in w)
        return self

    def ilike(self, col, val):
        needle = str(val or "").lower()
        self.filters.append(
            lambda row, c=col, n=needle: str(row.get(c) or "").lower() == n
        )
        return self

    def is_(self, col, val):
        if val == "null":
            self.filters.append(lambda row, c=col: row.get(c) is None)
        else:
            self.filters.append(lambda row, c=col, v=val: row.get(c) == v)
        return self

    def order(self, col, desc=False, **_k):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self.single = True
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

    def upsert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def _match(self):
        return [row for row in self.db.tables[self.table] if all(f(row) for f in self.filters)]

    async def execute(self):
        matched = self._match()
        if self.op == "insert":
            row = copy.deepcopy(self.payload)
            self.db.tables[self.table].append(row)
            self.db.events.append(("insert", self.table, copy.deepcopy(row)))
            return SimpleNamespace(data=[row])
        if self.op == "update":
            for row in matched:
                row.update(self.payload)
            self.db.events.append(("update", self.table, copy.deepcopy(self.payload), [r.get("id") for r in matched]))
            return SimpleNamespace(data=copy.deepcopy(matched))
        if self.op == "delete":
            ids = [row.get("id") for row in matched]
            self.db.tables[self.table] = [row for row in self.db.tables[self.table] if row not in matched]
            self.db.events.append(("delete", self.table, ids))
            return SimpleNamespace(data=ids)
        rows = matched
        if self._order:
            col, desc = self._order
            rows = sorted(rows, key=lambda r: str(r.get(col) or ""), reverse=bool(desc))
        if self._limit is not None:
            rows = rows[: self._limit]
        if self.single:
            if not rows:
                return None
            return SimpleNamespace(data=copy.deepcopy(rows[0]))
        return SimpleNamespace(data=copy.deepcopy(rows))


class MemoryDB:
    """In-memory stand-in for the PostgREST calls these handlers issue.

    Filters are applied. A handler that authorizes one row and writes another
    shows up as a mutation of the other row.
    """

    def __init__(self):
        self.tables = {
            "jobs": [],
            "uploads": [],
            "vendor_members": [],
            "vendor_terminations": [],
            "vendor_availability": [],
            "vendor_documents": [],
        }
        self.events = []
        self.users = {}
        self.auth = self._Auth(self)

    class _Auth:
        def __init__(self, db):
            self.db = db

        async def get_user(self, token):
            # Shape read by resolve_vendor_actor_id after a live get_user.
            user = self.db.users.get(token)
            if user is None:
                raise RuntimeError("invalid session")
            return SimpleNamespace(user=user)

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = []
        return _Query(self, name)

    def snapshot(self):
        return copy.deepcopy({"jobs": self.tables["jobs"], "uploads": self.tables["uploads"]})


def _user(uid, email):
    return SimpleNamespace(
        id=uid,
        email=email,
        user_metadata={"name": email.split("@")[0]},
        email_confirmed_at="2026-11-01T00:00:00+00:00",
        identities=[],
    )


def _job(job_id, assignee, *, status="completed", uploaded=1, deliverables=1):
    return {
        "id": job_id,
        "vendor_id": ORG,
        "assignee_id": assignee,
        "status": status,
        "uploaded_count": uploaded,
        "deliverables_count": deliverables,
        "address": "10 Example St",
    }


def _upload(upload_id, job_id, file_url, thumb):
    return {
        "id": upload_id,
        "job_id": job_id,
        "filename": "living-room.jpg",
        "file_size": 1200,
        "mime_type": "image/jpeg",
        "thumbnail": thumb,
        "file_url": file_url,
        "thumbnail_url": file_url + ".thumb",
        "created_at": "2026-11-01T12:00:00+00:00",
        "processing_status": "pending",
    }


class World:
    def __init__(self, monkeypatch):
        clear_auth_cooldown()
        self.db = MemoryDB()
        self.db.tables["jobs"] = [
            _job(VICTIM_JOB, VICTIM_ASSIGNEE),
            _job(OWN_JOB, "unassigned-placeholder", status="assigned"),
        ]
        self.db.tables["uploads"] = [
            _upload(VICTIM_UPLOAD, VICTIM_JOB, VICTIM_FILE, VICTIM_THUMB),
            _upload(OWN_UPLOAD, OWN_JOB, OWN_FILE, OWN_THUMB),
        ]
        self.signed = []
        self.stored = []
        self.processed = []

        async def get_db():
            return self.db

        monkeypatch.setattr(uploads, "get_db", get_db)
        monkeypatch.setattr(shared_auth, "get_db", get_db)
        monkeypatch.setattr(auth_router, "get_db", get_db)
        monkeypatch.setattr(onboarding, "get_db", get_db)

        async def fake_upload_file(**kwargs):
            self.stored.append(kwargs)
            return (kwargs.get("filename"), "thumb-path", "inline-thumb", None)

        async def fake_sign(key, content_type, file_size=0, *, video=False):
            self.signed.append({"key": key, "content_type": content_type, "file_size": file_size, "video": video})
            return {
                "signed_url": "https://example.test/signed-upload",
                "storage_path": key,
                "path": key,
                "token": "placeholder-sign-token",
                "method": "PUT",
                "headers": {"Content-Type": content_type},
                "via_proxy": False,
                "provider": "test",
                "max_file_bytes": 50_000_000,
            }

        async def fake_process(job_id):
            self.processed.append(job_id)

        monkeypatch.setattr(uploads, "upload_file", fake_upload_file)
        monkeypatch.setattr(uploads, "process_job_assets", fake_process)
        monkeypatch.setattr("app.services.upload_backend.create_direct_upload", fake_sign)

        self.app = FastAPI()
        self.app.include_router(uploads.router)

    def login(self, uid, email):
        token = "test-session-" + uid
        self.db.users[token] = _user(uid, email)
        return token

    def member(self, **fields):
        row = {
            "vendor_id": ORG,
            "is_active": True,
            "onboarding_status": "approved",
            "role": "Photographer",
            "title": "Photographer IC",
            "auth_user_id": fields.get("id"),
        }
        row.update(fields)
        self.db.tables["vendor_members"].append(row)
        return row

    def assign_own(self, member_id):
        for job in self.db.tables["jobs"]:
            if job["id"] == OWN_JOB:
                job["assignee_id"] = member_id


def _run(coro):
    return asyncio.run(coro)


async def _invoke(app, method, path, *, token=None, query=b"", json_body=None, form=None):
    body = b""
    headers = []
    if token:
        headers.append((b"authorization", ("Bearer " + token).encode()))
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers.append((b"content-type", b"application/json"))
    elif form is not None:
        boundary = "----uploadboundary"
        parts = []
        for key, value in form.items():
            parts.append(
                (
                    "--" + boundary + "\r\n"
                    'Content-Disposition: form-data; name="' + key + '"\r\n\r\n'
                    + str(value) + "\r\n"
                ).encode()
            )
        parts.append(("--" + boundary + "--\r\n").encode())
        body = b"".join(parts)
        headers.append((b"content-type", ("multipart/form-data; boundary=" + boundary).encode()))
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    parsed = json.loads(raw.decode() or "null") if raw else None
    return start["status"], parsed


def _text(body) -> str:
    return json.dumps(body)


def _assert_refused(status, body, world, before):
    assert status in (401, 403, 404), (status, body)
    leaked = _text(body)
    assert VICTIM_THUMB not in leaked
    assert VICTIM_FILE not in leaked
    assert APPLICANT_THUMB not in leaked
    assert APPLICANT_FILE not in leaked
    assert world.db.snapshot() == before
    assert world.stored == []
    assert world.signed == []
    assert world.processed == []


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def test_missing_session_cannot_list_uploads(world):
    before = world.db.snapshot()
    status, body = _run(_invoke(world.app, "GET", "/api/uploads"))
    _assert_refused(status, body, world, before)


def test_assignee_can_read_and_write_only_their_job(world):
    """The assigned photographer still reaches their own job. The storage key
    and the inserted row are that job, not another job they also sent."""
    ic = "33333333-3333-4333-8333-333333333333"
    world.member(id=ic, email="photographer@example.com")
    world.assign_own(ic)
    token = world.login(ic, "photographer@example.com")

    status, body = _run(_invoke(world.app, "GET", "/api/uploads", token=token, query=b"job_id=" + OWN_JOB.encode()))
    assert status == 200, body
    assert OWN_THUMB in _text(body)
    assert VICTIM_THUMB not in _text(body)
    assert VICTIM_FILE not in _text(body)

    upload_id = str(uuid.uuid4())
    path = "bricks-go/jobs/" + OWN_JOB + "/" + upload_id + "/kitchen.jpg"
    status, body = _run(_invoke(
        world.app, "POST", "/api/uploads/complete", token=token,
        json_body={
            "job_id": OWN_JOB,
            "upload_id": upload_id,
            "storage_path": path,
            "filename": "kitchen.jpg",
            "mime_type": "image/jpeg",
            "file_size": 1200,
            "media_type": "hdr",
        },
    ))
    assert status == 200, body
    assert body.get("job_id") == OWN_JOB
    inserted = [row for row in world.db.tables["uploads"] if row.get("id") == upload_id]
    assert inserted and inserted[0]["job_id"] == OWN_JOB
    assert all(row["job_id"] != VICTIM_JOB or row["id"] == VICTIM_UPLOAD for row in world.db.tables["uploads"])
    assert world.processed == []  # own job is not in completed + count-met state

    world.signed.clear()
    status, body = _run(_invoke(
        world.app, "POST", "/api/uploads/sign", token=token,
        json_body={"job_id": OWN_JOB, "filename": "kitchen.jpg", "mime_type": "image/jpeg", "file_size": 1200},
    ))
    assert status == 200, body
    assert world.signed and OWN_JOB in world.signed[0]["key"] and VICTIM_JOB not in world.signed[0]["key"]
    assert body.get("job_id") == OWN_JOB


def test_active_admin_can_list_org_uploads(world):
    owner = "44444444-4444-4444-8444-444444444444"
    world.member(id=owner, email="owner@example.com", role="Owner", title="Owner", is_active=True, onboarding_status="approved")
    token = world.login(owner, "owner@example.com")
    status, body = _run(_invoke(world.app, "GET", "/api/uploads", token=token))
    assert status == 200, body
    assert VICTIM_THUMB in _text(body)


def _arm_post_production(world, job_id):
    """A completed job one still short of its declared count. Registering a
    file here is what calls process_job_assets."""
    for job in world.db.tables["jobs"]:
        if job["id"] == job_id:
            job["uploaded_count"] = 0
            job["deliverables_count"] = 1
            job["status"] = "completed"


def _attack(world, token, op, job_id, upload_id):
    new_id = str(uuid.uuid4())
    if op == "list":
        return _run(_invoke(world.app, "GET", "/api/uploads", token=token))
    if op == "list_job":
        return _run(_invoke(world.app, "GET", "/api/uploads", token=token, query=b"job_id=" + job_id.encode()))
    if op == "get":
        return _run(_invoke(world.app, "GET", "/api/uploads/" + upload_id, token=token))
    if op == "preview":
        return _run(_invoke(
            world.app, "POST", "/api/uploads/" + upload_id + "/preview", token=token,
            json_body={"job_id": job_id},
        ))
    if op == "delete":
        return _run(_invoke(world.app, "DELETE", "/api/uploads/" + upload_id, token=token))
    if op == "sign":
        return _run(_invoke(
            world.app, "POST", "/api/uploads/sign", token=token,
            json_body={"job_id": job_id, "filename": "evil.jpg", "mime_type": "image/jpeg", "file_size": 1000},
        ))
    if op == "complete":
        return _run(_invoke(
            world.app, "POST", "/api/uploads/complete", token=token,
            json_body={
                "job_id": job_id,
                "upload_id": new_id,
                "storage_path": "bricks-go/jobs/" + job_id + "/" + new_id + "/evil.jpg",
                "filename": "evil.jpg",
                "mime_type": "image/jpeg",
                "file_size": 1000,
            },
        ))
    if op == "create":
        return _run(_invoke(
            world.app, "POST", "/api/uploads", token=token,
            form={
                "job_id": job_id,
                "filename": "evil.jpg",
                "file_size": "1000",
                "mime_type": "image/jpeg",
                "file_data": "aGVsbG8=",
                "media_type": "hdr",
            },
        ))
    raise AssertionError(op)


_OPS = ["list", "list_job", "get", "preview", "delete", "sign", "complete", "create"]


@pytest.mark.parametrize("op", _OPS)
def test_approved_photographer_cannot_touch_another_jobs_uploads(world, op):
    ic = "55555555-5555-4555-8555-555555555555"
    world.member(id=ic, email="other@example.com")
    world.assign_own(ic)
    token = world.login(ic, "other@example.com")
    if op == "list":
        status, body = _attack(world, token, op, VICTIM_JOB, VICTIM_UPLOAD)
        assert VICTIM_THUMB not in _text(body) and VICTIM_FILE not in _text(body)
        assert OWN_THUMB in _text(body)
        assert world.stored == [] and world.signed == [] and world.processed == []
        return
    if op in ("complete", "create"):
        _arm_post_production(world, VICTIM_JOB)
    before = world.db.snapshot()
    status, body = _attack(world, token, op, VICTIM_JOB, VICTIM_UPLOAD)
    _assert_refused(status, body, world, before)


def _otp_member(world):
    """Real verify route. The OTP exchange itself is stubbed with the session
    shape vendor_os_verify reads; link_member_after_otp is not stubbed."""
    uid = "77777777-7777-4777-8777-777777777777"
    email = "new-photographer@example.com"
    user = _user(uid, email)
    token = "test-session-" + uid
    otp = MagicMock()
    otp.auth.verify_otp = AsyncMock(return_value=SimpleNamespace(
        session=SimpleNamespace(access_token=token, refresh_token="test-refresh", expires_in=3600),
        user=user,
    ))
    with patch("app.shared.database.new_auth_client", AsyncMock(return_value=otp)), \
         patch.object(auth_router, "write_audit_event", AsyncMock()):
        out = _run(auth_router.vendor_os_verify({"email": email, "token": "placeholder-otp"}))
    assert out["access_token"] == token
    created = next(row for row in world.db.tables["vendor_members"] if row.get("id") == uid)
    assert created["vendor_id"] == ORG and created["role"] == "Photographer"
    world.db.users[token] = user
    world.db.events.clear()
    return token


@pytest.mark.parametrize("op", _OPS)
def test_email_code_signup_cannot_touch_org_uploads(world, op):
    token = _otp_member(world)
    if op in ("complete", "create"):
        _arm_post_production(world, VICTIM_JOB)
    before = world.db.snapshot()
    status, body = _attack(world, token, op, VICTIM_JOB, VICTIM_UPLOAD)
    _assert_refused(status, body, world, before)


def _applicant_session(world):
    email = "applicant@example.com"
    application = VendorApplicationCreate(
        name="Alex Applicant",
        email=email,
        phone="4165550100",
        specialties=["photo"],
        city="Toronto",
        accepted_terms=True,
        equipment_list=["camera body and lens"],
        hst_exempt=True,
    )
    created = _run(onboarding._apply_to_bricks_inner(application))
    member_id = created["member_id"]
    row = next(r for r in world.db.tables["vendor_members"] if r.get("id") == member_id)
    assert row["vendor_id"] == ORG
    assert row["is_active"] is False
    assert row["onboarding_status"] == "pending"
    uid = "88888888-8888-4888-8888-888888888888"
    from app.services.vendor_otp_link import link_member_after_otp
    linked = _run(link_member_after_otp(world.db, uid, email, "Alex Applicant"))
    assert linked["action"] == "linked" and linked["member"]["id"] == member_id
    world.db.tables["jobs"].append(_job(APPLICANT_JOB, member_id, status="completed", uploaded=0, deliverables=1))
    world.db.tables["uploads"].append(_upload(APPLICANT_UPLOAD, APPLICANT_JOB, APPLICANT_FILE, APPLICANT_THUMB))
    token = world.login(uid, email)
    world.db.events.clear()
    world.stored.clear()
    world.signed.clear()
    world.processed.clear()
    return token


@pytest.mark.parametrize("op", _OPS)
def test_applicant_cannot_use_uploads_even_when_assigned(world, op):
    token = _applicant_session(world)
    if op in ("complete", "create"):
        _arm_post_production(world, APPLICANT_JOB)
    before = world.db.snapshot()
    # Target the job assigned to this applicant so a visibility-only fix
    # (assignee match, no active/approved check) still fails.
    status, body = _attack(world, token, op, APPLICANT_JOB, APPLICANT_UPLOAD)
    _assert_refused(status, body, world, before)
