"""Unhandled exceptions must still carry CORS for allowed origins.

Starlette attaches @app.exception_handler(Exception) to ServerErrorMiddleware,
which sits outside CORSMiddleware. Without a catch-all inside CORS (or echoed
allowlist headers on the 500), the GO app shows 'Failed to fetch'.
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import Response

from app.shared.cors_config import cors_headers_for_origin, install_browser_middleware


GO_ORIGIN = "https://go.brxs.ca"
EVIL_ORIGIN = "https://evil.example"


class CatchAllErrorMiddleware:
    """Same shape as server.CatchAllErrorMiddleware (pure ASGI)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def send_wrapper(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if response_started:
                raise
            origin = ""
            for k, v in scope.get("headers") or []:
                if k == b"origin":
                    origin = v.decode()
                    break
            headers = cors_headers_for_origin(origin)
            response = Response(
                status_code=500,
                content='{"error":"internal_server_error"}',
                media_type="application/json",
                headers=headers,
            )
            await response(scope, receive, send)


def _production_stack() -> FastAPI:
    """Middleware order matches server.py: catch-all, then rate limit, then CORS."""
    app = FastAPI()

    @app.post("/onboard-unhandled")
    async def unhandled():
        raise RuntimeError("contractors_vendor_id_fkey")

    @app.post("/onboard-handled")
    async def handled():
        raise HTTPException(status_code=502, detail="stripe_connect_failed")

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        headers = cors_headers_for_origin(request.headers.get("origin"))
        return Response(
            status_code=500,
            content='{"error":"internal_server_error"}',
            media_type="application/json",
            headers=headers,
        )

    app.add_middleware(CatchAllErrorMiddleware)
    install_browser_middleware(app)
    return app


def _call(app: FastAPI, path: str, origin: str) -> tuple[int, dict[str, str]]:
    messages: list[dict] = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"origin", origin.encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("dispatch-api.brxs.ca", 443),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    return start["status"], headers


def test_unhandled_exception_includes_go_cors():
    status, headers = _call(_production_stack(), "/onboard-unhandled", GO_ORIGIN)
    assert status == 500, headers
    assert headers.get("access-control-allow-origin") == GO_ORIGIN
    assert headers.get("access-control-allow-credentials") == "true"


def test_unhandled_exception_does_not_echo_unknown_origin():
    status, headers = _call(_production_stack(), "/onboard-unhandled", EVIL_ORIGIN)
    assert status == 500, headers
    assert "access-control-allow-origin" not in headers


def test_handled_http_exception_still_has_cors():
    status, headers = _call(_production_stack(), "/onboard-handled", GO_ORIGIN)
    assert status == 502, headers
    assert headers.get("access-control-allow-origin") == GO_ORIGIN
    assert headers.get("access-control-allow-credentials") == "true"


def test_cors_headers_helper_never_uses_wildcard():
    assert "*" not in cors_headers_for_origin(GO_ORIGIN).values()
    assert cors_headers_for_origin(EVIL_ORIGIN) == {}
    assert cors_headers_for_origin(None) == {}
    assert cors_headers_for_origin("*") == {}
