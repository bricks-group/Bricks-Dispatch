"""A rate-limited Dispatch response must still be readable by https://go.brxs.ca.

The auth tier returns 429 from RateLimitMiddleware without calling downstream.
If that response is emitted outside CORS, the browser reports net::ERR_FAILED
(no Access-Control-Allow-Origin) and GO treats /api/vendor-os/auth/me as logout.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from app.shared import rate_limit as rl
from app.shared.cors_config import install_browser_middleware


GO_ORIGIN = "https://go.brxs.ca"


class _PassThrough(BaseHTTPMiddleware):
    """Same shape as server.log_requests: outermost, always calls next."""

    async def dispatch(self, request, call_next):
        return await call_next(request)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/vendor-os/auth/me")
    async def me():
        return {"id": "vendor"}

    @app.get("/api/uploads")
    async def uploads():
        return {"uploads": []}

    @app.get("/health")
    async def health():
        return {"ok": True}

    # Production order, then the request logger which is registered later.
    install_browser_middleware(app)
    app.add_middleware(_PassThrough)
    return app


def _call(app: FastAPI, path: str, origin: str, forwarded_for: str) -> tuple[int, dict[str, str]]:
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"origin", origin.encode()),
            (b"x-forwarded-for", forwarded_for.encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("dispatch-api.brxs.ca", 443),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {
        k.decode().lower(): v.decode()
        for k, v in start.get("headers", [])
    }
    return start["status"], headers


def test_auth_tier_stays_ten_per_minute():
    tier = next(item for item in rl._TIERS if item[0] == "/api/vendor-os/auth/")
    assert tier[1] == 10
    assert tier[2] == 60


def test_non_auth_tiers_keep_their_own_limits():
    """Per-path max_r values stay what the limiter already intended."""
    expected = {
        "/api/onboarding/apply": (10, 60),
        "/api/onboarding/sample-uploads": (10, 60),
        "/api/v1/dispatch/jobs": (30, 60),
        "/api/jobs/": (30, 60),
        "/api/notifications": (30, 60),
        "/api/push-tokens": (30, 60),
        "/api/uploads": (30, 60),
    }
    got = {prefix: (max_r, window) for prefix, max_r, window in rl._TIERS}
    assert got == {**expected, "/api/vendor-os/auth/": (10, 60)}
    assert rl._DEFAULT_LIMIT == (100, 60)


def test_rate_limited_response_includes_go_cors():
    ip = "198.51.100.44"
    # Pre-fill only the auth bucket. Non-auth counters must not be involved.
    rl._hits[(ip, "/api/vendor-os/auth/")] = [time.time()] * 10
    app = _app()
    try:
        status, headers = _call(app, "/api/vendor-os/auth/me", GO_ORIGIN, ip)
        assert status == 429, headers
        assert headers.get("access-control-allow-origin") == GO_ORIGIN
        assert headers.get("access-control-allow-credentials") == "true"

        ok_status, ok_headers = _call(app, "/health", GO_ORIGIN, ip)
        assert ok_status == 200
        assert ok_headers.get("access-control-allow-origin") == headers.get(
            "access-control-allow-origin"
        )

        other_status, other_headers = _call(
            app, "/api/vendor-os/auth/me", "https://evil.example", ip
        )
        assert other_status == 429
        assert "access-control-allow-origin" not in other_headers
    finally:
        rl._hits.pop((ip, "/api/vendor-os/auth/"), None)


def test_uploads_do_not_consume_auth_budget():
    """Ten non-auth calls must not spend the 10/min auth counter."""
    ip = "198.51.100.60"
    app = _app()
    try:
        for _ in range(10):
            status, _headers = _call(app, "/api/uploads", GO_ORIGIN, ip)
            assert status != 429, "uploads itself is not a 10/min route"
        status, headers = _call(app, "/api/vendor-os/auth/me", GO_ORIGIN, ip)
        assert status != 429, headers
        assert headers.get("access-control-allow-origin") == GO_ORIGIN
    finally:
        rl._hits.clear()


def test_eleventh_auth_request_is_still_429():
    """Auth stays 10/minute on its own counter. The 11th call is 429 with CORS."""
    ip = "198.51.100.61"
    app = _app()
    try:
        for i in range(10):
            status, _headers = _call(app, "/api/vendor-os/auth/me", GO_ORIGIN, ip)
            assert status == 200, i
        status, headers = _call(app, "/api/vendor-os/auth/me", GO_ORIGIN, ip)
        assert status == 429, headers
        assert headers.get("access-control-allow-origin") == GO_ORIGIN
        assert headers.get("access-control-allow-credentials") == "true"
        # A non-auth call must not be blocked by the auth counter.
        other, _other_headers = _call(app, "/api/uploads", GO_ORIGIN, ip)
        assert other != 429
    finally:
        rl._hits.clear()


def test_default_origins_exclude_legacy_vercel_dispatch_web(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    from app.shared.cors_config import allowed_origins
    origins = allowed_origins()
    assert "https://bricks-dispatch-web.vercel.app" not in origins
    assert "https://go.brxs.ca" in origins
    assert "https://app.brxs.ca" in origins
