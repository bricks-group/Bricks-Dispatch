"""Anonymous applicant sample-upload minting must not be able to fill storage.

POST /api/onboarding/sample-uploads is mounted on the Vercel app
(backend/api/index.py -> server.py) and on backend/app/main.py. These tests
drive that route with no credentials. They fail while an anonymous caller
receives signed upload URLs for brxs-deliverables, and they pass once that
caller is refused before any signed URL is minted and before any object is
stored.

The signed-upload stand-in follows storage3 2.33.0
(storage3/_async/file_api.py create_signed_upload_url: path plus an optional
upsert header, no file size) and Supabase Storage v1.99.5
(signUploadObjectUrl JWT claims are owner/url/upsert/scope; the PUT is capped
by the bucket and global file-size limit, not by the size this API was told).
The global cap used here is the 50 MB free-plan ceiling this repo documents
in app/services/upload_backend.py (SUPABASE_FREE_MAX_MB).

Other signed-upload routes are not this bug: POST /api/uploads/sign,
POST /api/capture frame signing, and POST /api/capture/visit/sign all go
through an authenticated vendor (or internal key) before they mint. They are
left untested here.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services import applicant_evidence as ev
from app.services.upload_backend import SUPABASE_FREE_MAX_MB
from app.shared import rate_limit as rl

# Placeholder only. Production reads APPLICANT_UPLOAD_SECRET (or the fallbacks
# in applicant_evidence._session_secret). This is not a live credential.
_UPLOAD_SECRET = "test-applicant-upload-secret"

_DECLARED_SAMPLE_BYTES = 1_000_000
# Larger than the declared size and the 15 MB sample cap, smaller than the
# 50 MB bucket/global ceiling the signed PUT actually enforces.
_ATTACK_BYTES = 16 * 1024 * 1024


class _SignedUploadBucket:
    """storage3 create_signed_upload_url + Storage v1.99.5 signed PUT."""

    def __init__(self, store: "_Deliverables", name: str):
        self.store = store
        self.name = name

    async def create_signed_upload_url(self, path, options=None):
        upsert = False
        if options is not None:
            raw = getattr(options, "upsert", None)
            if raw is None and isinstance(options, dict):
                raw = options.get("upsert")
            upsert = bool(raw)
        # Storage v1.99.5 signUploadObjectUrl. The JWT has no size claim.
        claims = {
            "owner": None,
            "url": f"{self.name}/{path}",
            "upsert": upsert,
            "scope": "upload",
        }
        token = json.dumps(claims, separators=(",", ":"))
        signed = f"https://storage.example/object/upload/sign/{self.name}/{path}?token={token}"
        self.store.sign_calls.append(
            {"bucket": self.name, "path": path, "claims": claims, "options": options}
        )
        return {"signed_url": signed, "signedUrl": signed, "token": token, "path": path}


class _Deliverables:
    def __init__(self):
        self.sign_calls: list[dict] = []
        self.objects: dict[str, bytes] = {}
        self.bucket_name: str | None = None
        # getStandardMaxFileSizeLimit: bucket file_size_limit is unset here,
        # so the cap is the project global limit.
        self.max_file_size = int(SUPABASE_FREE_MAX_MB * 1024 * 1024)

    def from_(self, name: str) -> _SignedUploadBucket:
        self.bucket_name = name
        return _SignedUploadBucket(self, name)

    def put_signed(self, path: str, token: str, body: bytes) -> bool:
        """Attacker PUT of the signed URL. Returns True only if the object is kept.

        Mirrors uploadSignedObject -> verifyObjectSignature -> uploadFromRequest
        -> fileUploadFromRequest / ByteCounterStream: a body over the bucket
        or global limit errors and is not kept. Nothing in the token tightens
        that limit to the size the client declared to this API.
        """
        claims = json.loads(token)
        if claims.get("scope") != "upload":
            return False
        if claims.get("url") != f"{self.bucket_name}/{path}":
            return False
        if len(body) > self.max_file_size:
            return False
        self.objects[path] = body
        return True


class _DB:
    def __init__(self, storage: _Deliverables):
        self.storage = storage


def _sample_body(session: str | None = None) -> dict:
    files = [
        {"name": f"sample-{i}.jpg", "type": "image/jpeg", "size": _DECLARED_SAMPLE_BYTES, "kind": "sample"}
        for i in range(ev.MAX_SAMPLES)
    ]
    files.append(
        {"name": "rpas.jpg", "type": "image/jpeg", "size": 500_000, "kind": "rpas_cert"}
    )
    files.append(
        {"name": "resume.pdf", "type": "application/pdf", "size": 50_000, "kind": "resume"}
    )
    body: dict = {"files": files}
    if session is not None:
        body["session"] = session
    return body


def _post_json(app, path: str, payload: dict, client_host: str) -> tuple[int, dict]:
    _load_real("httpx")
    import httpx

    async def _send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://dispatch-api.brxs.ca"
        ) as client:
            return await client.post(
                path,
                json=payload,
                headers={"x-real-ip": client_host},
            )

    response = asyncio.run(_send())
    try:
        parsed = response.json()
    except json.JSONDecodeError:
        parsed = {"_raw": response.text}
    return response.status_code, parsed if isinstance(parsed, dict) else {"_json": parsed}


def _fresh_limiter():
    """A new serverless isolate does not share the in-process window."""
    rl._hits.clear()


@pytest.fixture
def upload_secret(monkeypatch):
    monkeypatch.setenv("APPLICANT_UPLOAD_SECRET", _UPLOAD_SECRET)
    monkeypatch.delenv("CAPTURE_PUT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


@pytest.fixture
def deliverables(monkeypatch):
    store = _Deliverables()

    async def _get_db():
        return _DB(store)

    monkeypatch.setattr("app.services.storage.get_db", _get_db)
    return store


def _assert_refused(status: int, payload: dict, store: _Deliverables):
    assert status >= 400, payload
    assert store.sign_calls == [], store.sign_calls
    assert store.objects == {}


def _store_attack_body(payload: dict, store: _Deliverables) -> None:
    uploads = payload.get("uploads") or []
    blob = b"\xff\xd8\xff" + (b"A" * (_ATTACK_BYTES - 3))
    assert len(blob) == _ATTACK_BYTES
    assert _ATTACK_BYTES > _DECLARED_SAMPLE_BYTES
    assert _ATTACK_BYTES > ev.MAX_SAMPLE_BYTES
    assert _ATTACK_BYTES < store.max_file_size
    for item in uploads:
        if (item.get("kind") or "sample") != "sample":
            continue
        kept = store.put_signed(item["path"], item["token"], blob)
        claim_keys = sorted((store.sign_calls[0]["claims"] if store.sign_calls else {}).keys())
        assert kept is False, (
            f"anonymous mint stored {len(blob)} bytes at {item['path']} "
            f"in bucket {store.bucket_name}; declared size was {_DECLARED_SAMPLE_BYTES}; "
            f"signed-upload cap was {store.max_file_size}; "
            f"minted {len(uploads)} urls across {len(store.sign_calls)} signer calls; "
            f"token claims {claim_keys}"
        )
        assert item["path"] not in store.objects
        return
    if uploads:
        pytest.fail(f"signed uploads were minted with no sample object: {list(uploads[0])}")


def _load_real(name: str) -> None:
    """tests/conftest.py replaces some installed packages with MagicMock."""
    import sys

    mod = sys.modules.get(name)
    if mod is not None and not getattr(mod, "__file__", None):
        del sys.modules[name]
        for key in list(sys.modules):
            if key.startswith(name + "."):
                del sys.modules[key]


@pytest.fixture(scope="module")
def vercel_app():
    _load_real("sentry_sdk")
    _load_real("dotenv")
    import server

    return server.app


@pytest.fixture(scope="module")
def railway_app():
    # main.py's limiter is SlowAPIMiddleware, not the conftest MagicMock.
    _load_real("slowapi")
    _load_real("sentry_sdk")
    from app.main import app

    return app


def test_vercel_app_anonymous_sample_upload_does_not_mint(
    vercel_app, upload_secret, deliverables
):
    _fresh_limiter()
    status, payload = _post_json(
        vercel_app, "/api/onboarding/sample-uploads", _sample_body(), "203.0.113.10"
    )
    if status < 400:
        _store_attack_body(payload, deliverables)
    _assert_refused(status, payload, deliverables)


def test_fresh_isolate_and_second_ip_still_cannot_mint(
    vercel_app, upload_secret, deliverables
):
    """Each address is a new process-local window, the way a new instance is."""
    for host in ("203.0.113.20", "203.0.113.21"):
        _fresh_limiter()
        deliverables.sign_calls.clear()
        deliverables.objects.clear()
        status, payload = _post_json(
            vercel_app, "/api/onboarding/sample-uploads", _sample_body(), host
        )
        if status < 400:
            _store_attack_body(payload, deliverables)
        _assert_refused(status, payload, deliverables)


def test_valid_upload_session_is_not_applicant_proof(
    vercel_app, upload_secret, deliverables
):
    _fresh_limiter()
    session = ev.mint_upload_session()
    assert ev.verify_upload_session(session)
    status, payload = _post_json(
        vercel_app,
        "/api/onboarding/sample-uploads",
        _sample_body(session),
        "203.0.113.30",
    )
    if status < 400:
        _store_attack_body(payload, deliverables)
    _assert_refused(status, payload, deliverables)


def test_railway_main_anonymous_sample_upload_does_not_mint(
    railway_app, upload_secret, deliverables
):
    status, payload = _post_json(
        railway_app, "/api/onboarding/sample-uploads", _sample_body(), "203.0.113.40"
    )
    if status < 400:
        _store_attack_body(payload, deliverables)
    _assert_refused(status, payload, deliverables)
