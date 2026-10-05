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


def test_rate_limited_response_includes_go_cors():
    ip = "198.51.100.44"
    rl._hits[ip] = [time.time()] * 10
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
        rl._hits.pop(ip, None)
