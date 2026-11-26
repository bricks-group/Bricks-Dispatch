"""Form endpoints must enforce Starlette's request.form() field cap on urlencoded bodies.

FastAPI parses Form and File parameters by calling request.form() before the
route function runs. The pinned Starlette build applies the default field cap
to multipart bodies and does not apply it to application/x-www-form-urlencoded.
These tests are the spec for that gap: an over-cap urlencoded body must be
rejected the same way an over-cap multipart body already is, before the handler
runs. They fail while the urlencoded parser ignores the cap.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from fastapi import HTTPException
from starlette.requests import Request

# tests/conftest.py stubs sentry_sdk with a MagicMock before collection.
# The app imports sentry_sdk.integrations at startup, which that stub cannot satisfy.
for _name in [k for k in list(sys.modules) if k == "sentry_sdk" or k.startswith("sentry_sdk.")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from app.routers import onboarding, ops_autopilot, uploads, vendor_os, vendor_profile
from app.shared import rate_limit
from server import app

# request.form() default, and the multipart parser's default, in the pinned build.
FIELD_CAP = 1000
TOO_MANY = f"Too many fields. Maximum number of fields is {FIELD_CAP}."

# Every route whose body is parsed with request.form(). Form routes enter the
# handler once parsing succeeds. File routes still parse urlencoded bodies, then
# fail validation because the file part is absent.
FORM_ROUTES = [
    (
        "onboarding_documents",
        "/api/onboarding/documents",
        {"member_id": "m1", "doc_type": "coi", "filename": "a.pdf", "file_data": "QQ"},
        "onboarding",
    ),
    (
        "onboarding_portfolio",
        "/api/onboarding/portfolio",
        {"member_id": "m1", "media_type": "hdr", "filename": "a.jpg", "file_data": "QQ"},
        "onboarding",
    ),
    (
        "uploads_create",
        "/api/uploads",
        {"job_id": "j1", "filename": "a.jpg", "file_size": "1", "mime_type": "image/jpeg", "file_data": "QQ", "media_type": "photo"},
        "uploads",
    ),
    (
        "ops_act",
        "/api/ops/act",
        {"t": "not-a-signed-action"},
        "ops",
    ),
    (
        "ops_appeal",
        "/api/ops/appeal",
        {"t": "not-a-signed-action", "message": "hello"},
        "ops",
    ),
]

FILE_ROUTES = [
    ("noshow_photo", "/api/vendor-os/jobs/j1/noshow-photo", {"taken_at": "2026-01-01T00:00:00Z"}),
    ("checklist_photo", "/api/vendor-os/jobs/j1/checklist/i1/photos", {"taken_at": "2026-01-01T00:00:00Z"}),
    ("checklist_report", "/api/vendor-os/jobs/j1/checklist/i1/report", {}),
    ("profile_rpas", "/api/profile/rpas", {"level": "advanced", "certificate_number": "x"}),
    (
        "profile_insurance",
        "/api/profile/insurance",
        {"liability_cad": "100", "expires_on": "2027-01-01", "aviation_covered": "false", "insurer": "acme", "policy_number": "p"},
    ),
    ("profile_photo", "/api/profile/photo", {}),
    ("os_profile_rpas", "/api/vendor-os/profile/rpas", {"level": "advanced", "certificate_number": "x"}),
    (
        "os_profile_insurance",
        "/api/vendor-os/profile/insurance",
        {"liability_cad": "100", "expires_on": "2027-01-01", "aviation_covered": "false", "insurer": "acme", "policy_number": "p"},
    ),
    ("os_profile_photo", "/api/vendor-os/profile/photo", {}),
]


def _urlencoded(count: int, fields: dict, sep: str = "&") -> bytes:
    items = list(fields.items())
    n = 0
    while len(items) < count:
        items.append((f"p{n}", "1"))
        n += 1
    if len(items) != count:
        raise AssertionError(f"field count {len(items)} != {count}")
    return sep.join(f"{key}={value}" for key, value in items).encode()


def _multipart(count: int, fields: dict) -> tuple[bytes, str]:
    boundary = "----field-cap"
    items = list(fields.items())
    n = 0
    while len(items) < count:
        items.append((f"p{n}", "1"))
        n += 1
    chunks = []
    for key, value in items:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


async def _asgi(method: str, path: str, body: bytes, content_type: str) -> tuple[int, str]:
    rate_limit._hits.clear()
    sent = {"body": b"", "status": 0}
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        # BaseHTTPMiddleware drops the response body if the client disconnects
        # before the handler finishes sending it. Leave the connection open.
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", content_type.encode()),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("198.51.100.10", 443),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    return sent["status"], sent["body"].decode("utf-8", "replace")


def _post(path: str, body: bytes, content_type: str) -> tuple[int, str]:
    return asyncio.run(_asgi("POST", path, body, content_type))


@pytest.fixture
def handler_calls(monkeypatch):
    """Record handler entry and stop before any storage or database write."""
    calls = []

    async def _onboarding_db():
        calls.append("onboarding")
        raise HTTPException(status_code=404, detail="Member not found.")

    async def _uploads_actor(*_args, **_kwargs):
        calls.append("uploads")
        raise HTTPException(status_code=401, detail={"code": "auth_required", "message": "Authentication required"})

    def _ops_verify(_token, now=None):
        calls.append("ops")
        return None

    async def _no_upload(*_args, **_kwargs):
        calls.append("upload")
        raise AssertionError("form parsing reached a storage write")

    async def _no_profile_actor(*_args, **_kwargs):
        calls.append("profile")
        raise HTTPException(status_code=401, detail={"code": "auth_required", "message": "Authentication required"})

    async def _no_vendor_actor(*_args, **_kwargs):
        calls.append("vendor_os")
        raise HTTPException(status_code=401, detail={"code": "auth_required", "message": "Authentication required"})

    monkeypatch.setattr(onboarding, "get_db", _onboarding_db)
    monkeypatch.setattr(onboarding, "upload_file", _no_upload)
    monkeypatch.setattr(uploads, "resolve_vendor_actor_id", _uploads_actor)
    monkeypatch.setattr(uploads, "upload_file", _no_upload)
    monkeypatch.setattr(ops_autopilot.ops, "verify_action", _ops_verify)
    monkeypatch.setattr(vendor_profile, "_actor_member", _no_profile_actor)
    monkeypatch.setattr(vendor_os, "resolve_vendor_actor_id", _no_vendor_actor)
    return calls


def _assert_rejected(status: int, text: str, calls: list, route: str):
    assert status == 400, f"{route} status {status}: {text[:300]}"
    assert TOO_MANY in text, f"{route} body did not refuse at the field cap: {text[:300]}"
    assert calls == [], f"{route} ran the handler while refusing: {calls}"
    assert "upload" not in calls


@pytest.mark.parametrize("name,path,fields,spy", FORM_ROUTES, ids=[row[0] for row in FORM_ROUTES])
def test_over_cap_urlencoded_rejected_before_handler(name, path, fields, spy, handler_calls):
    body = _urlencoded(FIELD_CAP + 1, fields)
    status, text = _post(path, body, "application/x-www-form-urlencoded")
    _assert_rejected(status, text, handler_calls, name)


@pytest.mark.parametrize("name,path,fields,spy", FORM_ROUTES, ids=[row[0] for row in FORM_ROUTES])
def test_cap_urlencoded_still_reaches_handler(name, path, fields, spy, handler_calls):
    """The cap is exclusive: FIELD_CAP fields are a normal form post."""
    body = _urlencoded(FIELD_CAP, fields)
    status, text = _post(path, body, "application/x-www-form-urlencoded")
    assert TOO_MANY not in text
    assert "missing_internal_auth" not in text
    assert spy in handler_calls, f"{name} did not enter the handler (status {status}: {text[:300]})"
    assert "upload" not in handler_calls
    assert status < 500, f"{name} status {status}: {text[:300]}"


@pytest.mark.parametrize("name,path,fields", FILE_ROUTES, ids=[row[0] for row in FILE_ROUTES])
def test_over_cap_urlencoded_file_route_rejected_before_handler(name, path, fields, handler_calls):
    body = _urlencoded(FIELD_CAP + 1, fields)
    status, text = _post(path, body, "application/x-www-form-urlencoded")
    _assert_rejected(status, text, handler_calls, name)


@pytest.mark.parametrize("name,path,fields", FILE_ROUTES, ids=[row[0] for row in FILE_ROUTES])
def test_cap_urlencoded_file_route_is_parsed(name, path, fields, handler_calls):
    """At the cap, parsing finishes and validation reports the missing file."""
    body = _urlencoded(FIELD_CAP, fields)
    status, text = _post(path, body, "application/x-www-form-urlencoded")
    assert status == 422, f"{name} status {status}: {text[:300]}"
    assert TOO_MANY not in text
    assert "missing_internal_auth" not in text
    assert handler_calls == []


def test_semicolon_separators_count_toward_the_cap(handler_calls):
    """Both separators the urlencoded parser accepts must count as fields."""
    fields = {"member_id": "m1", "doc_type": "coi", "filename": "a.pdf", "file_data": "QQ"}
    body = _urlencoded(FIELD_CAP + 1, fields, sep=";")
    status, text = _post("/api/onboarding/documents", body, "application/x-www-form-urlencoded")
    _assert_rejected(status, text, handler_calls, "semicolon_documents")


def test_multipart_over_cap_rejected_before_handler(handler_calls):
    fields = {"member_id": "m1", "doc_type": "coi", "filename": "a.pdf", "file_data": "QQ"}
    body, content_type = _multipart(FIELD_CAP + 1, fields)
    status, text = _post("/api/onboarding/documents", body, content_type)
    _assert_rejected(status, text, handler_calls, "multipart_documents")


def test_multipart_at_cap_reaches_handler(handler_calls):
    fields = {"member_id": "m1", "doc_type": "coi", "filename": "a.pdf", "file_data": "QQ"}
    body, content_type = _multipart(FIELD_CAP, fields)
    status, text = _post("/api/onboarding/documents", body, content_type)
    assert TOO_MANY not in text
    assert "onboarding" in handler_calls
    assert "upload" not in handler_calls
    assert status == 404


def test_far_over_cap_urlencoded_rejected_before_handler(handler_calls):
    fields = {"t": "not-a-signed-action"}
    body = _urlencoded(FIELD_CAP * 5, fields)
    status, text = _post("/api/ops/act", body, "application/x-www-form-urlencoded")
    _assert_rejected(status, text, handler_calls, "ops_act_five_times_cap")


def test_request_form_default_cap_matches_route_behavior():
    """The cap the routes inherit is request.form()'s default, not a route argument."""

    async def parse(count: int):
        body = _urlencoded(count, {"t": "x"})
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/ops/act",
            "raw_path": b"/api/ops/act",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("198.51.100.10", 443),
            "server": ("test", 80),
            "app": app,
        }
        try:
            form = await Request(scope, receive).form()
        except HTTPException as exc:
            return exc.status_code, str(exc.detail), None
        return None, None, len(form)

    status, detail, count = asyncio.run(parse(FIELD_CAP + 1))
    assert status == 400, f"request.form() accepted {count} fields (detail={detail})"
    assert detail == TOO_MANY

    status, detail, count = asyncio.run(parse(FIELD_CAP))
    assert status is None and count == FIELD_CAP
