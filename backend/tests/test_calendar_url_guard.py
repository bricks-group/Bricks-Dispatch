"""ICS and CalDAV fetches stay on public HTTPS and do not follow unsafe redirects."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.calendar_url_guard import UnsafeCalendarURL, assert_calendar_url_safe, guarded_request


def _public(_host, _port):
    return ["93.184.216.34"]


def _loopback(_host, _port):
    return ["127.0.0.1"]


def test_https_public_host_is_allowed():
    out = assert_calendar_url_safe("https://example.com/cal.ics", resolver=_public)
    assert out["ips"] == ["93.184.216.34"]
    assert out["port"] == 443


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/cal.ics",
        "https://user:pw@example.com/cal.ics",
        "https://example.com:8443/cal.ics",
        "https://127.0.0.1/cal.ics",
        "https://10.1.2.3/cal.ics",
        "https://192.168.1.1/cal.ics",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/cal.ics",
        "https://[fd00::1]/cal.ics",
        "https://[fe80::1]/cal.ics",
        "https://localhost/cal.ics",
        "https://metadata.google.internal/",
        "file:///etc/passwd",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeCalendarURL):
        assert_calendar_url_safe(url, resolver=_public)


def test_dns_to_private_address_rejected():
    with pytest.raises(UnsafeCalendarURL):
        assert_calendar_url_safe("https://example.com/cal.ics", resolver=_loopback)


def test_every_dns_answer_must_be_public():
    def mixed(_host, _port):
        return ["93.184.216.34", "10.0.0.5"]

    with pytest.raises(UnsafeCalendarURL):
        assert_calendar_url_safe("https://example.com/cal.ics", resolver=mixed)


def test_ipv6_and_mapped_private_rejected():
    def mapped(_host, _port):
        return ["::ffff:169.254.169.254"]

    with pytest.raises(UnsafeCalendarURL):
        assert_calendar_url_safe("https://example.com/cal.ics", resolver=mapped)

    def nat64(_host, _port):
        return ["64:ff9b::127.0.0.1"]

    with pytest.raises(UnsafeCalendarURL):
        assert_calendar_url_safe("https://example.com/cal.ics", resolver=nat64)


@pytest.mark.asyncio
async def test_redirect_to_metadata_is_not_fetched(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, status, location="", text="BEGIN:VCALENDAR"):
            self.status_code = status
            self.headers = {"location": location} if location else {}
            self.text = text
            self.content = text.encode()

    async def send(method, url, *, headers, content, auth, timeout, sni_hostname):
        calls.append({"url": url, "auth": auth, "host": headers.get("Host"), "sni": sni_hostname})
        if len(calls) == 1:
            return Resp(302, "https://169.254.169.254/latest/meta-data")
        return Resp(200)

    monkeypatch.setattr("app.services.calendar_url_guard._send", send)
    with pytest.raises(UnsafeCalendarURL):
        await guarded_request(
            "GET",
            "https://example.com/cal.ics",
            auth=("user", "secret"),
            resolver=_public,
        )
    assert len(calls) == 1
    assert calls[0]["auth"] == ("user", "secret")
    assert "169.254.169.254" not in calls[0]["url"]


@pytest.mark.asyncio
async def test_redirect_drops_credentials_and_pins_public_ip(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, status, location="", text="BEGIN:VCALENDAR"):
            self.status_code = status
            self.headers = {"location": location} if location else {}
            self.text = text
            self.content = text.encode()

    def resolver(host, _port):
        return {"example.com": ["93.184.216.34"], "cal.example.net": ["1.1.1.1"]}[host]

    async def send(method, url, *, headers, content, auth, timeout, sni_hostname):
        calls.append({"url": url, "auth": auth, "host": headers.get("Host"), "sni": sni_hostname, "method": method})
        if len(calls) == 1:
            return Resp(302, "https://cal.example.net/feed.ics")
        return Resp(200, text="BEGIN:VCALENDAR")

    monkeypatch.setattr("app.services.calendar_url_guard._send", send)
    resp = await guarded_request(
        "REPORT",
        "https://example.com/dav/",
        auth=("user", "secret"),
        content=b"<xml/>",
        resolver=resolver,
    )
    assert resp.status_code == 200
    assert calls[0]["url"].startswith("https://93.184.216.34/")
    assert calls[0]["host"] == "example.com"
    assert calls[0]["sni"] == "example.com"
    assert calls[0]["auth"] == ("user", "secret")
    assert calls[1]["url"].startswith("https://1.1.1.1/")
    assert calls[1]["auth"] is None
    assert calls[1]["method"] == "GET"
