"""Network-level SSRF guard tests. The suite conftest stubs httpx with MagicMock,
so run these with real httpx:  python -m pytest --noconftest tests/test_calendar_url_guard_net.py"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

if isinstance(httpx, MagicMock):  # pragma: no cover
    pytest.skip("httpx stubbed by conftest; run with --noconftest", allow_module_level=True)

from app.services import calendar_url_guard as guard


def _mock_client(handler, monkeypatch):
    real = httpx.AsyncClient

    class C(real):
        def __init__(self, **kw):
            kw.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(guard.httpx, "AsyncClient", C)


def test_connection_is_pinned_to_vetted_ip(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["host"] = str(request.url), request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR")
    _mock_client(handler, monkeypatch)
    st, text = asyncio.run(guard.guarded_request("GET", "https://cal.example.com/a.ics", resolver=lambda *_: ["93.184.216.34"]))
    assert st == 200 and "VCALENDAR" in text
    assert seen["url"].startswith("https://93.184.216.34/") and seen["host"] == "cal.example.com" and seen["sni"] == "cal.example.com"


def test_redirect_to_private_rejected(monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://internal.example.com/meta"})
    _mock_client(handler, monkeypatch)
    res = {"cal.example.com": ["93.184.216.34"], "internal.example.com": ["169.254.169.254"]}
    with pytest.raises(guard.UnsafeCalendarURL):
        asyncio.run(guard.guarded_request("GET", "https://cal.example.com/a.ics", resolver=lambda h, p: res[h]))


def test_redirect_loop_capped(monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://cal.example.com/again"})
    _mock_client(handler, monkeypatch)
    with pytest.raises(guard.UnsafeCalendarURL):
        asyncio.run(guard.guarded_request("GET", "https://cal.example.com/a.ics", resolver=lambda *_: ["93.184.216.34"]))


def test_size_limit_stops_reading(monkeypatch):
    chunks = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(50):
                chunks.append(i)
                yield b"x" * 1_000_000
    _mock_client(lambda request: httpx.Response(200, stream=Body()), monkeypatch)
    with pytest.raises(guard.UnsafeCalendarURL):
        asyncio.run(guard.guarded_request("GET", "https://cal.example.com/a.ics", resolver=lambda *_: ["93.184.216.34"], max_bytes=2_000_000))
    assert len(chunks) <= 3


