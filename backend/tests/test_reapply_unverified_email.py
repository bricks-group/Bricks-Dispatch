"""Unauthenticated reapply must not take over an existing applicant.

POST /api/onboarding/apply is public (internal-key middleware skips
/api/onboarding). When the email matches a vendor_members row rejected long
enough ago, the handler reuses that row and returns a new application_token.

These tests are the spec for that hole. They fail while an anonymous caller
can rewrite the row or use a token from that response to read or extend the
existing application. They pass once that caller cannot do either, including
when a fix looks the member up again before writing. A write that lands before
the request is refused is still a failure.

Run:
    cd backend && python3 -m pytest tests/test_reapply_unverified_email.py -q
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI

# conftest replaces sentry_sdk with a Mock, which cannot satisfy
# `from sentry_sdk.integrations.fastapi import FastApiIntegration` in server.py.
# Drop that stand-in so the real middleware class can be imported.
for _name in [n for n in list(sys.modules) if n == "sentry_sdk" or n.startswith("sentry_sdk.")]:
    del sys.modules[_name]

from app.routers import onboarding as ob
from app.services import applicant_evidence as ev
from app.services import applicant_review_runner as review_runner
from app.services import notifications as notifications
from app.services import storage as storage
from server import InternalAuthMiddleware
from tests._fake_supabase import FakeSupabase

VICTIM_ID = "11111111-1111-4111-8111-111111111111"
DOC_ID = "33333333-3333-4333-8333-333333333333"
AVAIL_ID = "44444444-4444-4444-8444-444444444444"
VICTIM_EMAIL = "victim.applicant@example.com"
VICTIM_NAME = "Casey Applicant"
VICTIM_PHONE = "4165550101"
VICTIM_TOKEN = "victim-application-token-placeholder"
VICTIM_PORTFOLIO = "https://victim.example/work"
VICTIM_CITY = "Toronto"
POLICY_NUMBER = "POL-VICTIM-8841"
INSURER_NAME = "Northwind Assurance"
STORAGE_KEY = "onboarding/victim-member/coi-prior.pdf"
ATTACKER_NAME = "Alex Attacker"
ATTACKER_PHONE = "6475550199"
ATTACKER_PORTFOLIO = "https://attacker.example/portfolio"
ATTACKER_CITY = "Hamilton"

# Columns the reapply write copies off the anonymous body, plus the credential
# and the status that locks the original applicant out.
GUARDED = (
    "name",
    "phone",
    "email",
    "portfolio_links",
    "portfolio_samples",
    "service_areas",
    "equipment_list",
    "home_city",
    "onboarding_status",
    "application_token",
    "rejected_at",
    "application_profile",
    "is_active",
    "role",
)


class _Bucket:
    """In-memory stand-in for the two storage methods this path calls.

    upload_file awaits bucket.upload and, on success, returns the key it built
    before the call. create_signed_upload_url awaits
    bucket.create_signed_upload_url and reads signedURL off the result.
    """

    def __init__(self, saved, name):
        self.saved = saved
        self.name = name

    async def upload(self, path, file, file_options=None):
        self.saved.append({
            "bucket": self.name,
            "path": path,
            "file": bytes(file),
            "file_options": file_options,
        })
        return {"path": path}

    async def create_signed_upload_url(self, path):
        return {
            "path": path,
            "signedURL": "https://storage.example/upload-placeholder",
            "token": "placeholder-signed-upload-token",
        }

    async def create_signed_url(self, path, expiry=None):
        return {"signedURL": "https://storage.example/download-placeholder"}


class _Storage:
    def __init__(self):
        self.uploads = []

    def from_(self, name):
        return _Bucket(self.uploads, name)


class AppDb(FakeSupabase):
    def __init__(self):
        super().__init__()
        self.storage = _Storage()


def _real_httpx():
    """The httpx package, not the MagicMock conftest installs for app imports.

    BaseHTTPMiddleware drops a hand-rolled ASGI response body when the test
    receive() answers the post-body disconnect too early. httpx's ASGI
    transport speaks that handshake. App modules already bound the mock, and
    sys.modules is put back so a later `import httpx` inside a request stays
    on that mock.
    """
    cached = getattr(_real_httpx, "mod", None)
    if cached is not None:
        return cached
    saved = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "httpx" or name.startswith("httpx.")
    }
    for name in saved:
        del sys.modules[name]
    import httpx
    real = httpx
    sys.modules.update(saved)
    _real_httpx.mod = real
    return real


def _http(app, method, path, *, json_body=None, form=None, query=None):
    """One ASGI request. No internal API key, no session cookie."""
    httpx = _real_httpx()

    async def _send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            if form is not None:
                response = await client.request(method, path, data=form, params=query)
            else:
                response = await client.request(method, path, json=json_body, params=query)
        return response.status_code, response.text

    status, text = asyncio.run(_send())
    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = text
    return status, parsed


def _dump(body) -> str:
    return json.dumps(body, default=str, sort_keys=True)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("APPLICANT_UPLOAD_SECRET", "placeholder-upload-session-secret")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    db = AppDb()

    async def get_db():
        return db

    monkeypatch.setattr(ob, "get_db", get_db)
    monkeypatch.setattr(storage, "get_db", get_db)
    monkeypatch.setattr(notifications, "get_db", get_db)
    monkeypatch.setattr(review_runner, "get_db", get_db)

    app = FastAPI()
    app.include_router(ob.router)
    app.add_middleware(InternalAuthMiddleware)
    return app, db


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed_member(db, *, member_id=VICTIM_ID, status="rejected", rejected_at="set", token=VICTIM_TOKEN, days=91):
    when = _ago(days)
    row = {
        "id": member_id,
        "vendor_id": "default_vendor",
        "name": VICTIM_NAME,
        "email": VICTIM_EMAIL,
        "phone": VICTIM_PHONE,
        "role": "Applicant",
        "vendor_type": "photographer",
        "onboarding_status": status,
        "is_active": False,
        "portfolio_links": [VICTIM_PORTFOLIO],
        "portfolio_samples": [{"media_type": "hdr", "link": VICTIM_PORTFOLIO}],
        "equipment_list": ["Canon body kept on file"],
        "service_areas": [VICTIM_CITY],
        "home_city": VICTIM_CITY,
        "application_token": token,
        "application_profile": {"version": 1, "kept": True},
        "internal_notes": "original application notes",
        "rejected_at": when if rejected_at == "set" else rejected_at,
        "created_at": when,
        "updated_at": when,
        "terms_accepted_at": when,
    }
    db.tables.setdefault("vendor_members", []).append(row)
    db.tables.setdefault("vendor_documents", []).append({
        "id": DOC_ID,
        "vendor_id": "default_vendor",
        "member_id": member_id,
        "doc_type": "coi",
        "file_url": STORAGE_KEY,
        "status": "flagged",
        "expiry_date": "2027-06-01",
        "validation_result": {
            "outcome": "insufficient",
            "doc_type": "coi",
            "extracted": {
                "insured_name": VICTIM_NAME,
                "insurer_name": INSURER_NAME,
                "policy_number": POLICY_NUMBER,
            },
        },
    })
    db.tables.setdefault("vendor_availability", []).append({
        "id": AVAIL_ID,
        "member_id": member_id,
        "vendor_id": "default_vendor",
        "available": True,
        "work_start": "09:00",
        "work_end": "17:00",
        "days_of_week": ["mon", "tue", "wed"],
    })
    return row


def _snap(db, member_id=VICTIM_ID):
    member = db.one("vendor_members", id=member_id)
    return {
        "guarded": {key: copy.deepcopy(member.get(key)) for key in GUARDED},
        "docs": copy.deepcopy(db.rows("vendor_documents", member_id=member_id)),
        "avail": copy.deepcopy(db.rows("vendor_availability", member_id=member_id)),
    }


def _assert_intact(db, snap, member_id=VICTIM_ID):
    member = db.one("vendor_members", id=member_id)
    assert member is not None
    for key in GUARDED:
        assert member.get(key) == snap["guarded"][key], key
    assert db.rows("vendor_documents", member_id=member_id) == snap["docs"]
    assert db.rows("vendor_availability", member_id=member_id) == snap["avail"]


def _availability():
    start = datetime.now(timezone.utc).date().isoformat()
    return {
        "days_of_week": ["sat", "sun"],
        "time_windows": ["evening"],
        "weekends": True,
        "earliest_start_date": start,
        "shoots_per_week": 4,
        "max_shoots_per_day": 2,
        "notice_hours": 24,
        "has_car": True,
        "has_licence": True,
    }


def _sample_files():
    sample = {"name": "room.jpg", "type": "image/jpeg", "size": 50 * 1024, "kind": "sample"}
    resume = {"name": "resume.pdf", "type": "application/pdf", "size": 2048, "kind": "resume"}
    return [dict(sample) for _ in range(ev.MIN_SAMPLES)] + [resume]


def _photographer_body(app, email):
    status, minted = _http(app, "POST", "/api/onboarding/sample-uploads", json_body={"files": _sample_files()})
    assert status == 200, minted
    uploads = minted["uploads"]
    paths = [row["path"] for row in uploads if row.get("kind") != "resume"]
    resume = next(row["path"] for row in uploads if row.get("kind") == "resume")
    return {
        "name": ATTACKER_NAME,
        "email": email,
        "phone": ATTACKER_PHONE,
        "vendor_type": "photographer",
        "specialties": ["photo"],
        "portfolio_links": [ATTACKER_PORTFOLIO],
        "equipment_list": ["Sony body and 24-70mm lens"],
        "city": ATTACKER_CITY,
        "preferred_regions": ["Ottawa"],
        "accepted_terms": True,
        "hst_exempt": True,
        "samples_are_real_estate": True,
        "sample_upload_session": minted["session"],
        "sample_paths": paths,
        "resume_path": resume,
        "resume_name": "resume.pdf",
        "availability": _availability(),
    }


def _stager_body(email):
    return {
        "name": ATTACKER_NAME,
        "email": email,
        "phone": ATTACKER_PHONE,
        "vendor_type": "stager",
        "specialties": ["staging"],
        "equipment_list": ["Sofa, lamps, and folding tables"],
        "city": ATTACKER_CITY,
        "preferred_regions": ["Ottawa"],
        "accepted_terms": True,
        "hst_exempt": True,
        "availability": _availability(),
    }


def _apply(app, body):
    return _http(app, "POST", "/api/onboarding/apply", json_body=body)


def _status(app, member_id, token):
    return _http(
        app,
        "GET",
        f"/api/onboarding/status/{member_id}",
        query={"token": token} if token is not None else None,
    )


def _token_from(body):
    if not isinstance(body, dict):
        return None
    return body.get("application_token") or body.get("token")


def _member_from(body):
    if not isinstance(body, dict):
        return None
    return body.get("member_id")


def test_onboarding_is_outside_the_internal_key_wall(world):
    """A dispatch path without the internal key is refused. Onboarding is not."""
    app, _db = world
    status, body = _http(app, "POST", "/api/v1/dispatch/jobs", json_body={})
    assert status == 401, body
    assert "missing_internal_auth" in _dump(body)


def test_rejected_reapply_does_not_rewrite_member_without_email_proof(world):
    app, db = world
    _seed_member(db)
    snap = _snap(db)
    status, body = _apply(app, _photographer_body(app, "Victim.Applicant@Example.com"))
    assert status != 401, body
    _assert_intact(db, snap)
    assert POLICY_NUMBER not in _dump(body)
    assert VICTIM_TOKEN not in _dump(body)


def test_reapply_response_cannot_read_prior_compliance_documents(world):
    app, db = world
    _seed_member(db)
    before, prior = _status(app, VICTIM_ID, VICTIM_TOKEN)
    assert before == 200, prior
    assert POLICY_NUMBER in _dump(prior)
    assert INSURER_NAME in _dump(prior)
    assert STORAGE_KEY in _dump(prior)
    denied, _hidden = _status(app, VICTIM_ID, "not-the-applicant-token")
    assert denied == 404

    status, body = _apply(app, _photographer_body(app, VICTIM_EMAIL))
    token = _token_from(body)
    issued_for = _member_from(body)
    leaked = False
    if token:
        for member_id in {VICTIM_ID, issued_for} - {None}:
            viewed_status, viewed = _status(app, member_id, token)
            viewed_text = _dump(viewed)
            if viewed_status == 200 and POLICY_NUMBER in viewed_text and INSURER_NAME in viewed_text:
                leaked = True
    still_status, still = _status(app, VICTIM_ID, VICTIM_TOKEN)
    victim_locked_out = not (still_status == 200 and POLICY_NUMBER in _dump(still))
    assert not leaked and not victim_locked_out, (
        f"leaked={leaked} victim_locked_out={victim_locked_out} "
        f"apply_status={status} issued_for={issued_for} token_issued={bool(token)}"
    )


def test_reapply_token_cannot_upload_documents_onto_existing_member(world):
    app, db = world
    _seed_member(db)
    _status_code, body = _apply(app, _photographer_body(app, VICTIM_EMAIL))
    token = _token_from(body) or "attacker-supplied-token"
    payload = base64.b64encode(b"%PDF-1.4 attacker planted certificate").decode()
    up_status, up_body = _http(
        app,
        "POST",
        "/api/onboarding/documents",
        form={
            "member_id": VICTIM_ID,
            "doc_type": "id_verification",
            "filename": "attacker-planted.pdf",
            "file_data": payload,
            "token": token,
        },
    )
    docs = db.rows("vendor_documents", member_id=VICTIM_ID)
    assert [row["id"] for row in docs] == [DOC_ID], {"status": up_status, "body": up_body, "docs": docs}
    assert all("attacker-planted" not in str(row.get("file_url") or "") for row in docs)
    assert up_status != 200


def test_reapply_token_cannot_mark_existing_application_submitted(world):
    app, db = world
    _seed_member(db)
    body = _photographer_body(app, VICTIM_EMAIL)
    body["docs_pending"] = True
    _status_code, applied = _apply(app, body)
    token = _token_from(applied) or "attacker-supplied-token"
    status, submitted = _http(
        app,
        "POST",
        f"/api/onboarding/apply/{VICTIM_ID}/submitted",
        json_body={"token": token},
    )
    assert not (status == 200 and isinstance(submitted, dict) and submitted.get("ok") is True), submitted
    assert db.rows("vendor_applicant_reviews", member_id=VICTIM_ID) == []


def test_reapply_token_cannot_replace_portfolio(world):
    app, db = world
    _seed_member(db)
    snap = _snap(db)
    _status_code, body = _apply(app, _photographer_body(app, VICTIM_EMAIL))
    token = _token_from(body) or "attacker-supplied-token"
    payload = base64.b64encode(b"not-a-real-image").decode()
    port_status, port_body = _http(
        app,
        "POST",
        "/api/onboarding/portfolio",
        form={
            "member_id": VICTIM_ID,
            "media_type": "hdr",
            "filename": "attacker.jpg",
            "file_data": payload,
            "link": "https://attacker.example/hdr",
            "token": token,
        },
    )
    member = db.one("vendor_members", id=VICTIM_ID)
    docs = db.rows("vendor_documents", member_id=VICTIM_ID)
    assert member.get("portfolio_samples") == snap["guarded"]["portfolio_samples"]
    assert member.get("portfolio_links") == snap["guarded"]["portfolio_links"]
    assert [row["id"] for row in docs] == [DOC_ID], {"status": port_status, "body": port_body}
    assert port_status != 200


def test_reapply_when_only_created_at_is_old_still_requires_email_proof(world):
    """Cooldown falls through updated_at and created_at when rejected_at is empty."""
    app, db = world
    _seed_member(db, rejected_at=None)
    snap = _snap(db)
    _status_code, body = _apply(app, _photographer_body(app, VICTIM_EMAIL))
    _assert_intact(db, snap)
    token = _token_from(body)
    if token:
        viewed_status, viewed = _status(app, VICTIM_ID, token)
        if viewed_status == 200:
            assert POLICY_NUMBER not in _dump(viewed)


def test_null_token_rejected_member_is_not_rewritten(world):
    app, db = world
    _seed_member(db, token=None)
    snap = _snap(db)
    status, body = _apply(app, _photographer_body(app, VICTIM_EMAIL))
    _assert_intact(db, snap)
    token = _token_from(body)
    if token:
        viewed_status, viewed = _status(app, VICTIM_ID, token)
        if viewed_status == 200:
            assert POLICY_NUMBER not in _dump(viewed)
    assert status != 200 or token is None or _member_from(body) != VICTIM_ID


def test_non_photographer_reapply_requires_email_proof(world):
    app, db = world
    _seed_member(db)
    snap = _snap(db)
    status, body = _apply(app, _stager_body("Victim.Applicant@Example.com"))
    assert status != 401, body
    _assert_intact(db, snap)
    token = _token_from(body)
    if token:
        viewed_status, viewed = _status(app, VICTIM_ID, token)
        if viewed_status == 200:
            assert POLICY_NUMBER not in _dump(viewed)


def test_recent_rejection_is_not_replaced(world):
    app, db = world
    _seed_member(db, days=10)
    snap = _snap(db)
    status, body = _apply(app, _photographer_body(app, VICTIM_EMAIL))
    assert status == 400, body
    _assert_intact(db, snap)
    assert _token_from(body) is None


def test_pending_application_is_not_replaced(world):
    app, db = world
    _seed_member(db, status="pending", rejected_at=None, days=400)
    snap = _snap(db)
    status, body = _apply(app, _photographer_body(app, VICTIM_EMAIL))
    assert status == 400, body
    _assert_intact(db, snap)
    assert _token_from(body) is None
    assert "already exists" in _dump(body).lower()


def test_first_application_for_new_email_still_succeeds(world):
    app, db = world
    _seed_member(db)
    snap = _snap(db)
    status, body = _apply(app, _photographer_body(app, "new.applicant@example.com"))
    assert status == 200, body
    new_id = _member_from(body)
    token = _token_from(body)
    assert new_id and new_id != VICTIM_ID
    assert token
    _assert_intact(db, snap)
    viewed_status, viewed = _status(app, new_id, token)
    assert viewed_status == 200, viewed
    assert POLICY_NUMBER not in _dump(viewed)
    assert INSURER_NAME not in _dump(viewed)
    denied, _hidden = _status(app, VICTIM_ID, token)
    assert denied == 404

