"""Owner email after /photographers apply must only claim what was checked.

Regression: https://blueelitefilms.ca does not resolve, yet the email said
"The portfolio looks like a fit for this work." The old bullet came from a
text-only model reading the URL string; nothing ever opened the link.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import portfolio_check as pc
from app.services.portfolio_check import (
    FAILED,
    INSTAGRAM,
    OK_IMAGE,
    OK_PAGE,
    LinkCheck,
    PortfolioCheck,
    check_link,
    check_portfolio,
    classify_html,
)
from app.services.vendor_selection import (
    location_check,
    notify_owner_vendor_application,
    vendor_application_email_body,
    vendor_application_facts,
)

REAL_PAGE = (
    "<!doctype html><html><head><title>Jane Doe Photography</title>"
    '<meta property="og:image" content="https://x/y.jpg"></head><body>'
    "<h1>Jane Doe Real Estate Photography</h1><p>Interiors, exteriors and drone "
    "work across Toronto.</p><img src='/a.jpg'><img src='/b.jpg'></body></html>"
)
PARKED_PAGE = (
    "<html><head><title>blueelitefilms.ca</title></head><body>"
    "<h1>This domain is for sale!</h1><p>Make an offer on this domain.</p></body></html>"
)


def _public(monkeypatch, verdict=True):
    async def fake(host, port):
        return verdict(host) if callable(verdict) else verdict
    monkeypatch.setattr(pc, "_host_is_public", fake)


# conftest stubs httpx with a MagicMock, so tests use a small stand-in that
# speaks the two calls portfolio_check makes: client.stream() and aiter_bytes().
class _Timeout(Exception):
    pass


class _ConnectError(Exception):
    pass


class _FakeHttpx:
    TimeoutException = _Timeout
    ConnectError = _ConnectError


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch):
    monkeypatch.setattr(pc, "httpx", _FakeHttpx)


class _Resp:
    def __init__(self, status_code, text="", content=None, headers=None):
        self.status_code = status_code
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._body = content if content is not None else text.encode()

    async def aiter_bytes(self):
        for i in range(0, len(self._body), 1024):
            yield self._body[i:i + 1024]


class _Req:
    def __init__(self, url):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        self.host = parts.hostname
        self.path = parts.path or "/"


class _Client:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def stream(self, method, url):
        client = self

        class _CM:
            async def __aenter__(self_inner):
                client.calls.append(url)
                return client.handler(_Req(url))

            async def __aexit__(self_inner, *a):
                return False

        return _CM()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _client(handler):
    return _Client(handler)


# ---- link fetch -------------------------------------------------------------

@pytest.mark.asyncio
async def test_dns_failure_is_not_opened(monkeypatch):
    _public(monkeypatch, None)
    called = []
    async with _client(lambda r: called.append(r) or _Resp(200, text=REAL_PAGE)) as c:
        res = await check_link("https://blueelitefilms.ca", client=c)
    assert res == LinkCheck("https://blueelitefilms.ca", FAILED, "dns")
    assert called == []


@pytest.mark.asyncio
async def test_real_dns_failure_end_to_end():
    # .invalid never resolves (RFC 2606). No network mock: the real resolver says no.
    class _Unused:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return _Client(lambda r: (_ for _ in ()).throw(AssertionError("no request")))

        async def __aexit__(self, *a):
            return False

    _FakeHttpx.AsyncClient = _Unused
    res = await check_link("https://blueelitefilms.invalid/")
    assert res.status == FAILED
    assert res.detail in ("dns", "blocked", "connect", "timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [404, 500, 403, 410])
async def test_non_2xx_is_not_opened(monkeypatch, code):
    _public(monkeypatch)
    async with _client(lambda r: _Resp(code, text="nope")) as c:
        res = await check_link("https://example.com/p", client=c)
    assert res.status == FAILED and res.detail == f"http_{code}"


@pytest.mark.asyncio
async def test_timeout_is_not_opened(monkeypatch):
    _public(monkeypatch)

    def handler(request):
        raise _Timeout("slow")

    async with _client(handler) as c:
        res = await check_link("https://example.com/slow", client=c)
    assert res.status == FAILED and res.detail == "timeout"


@pytest.mark.asyncio
async def test_parked_and_empty_pages_are_not_opened(monkeypatch):
    _public(monkeypatch)
    pages = {
        "/parked": PARKED_PAGE,
        "/empty": "<html><head></head><body>   </body></html>",
        "/blank": "",
    }
    async with _client(lambda r: _Resp(
        200, text=pages[r.path], headers={"content-type": "text/html"}
    )) as c:
        parked = await check_link("https://example.com/parked", client=c)
        empty = await check_link("https://example.com/empty", client=c)
        blank = await check_link("https://example.com/blank", client=c)
    assert (parked.status, parked.detail) == (FAILED, "parked")
    assert (empty.status, empty.detail) == (FAILED, "empty")
    assert (blank.status, blank.detail) == (FAILED, "empty")


@pytest.mark.asyncio
async def test_real_page_and_image_open(monkeypatch):
    _public(monkeypatch)

    def handler(request):
        if request.path == "/a.jpg":
            return _Resp(200, content=b"\xff\xd8\xff" + b"0" * 50,
                                  headers={"content-type": "image/jpeg"})
        return _Resp(200, text=REAL_PAGE, headers={"content-type": "text/html"})

    async with _client(handler) as c:
        page = await check_link("https://example.com/", client=c)
        image = await check_link("https://example.com/a.jpg", client=c)
    assert page.status == OK_PAGE
    assert image.status == OK_IMAGE
    assert classify_html(REAL_PAGE) == ""


@pytest.mark.asyncio
async def test_redirect_followed_and_private_target_blocked(monkeypatch):
    _public(monkeypatch, lambda host: host != "internal.local")

    def handler(request):
        if request.host == "example.com" and request.path == "/go":
            return _Resp(302, headers={"location": "https://example.org/home"})
        if request.host == "example.com" and request.path == "/evil":
            return _Resp(302, headers={"location": "http://internal.local/admin"})
        return _Resp(200, text=REAL_PAGE, headers={"content-type": "text/html"})

    async with _client(handler) as c:
        ok = await check_link("https://example.com/go", client=c)
        bad = await check_link("https://example.com/evil", client=c)
    assert ok.status == OK_PAGE
    assert (bad.status, bad.detail) == (FAILED, "blocked")


@pytest.mark.asyncio
async def test_instagram_is_not_fetched(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("instagram must not be fetched")
    monkeypatch.setattr(pc, "_fetch", boom)
    res = await check_portfolio(["https://www.instagram.com/blueelitefilms/"])
    assert res.links == (LinkCheck("https://www.instagram.com/blueelitefilms/", INSTAGRAM),)
    assert res.needs_review is True


def test_private_addresses_are_not_public():
    for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "fd00::1"):
        assert pc._ip_is_public(ip) is False
    assert pc._ip_is_public("93.184.216.34") is True


# ---- location ---------------------------------------------------------------

@pytest.mark.parametrize("city", ["Türkiye/ toronto", "Turkey / Toronto", "Hamilton",
                                  "Montreal", "Toronto & Vancouver", "Ontario", "", "Canada"])
def test_location_unclear(city):
    line, unclear = location_check(city)
    assert unclear is True
    assert "fits" not in line.lower() and "looks like a fit" not in line.lower()
    if city:
        assert line == f"Location unclear / may not be in the GTA (says: {city})."


@pytest.mark.parametrize("city", ["Toronto", "Toronto, ON", "Mississauga, Ontario, Canada",
                                  "North York", "Downtown Toronto", "GTA", "Toronto/Mississauga",
                                  "Richmond Hill ON"])
def test_location_self_reported_gta(city):
    line, unclear = location_check(city)
    assert unclear is False
    assert line == f"Self-reported city: {city} (GTA)."


# ---- owner email ------------------------------------------------------------

def _body(check, links, city="Toronto", prob=0.3, equipment=("Sony A7III",), drone=True):
    facts, review = vendor_application_facts(
        city, links, portfolio_check=check, equipment=list(equipment), drone=drone,
    )
    return vendor_application_email_body(
        "Test Applicant", city, "photographer", prob, (), links, facts, review
    )


def test_unreachable_portfolio_needs_review_not_fit():
    url = "https://blueelitefilms.ca"
    body = _body(PortfolioCheck((LinkCheck(url, FAILED, "dns"),)), [url])
    assert "- Portfolio couldn't be opened (link: https://blueelitefilms.ca)." in body
    assert "This needs review." in body
    assert "does not look like a weak fit" not in body
    assert "looks like a fit" not in body


def test_instagram_only_is_not_reviewed():
    url = "https://instagram.com/someone"
    body = _body(PortfolioCheck((LinkCheck(url, INSTAGRAM),)), [url])
    assert "- Instagram link provided, not reviewed." in body
    assert "This needs review." in body
    assert "looks like a fit" not in body


def test_opened_page_is_not_called_a_fit():
    url = "https://janedoe.example.com"
    body = _body(PortfolioCheck((LinkCheck(url, OK_PAGE),)), [url])
    assert "- Portfolio opened, work not reviewed (link: https://janedoe.example.com)." in body
    assert "This does not look like a weak fit." in body
    assert "looks like a fit for this work" not in body


def test_turkiye_toronto_flags_location_and_self_reported_bullets():
    url = "https://janedoe.example.com"
    body = _body(PortfolioCheck((LinkCheck(url, OK_PAGE),)), [url], city="Türkiye/ toronto")
    assert "- Location unclear / may not be in the GTA (says: Türkiye/ toronto)." in body
    assert "This needs review." in body
    assert "- Says they have: Sony A7III." in body
    assert "- Says they are drone certified, not verified." in body
    assert "city looks like a fit" not in body.lower()


def test_weak_score_still_wins_and_no_score_no_name():
    url = "https://blueelitefilms.ca"
    check = PortfolioCheck((LinkCheck(url, FAILED, "dns"),))
    assert "This looks like a weak fit." in _body(check, [url], prob=0.9)
    body = _body(check, [url], prob=None)
    assert "This needs review." in body
    for text in (body, _body(check, [url], prob=0.42)):
        assert "jev" not in text.lower()
        assert "0.42" not in text and "0.9" not in text


def test_missing_check_is_not_a_fit():
    url = "https://janedoe.example.com"
    body = _body(None, [url])
    assert "- Portfolio not checked (link: https://janedoe.example.com)." in body
    assert "This needs review." in body


@pytest.mark.asyncio
async def test_notify_sends_only_to_admin_with_honest_bullets():
    url = "https://blueelitefilms.ca"
    db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute = AsyncMock(side_effect=RuntimeError("no db"))
    db.table.return_value = chain
    email = AsyncMock(return_value=True)
    with patch("app.services.notifications.send_email_via_resend", email):
        await notify_owner_vendor_application(
            db, name="Test Applicant", city="Türkiye/ toronto", vendor_type="photographer",
            yes_probability=0.2, reasons=(), portfolio_links=[url],
            portfolio_check=PortfolioCheck((LinkCheck(url, FAILED, "dns"),)),
            equipment=["Canon R6"], drone=False,
        )
    kwargs = email.await_args.kwargs
    assert kwargs["to"] == "admin@brxs.ca"
    html = kwargs["html"]
    assert "Portfolio couldn&#x27;t be opened" in html or "Portfolio couldn't be opened" in html
    assert "Location unclear / may not be in the GTA" in html
    assert "No drone certificate claimed." in html
    assert "This needs review." in html
    assert "looks like a fit" not in html


def test_no_fit_claims_left_in_source():
    root = Path(__file__).resolve().parents[1] / "app" / "services"
    for name in ("vendor_selection.py", "jev_hold.py"):
        src = (root / name).read_text()
        assert "The portfolio looks like a fit" not in src
        assert "The city looks like a fit" not in src
        assert "Drone certification looks like a fit" not in src
        assert "Equipment looks suitable" not in src


def test_found_us_via_line_kept_with_honest_bullets():
    url = "https://blueelitefilms.ca"
    check = PortfolioCheck((LinkCheck(url, FAILED, "dns"),))
    facts, review = vendor_application_facts("Toronto", [url], portfolio_check=check)
    body = vendor_application_email_body(
        "Test Applicant", "Toronto", "photographer", 0.3, (), [url], facts, review,
        source="instagram",
    )
    assert "Found us via: Instagram" in body
    assert "This needs review." in body
    assert "- Portfolio couldn't be opened (link: https://blueelitefilms.ca)." in body
