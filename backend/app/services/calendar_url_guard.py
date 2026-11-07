"""HTTPS-only fetch for vendor ICS and CalDAV URLs.

Rejects private, loopback, link-local, metadata, and other non-global
destinations, including IPv6 and redirects. Credentials are not forwarded
to a different host. DNS is checked again immediately before each request
and the connection is pinned to an address that passed that check.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 3
MAX_BYTES = 2_000_000
_BLOCKED_HOSTS = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.google.com",
    "instance-data",
}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")


class UnsafeCalendarURL(ValueError):
    """The URL is not safe to fetch from the Dispatch API."""


def _is_ip(raw: str) -> bool:
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return False
    return True


def _blocked_ip(raw: str) -> bool:
    """True when raw is an IP that must not be contacted. Non-IPs return False."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or not ip.is_global
    ):
        return True
    return False


def resolve_host(host: str, port: int) -> list[str]:
    """All A/AAAA answers. Tests replace this to avoid live DNS."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeCalendarURL("Calendar host did not resolve") from e
    ips = []
    for info in infos:
        addr = info[4][0]
        if addr not in ips:
            ips.append(addr)
    if not ips:
        raise UnsafeCalendarURL("Calendar host did not resolve")
    return ips


def assert_calendar_url_safe(url: str, *, resolver: Optional[Callable[..., list[str]]] = None) -> dict:
    """Return {url, host, port, ips} or raise UnsafeCalendarURL."""
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        raise UnsafeCalendarURL("Calendar URL must be HTTPS")
    if parts.username or parts.password:
        raise UnsafeCalendarURL("Calendar URL must not include a username or password")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeCalendarURL("Calendar host is not allowed")
    if host.isdigit() or host.startswith("0x"):
        raise UnsafeCalendarURL("Calendar host is not allowed")
    if _blocked_ip(host):
        raise UnsafeCalendarURL("Calendar host is not a public address")
    port = parts.port or 443
    if port != 443:
        raise UnsafeCalendarURL("Calendar URL must use port 443")
    lookup = resolver or resolve_host
    ips = list(lookup(host, port))
    if not ips or any(not _is_ip(ip) or _blocked_ip(ip) for ip in ips):
        raise UnsafeCalendarURL("Calendar host resolves to a blocked address")
    return {"url": raw, "host": host, "port": port, "ips": ips}


def _pin_url(url: str, ip: str) -> str:
    parts = urlsplit(url)
    host = f"[{ip}]" if ":" in ip else ip
    netloc = host  # port is 443; omit it
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


async def _send(
    method: str,
    url: str,
    *,
    headers: dict,
    content: Optional[bytes],
    auth: Any,
    timeout: float,
    sni_hostname: Optional[str],
) -> httpx.Response:
    extensions = {"sni_hostname": sni_hostname} if sni_hostname else None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        return await client.request(
            method,
            url,
            headers=headers,
            content=content,
            auth=auth,
            extensions=extensions,
        )


def _location(current: str, resp: httpx.Response) -> str:
    loc = resp.headers.get("location") or resp.headers.get("Location") or ""
    if not loc:
        raise UnsafeCalendarURL("Calendar redirect is missing a location")
    return urljoin(current, loc)


async def guarded_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    content: Optional[bytes] = None,
    auth: Any = None,
    timeout: float = 12.0,
    resolver: Optional[Callable[..., list[str]]] = None,
) -> httpx.Response:
    """Fetch one HTTPS calendar URL. Redirects are checked one hop at a time."""
    current = (url or "").strip()
    original_host = ""
    seen: set[str] = set()
    base_headers = {
        k: v
        for k, v in (headers or {}).items()
        if k.lower() not in ("authorization", "cookie", "proxy-authorization")
    }
    for hop in range(MAX_REDIRECTS + 1):
        checked = assert_calendar_url_safe(current, resolver=resolver)
        if not original_host:
            original_host = checked["host"]
        if current in seen:
            raise UnsafeCalendarURL("Calendar URL redirected to itself")
        seen.add(current)
        pinned = _pin_url(current, checked["ips"][0])
        use_auth = auth if checked["host"] == original_host else None
        req_headers = dict(base_headers)
        req_headers["Host"] = checked["host"]
        resp = await _send(
            method,
            pinned,
            headers=req_headers,
            content=content,
            auth=use_auth,
            timeout=timeout,
            sni_hostname=checked["host"],
        )
        size = resp.headers.get("content-length")
        try:
            if size is not None and int(size) > MAX_BYTES:
                raise UnsafeCalendarURL("Calendar response is too large")
        except UnsafeCalendarURL:
            raise
        except ValueError:
            pass
        body = getattr(resp, "content", b"") or b""
        if isinstance(body, str):
            body = body.encode()
        if len(body) > MAX_BYTES:
            raise UnsafeCalendarURL("Calendar response is too large")
        if resp.status_code in (301, 302, 303, 307, 308):
            current = _location(current, resp)
            if resp.status_code in (301, 302, 303):
                method = "GET"
                content = None
            continue
        return resp
    raise UnsafeCalendarURL("Calendar URL redirected too many times")
